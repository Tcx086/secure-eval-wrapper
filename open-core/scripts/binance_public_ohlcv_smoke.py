"""Optional public-network smoke check for the Binance Spot OHLCV adapter."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


OPEN_CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPEN_CORE_ROOT / "src"))

from secure_eval_wrapper.data_collection import (  # noqa: E402
    BinanceSpotOhlcvProvider,
    DataRequest,
    MarketDataType,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    """Fetch at most two completed public bars when explicitly enabled."""

    if os.environ.get("ENABLE_PUBLIC_NETWORK_SMOKE", "").strip().lower() != "true":
        print("Binance public OHLCV smoke is disabled by default.")
        return 0

    now_utc = datetime.now(timezone.utc)
    end_at_utc = now_utc.replace(second=0, microsecond=0) - timedelta(minutes=1)
    start_at_utc = end_at_utc - timedelta(minutes=2)
    provider = BinanceSpotOhlcvProvider()
    observations = provider.fetch_ohlcv(
        DataRequest(
            collection_run_id=uuid4(),
            provider_name="binance",
            data_type=MarketDataType.OHLCV,
            symbols=("BTC-USDT",),
            timeframe="1m",
            start_at_utc=start_at_utc,
            end_at_utc=end_at_utc,
            limit=2,
        )
    )
    hashes_valid = all(
        _SHA256_PATTERN.fullmatch(item.source_sha256) is not None
        for item in observations
    )
    print(
        "Binance public OHLCV smoke summary: "
        f"endpoint=/api/v3/klines observations={len(observations)} "
        f"source_hashes_valid={hashes_valid}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
