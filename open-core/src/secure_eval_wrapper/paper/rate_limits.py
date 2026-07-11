"""Provider-neutral deterministic rate limits and bounded retry schedules."""
from collections import defaultdict,deque
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts:int; delays_seconds:tuple[Decimal,...]
    def __post_init__(self):
        if self.maximum_attempts<1 or len(self.delays_seconds)<max(0,self.maximum_attempts-1) or any((not d.is_finite() or d<=0) for d in self.delays_seconds):raise ValueError("retry schedule must be positive and bounded")

class PaperRateLimiter:
    def __init__(self,*,orders_per_minute,cancellations_per_minute,clock):
        self.limits={"submit":orders_per_minute,"cancel":cancellations_per_minute}; self.clock=clock; self.history=defaultdict(deque); self.events=[]; self.consecutive_failures=0
    def acquire(self,operation):
        now=self.clock(); q=self.history[operation]
        while q and q[0]<=now-timedelta(seconds=60):q.popleft()
        limit=self.limits.get(operation)
        if limit is not None and len(q)>=limit:raise RuntimeError(f"paper {operation} rate limit reached")
        q.append(now); event={"operation":operation,"at_utc":now,"count":len(q),"local_limit":limit}; self.events.append(event); return event
    def record_result(self,success):self.consecutive_failures=0 if success else self.consecutive_failures+1

def bounded_retry(operation,*,policy,should_retry,clock,sleeper,on_attempt):
    result=None
    for attempt in range(1,policy.maximum_attempts+1):
        result=operation(attempt); on_attempt(attempt,result)
        if not should_retry(result) or attempt==policy.maximum_attempts:return result
        sleeper(policy.delays_seconds[attempt-1])
    return result
