"""Credential-free Phase 8B shadow assurance runtime."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from types import MappingProxyType
from typing import Callable, Mapping
from secrets import token_hex
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.http_transport import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    UrlLibHttpTransport,
)
from secure_eval_wrapper.data_collection.models import (
    DataRequest,
    InstrumentType,
    MarketDataType,
)
from secure_eval_wrapper.execution.models import OrderSide
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence
from secure_eval_wrapper.signals.models import SignalDirection, StandardizedSignal

from .approval import (
    LiveApprovalController,
    confirmation_challenge_hash,
    manifest_preview_hash,
)
from .authorities import LiveRuntimeRiskState
from .configuration import phase8a_dry_run_configuration
from .endpoints import endpoint_catalog_hash
from .identity import RuntimeRepositoryIdentity, resolve_runtime_repository_identity
from .manifests import create_live_manifest
from .models import (
    LiveAccountSnapshot,
    LiveCredentialReference,
    LiveKillState,
    LiveOrderIntent,
    LivePreflightCheck,
    LivePreflightPurpose,
    LivePreflightReport,
    LivePreflightStatus,
    LiveReconciliationStatus,
)
from .provider_identity import OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH
from .reservations import calculate_live_reservation
from .risk import evaluate_live_risk
from .shadow_models import (
    ShadowDataProvenance,
    ShadowDecisionRecord,
    ShadowDecisionRequest,
    ShadowEvidenceClassification,
    ShadowMarketSnapshot,
    ShadowOrderIntent,
    ShadowRunSummary,
    ShadowSafetyFacts,
    SyntheticAccountSnapshot,
    SyntheticBalance,
    SyntheticPendingOrder,
    SyntheticPosition,
    shadow_uuid,
)
from .shadow_repository import (
    MemoryShadowRepository,
    PostgresShadowRepository,
    ShadowInjectedCrash,
)
from .shadow_scenarios import SHADOW_FIXTURE_TIME, ShadowScenarioSpec, scenario_by_id


RUNTIME_CRASH_POINTS = frozenset({
    "market_snapshot_normalized",
    "synthetic_account_validated",
    "risk_evaluated",
    "approval_created",
    "manifest_created",
    "before_decision_persist",
    "after_decision_persist_before_summary",
    "before_transaction_commit",
    "after_transaction_commit_before_response",
})


class ShadowAuthorityError(PermissionError):
    pass


_PUBLIC_ENDPOINT_IDENTITIES = (
    "GET /api/v5/public/instruments",
    "GET /api/v5/market/history-trades",
)
_AUDITED_URLLIB_TRANSPORT_TYPE = UrlLibHttpTransport
_AUDITED_URLLIB_SEND_METHOD = UrlLibHttpTransport.send
_SOURCE_INSTANCE_ID_FACTORY = token_hex


@dataclass(frozen=True, slots=True)
class _PublicSourceProvenance:
    source_exact_type: str
    endpoint_identities: tuple[str, ...]
    actual_send_count: int
    response_source_hashes: tuple[str, ...]
    instrument: str
    source_instance_id: str
    classification: str
    payload_hash: str
    failure_kind: str | None
    token_hash: str
    _capability: object


@dataclass(frozen=True, slots=True)
class _PublicMarketLoad:
    payload: Mapping[str, object]
    provenance: _PublicSourceProvenance


def _public_failure_kind(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message:
        return "timeout"
    if "429" in message or "rate limit" in message:
        return "rate_limit"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "malformed_json"
    return "connection_failure"


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be an ISO-8601 value")
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return result


def _parse_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("value is not a Decimal") from exc


def _provenance_core(
    *,
    source_exact_type: str,
    endpoint_identities: tuple[str, ...],
    actual_send_count: int,
    response_source_hashes: tuple[str, ...],
    instrument: str,
    source_instance_id: str,
    classification: str,
    payload_hash: str,
    failure_kind: str | None,
) -> dict[str, object]:
    return {
        "source_exact_type": source_exact_type,
        "endpoint_identities": endpoint_identities,
        "actual_send_count": actual_send_count,
        "response_source_hashes": response_source_hashes,
        "instrument": instrument,
        "source_instance_id": source_instance_id,
        "classification": classification,
        "payload_hash": payload_hash,
        "failure_kind": failure_kind,
    }


class ShadowPublicDataFailure(RuntimeError):
    def __init__(self, failure_kind: str, load: _PublicMarketLoad) -> None:
        self.failure_kind = failure_kind
        self.load = load
        self.provenance = load.provenance
        self.network_read_count = load.provenance.actual_send_count
        super().__init__("bounded public shadow market read failed")


class _CountingPublicTransport:
    __slots__ = (
        "_delegate",
        "_send_method",
        "actual_send_count",
        "endpoint_identities",
        "response_hashes",
    )

    def __init__(self, delegate: HttpTransport, *, send_method=None) -> None:
        self._delegate = delegate
        self._send_method = send_method
        self.actual_send_count = 0
        self.endpoint_identities: list[str] = []
        self.response_hashes: list[str] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        path = urlsplit(request.url).path
        identity = f"{request.method} {path}"
        if (
            self.actual_send_count >= len(_PUBLIC_ENDPOINT_IDENTITIES)
            or identity != _PUBLIC_ENDPOINT_IDENTITIES[self.actual_send_count]
            or request.method != "GET"
            or dict(request.headers)
            or any("auth" in str(key).lower() for key in request.headers)
        ):
            raise ShadowAuthorityError("public shadow transport rejected request identity")
        self.actual_send_count += 1
        self.endpoint_identities.append(identity)
        response = (
            self._delegate.send(request)
            if self._send_method is None
            else self._send_method(self._delegate, request)
        )
        if type(response) is not HttpResponse:
            raise ShadowAuthorityError("public shadow transport returned an invalid response type")
        if 200 <= response.status < 300:
            self.response_hashes.append(sha256_payload({
                "status": response.status,
                "body_sha256": sha256(response.body_bytes).hexdigest(),
                "headers": dict(response.headers),
            }))
        return response


class FixtureShadowMarketSource:
    """Exact fixture catalog source with no socket-capable dependency."""

    __slots__ = ()

    def load(self, scenario_id: str, *, at_utc: datetime) -> Mapping[str, object]:
        del at_utc
        return MappingProxyType(dict(scenario_by_id(scenario_id).market_payload))


class OkxPublicShadowMarketSource:
    """Construct only the audited transport and issue instance-bound provenance."""

    __slots__ = (
        "__timeout_seconds",
        "__transport",
        "__source_instance_id",
        "__provenance_capability",
        "__sealed",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_OkxPublicShadowMarketSource__sealed", False):
            raise AttributeError("public shadow source authority is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        allow_public_network: bool,
        timeout_seconds: float = 3.0,
    ) -> None:
        if allow_public_network is not True:
            raise ShadowAuthorityError("public network source requires the exact opt-in flag")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 10:
            raise ValueError("public network timeout must be in (0, 10] seconds")
        transport = _AUDITED_URLLIB_TRANSPORT_TYPE()
        if (
            type(transport) is not _AUDITED_URLLIB_TRANSPORT_TYPE
            or _AUDITED_URLLIB_TRANSPORT_TYPE.send is not _AUDITED_URLLIB_SEND_METHOD
        ):
            raise ShadowAuthorityError("audited public transport construction was replaced")
        object.__setattr__(
            self,
            "_OkxPublicShadowMarketSource__timeout_seconds",
            float(timeout_seconds),
        )
        object.__setattr__(
            self,
            "_OkxPublicShadowMarketSource__transport",
            transport,
        )
        object.__setattr__(
            self,
            "_OkxPublicShadowMarketSource__source_instance_id",
            _SOURCE_INSTANCE_ID_FACTORY(32),
        )
        object.__setattr__(
            self,
            "_OkxPublicShadowMarketSource__provenance_capability",
            object(),
        )
        object.__setattr__(
            self,
            "_OkxPublicShadowMarketSource__sealed",
            True,
        )

    @property
    def timeout_seconds(self) -> float:
        return object.__getattribute__(
            self, "_OkxPublicShadowMarketSource__timeout_seconds"
        )

    @property
    def safety_facts(self) -> ShadowSafetyFacts:
        return ShadowSafetyFacts(network_read_count=0)

    def __issue_provenance(
        self,
        *,
        transport: _CountingPublicTransport,
        response_source_hashes: tuple[str, ...],
        classification: str,
        payload_hash: str,
        failure_kind: str | None,
    ) -> _PublicSourceProvenance:
        source_exact_type = f"{type(self).__module__}.{type(self).__qualname__}"
        source_instance_id = object.__getattribute__(
            self, "_OkxPublicShadowMarketSource__source_instance_id"
        )
        core = _provenance_core(
            source_exact_type=source_exact_type,
            endpoint_identities=tuple(transport.endpoint_identities),
            actual_send_count=transport.actual_send_count,
            response_source_hashes=response_source_hashes,
            instrument="BTC-USDT",
            source_instance_id=source_instance_id,
            classification=classification,
            payload_hash=payload_hash,
            failure_kind=failure_kind,
        )
        durable = ShadowDataProvenance(
            classification,
            tuple(transport.endpoint_identities),
            transport.actual_send_count,
            response_source_hashes,
            source_instance_id,
            payload_hash,
            failure_kind,
        )
        return _PublicSourceProvenance(
            **core,
            token_hash=durable.provenance_hash,
            _capability=object.__getattribute__(
                self,
                "_OkxPublicShadowMarketSource__provenance_capability",
            ),
        )

    def __failure(
        self,
        exc: Exception,
        *,
        at_utc: datetime,
        transport: _CountingPublicTransport,
        response_source_hashes: tuple[str, ...],
    ) -> ShadowPublicDataFailure:
        response_source_hashes = tuple(transport.response_hashes)
        failure_kind = _public_failure_kind(exc)
        payload = dict(scenario_by_id("normal_public_snapshot").market_payload)
        payload.update(
            classification="unavailable",
            failure_kind=failure_kind,
            source_identity="okx-public-network-unavailable",
            network_read_count=transport.actual_send_count,
            public_timestamp_utc=at_utc.isoformat(),
            public_source_hashes=response_source_hashes,
        )
        provenance = _AUDITED_PUBLIC_ISSUE_METHOD(
            self,
            transport=transport,
            response_source_hashes=response_source_hashes,
            classification="unavailable",
            payload_hash=sha256_payload(payload),
            failure_kind=failure_kind,
        )
        return ShadowPublicDataFailure(
            failure_kind,
            _PublicMarketLoad(MappingProxyType(payload), provenance),
        )

    def load(self, scenario_id: str, *, at_utc: datetime) -> _PublicMarketLoad:
        if scenario_id != "public_network_okx_btc_usdt":
            raise ValueError("public shadow source permits only BTC-USDT")
        from secure_eval_wrapper.data_collection.okx_v5_public import (
            OkxPublicProvider,
            okx_spot_instrument_key,
        )

        key = okx_spot_instrument_key("BTC-USDT")
        collection_id = shadow_uuid("public-collection", {"at": at_utc, "instrument": "BTC-USDT"})
        transport = object.__getattribute__(
            self, "_OkxPublicShadowMarketSource__transport"
        )
        if (
            type(transport) is not _AUDITED_URLLIB_TRANSPORT_TYPE
            or _AUDITED_URLLIB_TRANSPORT_TYPE.send is not _AUDITED_URLLIB_SEND_METHOD
        ):
            raise ShadowAuthorityError("audited public transport authority was replaced")
        counting = _CountingPublicTransport(
            transport,
            send_method=_AUDITED_URLLIB_SEND_METHOD,
        )
        provider = OkxPublicProvider(
            transport=counting,
            timeout=self.timeout_seconds,
            max_pages=1,
            clock=lambda: at_utc,
        )
        response_hashes: tuple[str, ...] = ()
        try:
            instruments = provider.fetch_instruments(
                DataRequest(
                    collection_id,
                    "okx",
                    MarketDataType.INSTRUMENTS,
                    (),
                    limit=1,
                    instruments=(key,),
                )
            )
        except Exception as exc:
            raise _AUDITED_PUBLIC_FAILURE_METHOD(
                self,
                exc, at_utc=at_utc, transport=counting, response_source_hashes=response_hashes
            ) from exc
        if len(instruments) != 1:
            exc = ValueError("public instrument response must contain exactly one record")
            raise _AUDITED_PUBLIC_FAILURE_METHOD(
                self,
                exc, at_utc=at_utc, transport=counting, response_source_hashes=response_hashes
            )
        response_hashes = (instruments[0].source_sha256,)
        try:
            trades = provider.fetch_trades(
                DataRequest(
                    collection_id,
                    "okx",
                    MarketDataType.TRADES,
                    ("BTC-USDT",),
                    start_at_utc=at_utc - timedelta(minutes=5),
                    end_at_utc=at_utc + timedelta(milliseconds=1),
                    limit=10,
                    max_pages=1,
                )
            )
        except Exception as exc:
            raise _AUDITED_PUBLIC_FAILURE_METHOD(
                self,
                exc, at_utc=at_utc, transport=counting, response_source_hashes=response_hashes
            ) from exc
        if not trades:
            exc = ValueError("public trade response must contain at least one record")
            raise _AUDITED_PUBLIC_FAILURE_METHOD(
                self,
                exc, at_utc=at_utc, transport=counting, response_source_hashes=response_hashes
            )
        latest = max(trades, key=lambda observation: observation.observed_at_utc)
        response_hashes = tuple(counting.response_hashes)
        metadata = dict(instruments[0].payload)
        trade = dict(latest.payload)
        price = str(trade["price"])
        payload = {
            "provider": "okx",
            "instrument": "BTC-USDT",
            "instrument_type": "spot",
            "bid": price,
            "ask": price,
            "last_price": price,
            "public_timestamp_utc": latest.observed_at_utc.isoformat(),
            "instrument_status": metadata.get("status"),
            "settlement_asset": "USDT",
            "tick_size": metadata.get("tick_size"),
            "lot_size": metadata.get("quantity_step"),
            "minimum_quantity": metadata.get("minimum_quantity"),
            "maximum_quantity": "0.1",
            "source_identity": "secure_eval_wrapper.data_collection.OkxPublicProvider",
            "classification": "public_network",
            "network_read_count": counting.actual_send_count,
            "response_rows": 1,
            "metadata_present": True,
            "response_complete": True,
            "provider_code": "0",
            "replayed": False,
            "cached": False,
            "conflicting_sources": False,
            "fixture_declared_operational": False,
            "operational_declared_fixture": False,
            "failure_kind": None,
            "public_source_hashes": response_hashes,
        }
        provenance = _AUDITED_PUBLIC_ISSUE_METHOD(
            self,
            transport=counting,
            response_source_hashes=response_hashes,
            classification="public_network",
            payload_hash=sha256_payload(payload),
            failure_kind=None,
        )
        return _PublicMarketLoad(MappingProxyType(payload), provenance)


_AUDITED_PUBLIC_INIT_METHOD = OkxPublicShadowMarketSource.__init__
_AUDITED_PUBLIC_SETATTR_METHOD = OkxPublicShadowMarketSource.__setattr__
_AUDITED_PUBLIC_ISSUE_METHOD = (
    OkxPublicShadowMarketSource._OkxPublicShadowMarketSource__issue_provenance
)
_AUDITED_PUBLIC_FAILURE_METHOD = (
    OkxPublicShadowMarketSource._OkxPublicShadowMarketSource__failure
)
_AUDITED_PUBLIC_LOAD_METHOD = OkxPublicShadowMarketSource.load


def _validate_public_source_provenance(
    source: OkxPublicShadowMarketSource,
    provenance: _PublicSourceProvenance,
    market_payload: Mapping[str, object],
) -> None:
    source_instance_id = object.__getattribute__(
        source, "_OkxPublicShadowMarketSource__source_instance_id"
    )
    source_capability = object.__getattribute__(
        source, "_OkxPublicShadowMarketSource__provenance_capability"
    )
    if (
        type(source) is not OkxPublicShadowMarketSource
        or type(provenance) is not _PublicSourceProvenance
        or provenance._capability is not source_capability
        or provenance.source_instance_id != source_instance_id
    ):
        raise ShadowAuthorityError("public shadow provenance capability is invalid")
    source_type = (
        f"{OkxPublicShadowMarketSource.__module__}."
        f"{OkxPublicShadowMarketSource.__qualname__}"
    )
    classification = str(market_payload.get("classification"))
    failure_kind = market_payload.get("failure_kind")
    if classification == "public_network":
        payload_hash = sha256_payload(dict(market_payload))
        if (
            provenance.actual_send_count != 2
            or provenance.endpoint_identities != _PUBLIC_ENDPOINT_IDENTITIES
            or len(provenance.response_source_hashes) != 2
            or tuple(market_payload.get("public_source_hashes", ()))
            != provenance.response_source_hashes
            or failure_kind is not None
        ):
            raise ShadowAuthorityError("public shadow success provenance is incomplete")
    elif classification == "unavailable":
        payload_hash = sha256_payload(dict(market_payload))
        if (
            provenance.endpoint_identities
            != _PUBLIC_ENDPOINT_IDENTITIES[:provenance.actual_send_count]
            or provenance.actual_send_count not in (0, 1, 2)
            or len(provenance.response_source_hashes) > provenance.actual_send_count
            or tuple(market_payload.get("public_source_hashes", ())) != provenance.response_source_hashes
            or not isinstance(failure_kind, str)
        ):
            raise ShadowAuthorityError("public shadow failure provenance is incomplete")
    else:
        raise ShadowAuthorityError("public shadow classification is not source-issued")
    core = _provenance_core(
        source_exact_type=provenance.source_exact_type,
        endpoint_identities=provenance.endpoint_identities,
        actual_send_count=provenance.actual_send_count,
        response_source_hashes=provenance.response_source_hashes,
        instrument=provenance.instrument,
        source_instance_id=provenance.source_instance_id,
        classification=provenance.classification,
        payload_hash=provenance.payload_hash,
        failure_kind=provenance.failure_kind,
    )
    if (
        provenance.source_exact_type != source_type
        or provenance.instrument != "BTC-USDT"
        or provenance.classification != classification
        or provenance.failure_kind != failure_kind
        or provenance.payload_hash != payload_hash
        or provenance.actual_send_count != int(market_payload.get("network_read_count", -1))
    ):
        raise ShadowAuthorityError("public shadow provenance token does not bind the payload")
    durable = ShadowDataProvenance(
        provenance.classification,
        provenance.endpoint_identities,
        provenance.actual_send_count,
        provenance.response_source_hashes,
        provenance.source_instance_id,
        provenance.payload_hash,
        provenance.failure_kind,
        provenance.token_hash,
    )
    if durable.provenance_hash != provenance.token_hash:
        raise ShadowAuthorityError("public shadow durable provenance binding is invalid")


_ALLOWED_SOURCE_TYPES = (FixtureShadowMarketSource, OkxPublicShadowMarketSource)
_ALLOWED_REPOSITORY_TYPES = (MemoryShadowRepository, PostgresShadowRepository)


def _validate_no_write_dependency(value: object, *, dependency_name: str) -> None:
    if type(value) not in (_ALLOWED_SOURCE_TYPES if dependency_name == "market_source" else _ALLOWED_REPOSITORY_TYPES):
        raise ShadowAuthorityError(f"unsupported shadow {dependency_name} dependency")
    for symbol in (
        "submit_order",
        "cancel_order",
        "withdraw",
        "transfer",
        "borrow",
        "set_leverage",
        "send",
        "request_endpoint",
    ):
        if callable(getattr(value, symbol, None)):
            raise ShadowAuthorityError(
                f"shadow {dependency_name} exposes forbidden write/arbitrary transport symbol"
            )
    if callable(value):
        raise ShadowAuthorityError(f"shadow {dependency_name} cannot be callable")


class _ShadowRiskMarketEvidence:
    """Delegate to the Phase 7 evidence contract without promoting a fixture."""

    def __init__(self, evidence: PaperMarketDataEvidence) -> None:
        self._evidence = evidence
        self.price = evidence.price
        self.source_kind = evidence.source_kind
        self.evidence_sha256 = evidence.evidence_sha256

    def rejection_reasons(self, **kwargs):
        kwargs["allow_fixture"] = True
        return self._evidence.rejection_reasons(**kwargs)


def _market_validation_blockers(
    payload: Mapping[str, object],
    *,
    configuration,
    at_utc: datetime,
) -> tuple[str, ...]:
    failure_kind = payload.get("failure_kind")
    failure_blockers = {
        "malformed_json": "malformed_public_response",
        "timeout": "public_network_timeout",
        "connection_failure": "public_network_connection_failure",
        "rate_limit": "public_network_rate_limit",
        "partial_page": "partial_public_response",
    }
    if failure_kind in failure_blockers:
        return (failure_blockers[failure_kind],)
    if payload.get("metadata_present") is not True:
        return ("missing_instrument_metadata",)
    if payload.get("response_complete") is not True:
        return ("incomplete_public_response",)
    if payload.get("provider_code") != "0":
        return ("public_provider_error",)
    if payload.get("response_rows") != 1:
        return ("duplicate_public_response_rows",)
    if payload.get("conflicting_sources") is True:
        return ("conflicting_public_sources",)
    if payload.get("replayed") is True:
        return ("public_response_replay",)
    if payload.get("cached") is True:
        return ("stale_cached_response",)
    if payload.get("fixture_declared_operational") is True:
        return ("fixture_classification_mismatch",)
    if payload.get("operational_declared_fixture") is True:
        return ("operational_classification_mismatch",)
    if str(payload.get("instrument_status", "")).lower() == "delisted":
        return ("instrument_delisted",)
    if str(payload.get("instrument_status", "")).lower() != "live":
        return ("instrument_not_live",)
    if str(payload.get("instrument_type", "")).lower() != "spot":
        return ("wrong_instrument_type",)
    try:
        bid = _parse_decimal(payload.get("bid"))
        ask = _parse_decimal(payload.get("ask"))
        last = _parse_decimal(payload.get("last_price"))
    except ValueError:
        return ("market_price_not_finite",)
    if not all(value.is_finite() for value in (bid, ask, last)):
        return ("market_price_not_finite",)
    if bid <= 0:
        return ("bid_must_be_positive",)
    if ask <= 0:
        return ("ask_must_be_positive",)
    if last <= 0:
        return ("market_price_must_be_positive",)
    if bid > ask:
        return ("crossed_bid_ask",)
    try:
        public_at = _parse_datetime(payload.get("public_timestamp_utc"))
    except (TypeError, ValueError):
        return ("malformed_public_response",)
    delta = Decimal(str((public_at - at_utc).total_seconds()))
    if delta > configuration.maximum_clock_skew_seconds:
        return ("maximum_clock_skew",)
    if delta > 0:
        return ("public_market_future_timestamp",)
    if -delta > configuration.market_data_freshness_seconds:
        return ("stale_market_data",)
    return ()


def _build_market_snapshot(
    payload: Mapping[str, object],
    *,
    at_utc: datetime,
) -> ShadowMarketSnapshot:
    classification = ShadowEvidenceClassification(str(payload["classification"]))
    response_hash = sha256_payload({
        key: value for key, value in payload.items()
        if key not in {"provider_payload", "raw_response"}
    })
    return ShadowMarketSnapshot(
        str(payload["provider"]),
        str(payload["instrument"]),
        str(payload["instrument_type"]),
        _parse_decimal(payload["bid"]),
        _parse_decimal(payload["ask"]),
        _parse_decimal(payload["last_price"]),
        _parse_datetime(payload["public_timestamp_utc"]),
        at_utc,
        str(payload["source_identity"]),
        response_hash,
        classification,
        str(payload["instrument_status"]),
        str(payload["settlement_asset"]),
        _parse_decimal(payload["tick_size"]),
        _parse_decimal(payload["lot_size"]),
        _parse_decimal(payload["minimum_quantity"]),
        _parse_decimal(payload["maximum_quantity"]),
        int(payload.get("network_read_count", 0)),
    )


def _account_validation_blockers(
    payload: Mapping[str, object],
    *,
    configuration,
) -> tuple[str, ...]:
    required = {
        "synthetic_account",
        "account_classification",
        "balances",
        "positions",
        "pending_orders",
        "reserved_notional",
        "permissions",
    }
    if not required.issubset(payload) or payload.get("synthetic_account") is not True:
        return ("malformed_account_snapshot",)
    if payload.get("account_classification") != "synthetic_spot":
        return ("conflicting_account_classification",)
    permissions = tuple(str(item).lower() for item in payload.get("permissions", ()))
    if "synthetic_trade_profile" not in permissions:
        return ("synthetic_permission_not_trade_enabled",)
    balances = payload.get("balances")
    positions = payload.get("positions")
    if not isinstance(balances, list) or not isinstance(positions, list):
        return ("malformed_account_snapshot",)
    try:
        balance_values = [
            (
                str(row["asset"]).upper(),
                _parse_decimal(row["total"]),
                _parse_decimal(row["available"]),
                _parse_decimal(row["reserved"]),
            )
            for row in balances
        ]
    except (KeyError, TypeError, ValueError):
        return ("malformed_account_snapshot",)
    if any(not value.is_finite() or value < 0 for row in balance_values for value in row[1:]):
        return ("negative_synthetic_balance",)
    position_keys: list[tuple[str, str]] = []
    try:
        for row in positions:
            key = (str(row["instrument"]).upper(), str(row["instrument_type"]).lower())
            position_keys.append(key)
            quantity = _parse_decimal(row["quantity"])
            notional = _parse_decimal(row["notional"])
            if not quantity.is_finite() or not notional.is_finite():
                return ("malformed_account_snapshot",)
            if str(row["settlement_asset"]).upper() not in configuration.allowed_settlement_assets:
                return ("wrong_settlement_asset",)
            if key[1] != "spot":
                return ("synthetic_derivative_exposure",)
            if quantity < 0 or notional < 0:
                return ("synthetic_short_position",)
    except (KeyError, TypeError, ValueError):
        return ("malformed_account_snapshot",)
    if len(set(position_keys)) != len(position_keys):
        return ("duplicate_synthetic_position",)
    try:
        reserved = _parse_decimal(payload["reserved_notional"])
    except ValueError:
        return ("malformed_account_snapshot",)
    if not reserved.is_finite() or reserved < 0:
        return ("malformed_account_snapshot",)
    if reserved > configuration.maximum_gross_exposure:
        return ("excessive_reserved_notional",)
    return ()


def _build_account_snapshot(
    scenario_id: str,
    payload: Mapping[str, object],
    *,
    at_utc: datetime,
) -> SyntheticAccountSnapshot:
    balances = tuple(SyntheticBalance(
        str(row["asset"]),
        _parse_decimal(row["total"]),
        _parse_decimal(row["available"]),
        _parse_decimal(row["reserved"]),
    ) for row in payload["balances"])
    positions = tuple(SyntheticPosition(
        str(row["instrument"]),
        str(row["instrument_type"]),
        _parse_decimal(row["quantity"]),
        _parse_decimal(row["notional"]),
        str(row["settlement_asset"]),
    ) for row in payload["positions"])
    pending = tuple(SyntheticPendingOrder(
        str(row["instrument"]),
        str(row["side"]),
        _parse_decimal(row["quantity"]),
        _parse_decimal(row["reserved_notional"]),
    ) for row in payload["pending_orders"])
    source_hash = sha256_payload({"scenario_id": scenario_id, "account": dict(payload)})
    return SyntheticAccountSnapshot(
        scenario_id,
        source_hash,
        at_utc,
        balances,
        positions,
        pending,
        _parse_decimal(payload["reserved_notional"]),
        dict(payload.get("risk_limits", {})),
        tuple(payload["permissions"]),
        str(payload["account_classification"]),
        _parse_decimal(payload.get("daily_realized_pnl", "0")),
        _parse_decimal(payload.get("current_equity", "10000")),
        _parse_decimal(payload.get("high_watermark_equity", "10000")),
        bool(payload.get("kill_switch_active", False)),
        True,
    )


def _request_validation_blockers(
    payload: Mapping[str, object],
    *,
    market: ShadowMarketSnapshot | None,
    configuration,
) -> tuple[str, ...]:
    try:
        quantity = _parse_decimal(payload.get("quantity"))
    except ValueError:
        return ("quantity_not_finite",)
    if not quantity.is_finite():
        return ("quantity_not_finite",)
    if quantity <= 0:
        return ("quantity_must_be_positive",)
    instrument = str(payload.get("instrument", "")).upper()
    if instrument not in configuration.allowed_instruments:
        return ("instrument_not_allowed",)
    if str(payload.get("order_type", "")).lower() != "limit":
        return ("only_limit_orders_allowed",)
    if market is None:
        return ("market_snapshot_unavailable",)
    rounded = (quantity / market.lot_size).to_integral_value(rounding=ROUND_DOWN) * market.lot_size
    if rounded < market.minimum_quantity:
        return ("quantity_below_minimum_after_rounding",)
    if rounded > market.maximum_quantity:
        return ("quantity_above_maximum_after_rounding",)
    return ()


def _synthetic_fingerprint(synthetic_account_id: str) -> str:
    value = sha256_payload({"synthetic_shadow_account": synthetic_account_id})[:16]
    return "1" + value[1:] if value == "0000000000000000" else value


class ShadowAssuranceRuntime:
    """Run shared guarded-live policy without a write-capable transport graph."""

    def __init__(
        self,
        *,
        repository: MemoryShadowRepository | PostgresShadowRepository,
        market_source: FixtureShadowMarketSource | OkxPublicShadowMarketSource,
        identity_resolver: Callable[[], RuntimeRepositoryIdentity] | None = None,
    ) -> None:
        _validate_no_write_dependency(repository, dependency_name="repository")
        _validate_no_write_dependency(market_source, dependency_name="market_source")
        self.repository = repository
        self.market_source = market_source
        self.identity_resolver = (
            resolve_runtime_repository_identity if identity_resolver is None else identity_resolver
        )

    def run_fixture(
        self,
        fixture_name: str,
        *,
        shadow_run_id: UUID | None = None,
        parent_input_hash: str | None = None,
        crash_at: str | None = None,
    ) -> ShadowRunSummary:
        if type(self.market_source) is not FixtureShadowMarketSource:
            raise ShadowAuthorityError("fixture execution requires the exact fixture source")
        return self._run_validated_input(
            scenario_by_id(fixture_name),
            shadow_run_id=shadow_run_id,
            parent_input_hash=parent_input_hash,
            crash_at=crash_at,
            _source_mode="fixture",
            _public_provenance=None,
        )

    def _run_fixture_scenario_for_test(
        self,
        scenario: ShadowScenarioSpec,
        *,
        shadow_run_id: UUID | None = None,
        parent_input_hash: str | None = None,
        crash_at: str | None = None,
    ) -> ShadowRunSummary:
        if type(self.market_source) is not FixtureShadowMarketSource:
            raise ShadowAuthorityError("fixture-only test execution requires the exact fixture source")
        return self._run_validated_input(
            scenario,
            shadow_run_id=shadow_run_id,
            parent_input_hash=parent_input_hash,
            crash_at=crash_at,
            _source_mode="fixture",
            _public_provenance=None,
        )

    def run_public(
        self,
        *,
        provider: str,
        instrument: str,
        at_utc: datetime | None = None,
        shadow_run_id: UUID | None = None,
    ) -> ShadowRunSummary:
        if type(self.market_source) is not OkxPublicShadowMarketSource:
            raise ShadowAuthorityError("public execution requires the exact public source")
        bound_load = getattr(self.market_source, "load", None)
        if (
            getattr(bound_load, "__func__", None) is not _AUDITED_PUBLIC_LOAD_METHOD
            or type(self.market_source).__init__ is not _AUDITED_PUBLIC_INIT_METHOD
            or type(self.market_source).__setattr__ is not _AUDITED_PUBLIC_SETATTR_METHOD
        ):
            raise ShadowAuthorityError("public source authority implementation was replaced")
        if provider.lower() != "okx" or instrument.upper() != "BTC-USDT":
            raise ShadowAuthorityError("public shadow mode permits only audited OKX BTC-USDT Spot")
        now = datetime.now(timezone.utc) if at_utc is None else at_utc
        base = scenario_by_id("normal_public_snapshot")
        try:
            loaded = bound_load("public_network_okx_btc_usdt", at_utc=now)
        except ShadowPublicDataFailure as exc:
            loaded = exc.load
        if type(loaded) is not _PublicMarketLoad:
            raise ShadowAuthorityError("public source returned an unsealed payload")
        market = dict(loaded.payload)
        _validate_public_source_provenance(self.market_source, loaded.provenance, market)
        failure_blocker = {
            "timeout": "public_network_timeout",
            "rate_limit": "public_network_rate_limit",
            "malformed_json": "malformed_public_response",
            "connection_failure": "public_network_connection_failure",
        }.get(market.get("failure_kind"))
        request = dict(base.request_payload)
        request["decision_at_utc"] = now.isoformat()
        scenario = ShadowScenarioSpec(
            "public_network_okx_btc_usdt",
            "market",
            base.account_payload,
            market,
            request,
            "accepted" if failure_blocker is None else "blocked",
            () if failure_blocker is None else (failure_blocker,),
            1 if failure_blocker is None else 0,
            loaded.provenance.actual_send_count,
            0,
            "persisted",
        )
        return self._run_validated_input(
            scenario,
            shadow_run_id=shadow_run_id,
            _source_mode="public",
            _public_provenance=loaded.provenance,
        )

    def _run_validated_input(
        self,
        scenario: ShadowScenarioSpec,
        *,
        shadow_run_id: UUID | None = None,
        parent_input_hash: str | None = None,
        crash_at: str | None = None,
        _source_mode: str,
        _public_provenance: _PublicSourceProvenance | None,
    ) -> ShadowRunSummary:
        if type(scenario) is not ShadowScenarioSpec:
            raise ShadowAuthorityError("shadow input must use the exact scenario type")
        classification = scenario.market_payload.get("classification")
        reads = scenario.market_payload.get("network_read_count")
        if _source_mode == "fixture":
            if (
                type(self.market_source) is not FixtureShadowMarketSource
                or classification != "fixture"
                or reads != 0
                or _public_provenance is not None
                or "public_source_hashes" in scenario.market_payload
            ):
                raise ShadowAuthorityError(
                    "fixture-only execution requires fixture classification and zero reads"
                )
        elif _source_mode == "public":
            if (
                type(self.market_source) is not OkxPublicShadowMarketSource
                or _public_provenance is None
            ):
                raise ShadowAuthorityError("public execution requires source-produced provenance")
            _validate_public_source_provenance(
                self.market_source, _public_provenance, scenario.market_payload
            )
        else:
            raise ShadowAuthorityError("unknown shadow source mode")
        if crash_at is not None and crash_at not in RUNTIME_CRASH_POINTS:
            raise ValueError("unknown Phase 8B shadow crash point")
        data_provenance = (
            ShadowDataProvenance.fixture()
            if _public_provenance is None
            else ShadowDataProvenance(
                _public_provenance.classification,
                _public_provenance.endpoint_identities,
                _public_provenance.actual_send_count,
                _public_provenance.response_source_hashes,
                _public_provenance.source_instance_id,
                _public_provenance.payload_hash,
                _public_provenance.failure_kind,
                _public_provenance.token_hash,
            )
        )
        identity = self.identity_resolver()
        repository_sha = identity.observed_commit_sha
        raw_account_hash = sha256_payload(dict(scenario.account_payload))
        synthetic_fingerprint = _synthetic_fingerprint(raw_account_hash)
        configuration = phase8a_dry_run_configuration(
            account_fingerprint=synthetic_fingerprint,
            endpoint_catalog_hash=endpoint_catalog_hash(),
            provider_implementation_hash=OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH,
        )
        at_utc = _parse_datetime(scenario.request_payload["decision_at_utc"])
        market_blockers = _market_validation_blockers(
            scenario.market_payload, configuration=configuration, at_utc=at_utc
        )
        market = None if market_blockers else _build_market_snapshot(
            scenario.market_payload, at_utc=at_utc
        )
        if crash_at == "market_snapshot_normalized":
            raise ShadowInjectedCrash(crash_at)

        account_blockers = _account_validation_blockers(
            scenario.account_payload, configuration=configuration
        )
        account = None if account_blockers else _build_account_snapshot(
            scenario.scenario_id, scenario.account_payload, at_utc=at_utc
        )
        if crash_at == "synthetic_account_validated":
            raise ShadowInjectedCrash(crash_at)

        request_blockers = () if market_blockers else (
            _request_validation_blockers(
                scenario.request_payload, market=market, configuration=configuration
            )
        )
        preflight_blockers = tuple(dict.fromkeys(
            market_blockers + account_blockers + request_blockers
        ))
        network_reads = int(scenario.market_payload.get("network_read_count", 0))
        safety = ShadowSafetyFacts(network_reads)
        run_id = shadow_run_id or shadow_uuid("run", {
            "scenario": scenario.scenario_id,
            "input": scenario.input_hash,
            "repository": repository_sha,
            "parent": parent_input_hash,
        })
        credential = LiveCredentialReference(
            "okx",
            "phase8b-shadow-synthetic-reference",
            "synthetic_no_credential",
            synthetic_fingerprint,
            False,
            None,
            (),
        )
        account_hash = raw_account_hash if account is None else account.snapshot_hash
        checks = tuple(
            LivePreflightCheck(
                blocker,
                False,
                True,
                at_utc,
                "shared guarded-live policy blocked synthetic/public shadow input",
                sha256_payload({"blocker": blocker, "scenario": scenario.scenario_id}),
            )
            for blocker in preflight_blockers
        ) or (
            LivePreflightCheck(
                "shadow_shared_guarded_policy",
                True,
                True,
                at_utc,
                "typed configuration, public Spot metadata, and synthetic account checks passed",
                sha256_payload({
                    "scenario": scenario.scenario_id,
                    "configuration": configuration.configuration_hash,
                    "market": market.snapshot_hash,
                    "account": account.snapshot_hash,
                }),
            ),
        )
        report = LivePreflightReport(
            run_id,
            configuration.configuration_hash,
            configuration.provider_implementation_hash,
            repository_sha,
            configuration.endpoint_catalog_hash,
            credential.record_hash,
            account_hash,
            at_utc,
            checks,
            preflight_blockers,
            ("synthetic_account=true", "shadow_only=true"),
            LivePreflightStatus.BLOCKED if preflight_blockers else LivePreflightStatus.PASSED,
            purpose=LivePreflightPurpose.RUN_START,
        )

        if preflight_blockers:
            input_hash = sha256_payload({
                "scenario_input": scenario.input_hash,
                "preflight": report.record_hash,
                "parent": parent_input_hash,
            })
            decision = ShadowDecisionRecord(
                run_id,
                scenario.scenario_id,
                input_hash,
                None if market is None else market.snapshot_hash,
                None if account is None else account.snapshot_hash,
                configuration.configuration_hash,
                report.record_hash,
                None,
                None,
                None,
                False,
                preflight_blockers,
                None,
                safety,
                data_provenance,
                repository_sha,
                parent_input_hash,
            )
            return self._persist_and_summarize(
                decision,
                crash_at=crash_at,
            )

        series = SeriesIdentity(
            "okx",
            "okx",
            market.instrument,
            market.instrument,
            InstrumentType.SPOT,
            "1m",
            market.settlement_asset,
        )
        direction = SignalDirection(str(scenario.request_payload["direction"]).lower())
        score = Decimal(1) if direction is SignalDirection.LONG else Decimal(-1)
        signal = StandardizedSignal(
            uuid5(NAMESPACE_URL, f"phase8b-shadow-signal:{scenario.input_hash}"),
            uuid5(NAMESPACE_URL, f"phase8b-shadow-signal-run:{scenario.input_hash}"),
            ("public-shadow-fixture:1",),
            (uuid5(NAMESPACE_URL, "phase8b-shadow-public-alpha"),),
            market.instrument,
            at_utc,
            direction,
            score,
            score,
            None,
            None,
            Decimal("1"),
            "1m",
            (uuid5(NAMESPACE_URL, f"phase8b-shadow-alpha-value:{scenario.input_hash}"),),
            configuration.configuration_hash,
            market.snapshot_hash,
            sha256_payload({"repository_commit_sha": repository_sha}),
            {"public_safe": True, "shadow_only": True},
            series,
            repository_commit_sha=repository_sha,
        )
        request = ShadowDecisionRequest(
            run_id,
            scenario.scenario_id,
            market,
            account,
            signal,
            _parse_decimal(scenario.request_payload["quantity"]),
            _parse_decimal(scenario.request_payload["limit_price"]),
            str(scenario.request_payload["order_type"]),
            at_utc,
            repository_sha,
            parent_input_hash,
        )
        live_account = LiveAccountSnapshot(
            run_id,
            synthetic_fingerprint,
            account.observed_at_utc,
            market.public_timestamp_utc,
            {
                balance.asset: {
                    "total": balance.total,
                    "available": balance.available,
                    "reserved": balance.reserved,
                }
                for balance in account.balances
            },
            {
                position.instrument: {
                    "instrument_type": position.instrument_type,
                    "quantity": position.quantity,
                    "notional": position.notional,
                    "settlement_asset": position.settlement_asset,
                }
                for position in account.positions
            },
            len(account.pending_orders),
            account.balance_map[configuration.base_currency].total,
            account.balance_map[configuration.base_currency].available,
            account.balance_map[configuration.base_currency].reserved,
            "spot_cash",
        )
        report = LivePreflightReport(
            run_id,
            configuration.configuration_hash,
            configuration.provider_implementation_hash,
            repository_sha,
            configuration.endpoint_catalog_hash,
            credential.record_hash,
            live_account.record_hash,
            at_utc,
            checks,
            (),
            ("synthetic_account=true", "shadow_only=true"),
            LivePreflightStatus.PASSED,
            purpose=LivePreflightPurpose.RUN_START,
        )
        preview = manifest_preview_hash(
            live_run_id=run_id,
            configuration=configuration,
            credential_reference_hash=credential.record_hash,
            preflight_report_id=report.report_id,
            account_snapshot_hash=live_account.record_hash,
            repository_commit_sha=repository_sha,
        )
        approval_created = at_utc
        approval_expires = at_utc + timedelta(seconds=300)
        challenge = confirmation_challenge_hash(
            live_run_id=run_id,
            configuration=configuration,
            account_fingerprint=synthetic_fingerprint,
            manifest_hash=preview,
            repository_commit_sha=repository_sha,
            nonce=f"shadow:{scenario.scenario_id}:{request.input_hash}",
            approving_actor="synthetic-shadow-controller",
            created_at_utc=approval_created,
            expires_at_utc=approval_expires,
            maximum_total_approved_notional=configuration.maximum_order_notional,
        )
        approval = LiveApprovalController().create(
            report=report,
            configuration=configuration,
            account_snapshot=live_account,
            manifest_hash=preview,
            created_at_utc=approval_created,
            ttl_seconds=300,
            nonce=f"shadow:{scenario.scenario_id}:{request.input_hash}",
            approving_actor="synthetic-shadow-controller",
            maximum_total_approved_notional=configuration.maximum_order_notional,
            exact_confirmation_challenge_hash=challenge,
        )
        if crash_at == "approval_created":
            raise ShadowInjectedCrash(crash_at)
        manifest = create_live_manifest(
            configuration=configuration,
            report=report,
            approval=approval,
            account_snapshot=live_account,
            credential_reference=credential,
            at_utc=at_utc,
        )
        if crash_at == "manifest_created":
            raise ShadowInjectedCrash(crash_at)
        market_evidence = PaperMarketDataEvidence(
            series,
            "okx",
            market.instrument,
            "public_trade_snapshot",
            str(shadow_uuid("market-observation", market.snapshot_hash)),
            market.public_timestamp_utc,
            market.normalized_at_utc,
            True,
            "accepted",
            market.public_response_hash,
            market.snapshot_hash,
            price=market.last_price,
            quote_currency=market.settlement_asset,
            source_kind="fixture",
        )
        side = OrderSide.BUY if direction is SignalDirection.LONG else OrderSide.SELL
        live_intent = LiveOrderIntent(
            run_id,
            manifest.manifest_id,
            series,
            side,
            request.quantity,
            market.last_price,
            request.limit_price,
            at_utc,
            market_evidence.evidence_id,
            market_evidence.evidence_sha256,
            market.snapshot_hash,
            account.snapshot_hash,
            sha256_payload({"synthetic_reconciliation": account.snapshot_hash}),
        )
        gross = sum((abs(position.notional) for position in account.positions), Decimal(0))
        net = sum((position.notional for position in account.positions), Decimal(0))
        state = LiveRuntimeRiskState(
            run_id,
            at_utc.date(),
            account.current_equity,
            account.high_watermark_equity,
            account.reserved_notional,
            account.daily_realized_pnl,
            gross,
            net,
            (),
            (),
            len(account.pending_orders),
            None,
            None,
            market.public_timestamp_utc,
            account.observed_at_utc,
            at_utc,
            LiveReconciliationStatus.RECONCILED,
            abs(Decimal(str((at_utc - market.public_timestamp_utc).total_seconds()))),
            at_utc,
            0,
            {
                balance.asset: {
                    "total": str(balance.total),
                    "available": str(balance.available),
                    "reserved": str(balance.reserved),
                }
                for balance in account.balances
            },
            {
                position.instrument: {"notional": str(position.notional)}
                for position in account.positions
            },
            0,
        )
        risk = evaluate_live_risk(
            intent=live_intent,
            market_evidence=_ShadowRiskMarketEvidence(market_evidence),
            configuration=configuration,
            state=state,
            approval=approval,
            approval_consumed_notional=account.reserved_notional,
            kill_switch_state=(
                LiveKillState.STOPPED if account.kill_switch_active else LiveKillState.ARMED
            ),
            evaluated_at_utc=at_utc,
        )
        if crash_at == "risk_evaluated":
            raise ShadowInjectedCrash(crash_at)
        blockers = list(risk.reasons)
        reservation = calculate_live_reservation(
            intent=live_intent,
            risk_decision=risk,
            maximum_fee_bps=configuration.maximum_fee_bps,
        )
        balance = account.balance_map.get(reservation.currency)
        if balance is None or balance.available < reservation.original_amount:
            blockers.append(
                "insufficient_base_balance"
                if side is OrderSide.SELL else "insufficient_quote_balance"
            )
        blockers = list(dict.fromkeys(blockers))
        accepted = not blockers
        shadow_intent = ShadowOrderIntent(
            market.instrument,
            side.value,
            request.order_type,
            request.quantity,
            request.limit_price,
            risk.risk_notional,
            accepted,
            tuple(blockers),
            "shared_exact_approval_validated",
            manifest.manifest_hash,
            live_intent.record_hash,
            (
                "would_pass_guarded_policy_but_shadow_suppressed"
                if accepted
                else "blocked_by_guarded_policy"
            ),
        )
        decision = ShadowDecisionRecord(
            run_id,
            scenario.scenario_id,
            request.input_hash,
            market.snapshot_hash,
            account.snapshot_hash,
            configuration.configuration_hash,
            report.record_hash,
            approval.record_hash,
            manifest.manifest_hash,
            risk.record_hash,
            accepted,
            tuple(blockers),
            shadow_intent,
            safety,
            data_provenance,
            repository_sha,
            parent_input_hash,
        )
        return self._persist_and_summarize(
            decision,
            crash_at=crash_at,
        )

    def _persist_and_summarize(
        self,
        decision: ShadowDecisionRecord,
        *,
        crash_at: str | None,
    ) -> ShadowRunSummary:
        if crash_at == "before_decision_persist":
            raise ShadowInjectedCrash(crash_at)
        replayed = self.repository.persist_bundle(decision, crash_at=crash_at)
        return ShadowRunSummary(
            decision.shadow_run_id,
            decision.scenario_id,
            decision.input_hash,
            decision.decision_hash,
            decision.manifest_hash,
            decision.accepted,
            decision.blockers,
            int(decision.shadow_intent is not None),
            "idempotent_replay" if replayed else "persisted",
            replayed,
            decision.safety_facts,
            decision.data_provenance,
        )


__all__ = [
    "FixtureShadowMarketSource",
    "OkxPublicShadowMarketSource",
    "RUNTIME_CRASH_POINTS",
    "ShadowAssuranceRuntime",
    "ShadowAuthorityError",
    "ShadowPublicDataFailure",
]
