from typing import Dict, Any

from src.core.strategy_base import StrategyBase, Signal


class DemoStrategy(StrategyBase):
    """A toy strategy for public demo. No proprietary edge here."""

    name = "demo"

    def generate_signal(self, features: Dict[str, Any]) -> Signal:
        price_momentum = float(features.get("price_momentum", 0.0))
        news_sentiment = float(features.get("news_sentiment", 0.0))
        volatility = float(features.get("volatility", 1.0))

        score = 0.6 * price_momentum + 0.4 * news_sentiment - 0.2 * volatility

        if score > 0.15:
            action = "LONG"
        elif score < -0.15:
            action = "SHORT"
        else:
            action = "FLAT"

        confidence = min(1.0, max(0.0, abs(score)))

        return Signal(
            action=action,
            score=round(score, 4),
            confidence=round(confidence, 4),
            meta={"strategy": self.name, "is_demo": True},
        )
