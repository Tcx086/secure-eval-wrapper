"""Lazy credential boundary and centralized redaction."""
from __future__ import annotations
import hashlib,os,re
from abc import ABC,abstractmethod
from collections.abc import Mapping
from .enums import CredentialSourceType,PaperEnvironment,PaperProvider
from .models import CredentialReference

_SECRET_KEYS=re.compile(r"(api.?key|secret|passphrase|signature|authorization|cookie|token|ok-access-(?:key|sign|passphrase))",re.I)
_SECRET_QUERY=re.compile(r"([?&](?:api_?key|signature|token|passphrase|secret)=)[^&]+",re.I)

def redact(value):
    if isinstance(value,Mapping):return {str(k):("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return type(value)(redact(v) for v in value)
    if isinstance(value,str):return _SECRET_QUERY.sub(r"\1[REDACTED]",value)
    return value

class CredentialMaterial:
    __slots__=("_api_key","_secret_key","_passphrase")
    def __init__(self,api_key,secret_key,passphrase):
        if not all(isinstance(v,str) and v for v in (api_key,secret_key,passphrase)):raise ValueError("required credential field is missing")
        self._api_key=api_key; self._secret_key=secret_key; self._passphrase=passphrase
    def values_for_request(self):return self._api_key,self._secret_key,self._passphrase
    def __repr__(self):return "CredentialMaterial([REDACTED])"
    __str__=__repr__

class CredentialProvider(ABC):
    @abstractmethod
    def reference(self)->CredentialReference:raise NotImplementedError
    @abstractmethod
    def load(self,*,gates:Mapping[str,bool])->CredentialMaterial:raise NotImplementedError

_REQUIRED_GATES=("cli_external_sandbox","paper_enabled","provider_selected","sandbox_environment","endpoint_validated","configuration_valid","live_false","kill_switch_inactive","limits_configured")
def _validate_gates(gates):
    missing=[name for name in _REQUIRED_GATES if gates.get(name) is not True]
    if missing:raise PermissionError("credential load blocked by paper safety gates: "+",".join(missing))

class EnvironmentCredentialProvider(CredentialProvider):
    def __init__(self,*,provider=PaperProvider.OKX_DEMO,alias="OKX_DEMO_API_KEY",key_var="OKX_DEMO_API_KEY",secret_var="OKX_DEMO_SECRET_KEY",passphrase_var="OKX_DEMO_PASSPHRASE"):
        self.provider=PaperProvider(provider); self.alias=alias; self.key_var=key_var; self.secret_var=secret_var; self.passphrase_var=passphrase_var; self.load_count=0
    def reference(self):
        fingerprint=hashlib.sha256(self.alias.encode()).hexdigest()[:16]
        return CredentialReference(self.provider,self.alias,CredentialSourceType.ENVIRONMENT,fingerprint,False)
    def load(self,*,gates):
        _validate_gates(gates); self.load_count+=1
        return CredentialMaterial(os.environ.get(self.key_var,""),os.environ.get(self.secret_var,""),os.environ.get(self.passphrase_var,""))

class InjectedCredentialProvider(CredentialProvider):
    def __init__(self,api_key="public-test-key-id",secret_key="injected-secret",passphrase="injected-passphrase",alias="injected-test"):
        self._values=(api_key,secret_key,passphrase); self.alias=alias; self.load_count=0
    def reference(self):return CredentialReference(PaperProvider.OKX_DEMO,self.alias,CredentialSourceType.INJECTED_TEST,hashlib.sha256(self._values[0].encode()).hexdigest()[:16],False)
    def load(self,*,gates):_validate_gates(gates); self.load_count+=1; return CredentialMaterial(*self._values)
