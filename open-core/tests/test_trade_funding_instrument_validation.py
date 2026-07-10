"""Offline normalization, validation, and gating tests for Phase 2J-2M."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection import (
    FundingIntervalSource,
    FundingRate,
    InstrumentMetadata,
    InstrumentStatus,
    InstrumentType,
    NormalizedTrade,
    TradeSide,
    binance_usdm_instrument_key,
    okx_spot_instrument_key,
    okx_swap_instrument_key,
)
from secure_eval_wrapper.data_validation import (
    ValidationCheckStatus,
    ValidationStatus,
    accepted_funding_rates,
    accepted_instruments,
    accepted_trades,
    compare_instrument_metadata,
    validate_funding_rates,
    validate_instruments,
    validate_trades,
)
from secure_eval_wrapper.data_validation.funding import (
    FUNDING_PROVIDER_INSTRUMENT_MISMATCH,
    INVALID_FUNDING_TIMESTAMP,
    FUNDING_TIMESTAMP_GAP,
)
from secure_eval_wrapper.data_validation.instruments import (
    INSTRUMENT_METADATA_DRIFT,
    INVALID_TICK_SIZE,
)
from secure_eval_wrapper.data_validation.trades import (
    DUPLICATE_PROVIDER_TRADE_ID,
    INVALID_TRADE_PRICE,
    MALFORMED_AGGREGATE_TRADE_RANGE,
)


RUN_ID = UUID("82000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=24)
SPOT = okx_spot_instrument_key("BTC-USDT")
SWAP = okx_swap_instrument_key("BTC-USDT-SWAP", settlement_asset="USDT")


def _obs(label: str):
    return uuid5(NAMESPACE_URL, f"validation-observation:{label}")


def _trade(label: str, second: int, *, provider_id: str | None = None):
    provider_id = provider_id or label
    return NormalizedTrade(
        trade_id=uuid5(NAMESPACE_URL, f"trade:{label}"),
        symbol=SPOT.canonical_symbol,
        exchange=SPOT.exchange_name,
        traded_at_utc=START + timedelta(seconds=second),
        price=Decimal("100"),
        quantity=Decimal("1"),
        side=TradeSide.BUY,
        source_observation_ids=(_obs(label),),
        provider_trade_id=provider_id,
        ingested_at_utc=NOW,
        provenance={
            "provider_name": "okx",
            "provider_instrument_id": SPOT.provider_instrument_id,
            "source_sha256": f"{second + 1:064x}",
        },
        instrument_key=SPOT,
        provider_sequence=second,
    )


def _funding(label: str, hour: int, rate: str = "0"):
    return FundingRate(
        funding_rate_id=uuid5(NAMESPACE_URL, f"funding:{label}"),
        symbol=SWAP.canonical_symbol,
        exchange=SWAP.exchange_name,
        funding_time_utc=START + timedelta(hours=hour),
        rate=Decimal(rate),
        source_observation_ids=(_obs(label),),
        funding_interval="8h",
        funding_interval_source=FundingIntervalSource.PROVIDER_REPORTED,
        mark_price=Decimal("100"),
        provenance={
            "provider_name": "okx",
            "provider_instrument_id": SWAP.provider_instrument_id,
            "source_sha256": f"{hour + 1:064x}",
        },
        instrument_key=SWAP,
        provider_instrument_id=SWAP.provider_instrument_id,
    )


def _instrument(label: str, *, tick: str = "0.01"):
    return InstrumentMetadata(
        instrument_id=uuid5(NAMESPACE_URL, f"instrument:{label}:{tick}"),
        symbol=SPOT.canonical_symbol,
        exchange=SPOT.exchange_name,
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.SPOT,
        status=InstrumentStatus.ACTIVE,
        source_observation_ids=(_obs(label),),
        first_seen_at_utc=NOW,
        last_seen_at_utc=NOW,
        metadata={"provenance": {"provider_name": "okx", "source_sha256": "a" * 64}},
        instrument_key=SPOT,
        tick_size=Decimal(tick),
        quantity_step=Decimal("0.00001"),
        minimum_quantity=Decimal("0.00001"),
        metadata_sha256=("b" if tick == "0.01" else "c") * 64,
    )


def _check(report, check_type):
    return next(item for item in report.results if item.details["check_type"] == check_type)


class TradeValidationTests(unittest.TestCase):
    def test_valid_trades_and_unknown_side_are_accepted(self):
        records = (_trade("1", 1), replace(_trade("2", 2), side=TradeSide.UNKNOWN))
        report = validate_trades(
            validation_run_id=RUN_ID,
            dataset_ref="trade-valid",
            trades=records,
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual(accepted_trades(records, report), records)

    def test_duplicate_provider_id_rejects_only_affected_records(self):
        records = (_trade("1", 1, provider_id="same"), _trade("2", 2, provider_id="same"), _trade("3", 3))
        report = validate_trades(
            validation_run_id=RUN_ID,
            dataset_ref="trade-duplicate",
            trades=records,
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(_check(report, DUPLICATE_PROVIDER_TRADE_ID).status, ValidationCheckStatus.FAILED)
        self.assertEqual(accepted_trades(records, report), (records[2],))

    def test_invalid_price_and_malformed_aggregate_range_are_rejected(self):
        invalid = replace(
            _trade("bad", 1),
            price=Decimal("0"),
            first_provider_trade_id="20",
            last_provider_trade_id="10",
        )
        report = validate_trades(
            validation_run_id=RUN_ID,
            dataset_ref="trade-invalid",
            trades=(invalid,),
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(_check(report, INVALID_TRADE_PRICE).status, ValidationCheckStatus.FAILED)
        self.assertEqual(_check(report, MALFORMED_AGGREGATE_TRADE_RANGE).status, ValidationCheckStatus.FAILED)
        self.assertEqual(accepted_trades((invalid,), report), ())


class FundingValidationTests(unittest.TestCase):
    def test_positive_negative_and_zero_rates_are_valid(self):
        records = (_funding("a", 0, "0.001"), _funding("b", 8, "0"), _funding("c", 16, "-0.001"))
        report = validate_funding_rates(
            validation_run_id=RUN_ID,
            dataset_ref="funding-signs",
            funding_rates=records,
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual(accepted_funding_rates(records, report), records)

    def test_gap_is_warning_and_remains_usable(self):
        records = (_funding("a", 0), _funding("b", 16))
        report = validate_funding_rates(
            validation_run_id=RUN_ID,
            dataset_ref="funding-gap",
            funding_rates=records,
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(_check(report, FUNDING_TIMESTAMP_GAP).status, ValidationCheckStatus.WARNING)
        self.assertEqual(report.status, ValidationStatus.ACCEPTED_WITH_WARNINGS)
        self.assertEqual(accepted_funding_rates(records, report), records)

    def test_malformed_timestamp_is_reported_without_ordering_failure(self):
        record = replace(_funding("bad-time", 0), funding_time_utc=START.replace(tzinfo=None))
        report = validate_funding_rates(
            validation_run_id=RUN_ID,
            dataset_ref="funding-malformed-time",
            funding_rates=(record,),
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(
            _check(report, INVALID_FUNDING_TIMESTAMP).status,
            ValidationCheckStatus.FAILED,
        )
        self.assertEqual(accepted_funding_rates((record,), report), ())
    def test_provider_instrument_mismatch_rejects_record(self):
        record = replace(_funding("bad", 0), provider_instrument_id="ETH-USDT-SWAP")
        report = validate_funding_rates(
            validation_run_id=RUN_ID,
            dataset_ref="funding-mismatch",
            funding_rates=(record,),
            window_start_utc=START,
            window_end_utc=END,
            clock=lambda: NOW,
        )
        self.assertEqual(
            _check(report, FUNDING_PROVIDER_INSTRUMENT_MISMATCH).status,
            ValidationCheckStatus.FAILED,
        )
        self.assertEqual(accepted_funding_rates((record,), report), ())


class InstrumentValidationTests(unittest.TestCase):
    def test_spot_and_perpetual_symbols_are_unambiguous(self):
        usdm = binance_usdm_instrument_key(
            "BTCUSDT", base_asset="BTC", quote_asset="USDT", settlement_asset="USDT"
        )
        self.assertEqual(SPOT.canonical_symbol, "BTC-USDT")
        self.assertEqual(usdm.canonical_symbol, "BTC-USDT:USDT:PERPETUAL_SWAP")
        self.assertNotEqual(SPOT.identity_sha256, usdm.identity_sha256)

    def test_valid_instrument_is_accepted(self):
        record = _instrument("valid")
        report = validate_instruments(
            validation_run_id=RUN_ID,
            dataset_ref="instrument-valid",
            instruments=(record,),
            clock=lambda: NOW,
        )
        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual(accepted_instruments((record,), report), (record,))

    def test_invalid_increment_is_rejected(self):
        record = _instrument("invalid", tick="-0.01")
        report = validate_instruments(
            validation_run_id=RUN_ID,
            dataset_ref="instrument-invalid",
            instruments=(record,),
            clock=lambda: NOW,
        )
        self.assertEqual(_check(report, INVALID_TICK_SIZE).status, ValidationCheckStatus.FAILED)
        self.assertEqual(accepted_instruments((record,), report), ())

    def test_metadata_drift_is_structured_warning_and_versions_are_immutable(self):
        old = _instrument("snapshot", tick="0.01")
        new = replace(
            _instrument("snapshot-new", tick="0.02"),
            instrument_key=old.instrument_key,
        )
        changes = compare_instrument_metadata(old, new)
        self.assertEqual(changes["tick_size"], {"old": Decimal("0.01"), "new": Decimal("0.02")})
        report = validate_instruments(
            validation_run_id=RUN_ID,
            dataset_ref="instrument-drift",
            instruments=(new,),
            previous_instruments=(old,),
            clock=lambda: NOW,
        )
        drift = _check(report, INSTRUMENT_METADATA_DRIFT)
        self.assertEqual(drift.status, ValidationCheckStatus.WARNING)
        self.assertIn("tick_size", drift.details["findings"][0]["changes"])
        self.assertNotEqual(old.instrument_id, new.instrument_id)
        self.assertEqual(accepted_instruments((new,), report), (new,))


if __name__ == "__main__":
    unittest.main()
