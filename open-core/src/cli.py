import argparse
import json

from src.data.loader import load_feature_file
from src.registry import get_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Local strategy runner (public demo)")
    parser.add_argument("--input", required=True, help="Path to feature JSON")
    parser.add_argument("--strategy", default="demo", help="Strategy name")
    args = parser.parse_args()

    features = load_feature_file(args.input)
    strategy = get_strategy(args.strategy)
    signal = strategy.generate_signal(features)

    result = {
        "action": signal.action,
        "score": signal.score,
        "confidence": signal.confidence,
        "meta": signal.meta,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
