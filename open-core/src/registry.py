from typing import Dict

from src.core.strategy_base import StrategyBase
from src.strategies.demo_strategy import DemoStrategy


def get_strategy(name: str) -> StrategyBase:
    registry: Dict[str, StrategyBase] = {
        "demo": DemoStrategy(),
    }
    if name not in registry:
        raise ValueError(f"Unknown strategy: {name}")
    return registry[name]
