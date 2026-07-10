"""Offline persistence and typed pipeline tests for Phase 2J-2M."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotPublicProvider,
    BinanceUsdmPublicProvider,
    MarketDataProvider,
    MarketDataType,
    OkxPublicProvider,
    binance_usdm_instrument_key,
    okx_spot_instrument_key,
    okx_swap_instrument_key,
    spot_instrument_key,
)
from secure_eval_wrapper.data_pipeline import (
    FundingRatePipeline,
    FundingRatePipelineRequest,
    InstrumentMetadataPipeline,
    InstrumentMetadataPipelineRequest,
    PipelineStatus,
    TradePipeline,
    TradePipelineRequest,
    TypedPipelineError,
)
from secure_eval_wrapper.storage.postgres.repositories import PostgresMarketDataRepository


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "open-core/scripts/run_public_market_data_pipeline.py"
spec = importlib.util.spec_from_file_location("phase2_bundle_cli_for_tests", CLI_PATH)
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)

COLLECTION = UUID("83000000-0000-0000-0000-000000000001")
VALIDATION = UUID("83000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
TRADE_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
TRADE_END = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
FUNDING_START = datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)
FUNDING_END = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _providers():
    return cli._providers("offline-fixture")


def _keys():
    return (
        spot_instrument_key(
            provider_name="binance",
            exchange_name="Binance",
            provider_instrument_id="BTCUSDT",
            symbol="BTC-USDT",
        ),
        binance_usdm_instrument_key(
            "BTCUSDT", base_asset="BTC", quote_asset="USDT", settlement_asset="USDT"
        ),
        okx_spot_instrument_key("BTC-USDT"),
        okx_swap_instrument_key("BTC-USDT-SWAP", settlement_asset="USDT"),
    )


class FailingProvider(MarketDataProvider):
    def __init__(self, wrapped):
        self.wrapped = wrapped

    @property
    def spec(self):
        return self.wrapped.spec

    def fetch_ohlcv(self, request):
        raise RuntimeError("synthetic collection failure")

    def fetch_trades(self, request):
        raise RuntimeError("synthetic collection failure")

    def fetch_funding_rates(self, request):
        raise RuntimeError("synthetic collection failure")

    def fetch_instruments(self, request):
        raise RuntimeError("synthetic collection failure")


class FakeRepository:
    def __init__(self, fail_trade=False):
        self.fail_trade = fail_trade
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.raw = []
        self.reports = []
        self.checks = []
        self.trades = []
        self.funding = []
        self.instruments = []
        self.quarantine = []

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def record_raw_source_observation(self, row):
        self.raw.append(row)
        return row["observation_id"]

    def record_validation_report(self, row):
        self.reports.append(row)
        return row["validation_report_id"]

    def record_data_quality_check(self, row):
        self.checks.append(row)
        return row["check_id"]

    def record_validated_trade(self, row):
        if self.fail_trade:
            raise RuntimeError("synthetic child write failure")
        self.trades.append(row)
        return row["trade_id"]

    def record_funding_rate(self, row):
        self.funding.append(row)
        return row["funding_rate_id"]

    def upsert_instrument(self, row):
        self.instruments.append(row)
        return row["instrument_id"]

    def record_quarantine_decision(self, row):
        self.quarantine.append(row)
        return row["quarantine_id"]


class PoisonRepository:
    def transaction(self):
        raise AssertionError("disabled persistence touched database")


class TypedPipelineTests(unittest.TestCase):
    def test_trade_success_provider_order_and_no_database_when_disabled(self):
        binance, _, okx = _providers()
        result = TradePipeline(
            (okx, binance), repository=PoisonRepository(), clock=lambda: NOW
        ).run(
            TradePipelineRequest(
                COLLECTION,
                VALIDATION,
                ("okx", "binance"),
                "BTC-USDT",
                TRADE_START,
                TRADE_END,
                limit=3,
                max_pages=1,
            )
        )
        self.assertEqual(result.status, PipelineStatus.SUCCEEDED)
        self.assertEqual(result.provider_names, ("binance", "okx"))
        self.assertEqual(tuple(item.provider_name for item in result.outcomes), result.provider_names)
        self.assertEqual(len(result.accepted_records), 4)
        self.assertIsNone(result.persistence)

    def test_trade_partial_and_all_provider_failure_statuses(self):
        binance, _, okx = _providers()
        partial = TradePipeline((binance, FailingProvider(okx)), clock=lambda: NOW).run(
            TradePipelineRequest(
                COLLECTION,
                VALIDATION,
                ("binance", "okx"),
                "BTC-USDT",
                TRADE_START,
                TRADE_END,
                limit=3,
                max_pages=1,
            )
        )
        self.assertEqual(partial.status, PipelineStatus.PARTIAL)
        self.assertEqual(len(partial.errors), 1)

        failed = TradePipeline(
            (FailingProvider(binance), FailingProvider(okx)), clock=lambda: NOW
        ).run(
            TradePipelineRequest(
                COLLECTION,
                VALIDATION,
                ("binance", "okx"),
                "BTC-USDT",
                TRADE_START,
                TRADE_END,
                limit=3,
                max_pages=1,
            )
        )
        self.assertEqual(failed.status, PipelineStatus.FAILED)

    def test_fail_fast_stops_after_first_provider(self):
        binance, _, okx = _providers()
        pipeline = TradePipeline(
            (FailingProvider(binance), okx), clock=lambda: NOW
        )
        with self.assertRaises(TypedPipelineError) as raised:
            pipeline.run(
                TradePipelineRequest(
                    COLLECTION,
                    VALIDATION,
                    ("binance", "okx"),
                    "BTC-USDT",
                    TRADE_START,
                    TRADE_END,
                    limit=3,
                    max_pages=1,
                    fail_fast=True,
                )
            )
        self.assertEqual(raised.exception.failure.provider_name, "binance")
        self.assertEqual(len(raised.exception.outcomes), 1)

    def test_trade_persistence_uses_one_outer_transaction(self):
        binance, _, okx = _providers()
        repository = FakeRepository()
        result = TradePipeline(
            (binance, okx), repository=repository, clock=lambda: NOW
        ).run(
            TradePipelineRequest(
                COLLECTION,
                VALIDATION,
                ("binance", "okx"),
                "BTC-USDT",
                TRADE_START,
                TRADE_END,
                limit=3,
                max_pages=1,
                persistence_enabled=True,
            )
        )
        self.assertEqual(result.status, PipelineStatus.SUCCEEDED)
        self.assertEqual((repository.transactions, repository.commits, repository.rollbacks), (1, 1, 0))
        self.assertEqual(len(repository.raw), 4)
        self.assertEqual(len(repository.trades), 4)
        self.assertTrue(all(row["validation_report_id"] in {item["validation_report_id"] for item in repository.reports} for row in repository.trades))

    def test_child_write_failure_rolls_back_outer_transaction(self):
        binance, _, okx = _providers()
        repository = FakeRepository(fail_trade=True)
        with self.assertRaises(TypedPipelineError):
            TradePipeline(
                (binance, okx), repository=repository, clock=lambda: NOW
            ).run(
                TradePipelineRequest(
                    COLLECTION,
                    VALIDATION,
                    ("binance", "okx"),
                    "BTC-USDT",
                    TRADE_START,
                    TRADE_END,
                    limit=3,
                    max_pages=1,
                    persistence_enabled=True,
                )
            )
        self.assertEqual((repository.commits, repository.rollbacks), (0, 1))

    def test_funding_and_instrument_pipelines_succeed(self):
        binance, binance_usdm, okx = _providers()
        binance_spot, usdm, okx_spot, okx_swap = _keys()
        funding = FundingRatePipeline(
            (okx, binance_usdm), clock=lambda: NOW
        ).run(
            FundingRatePipelineRequest(
                COLLECTION,
                VALIDATION,
                {"okx": okx_swap, "binance_usdm": usdm},
                FUNDING_START,
                FUNDING_END,
                limit=3,
                max_pages=1,
            )
        )
        instruments = InstrumentMetadataPipeline(
            (okx, binance_usdm, binance), clock=lambda: NOW
        ).run(
            InstrumentMetadataPipelineRequest(
                COLLECTION,
                VALIDATION,
                {
                    "binance": (binance_spot,),
                    "binance_usdm": (usdm,),
                    "okx": (okx_spot, okx_swap),
                },
            )
        )
        self.assertEqual(funding.status, PipelineStatus.SUCCEEDED)
        self.assertEqual(len(funding.accepted_records), 4)
        self.assertEqual(instruments.status, PipelineStatus.SUCCEEDED)
        self.assertEqual(len(instruments.accepted_records), 4)


class RecordingCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []
        self.description = None

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return ()

    def close(self):
        return None


class RecordingConnection:
    def __init__(self, rows=()):
        self.cursor_instance = RecordingCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class RepositoryContractTests(unittest.TestCase):
    def test_trade_conflict_returns_database_id_and_compares_hash(self):
        existing = UUID("83000000-0000-0000-0000-000000000099")
        connection = RecordingConnection([None, (existing, "a" * 64)])
        repository = PostgresMarketDataRepository(connection)
        row = {
            "trade_id": UUID("83000000-0000-0000-0000-000000000098"),
            "provider_trade_id": "1",
            "provider_name": "okx",
            "provider_instrument_id": "BTC-USDT",
            "instrument_type": "spot",
            "symbol": "BTC-USDT",
            "exchange": "OKX",
            "traded_at_utc": TRADE_START,
            "price": 100,
            "quantity": 1,
            "quote_quantity": None,
            "side": "buy",
            "provider_sequence": 1,
            "record_sha256": "a" * 64,
            "validation_status": "accepted",
            "validation_report_id": VALIDATION,
            "source_observation_ids": [],
            "provenance_jsonb": {},
        }
        returned = repository.record_validated_trade(row)
        self.assertEqual(returned, existing)
        self.assertEqual(len(connection.cursor_instance.executions), 2)
        self.assertNotIn("BTC-USDT", connection.cursor_instance.executions[0][0])
        self.assertEqual(connection.commits, 1)

    def test_trade_conflict_with_different_hash_fails(self):
        connection = RecordingConnection([
            None,
            (UUID("83000000-0000-0000-0000-000000000099"), "b" * 64),
        ])
        repository = PostgresMarketDataRepository(connection)
        row = {
            "trade_id": UUID("83000000-0000-0000-0000-000000000098"),
            "provider_trade_id": "1",
            "provider_name": "okx",
            "provider_instrument_id": "BTC-USDT",
            "instrument_type": "spot",
            "symbol": "BTC-USDT",
            "exchange": "OKX",
            "traded_at_utc": TRADE_START,
            "price": 100,
            "quantity": 1,
            "side": "buy",
            "record_sha256": "a" * 64,
            "validation_status": "accepted",
            "validation_report_id": VALIDATION,
        }
        with self.assertRaisesRegex(RuntimeError, "record_sha256 differs"):
            repository.record_validated_trade(row)
        self.assertEqual(connection.rollbacks, 1)

    def test_read_queries_use_half_open_windows_and_explicit_instrument_filters(self):
        connection = RecordingConnection()
        repository = PostgresMarketDataRepository(connection)
        repository.list_validated_trades(
            provider_name="okx",
            provider_instrument_id="BTC-USDT",
            instrument_type="spot",
            start_utc=TRADE_START,
            end_utc=TRADE_END,
        )
        sql, params = connection.cursor_instance.executions[-1]
        self.assertIn("instrument_type = %s", sql)
        self.assertIn("traded_at_utc >= %s AND traded_at_utc < %s", sql)
        self.assertNotIn("BTC-USDT", sql)
        self.assertIn("spot", params)
        self.assertEqual(params[-2:], (TRADE_START, TRADE_END))


if __name__ == "__main__":
    unittest.main()
