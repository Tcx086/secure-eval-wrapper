"""Exact OKX production endpoint catalog for Phase 8A.

Verified 2026-07-13 against the official OKX V5 documentation.  The catalog contains
only named operations; callers cannot supply arbitrary paths.  Trading-write routes are
represented for hashing and exact request planning, but Phase 8A always suppresses them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from urllib.parse import urlencode

from secure_eval_wrapper.data_collection.hashing import sha256_payload


class EndpointClass(str, Enum):
    PUBLIC_READ = "public_read"
    AUTHENTICATED_READ = "authenticated_read"
    TRADING_WRITE = "trading_write"
    FORBIDDEN = "forbidden"


class LiveOperation(str, Enum):
    VENUE_TIME = "venue_time"
    PUBLIC_INSTRUMENTS = "public_instruments"
    PUBLIC_TICKER = "public_ticker"
    ACCOUNT_CONFIG = "account_config"
    ACCOUNT_INSTRUMENTS = "account_instruments"
    BALANCES = "balances"
    POSITIONS = "positions"
    ORDER_DETAILS = "order_details"
    OPEN_ORDERS = "open_orders"
    RECENT_ORDERS = "recent_orders"
    FILLS = "fills"
    SUBMIT_LIMIT_ORDER = "submit_limit_order"
    CANCEL_ORDER = "cancel_order"


@dataclass(frozen=True)
class EndpointRoute:
    operation: LiveOperation
    method: str
    path: str
    classification: EndpointClass
    authenticated: bool

    @property
    def record_hash(self) -> str:
        return sha256_payload(self.__dict__)


OKX_PRODUCTION_ORIGIN = "https://www.okx.com"
OFFICIAL_DOCUMENTATION_URL = "https://www.okx.com/docs-v5/en/"
_ROUTES = (
    EndpointRoute(LiveOperation.VENUE_TIME, "GET", "/api/v5/public/time", EndpointClass.PUBLIC_READ, False),
    EndpointRoute(LiveOperation.PUBLIC_INSTRUMENTS, "GET", "/api/v5/public/instruments", EndpointClass.PUBLIC_READ, False),
    EndpointRoute(LiveOperation.PUBLIC_TICKER, "GET", "/api/v5/market/ticker", EndpointClass.PUBLIC_READ, False),
    EndpointRoute(LiveOperation.ACCOUNT_CONFIG, "GET", "/api/v5/account/config", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.ACCOUNT_INSTRUMENTS, "GET", "/api/v5/account/instruments", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.BALANCES, "GET", "/api/v5/account/balance", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.POSITIONS, "GET", "/api/v5/account/positions", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.ORDER_DETAILS, "GET", "/api/v5/trade/order", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.OPEN_ORDERS, "GET", "/api/v5/trade/orders-pending", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.RECENT_ORDERS, "GET", "/api/v5/trade/orders-history", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.FILLS, "GET", "/api/v5/trade/fills-history", EndpointClass.AUTHENTICATED_READ, True),
    EndpointRoute(LiveOperation.SUBMIT_LIMIT_ORDER, "POST", "/api/v5/trade/order", EndpointClass.TRADING_WRITE, True),
    EndpointRoute(LiveOperation.CANCEL_ORDER, "POST", "/api/v5/trade/cancel-order", EndpointClass.TRADING_WRITE, True),
)
CATALOG = MappingProxyType({route.operation: route for route in _ROUTES})
FORBIDDEN_PREFIXES = (
    "/api/v5/asset/", "/api/v5/users/", "/api/v5/account/set-", "/api/v5/account/borrow",
    "/api/v5/account/repay", "/api/v5/account/interest", "/api/v5/account/leverage",
    "/api/v5/trade/batch", "/api/v5/trade/order-algo", "/api/v5/trade/cancel-algos",
    "/api/v5/trade/cancel-all-after", "/ws/",
)


def endpoint_catalog_hash() -> str:
    return sha256_payload({route.operation.value: route.record_hash for route in _ROUTES})


def route_for(operation: LiveOperation | str) -> EndpointRoute:
    try:
        return CATALOG[LiveOperation(operation)]
    except (KeyError, ValueError) as exc:
        raise PermissionError("operation is absent from the exact Phase 8A endpoint catalog") from exc


def classify_exact(method: str, path: str) -> EndpointClass:
    normalized_method = str(method).upper()
    normalized_path = str(path).split("?", 1)[0]
    if any(normalized_path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return EndpointClass.FORBIDDEN
    matches = [route for route in _ROUTES if route.method == normalized_method and route.path == normalized_path]
    if len(matches) != 1:
        return EndpointClass.FORBIDDEN
    return matches[0].classification


def build_request_path(operation: LiveOperation | str, query: dict[str, str] | None = None, *, allow_trading_write: bool = False) -> str:
    route = route_for(operation)
    if route.classification is EndpointClass.FORBIDDEN:
        raise PermissionError("forbidden endpoint")
    if route.classification is EndpointClass.TRADING_WRITE and not allow_trading_write:
        raise PermissionError("Phase 8A trading writes are suppressed before transport")
    query = {} if query is None else dict(query)
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in query.items()):
        raise ValueError("endpoint query values must be explicit strings")
    return route.path + ("?" + urlencode(sorted(query.items())) if query else "")


__all__ = [
    "EndpointClass", "LiveOperation", "EndpointRoute", "OKX_PRODUCTION_ORIGIN",
    "OFFICIAL_DOCUMENTATION_URL", "CATALOG", "FORBIDDEN_PREFIXES", "endpoint_catalog_hash",
    "route_for", "classify_exact", "build_request_path",
]
