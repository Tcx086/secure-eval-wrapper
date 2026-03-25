from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class Signal:
    action: str
    score: float
    confidence: float
    meta: Dict[str, Any]


class StrategyBase:
    name = "base"

    def generate_signal(self, features: Dict[str, Any]) -> Signal:
        raise NotImplementedError
