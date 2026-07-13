"""Exact OKX production Spot read-only adapter; write methods remain unreachable."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from ..endpoints import EndpointClass, LiveOperation, OKX_PRODUCTION_ORIGIN, build_request_path, classify_exact, route_for
from ..gates import common_ci_indicators
from ..venue import GuardedLiveVenue, ProductionWriteSuppressed


class UrllibReadOnlyTransport:
    is_fake = False

    def execute(self, *, method: str, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float = 10.0):
        if method != "GET" or classify_exact(method, url.removeprefix(OKX_PRODUCTION_ORIGIN)) not in {EndpointClass.PUBLIC_READ, EndpointClass.AUTHENTICATED_READ}:
            raise PermissionError("transport permits only exact catalogued GET requests")
        if common_ci_indicators():
            raise PermissionError("production network reads and credentials are prohibited in CI")
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
        return json.loads(payload.decode("utf-8"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def signed_headers(*, credential_material, method: str, request_path: str, body: bytes = b"", timestamp: str | None = None) -> dict[str, str]:
    key, secret, passphrase = credential_material.request_values()
    ts = _timestamp() if timestamp is None else timestamp
    prehash = ts + method.upper() + request_path + body.decode("utf-8")
    signature = base64.b64encode(hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()).decode("ascii")
    return {"Content-Type": "application/json", "OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": signature, "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase}


def _data(payload: object, *, maximum_rows: int = 100) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise ValueError("OKX response top-level code is not zero")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > maximum_rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("OKX response data must be a bounded object list")
    return rows


def _required(row: dict, names: tuple[str, ...]) -> None:
    if any(name not in row or row[name] is None or (isinstance(row[name], str) and not row[name]) for name in names):
        raise ValueError("OKX response is missing required fields")


def _number(value: object, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("OKX numeric field is invalid") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("OKX numeric field is outside its permitted range")
    return result


def _milliseconds(value: object) -> datetime:
    raw = _number(value, positive=True)
    return datetime.fromtimestamp(float(raw / Decimal(1000)), tz=timezone.utc)


class OkxProductionSpotAdapter(GuardedLiveVenue):
    provider_implementation_hash = sha256_payload({"adapter": "okx-production-spot", "version": 2, "writes": "phase8a-unreachable"})

    def __init__(self, *, transport, credential_material=None) -> None:
        self.transport = transport
        self.credential_material = credential_material
        self.network_reads = 0
        self.network_writes = 0

    @staticmethod
    def normalize_decimal(value: Decimal, step: Decimal) -> Decimal:
        value = Decimal(value); step = Decimal(step)
        if value <= 0 or step <= 0: raise ValueError("value and step must be positive")
        return (value // step) * step

    @classmethod
    def build_limit_order_body(cls, *, instrument: str, side: str, quantity: Decimal, limit_price: Decimal, client_order_id: str, tick_size: Decimal, lot_size: Decimal) -> dict[str, str]:
        if side not in {"buy", "sell"}: raise ValueError("side must be buy or sell")
        if not instrument or instrument.endswith(("-SWAP", "-FUTURES", "-OPTION")): raise ValueError("only SPOT instruments are supported")
        price = cls.normalize_decimal(limit_price, tick_size); size = cls.normalize_decimal(quantity, lot_size)
        if len(client_order_id) > 32 or not client_order_id.isalnum(): raise ValueError("OKX clOrdId must be at most 32 alphanumeric characters")
        return {"instId": instrument, "tdMode": "cash", "clOrdId": client_order_id, "side": side, "ordType": "limit", "px": format(price, "f"), "sz": format(size, "f")}

    @staticmethod
    def build_cancel_body(*, instrument: str, client_order_id: str) -> dict[str, str]:
        if not instrument or not client_order_id: raise ValueError("instrument and client order ID are required")
        return {"instId": instrument, "clOrdId": client_order_id}

    @staticmethod
    def parse_venue_time(payload: object) -> dict:
        rows = _data(payload, maximum_rows=1)
        if len(rows) != 1: raise ValueError("OKX time response must contain one row")
        _required(rows[0], ("ts",))
        return {"venue_time_at_utc": _milliseconds(rows[0]["ts"]), "response_hash": sha256_payload(payload)}

    @staticmethod
    def parse_instruments(payload: object, *, expected_instrument: str | None = None) -> tuple[dict, ...]:
        parsed = []
        for row in _data(payload):
            _required(row, ("instType", "instId", "baseCcy", "quoteCcy", "tickSz", "lotSz", "minSz", "state"))
            if row["instType"] != "SPOT": raise ValueError("OKX instrument response contains a non-Spot instrument")
            if expected_instrument is not None and row["instId"] != expected_instrument: raise ValueError("OKX instrument identity mismatch")
            parsed.append({**row, "tick_size": _number(row["tickSz"], positive=True), "lot_size": _number(row["lotSz"], positive=True), "minimum_size": _number(row["minSz"], positive=True)})
        return tuple(parsed)

    @staticmethod
    def parse_ticker(payload: object, *, expected_instrument: str) -> dict:
        rows = _data(payload, maximum_rows=1)
        if len(rows) != 1: raise ValueError("OKX ticker response must contain one row")
        row = rows[0]; _required(row, ("instId", "last", "ts"))
        if row["instId"] != expected_instrument: raise ValueError("OKX ticker instrument mismatch")
        return {**row, "last": _number(row["last"], positive=True), "observed_at_utc": _milliseconds(row["ts"]), "response_hash": sha256_payload(payload)}

    @staticmethod
    def parse_account_config(payload: object) -> dict:
        rows = _data(payload, maximum_rows=1)
        if len(rows) != 1: raise ValueError("OKX account config must contain one row")
        row = rows[0]; _required(row, ("uid", "acctLv", "posMode", "autoLoan", "enableSpotBorrow"))
        borrowing_disabled = all(
            str(row[name]).lower() in {"false", "0"} for name in ("autoLoan", "enableSpotBorrow")
        )
        if str(row["acctLv"]) != "1" or not borrowing_disabled: raise ValueError("OKX account is not Spot cash with borrowing disabled")
        return {**row, "account_mode": "spot_cash", "response_hash": sha256_payload(payload)}

    @staticmethod
    def parse_balances(payload: object) -> dict:
        rows = _data(payload, maximum_rows=1)
        if len(rows) != 1: raise ValueError("OKX balance response must contain one account row")
        row = rows[0]; _required(row, ("totalEq", "uTime", "details"))
        if not isinstance(row["details"], list): raise ValueError("OKX balance details must be a list")
        details = []
        for detail in row["details"]:
            if not isinstance(detail, dict): raise ValueError("OKX balance detail must be an object")
            _required(detail, ("ccy", "eq", "availEq", "frozenBal"))
            details.append({**detail, "equity": _number(detail["eq"], nonnegative=True), "available": _number(detail["availEq"], nonnegative=True), "reserved": _number(detail["frozenBal"], nonnegative=True)})
        return {"total_equity": _number(row["totalEq"], nonnegative=True), "updated_at_utc": _milliseconds(row["uTime"]), "details": tuple(details), "response_hash": sha256_payload(payload)}

    @staticmethod
    def parse_positions(payload: object) -> tuple[dict, ...]:
        parsed = []
        for row in _data(payload):
            _required(row, ("instId", "instType", "pos", "avgPx", "upl", "uTime"))
            if row["instType"] != "SPOT": raise ValueError("OKX positions include a non-Spot position")
            parsed.append({**row, "quantity": _number(row["pos"]), "average_price": _number(row["avgPx"], nonnegative=True), "unrealized_pnl": _number(row["upl"]), "updated_at_utc": _milliseconds(row["uTime"])})
        return tuple(parsed)

    @staticmethod
    def _parse_orders(payload: object, *, expected_instrument: str | None, maximum_rows: int) -> tuple[dict, ...]:
        parsed = []
        for row in _data(payload, maximum_rows=maximum_rows):
            _required(row, ("ordId", "clOrdId", "instId", "side", "sz", "px", "state", "accFillSz", "cTime", "uTime"))
            if expected_instrument is not None and row["instId"] != expected_instrument: raise ValueError("OKX order instrument mismatch")
            if row["side"] not in {"buy", "sell"}: raise ValueError("OKX order side is invalid")
            if row["state"] not in {"live", "partially_filled", "filled", "canceled"}:
                raise ValueError("OKX order state is invalid")
            parsed.append({**row, "quantity": _number(row["sz"], positive=True), "price": _number(row["px"], positive=True), "cumulative_quantity": _number(row["accFillSz"], nonnegative=True), "created_at_utc": _milliseconds(row["cTime"]), "updated_at_utc": _milliseconds(row["uTime"])})
        return tuple(parsed)

    @classmethod
    def parse_order_details(cls, payload: object, *, expected_instrument: str, expected_client_order_id: str) -> dict | None:
        rows = cls._parse_orders(payload, expected_instrument=expected_instrument, maximum_rows=1)
        if not rows: return None
        if rows[0]["clOrdId"] != expected_client_order_id: raise ValueError("OKX order client identity mismatch")
        return rows[0]

    @classmethod
    def parse_pending_orders(cls, payload: object, *, expected_instrument: str) -> tuple[dict, ...]:
        return cls._parse_orders(payload, expected_instrument=expected_instrument, maximum_rows=100)

    @classmethod
    def parse_order_history(cls, payload: object, *, expected_instrument: str) -> tuple[dict, ...]:
        return cls._parse_orders(payload, expected_instrument=expected_instrument, maximum_rows=100)

    @staticmethod
    def parse_fills_history(payload: object, *, expected_instrument: str) -> tuple[dict, ...]:
        parsed = []
        for row in _data(payload, maximum_rows=100):
            _required(row, ("tradeId", "ordId", "clOrdId", "instId", "side", "fillSz", "fillPx", "fee", "feeCcy", "ts"))
            if row["instId"] != expected_instrument: raise ValueError("OKX fill instrument mismatch")
            if row["side"] not in {"buy", "sell"}: raise ValueError("OKX fill side is invalid")
            parsed.append({**row, "quantity": _number(row["fillSz"], positive=True), "price": _number(row["fillPx"], positive=True), "fee_amount": _number(row["fee"]), "observed_at_utc": _milliseconds(row["ts"])})
        return tuple(parsed)

    @staticmethod
    def parse_order_response(payload: object) -> dict[str, str]:
        rows = _data(payload, maximum_rows=1)
        if len(rows) != 1: raise ValueError("malformed OKX order response")
        row = rows[0]; _required(row, ("ordId", "clOrdId", "sCode"))
        if "sMsg" not in row or row["sMsg"] is None:
            raise ValueError("OKX order response is missing sMsg")
        if str(row["sCode"]) != "0": raise ValueError(f"OKX order rejected with sCode={row['sCode']}")
        return {name: str(row[name]) for name in ("ordId", "clOrdId", "sCode", "sMsg")}

    def _read(self, operation: LiveOperation, query: dict[str, str] | None = None):
        route = route_for(operation)
        if route.classification not in {EndpointClass.PUBLIC_READ, EndpointClass.AUTHENTICATED_READ}: raise PermissionError("adapter read method selected a non-read endpoint")
        path = build_request_path(operation, query); headers = {"Content-Type": "application/json"}
        if route.authenticated:
            if self.credential_material is None: raise PermissionError("authenticated read requires local credential material")
            headers = signed_headers(credential_material=self.credential_material, method="GET", request_path=path)
        self.network_reads += 1
        return self.transport.execute(method="GET", url=OKX_PRODUCTION_ORIGIN + path, headers=headers, body=b"")

    def read_venue_time(self): return self.parse_venue_time(self._read(LiveOperation.VENUE_TIME))
    def read_instruments(self, *, instrument: str): return self.parse_instruments(self._read(LiveOperation.PUBLIC_INSTRUMENTS, {"instType": "SPOT", "instId": instrument}), expected_instrument=instrument)
    def read_ticker(self, *, instrument: str): return self.parse_ticker(self._read(LiveOperation.PUBLIC_TICKER, {"instId": instrument}), expected_instrument=instrument)
    def read_account_config(self): return self.parse_account_config(self._read(LiveOperation.ACCOUNT_CONFIG))
    def read_balances(self): return self.parse_balances(self._read(LiveOperation.BALANCES))
    def read_positions(self): return self.parse_positions(self._read(LiveOperation.POSITIONS, {"instType": "SPOT"}))
    def query_order(self, *, instrument: str, client_order_id: str): return self.parse_order_details(self._read(LiveOperation.ORDER_DETAILS, {"instId": instrument, "clOrdId": client_order_id}), expected_instrument=instrument, expected_client_order_id=client_order_id)
    def recent_orders(self, *, instrument: str): return self.parse_order_history(self._read(LiveOperation.RECENT_ORDERS, {"instType": "SPOT", "instId": instrument}), expected_instrument=instrument)
    def open_orders(self, *, instrument: str): return self.parse_pending_orders(self._read(LiveOperation.OPEN_ORDERS, {"instType": "SPOT", "instId": instrument}), expected_instrument=instrument)
    def fills(self, *, instrument: str): return self.parse_fills_history(self._read(LiveOperation.FILLS, {"instType": "SPOT", "instId": instrument}), expected_instrument=instrument)

    def submit_order(self, request_body):
        raise ProductionWriteSuppressed("Phase 8A cannot invoke OKX order submission")

    def cancel_order(self, request_body):
        raise ProductionWriteSuppressed("Phase 8A cannot invoke OKX cancellation")


__all__ = ["UrllibReadOnlyTransport", "signed_headers", "OkxProductionSpotAdapter"]
