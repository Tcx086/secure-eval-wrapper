"""Offline tests for Phase 2A collection and validation contracts."""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotPublicProvider,
    BinanceUsdmPublicProvider,
    CONCRETE_PROVIDER_SPECS,
    EXCHANGE_CAPABILITY_SUMMARIES,
    CollectionStatus,
    DataRequest,
    MarketDataProvider,
    MarketDataType,
    NormalizedBar,
    OkxPublicProvider,
    PLANNED_PROVIDER_SPECS,
    ProviderCapabilityStatus,
    RawObservation,
    get_exchange_capability_summary,
    get_provider_spec,
)
from secure_eval_wrapper.data_validation import (
    CrossSourceReconciler,
    DataValidator,
    DatasetPromoter,
    QuarantineReason,
    ValidationCheck,
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000002")
BAR_ID = UUID("00000000-0000-0000-0000-000000000003")
CHECK_ID = UUID("00000000-0000-0000-0000-000000000004")
RESULT_ID = UUID("00000000-0000-0000-0000-000000000005")
REPORT_ID = UUID("00000000-0000-0000-0000-000000000006")
UTC_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
SHA256_ZERO = "0" * 64


class CollectionContractTests(unittest.TestCase):
    def test_registry_contains_named_providers_and_current_capabilities(self) -> None:
        self.assertEqual(
            set(PLANNED_PROVIDER_SPECS),
            {"binance", "okx", "bybit", "coinbase"},
        )
        self.assertEqual(get_exchange_capability_summary(" OKX ").display_name, "OKX")
        self.assertEqual(
            get_provider_spec("coinbase").capabilities[MarketDataType.FUNDING_RATES],
            ProviderCapabilityStatus.UNKNOWN,
        )
        self.assertEqual(
            get_provider_spec("binance").capabilities[MarketDataType.OHLCV],
            ProviderCapabilityStatus.IMPLEMENTED,
        )
        for spec in PLANNED_PROVIDER_SPECS.values():
            self.assertTrue(spec.public_market_data_only)
            allowed = {
                ProviderCapabilityStatus.PLANNED,
                ProviderCapabilityStatus.UNKNOWN,
            }
            if spec.name in {"binance", "okx"}:
                allowed.add(ProviderCapabilityStatus.IMPLEMENTED)
            self.assertTrue(set(spec.capabilities.values()) <= allowed)

    def test_concrete_component_registry_matches_runtime_provider_names(self) -> None:
        self.assertEqual(set(CONCRETE_PROVIDER_SPECS), {"binance", "binance_usdm", "okx"})
        self.assertEqual(get_provider_spec("binance_usdm").name, "binance_usdm")
        self.assertEqual(
            get_provider_spec("binance").capabilities[MarketDataType.FUNDING_RATES],
            ProviderCapabilityStatus.PLANNED,
        )
        self.assertEqual(
            get_provider_spec("binance_usdm").capabilities[MarketDataType.FUNDING_RATES],
            ProviderCapabilityStatus.IMPLEMENTED,
        )
        self.assertEqual(
            get_provider_spec("binance_usdm").capabilities[MarketDataType.OHLCV],
            ProviderCapabilityStatus.PLANNED,
        )
        self.assertEqual(
            get_exchange_capability_summary("binance").capabilities[MarketDataType.FUNDING_RATES],
            ProviderCapabilityStatus.IMPLEMENTED,
        )
        self.assertEqual(set(EXCHANGE_CAPABILITY_SUMMARIES), {"binance", "okx", "bybit", "coinbase"})
    def test_implemented_capabilities_resolve_to_concrete_component_methods(self) -> None:
        components = {
            "binance": BinanceSpotPublicProvider(transport=object()),
            "binance_usdm": BinanceUsdmPublicProvider(transport=object()),
            "okx": OkxPublicProvider(transport=object()),
        }
        method_by_type = {
            MarketDataType.OHLCV: "fetch_ohlcv",
            MarketDataType.TRADES: "fetch_trades",
            MarketDataType.FUNDING_RATES: "fetch_funding_rates",
            MarketDataType.INSTRUMENTS: "fetch_instruments",
        }
        for name, component in components.items():
            self.assertEqual(component.spec.name, name)
            self.assertEqual(component.spec.capabilities, CONCRETE_PROVIDER_SPECS[name].capabilities)
            for data_type, status in component.spec.capabilities.items():
                if status is ProviderCapabilityStatus.IMPLEMENTED:
                    method_name = method_by_type[data_type]
                    self.assertIsNot(
                        getattr(type(component), method_name),
                        getattr(MarketDataProvider, method_name),
                    )
    def test_collection_models_are_constructible_without_io(self) -> None:
        request = DataRequest(
            collection_run_id=RUN_ID,
            provider_name="binance",
            data_type=MarketDataType.OHLCV,
            symbols=("BTC-USDT",),
            timeframe="1m",
        )
        observation = RawObservation(
            observation_id=OBSERVATION_ID,
            collection_run_id=RUN_ID,
            provider_name="binance",
            exchange_name="Binance",
            source_endpoint="planned.fetch_ohlcv",
            request_parameters={"symbol": "BTCUSDT"},
            request_timestamp_utc=UTC_NOW,
            ingested_at_utc=UTC_NOW,
            data_type=MarketDataType.OHLCV,
            payload={"synthetic": True},
            source_sha256=SHA256_ZERO,
            collection_status=CollectionStatus.SUCCEEDED,
            raw_symbol="BTCUSDT",
            normalized_symbol="BTC-USDT",
            timeframe="1m",
            observed_at_utc=UTC_NOW,
        )
        bar = NormalizedBar(
            bar_id=BAR_ID,
            symbol="BTC-USDT",
            exchange="Binance",
            timeframe="1m",
            bar_open_time_utc=UTC_NOW,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("10"),
            source_observation_ids=(OBSERVATION_ID,),
        )

        self.assertEqual(request.symbols, ("BTC-USDT",))
        self.assertEqual(observation.payload, {"synthetic": True})
        self.assertEqual(bar.source_observation_ids, (OBSERVATION_ID,))

    def test_provider_is_an_abstract_interface(self) -> None:
        self.assertTrue(inspect.isabstract(MarketDataProvider))
        with self.assertRaises(TypeError):
            MarketDataProvider()


class ValidationContractTests(unittest.TestCase):
    def test_validation_models_are_constructible_without_algorithms(self) -> None:
        check = ValidationCheck(
            check_id=CHECK_ID,
            check_type="missing_bars",
            description="Detect missing intervals.",
            severity=ValidationSeverity.ERROR,
            data_types=(MarketDataType.OHLCV,),
            parameters={"expected_timeframe": "1m"},
        )
        result = ValidationResult(
            result_id=RESULT_ID,
            validation_run_id=RUN_ID,
            check_id=CHECK_ID,
            status=ValidationCheckStatus.PASSED,
            created_at_utc=UTC_NOW,
            message="Synthetic contract result.",
        )
        report = ValidationReport(
            validation_report_id=REPORT_ID,
            validation_run_id=RUN_ID,
            dataset_ref="synthetic-contract-test",
            provider_names=("binance",),
            data_types=(MarketDataType.OHLCV,),
            symbols=("BTC-USDT",),
            timeframes=("1m",),
            window_start_utc=UTC_NOW,
            window_end_utc=UTC_NOW,
            results=(result,),
            accepted_count=1,
            rejected_count=0,
            warning_count=0,
            status=ValidationStatus.ACCEPTED,
            tolerance_config_sha256=SHA256_ZERO,
            source_hashes=(SHA256_ZERO,),
            report_sha256=None,
            created_at_utc=UTC_NOW,
        )

        self.assertEqual(report.results[0].check_id, check.check_id)
        self.assertEqual(
            QuarantineReason.CROSS_SOURCE_MISMATCH.value,
            "cross_source_mismatch",
        )

    def test_validation_services_are_abstract_interfaces(self) -> None:
        for interface in (DataValidator, CrossSourceReconciler, DatasetPromoter):
            self.assertTrue(inspect.isabstract(interface))
            with self.assertRaises(TypeError):
                interface()


if __name__ == "__main__":
    unittest.main()
