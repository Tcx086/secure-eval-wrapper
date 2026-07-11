"""Fixture-default socket-free monitoring demo."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.data_health import DataHealthInput, DataRecordSummary
from secure_eval_wrapper.monitoring.engine import MonitoringEngine, MonitoringInputs
from secure_eval_wrapper.monitoring.execution_health import ExecutionHealthInput
from secure_eval_wrapper.monitoring.models import MonitoredRunReference, PublicSafeProvenance
from secure_eval_wrapper.monitoring.risk_health import RiskHealthInput
from secure_eval_wrapper.monitoring.signal_health import SignalHealthInput
from secure_eval_wrapper.monitoring.system_health import SystemHealthInput


def build_demo_bundle():
    as_of = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
    data = DataHealthInput(
        "bar", "1m",
        records=(DataRecordSummary("bar-1", "1" * 64, as_of - timedelta(seconds=30),
                                   open_time_utc=as_of - timedelta(minutes=1), open=Decimal("100"),
                                   high=Decimal("102"), low=Decimal("99"), close=Decimal("101"), volume=Decimal("10")),),
        observation_start_utc=as_of - timedelta(minutes=2), observation_end_utc=as_of,
    )
    inputs = MonitoringInputs(
        data=data,
        signals=SignalHealthInput(as_of - timedelta(seconds=20), "completed", "partial", 1, 1),
        execution=ExecutionHealthInput(order_count=1, fill_count=1, position_reconciliation_ok=True,
                                       cash_reconciliation_ok=True, account_equity_reconciliation_ok=True,
                                       complete_reconstruction_ok=True, equity_point_count=1),
        risk=RiskHealthInput(decision_count=2, blocked_decision_count=0, maximum_limit_utilization=Decimal("0.2"),
                             gross_exposure_utilization=Decimal("0.2"), net_exposure_utilization=Decimal("0.2"),
                             maximum_series_position_utilization=Decimal("0.2"),
                             gross_exposure_to_equity_utilization=Decimal("0.2"), current_drawdown=Decimal("0.01"),
                             equity=Decimal("1000")),
        system=SystemHealthInput(observed_migration_version="0014", migration_hashes_match=True,
                                 expected_schema_objects_present=True, postgresql_available=None,
                                 persistence_transaction_ok=None, package_version_matches=True,
                                 source_tree_identity_matches=True, status_files_synchronized=True,
                                 live_trading_disabled=True, postgresql_only_authority=True,
                                 private_path_boundary_clean=True, configuration_valid=True, fix_session_healthy=True),
    )
    config = MonitoringConfiguration(maximum_data_age_seconds={"bar:1m": Decimal("60")}, maximum_signal_age_seconds=Decimal("60"))
    provenance = PublicSafeProvenance("a" * 64, "public-demo", "public-synthetic-fixture")
    return MonitoringEngine().evaluate(configuration=config, as_of_utc=as_of, inputs=inputs,
                                       reference=MonitoredRunReference("public-monitoring-demo"), provenance=provenance)


def _connect_postgres():
    """Import the optional driver only after both persistence gates pass."""
    import psycopg
    from secure_eval_wrapper.storage.postgres.connection import build_connection_kwargs
    return psycopg.connect(**build_connection_kwargs())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    if args.persist and os.environ.get("ENABLE_POSTGRES_PERSISTENCE") != "true":
        parser.error("--persist requires ENABLE_POSTGRES_PERSISTENCE=true")
    bundle = build_demo_bundle()
    persistence_status = "disabled"
    if args.persist:
        connection = _connect_postgres()
        try:
            from secure_eval_wrapper.monitoring.persistence import persist_monitoring_bundle
            from secure_eval_wrapper.storage.postgres.phase6_repositories import PostgresPhase6Repository
            persist_monitoring_bundle(PostgresPhase6Repository(connection), bundle)
            persistence_status = "postgresql"
        finally:
            connection.close()
    counts = {}
    for item in bundle.check_results:
        counts[item.reason_code] = counts.get(item.reason_code, 0) + 1
    print(json.dumps({
        "monitoring_run_id": str(bundle.run.monitoring_run_id),
        "overall_status": bundle.run.overall_status.value,
        "category_statuses": {snapshot.component: snapshot.health_status.value for snapshot in bundle.snapshots if snapshot.component != "overall"},
        "open_incident_count": sum(incident.state.value != "resolved" for incident in bundle.incidents),
        "reason_code_counts": counts,
        "persistence_status": persistence_status,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
