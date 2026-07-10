"""Fixed deterministic simulated processing latency."""
from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime,timedelta
from types import MappingProxyType
from typing import Mapping
from secure_eval_wrapper.fix.models import LatencySample,LatencyStage

@dataclass(frozen=True)
class FixedLatencyModel:
 stage_microseconds:Mapping[LatencyStage|str,int]=field(default_factory=dict)
 threshold_microseconds:Mapping[LatencyStage|str,int]=field(default_factory=dict)
 def __post_init__(self):
  stages={LatencyStage(k):int(v) for k,v in self.stage_microseconds.items()}; thresholds={LatencyStage(k):int(v) for k,v in self.threshold_microseconds.items()}
  if any(v<0 for v in (*stages.values(),*thresholds.values())): raise ValueError("latency and thresholds must be non-negative")
  object.__setattr__(self,"stage_microseconds",MappingProxyType(stages)); object.__setattr__(self,"threshold_microseconds",MappingProxyType(thresholds))
 def apply(self,*,fix_session_id,fix_message_id,stage,start_utc):
  stage=LatencyStage(stage); duration=self.stage_microseconds.get(stage,0); end=start_utc+timedelta(microseconds=duration)
  return LatencySample(fix_session_id=fix_session_id,fix_message_id=fix_message_id,stage=stage,simulated_start_utc=start_utc,simulated_end_utc=end,duration_microseconds=duration,threshold_microseconds=self.threshold_microseconds.get(stage))