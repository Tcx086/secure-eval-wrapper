"""Abstract repository contracts for PostgreSQL-backed storage.

These interfaces define boundaries only. They do not implement SQL, connect to PostgreSQL, collect
market data, generate signals, execute orders, run backtests, or emit monitoring events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID


StorageRecord = Mapping[str, Any]
StoragePayload = Mapping[str, Any]


class MarketDataRepository(ABC):
    """Persistence contract for raw and validated market data records."""

    @abstractmethod
    def record_raw_source_observation(self, observation: StoragePayload) -> UUID:
        """Record one raw source observation."""

    @abstractmethod
    def record_validated_bar(self, bar: StoragePayload) -> UUID:
        """Record one validated OHLCV bar."""

    @abstractmethod
    def record_validated_trade(self, trade: StoragePayload) -> UUID:
        """Record one validated trade."""

    @abstractmethod
    def record_funding_rate(self, funding_rate: StoragePayload) -> UUID:
        """Record one validated funding rate."""

    @abstractmethod
    def upsert_instrument(self, instrument: StoragePayload) -> UUID:
        """Create or update instrument metadata."""

    @abstractmethod
    def list_validated_bars(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        """List validated bars for a symbol/exchange/timeframe window."""
    @abstractmethod
    def list_validated_trades(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        """List validated trades in a half-open UTC window."""

    @abstractmethod
    def list_funding_rates(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        """List funding rates in a half-open UTC window."""

    @abstractmethod
    def get_instrument(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
    ) -> StorageRecord | None:
        """Return the latest versioned instrument snapshot."""

    @abstractmethod
    def list_instruments(
        self,
        *,
        provider_name: str | None = None,
        instrument_type: str | None = None,
    ) -> Sequence[StorageRecord]:
        """List instrument metadata snapshots with explicit filters."""


class DataQualityRepository(ABC):
    """Persistence contract for validation reports and quality checks."""

    @abstractmethod
    def record_validation_report(self, report: StoragePayload) -> UUID:
        """Record a validation report."""

    @abstractmethod
    def record_data_quality_check(self, check: StoragePayload) -> UUID:
        """Record a data quality check result."""

    @abstractmethod
    def get_validation_report(self, validation_report_id: UUID) -> StorageRecord | None:
        """Fetch a validation report by identifier."""


class QuarantineRepository(ABC):
    """Persistence contract for source-observation quarantine decisions."""

    @abstractmethod
    def record_quarantine_decision(self, decision: StoragePayload) -> UUID:
        """Record one failed-observation quarantine decision."""


class ReconciliationRepository(ABC):
    """Persistence contract for reconciliation summaries and child checks."""

    @abstractmethod
    def record_reconciliation_result(self, result: StoragePayload) -> UUID:
        """Record an idempotent reconciliation summary."""

    @abstractmethod
    def record_reconciliation_check_result(self, result: StoragePayload) -> UUID:
        """Record one idempotent child reconciliation check."""

    @abstractmethod
    def get_reconciliation_result(
        self,
        reconciliation_id: UUID,
    ) -> StorageRecord | None:
        """Fetch a reconciliation summary by identifier."""

    @abstractmethod
    def list_reconciliation_results(
        self,
        *,
        validation_run_id: UUID | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> Sequence[StorageRecord]:
        """List reconciliation summaries with optional parameterized filters."""


class AlphaRepository(ABC):
    """Persistence contract for public alpha registry metadata."""

    @abstractmethod
    def register_alpha(self, alpha: StoragePayload) -> UUID:
        """Register alpha metadata."""

    @abstractmethod
    def get_alpha(self, alpha_id: UUID) -> StorageRecord | None:
        """Fetch alpha metadata by identifier."""

    @abstractmethod
    def list_alphas(self, *, status: str | None = None) -> Sequence[StorageRecord]:
        """List alpha metadata, optionally filtered by status."""


class SignalRepository(ABC):
    """Persistence contract for signal runs and standardized signals."""

    @abstractmethod
    def record_signal_run(self, signal_run: StoragePayload) -> UUID:
        """Record signal run metadata."""

    @abstractmethod
    def record_signal(self, signal: StoragePayload) -> UUID:
        """Record one standardized signal."""

    @abstractmethod
    def list_signals(self, *, signal_run_id: UUID) -> Sequence[StorageRecord]:
        """List signals for a signal run."""


class ExecutionRepository(ABC):
    """Persistence contract for order intents, orders, fills, positions, and snapshots."""

    @abstractmethod
    def record_order_intent(self, order_intent: StoragePayload) -> UUID:
        """Record an order intent."""

    @abstractmethod
    def record_order(self, order: StoragePayload) -> UUID:
        """Record an order acknowledgement or status."""

    @abstractmethod
    def record_fill(self, fill: StoragePayload) -> UUID:
        """Record an execution fill."""

    @abstractmethod
    def upsert_position(self, position: StoragePayload) -> UUID:
        """Create or update a position record."""

    @abstractmethod
    def record_account_snapshot(self, account_snapshot: StoragePayload) -> UUID:
        """Record an account snapshot."""

    @abstractmethod
    def list_fills(self, *, order_id: UUID) -> Sequence[StorageRecord]:
        """List fills for an order."""


class BacktestRepository(ABC):
    """Persistence contract for backtest metadata, metrics, curves, and stress outputs."""

    @abstractmethod
    def record_backtest_run(self, backtest_run: StoragePayload) -> UUID:
        """Record backtest run metadata."""

    @abstractmethod
    def record_backtest_metric(self, metric: StoragePayload) -> UUID:
        """Record one backtest metric."""

    @abstractmethod
    def record_equity_curve_point(self, equity_curve_point: StoragePayload) -> UUID:
        """Record one equity curve point."""

    @abstractmethod
    def record_stress_result(self, stress_result: StoragePayload) -> UUID:
        """Record one stress result."""

    @abstractmethod
    def list_backtest_metrics(self, *, backtest_run_id: UUID) -> Sequence[StorageRecord]:
        """List metrics for a backtest run."""


class MonitoringRepository(ABC):
    """Persistence contract for monitoring, simulated FIX, and risk events."""

    @abstractmethod
    def record_monitoring_event(self, monitoring_event: StoragePayload) -> UUID:
        """Record one monitoring event."""

    @abstractmethod
    def record_fix_session_event(self, fix_session_event: StoragePayload) -> UUID:
        """Record one simulated FIX-style session event."""

    @abstractmethod
    def record_risk_event(self, risk_event: StoragePayload) -> UUID:
        """Record one risk event."""

    @abstractmethod
    def list_monitoring_events(self, *, run_id: UUID) -> Sequence[StorageRecord]:
        """List monitoring events for a run."""


class AuditRepository(ABC):
    """Persistence contract for manifests and schema migration metadata."""

    @abstractmethod
    def record_run_manifest(self, run_manifest: StoragePayload) -> UUID:
        """Record a run manifest."""

    @abstractmethod
    def get_run_manifest(self, run_id: UUID) -> StorageRecord | None:
        """Fetch a run manifest by run identifier."""

    @abstractmethod
    def list_schema_migrations(self) -> Sequence[StorageRecord]:
        """List recorded schema migrations."""


class ArtifactRepository(ABC):
    """Persistence contract for classified artifacts."""

    @abstractmethod
    def record_artifact(self, artifact: StoragePayload) -> UUID:
        """Record artifact metadata."""

    @abstractmethod
    def list_artifacts(self, *, run_id: UUID | None = None) -> Sequence[StorageRecord]:
        """List artifacts, optionally scoped to a run."""
