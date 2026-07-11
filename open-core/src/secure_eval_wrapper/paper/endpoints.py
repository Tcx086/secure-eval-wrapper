"""Immutable provider/environment endpoint catalog.

Verified 2026-07-11 against official OKX V5 documentation. OKX demo REST shares the
openapi.okx.com hostname with production, so the mandatory x-simulated-trading=1 marker and
route allowlist are inseparable proof. A missing marker is treated as production and denied.
"""
from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qsl,urlsplit
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from .enums import PaperEnvironment,PaperProvider,TransportRequestType

@dataclass(frozen=True)
class EndpointSpec:
    provider:PaperProvider; environment:PaperEnvironment; rest_origin:str; required_header_name:str; required_header_value:str; allowed_routes:tuple[tuple[str,str,TransportRequestType],...]; documentation_url:str; verified_on:str
    @property
    def record_sha256(self):return sha256_payload(self.__dict__)

OKX_DEMO=EndpointSpec(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX,"https://openapi.okx.com","x-simulated-trading","1",(
("GET","/api/v5/account/config",TransportRequestType.ACCOUNT_MODE),("GET","/api/v5/account/instruments",TransportRequestType.INSTRUMENTS),("GET","/api/v5/account/balance",TransportRequestType.BALANCES),("GET","/api/v5/account/positions",TransportRequestType.POSITIONS),("POST","/api/v5/trade/order",TransportRequestType.SUBMIT),("POST","/api/v5/trade/cancel-order",TransportRequestType.CANCEL),("GET","/api/v5/trade/order",TransportRequestType.QUERY_ORDER),("GET","/api/v5/trade/orders-pending",TransportRequestType.OPEN_ORDERS),("GET","/api/v5/trade/orders-history",TransportRequestType.RECENT_ORDERS),("GET","/api/v5/trade/fills",TransportRequestType.FILLS)),"https://www.okx.com/docs-v5/en/","2026-07-11")
CATALOG=MappingProxyType({(OKX_DEMO.provider,OKX_DEMO.environment):OKX_DEMO})
PRODUCTION_ONLY_HOSTS=frozenset({"www.okx.com","ws.okx.com","aws.okx.com","wsaws.okx.com"})
SENSITIVE_QUERY=frozenset({"api_key","apikey","signature","secret","passphrase","token","authorization","x-simulated-trading"})

def catalog_sha256():return sha256_payload({f"{p.value}:{e.value}":s.record_sha256 for (p,e),s in CATALOG.items()})

def endpoint_spec(provider,environment):
    provider=PaperProvider(provider); environment=PaperEnvironment(environment)
    if environment is PaperEnvironment.LIVE:raise ValueError("live endpoints are forbidden in Phase 7")
    try:return CATALOG[(provider,environment)]
    except KeyError as exc:raise ValueError("provider/environment is not in immutable paper endpoint catalog") from exc

def validate_endpoint_request(provider,environment,method,url,headers):
    spec=endpoint_spec(provider,environment); parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.username or parsed.password or parsed.fragment:raise ValueError("paper endpoint must be exact HTTPS without userinfo or fragment")
    if parsed.port not in (None,443):raise ValueError("unapproved trading endpoint port")
    origin=f"{parsed.scheme}://{parsed.hostname}"+(f":{parsed.port}" if parsed.port not in (None,443) else "")
    if parsed.hostname in PRODUCTION_ONLY_HOSTS or origin!=spec.rest_origin:raise ValueError("production or unapproved endpoint rejected")
    lowered={str(k).lower():str(v) for k,v in headers.items()}
    if lowered.get(spec.required_header_name)!=spec.required_header_value:raise ValueError("missing mandatory demo-trading environment marker; production access denied")
    query=dict(parse_qsl(parsed.query,keep_blank_values=True))
    if any(k.lower() in SENSITIVE_QUERY for k in query):raise ValueError("sensitive or environment-overriding query parameter rejected")
    route=(str(method).upper(),parsed.path)
    matches=[kind for m,p,kind in spec.allowed_routes if (m,p)==route]
    if not matches:raise ValueError("unapproved paper route")
    return matches[0]

def validate_redirect(provider,environment,location,headers):
    validate_endpoint_request(provider,environment,"GET",location,headers)
    raise ValueError("redirects are disabled even when the target appears allowlisted")
