"""Preconfigured deterministic simulated connection-fault schedule."""
from __future__ import annotations
from dataclasses import dataclass
from secure_eval_wrapper.fix.models import ConnectionFault,ConnectionFaultType

@dataclass
class FaultSchedule:
 faults:tuple[ConnectionFault,...]=()
 def __post_init__(self):
  self.faults=tuple(sorted(self.faults,key=lambda f:(f.scheduled_at_utc,f.fault_type.value,str(f.connection_fault_id)))); self._activated=set()
 def due(self,at_utc,*,fault_type=None):
  kind=None if fault_type is None else ConnectionFaultType(fault_type)
  return tuple(f for f in self.faults if f.connection_fault_id not in self._activated and f.scheduled_at_utc<=at_utc and (kind is None or f.fault_type is kind))
 def activate(self,fault,at_utc):
  if fault.connection_fault_id in self._activated: return fault
  self._activated.add(fault.connection_fault_id)
  return ConnectionFault(fix_session_id=fault.fix_session_id,fault_type=fault.fault_type,scheduled_at_utc=fault.scheduled_at_utc,reason_code=fault.reason_code,configuration=fault.configuration,activated_at_utc=at_utc)