"""Injectable bounded authenticated transport for official demo adapters."""
from __future__ import annotations
import base64,hashlib,hmac,json,socket
from abc import ABC,abstractmethod
from datetime import datetime,timezone
from decimal import Decimal
from urllib import error,request
from secure_eval_wrapper.data_collection.hashing import canonical_json_dumps
from .credentials import CredentialProvider,redact
from .endpoints import endpoint_spec,validate_endpoint_request
from .enums import PaperEnvironment,PaperProvider,TransportResultType
from .models import TransportRequest,TransportResult

class AuthenticatedTransport(ABC):
    @abstractmethod
    def execute(self,request_value:TransportRequest)->TransportResult:raise NotImplementedError

class RedirectDenied(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise error.HTTPError(args[0].full_url,args[1],"redirect rejected",args[3],args[4])

class StandardLibraryOkxDemoTransport(AuthenticatedTransport):
    def __init__(self,credential_provider:CredentialProvider,*,gates,connect_timeout_seconds=5,read_timeout_seconds=10,max_response_bytes=1_000_000,clock=lambda:datetime.now(timezone.utc)):
        self.credential_provider=credential_provider; self.gates=dict(gates); self.timeout=max(connect_timeout_seconds,read_timeout_seconds); self.max_response_bytes=max_response_bytes; self.clock=clock; self._opener=request.build_opener(RedirectDenied())
        if self.timeout<=0 or self.max_response_bytes<=0:raise ValueError("transport bounds must be positive")
    @staticmethod
    def signature(secret,timestamp,method,path,body):
        message=(timestamp+method.upper()+path+body.decode("utf-8")).encode("utf-8")
        return base64.b64encode(hmac.new(secret.encode(),message,hashlib.sha256).digest()).decode()
    def execute(self,value):
        spec=endpoint_spec(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX)
        validate_endpoint_request(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX,value.method,value.url,{spec.required_header_name:spec.required_header_value})
        material=self.credential_provider.load(gates=self.gates); api_key,secret,passphrase=material.values_for_request()
        now=self.clock(); timestamp=now.isoformat(timespec="milliseconds").replace("+00:00","Z")
        headers={"Content-Type":"application/json",spec.required_header_name:spec.required_header_value,"OK-ACCESS-KEY":api_key,"OK-ACCESS-TIMESTAMP":timestamp,"OK-ACCESS-PASSPHRASE":passphrase,"OK-ACCESS-SIGN":self.signature(secret,timestamp,value.method,value.path_with_query,value.body)}
        validate_endpoint_request(PaperProvider.OKX_DEMO,PaperEnvironment.PAPER_EXCHANGE_SANDBOX,value.method,value.url,headers)
        req=request.Request(value.url,data=value.body or None,headers=headers,method=value.method)
        try:
            with self._opener.open(req,timeout=self.timeout) as response:
                raw=response.read(self.max_response_bytes+1)
                ambiguous=value.request_type.value in ("submit","cancel")
                if len(raw)>self.max_response_bytes:return TransportResult(value.request_id,TransportResultType.UNKNOWN if ambiguous else TransportResultType.MALFORMED,int(response.status),self.clock(),None,{},True,explanation="oversized response after request transmission")
                try:payload=json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError,json.JSONDecodeError):return TransportResult(value.request_id,TransportResultType.UNKNOWN if ambiguous else TransportResultType.MALFORMED,int(response.status),self.clock(),hashlib.sha256(raw).hexdigest(),{},True,explanation="malformed response after request transmission")
                if not isinstance(payload,dict):return TransportResult(value.request_id,TransportResultType.UNKNOWN if ambiguous else TransportResultType.MALFORMED,int(response.status),self.clock(),hashlib.sha256(raw).hexdigest(),{},True,explanation="non-object response after request transmission")
                result=TransportResultType.SUCCEEDED if str(payload.get("code","0"))=="0" else TransportResultType.REJECTED
                return TransportResult(value.request_id,result,int(response.status),self.clock(),hashlib.sha256(raw).hexdigest(),redact(payload),False,explanation="OKX demo response")
        except error.HTTPError as exc:
            status=int(exc.code); ambiguous=value.request_type.value in ("submit","cancel") and status in (408,429,500,502,503,504)
            kind=TransportResultType.UNKNOWN if ambiguous else TransportResultType.RATE_LIMITED if status==429 else TransportResultType.AUTHENTICATION_FAILED if status in (401,403) else TransportResultType.REJECTED
            return TransportResult(value.request_id,kind,status,self.clock(),None,{},status in (429,500,502,503,504),Decimal(str(exc.headers.get("Retry-After"))) if status==429 and exc.headers.get("Retry-After") else None,"bounded OKX demo HTTP failure")
        except (TimeoutError,socket.timeout,error.URLError,ConnectionError,ConnectionResetError,EOFError,OSError):
            kind=TransportResultType.UNKNOWN if value.request_type.value in ("submit","cancel") else TransportResultType.TIMEOUT
            return TransportResult(value.request_id,kind,None,self.clock(),None,{},True,explanation="bounded transport timeout")
        finally:
            del material,api_key,secret,passphrase

class FakeTransport(AuthenticatedTransport):
    def __init__(self,results):self.results=list(results); self.requests=[]
    def execute(self,value):
        self.requests.append(value)
        if not self.results:raise RuntimeError("fake transport has no result")
        result=self.results.pop(0)
        return result(value) if callable(result) else result
