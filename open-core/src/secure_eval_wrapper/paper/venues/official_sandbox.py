"""Official OKX V5 demo-trading REST adapter (verified 2026-07-11).

Only exact allowlisted account/order/fill routes are present. No funding transfer, withdrawal,
deposit, API-key management, arbitrary URL, production marker, or WebSocket is implemented.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from decimal import Decimal
from urllib.parse import urlencode
from secure_eval_wrapper.data_collection.hashing import canonical_json_dumps,sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from ..endpoints import endpoint_spec,validate_endpoint_request
from ..enums import AccountSnapshotStatus,PaperEnvironment,PaperProvider,TransportRequestType,TransportResultType,VenueOrderState
from ..models import PaperAccountSnapshot,TransportRequest,VenueBalance,VenueFill,VenueOrder,VenuePosition
from ..venue import EconomicConflictError,PaperVenue,UnknownSubmissionResult,VenueTimeout

_STATE={"live":VenueOrderState.ACKNOWLEDGED,"partially_filled":VenueOrderState.PARTIALLY_FILLED,"filled":VenueOrderState.FILLED,"canceled":VenueOrderState.CANCELLED,"mmp_canceled":VenueOrderState.CANCELLED,"effective":VenueOrderState.ACKNOWLEDGED}
def _utc_ms(value,name):
    try:return datetime.fromtimestamp(int(value)/1000,tz=timezone.utc)
    except Exception as exc:raise ValueError(f"{name} must be an OKX UTC epoch-millisecond timestamp") from exc
class OkxDemoVenue(PaperVenue):
    def __init__(self,transport,*,account_reference="okx-demo-local-alias",clock):
        self.transport=transport; self.account_reference=account_reference; self.clock=clock; self.spec=endpoint_spec(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX); self._orders={}; self._fills={}; self._submissions={}; self._sequence=0
    def _request(self,kind,method,path,*,query=None,body=None,idempotency_key=None):
        query=tuple(sorted((query or {}).items())); suffix=("?"+urlencode(query)) if query else ""; raw=b"" if body is None else canonical_json_dumps(body).encode(); url=self.spec.rest_origin+path+suffix
        validate_endpoint_request(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX,method,url,{self.spec.required_header_name:self.spec.required_header_value})
        value=TransportRequest(kind,method,url,path+suffix,raw,idempotency_key,self.clock()); result=self.transport.execute(value)
        if result.result_type is TransportResultType.UNKNOWN:raise UnknownSubmissionResult("official sandbox submission result unknown")
        if result.result_type is TransportResultType.TIMEOUT:raise VenueTimeout("official sandbox request timed out")
        if result.result_type is not TransportResultType.SUCCEEDED:raise RuntimeError("official sandbox request rejected: "+result.result_type.value)
        if str(result.payload.get("code","0"))!="0":raise RuntimeError("official sandbox returned a non-zero public-safe result code")
        return result.payload
    def verify_credentials(self):return self._request(TransportRequestType.VERIFY_CREDENTIALS,"GET","/api/v5/account/config")
    def fetch_account_mode(self):
        data=self._request(TransportRequestType.ACCOUNT_MODE,"GET","/api/v5/account/config").get("data",[])
        if not isinstance(data,list) or not data:raise ValueError("OKX demo account config response is missing data")
        return str(data[0].get("acctLv",""))
    def fetch_instruments(self,inst_type="SPOT"):return self._request(TransportRequestType.INSTRUMENTS,"GET","/api/v5/account/instruments",query={"instType":inst_type}).get("data",[])
    def submit_order(self,s):
        existing=self._submissions.get(s.client_order_id)
        if existing:
            if existing.economics_sha256!=s.economics_sha256:raise EconomicConflictError("client order retry changed economics")
            return self._orders.get(s.client_order_id) or self.query_order(s.client_order_id)
        if s.order_type not in (OrderType.MARKET,OrderType.LIMIT):raise ValueError("verified OKX demo subset supports market and limit only")
        body={"instId":s.series_identity.provider_instrument_id,"tdMode":"cash","clOrdId":s.client_order_id,"side":s.side.value,"ordType":s.order_type.value,"sz":format(s.quantity,"f")}
        if s.order_type is OrderType.LIMIT:body["px"]=format(s.limit_price,"f")
        payload=self._request(TransportRequestType.SUBMIT,"POST","/api/v5/trade/order",body=body,idempotency_key=s.idempotency_key); data=payload.get("data",[])
        if not isinstance(data,list) or len(data)!=1:raise ValueError("OKX demo order acknowledgement must contain exactly one item")
        item=data[0]
        if str(item.get("sCode","0"))!="0":raise RuntimeError("OKX demo order was rejected")
        venue_id=str(item.get("ordId","")).strip()
        if not venue_id:raise ValueError("OKX demo acknowledgement missing ordId")
        self._sequence+=1; order=VenueOrder(s.paper_run_id,s.submission_id,s.client_order_id,venue_id,s.series_identity,s.side,s.order_type,s.time_in_force,s.accounting_mode,s.quantity,Decimal(0),None,VenueOrderState.PENDING_ACK,s.submitted_at_utc,self.clock(),self._sequence,s.economics_sha256,s.limit_price,s.stop_price)
        self._submissions[s.client_order_id]=s; self._orders[s.client_order_id]=order; return order
    def _parse_order(self,item,prior):
        state=_STATE.get(str(item.get("state")))
        if state is None:raise ValueError("unsupported OKX demo order state")
        created=_utc_ms(item.get("cTime"),"cTime"); updated=_utc_ms(item.get("uTime"),"uTime"); cumulative=Decimal(str(item.get("accFillSz","0"))); average=Decimal(str(item["avgPx"])) if item.get("avgPx") else None
        if prior and cumulative<prior.cumulative_filled_quantity:raise ValueError("OKX cumulative filled quantity decreased")
        self._sequence+=1
        return VenueOrder(prior.paper_run_id,prior.submission_id,prior.client_order_id,str(item.get("ordId")),prior.series_identity,prior.side,prior.order_type,prior.time_in_force,prior.accounting_mode,prior.quantity,cumulative,average,state,created,updated,self._sequence,prior.economics_sha256,prior.limit_price,prior.stop_price)
    def query_order(self,client_order_id):
        prior=self._orders.get(client_order_id)
        if prior is None:return None
        data=self._request(TransportRequestType.QUERY_ORDER,"GET","/api/v5/trade/order",query={"clOrdId":client_order_id,"instId":prior.series_identity.provider_instrument_id}).get("data",[])
        if not data:return None
        order=self._parse_order(data[0],prior); self._orders[client_order_id]=order; return order
    def cancel_order(self,client_order_id,at_utc):
        prior=self._orders[client_order_id]; self._request(TransportRequestType.CANCEL,"POST","/api/v5/trade/cancel-order",body={"instId":prior.series_identity.provider_instrument_id,"clOrdId":client_order_id},idempotency_key=client_order_id+":cancel"); order=VenueOrder(**{**prior.__dict__,"state":VenueOrderState.CANCEL_PENDING,"updated_at_utc":at_utc,"venue_sequence":prior.venue_sequence+1}); self._orders[client_order_id]=order; return order
    def list_open_orders(self):return tuple(o for o in self._orders.values() if o.state not in (VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED))
    def fetch_balances(self):
        data=self._request(TransportRequestType.BALANCES,"GET","/api/v5/account/balance").get("data",[])
        details=data[0].get("details",[]) if data else []
        rows=[]
        for item in details:
            total=Decimal(str(item.get("cashBal") or item.get("eq") or "0")); available=Decimal(str(item.get("availBal") or item.get("availEq") or "0")); reserved=max(Decimal(0),total-available); rows.append(VenueBalance(str(item.get("ccy")),total,available,reserved))
        return tuple(sorted(rows,key=lambda b:b.currency))
    def fetch_positions(self):
        data=self._request(TransportRequestType.POSITIONS,"GET","/api/v5/account/positions").get("data",[]); rows=[]
        from secure_eval_wrapper.alpha.identity import SeriesIdentity
        from secure_eval_wrapper.data_collection.models import InstrumentType
        for item in data:
            inst=str(item.get("instId","")).strip(); qty=Decimal(str(item.get("pos") or "0"))
            if not inst or qty==0:continue
            identity=SeriesIdentity("okx","OKX",inst,inst,InstrumentType.PERPETUAL_SWAP,"account","USDT"); avg=Decimal(str(item.get("avgPx"))) if item.get("avgPx") else None; rows.append(VenuePosition(identity,AccountingMode.LINEAR_PERPETUAL,qty,avg,Decimal(str(item.get("realizedPnl") or "0"))))
        return tuple(rows)
    def fetch_fills(self):
        data=self._request(TransportRequestType.FILLS,"GET","/api/v5/trade/fills").get("data",[])
        for item in data:
            client=str(item.get("clOrdId","")).strip(); prior=self._orders.get(client)
            if prior is None:continue
            venue_fill_id=str(item.get("tradeId","")).strip()
            if not venue_fill_id:raise ValueError("OKX demo fill missing tradeId")
            fee=abs(Decimal(str(item.get("fee") or "0"))); fill=VenueFill(prior.paper_run_id,prior.submission_id,client,str(item.get("ordId")),venue_fill_id,prior.series_identity,prior.side,prior.accounting_mode,Decimal(str(item.get("fillSz"))),Decimal(str(item.get("fillPx"))),fee,str(item.get("feeCcy") or "USDT"),_utc_ms(item.get("fillTime"),"fillTime"),self._sequence,PaperEnvironment.PAPER_EXCHANGE_SANDBOX); self._fills.setdefault(venue_fill_id,fill)
        return tuple(sorted(self._fills.values(),key=lambda f:(f.filled_at_utc,f.venue_fill_id)))
    def fetch_recent_orders(self):return self._request(TransportRequestType.RECENT_ORDERS,"GET","/api/v5/trade/orders-history",query={"instType":"SPOT"}).get("data",[])
    def fetch_account_snapshot(self,paper_run_id,at_utc):
        mode=self.fetch_account_mode(); balances=self.fetch_balances(); positions=self.fetch_positions(); return PaperAccountSnapshot(paper_run_id,self.account_reference,AccountSnapshotStatus.FRESH,at_utc,at_utc,mode,balances,positions,tuple(o.client_order_id for o in self.list_open_orders()),self._sequence,"okx_official_demo")
