"""OKX production SPOT adapter with named read-only calls and write planning only.

Official contract verified 2026-07-13: authentication uses OK-ACCESS-KEY,
OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP, and OK-ACCESS-PASSPHRASE.  The signature is
Base64(HMAC-SHA256(timestamp + method + requestPath + body)).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.request import Request, urlopen

from secure_eval_wrapper.data_collection.hashing import canonical_json_dumps, sha256_payload

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


class OkxProductionSpotAdapter(GuardedLiveVenue):
    provider_implementation_hash = sha256_payload({"adapter": "okx-production-spot", "version": 1, "writes": "phase8a-suppressed"})

    def __init__(self, *, transport, credential_material=None) -> None:
        self.transport = transport
        self.credential_material = credential_material
        self.network_reads = 0
        self.network_writes = 0

    @staticmethod
    def normalize_decimal(value: Decimal, step: Decimal) -> Decimal:
        value = Decimal(value); step = Decimal(step)
        if value <= 0 or step <= 0:
            raise ValueError("value and step must be positive")
        return (value // step) * step

    @classmethod
    def build_limit_order_body(cls, *, instrument: str, side: str, quantity: Decimal, limit_price: Decimal, client_order_id: str, tick_size: Decimal, lot_size: Decimal) -> dict[str, str]:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if not instrument or instrument.endswith(("-SWAP", "-FUTURES", "-OPTION")):
            raise ValueError("only SPOT instruments are supported")
        price = cls.normalize_decimal(limit_price, tick_size)
        size = cls.normalize_decimal(quantity, lot_size)
        if price <= 0 or size <= 0:
            raise ValueError("normalized price and size must be positive")
        if len(client_order_id) > 32 or not client_order_id.isalnum():
            raise ValueError("OKX clOrdId must be at most 32 alphanumeric characters")
        return {"instId": instrument, "tdMode": "cash", "clOrdId": client_order_id, "side": side, "ordType": "limit", "px": format(price, "f"), "sz": format(size, "f")}

    @staticmethod
    def build_cancel_body(*, instrument: str, client_order_id: str) -> dict[str, str]:
        if not instrument or not client_order_id:
            raise ValueError("instrument and client order ID are required")
        return {"instId": instrument, "clOrdId": client_order_id}

    @staticmethod
    def parse_order_response(payload: dict) -> dict[str, str]:
        if str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list) or len(payload["data"]) != 1:
            raise ValueError("malformed or rejected OKX order response")
        row = payload["data"][0]
        required = ("ordId", "clOrdId", "sCode", "sMsg")
        if any(name not in row for name in required):
            raise ValueError("OKX order response is missing required fields")
        return {name: str(row[name]) for name in required}

    def _read(self, operation: LiveOperation, query: dict[str, str] | None = None):
        route = route_for(operation)
        if route.classification not in {EndpointClass.PUBLIC_READ, EndpointClass.AUTHENTICATED_READ}:
            raise PermissionError("adapter read method selected a non-read endpoint")
        path = build_request_path(operation, query)
        headers = {"Content-Type": "application/json"}
        if route.authenticated:
            if self.credential_material is None:
                raise PermissionError("authenticated read requires local credential material")
            headers = signed_headers(credential_material=self.credential_material, method="GET", request_path=path)
        self.network_reads += 1
        return self.transport.execute(method="GET", url=OKX_PRODUCTION_ORIGIN + path, headers=headers, body=b"")

    def read_account_config(self): return self._read(LiveOperation.ACCOUNT_CONFIG)
    def read_balances(self): return self._read(LiveOperation.BALANCES)
    def read_positions(self): return self._read(LiveOperation.POSITIONS, {"instType": "SPOT"})
    def query_order(self, *, instrument: str, client_order_id: str): return self._read(LiveOperation.ORDER_DETAILS, {"instId": instrument, "clOrdId": client_order_id})
    def recent_orders(self, *, instrument: str): return self._read(LiveOperation.RECENT_ORDERS, {"instType": "SPOT", "instId": instrument})
    def open_orders(self, *, instrument: str): return self._read(LiveOperation.OPEN_ORDERS, {"instType": "SPOT", "instId": instrument})
    def fills(self, *, instrument: str): return self._read(LiveOperation.FILLS, {"instType": "SPOT", "instId": instrument})

    def submit_order(self, request_body):
        self.network_writes += 0
        raise ProductionWriteSuppressed("Phase 8A cannot invoke OKX order submission")

    def cancel_order(self, request_body):
        self.network_writes += 0
        raise ProductionWriteSuppressed("Phase 8A cannot invoke OKX cancellation")


__all__ = ["UrllibReadOnlyTransport", "signed_headers", "OkxProductionSpotAdapter"]
