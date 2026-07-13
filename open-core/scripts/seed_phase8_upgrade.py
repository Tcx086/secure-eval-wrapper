"""Seed public-safe 0022 rows for the PostgreSQL 0022-to-0023 upgrade test."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from secure_eval_wrapper.storage.postgres.config import load_postgres_config


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _id(value: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{value:012d}")


def main() -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    configuration_id, credential_id, account_id, report_id, check_id = (_id(value) for value in range(801, 806))
    approval_id, manifest_id, run_id, kill_id, intent_id = (_id(value) for value in range(806, 811))
    decision_id, reservation_id, outbox_id, event_id = (_id(value) for value in range(811, 815))
    configuration_hash = _hash("seeded-phase8-configuration")
    credential_hash = _hash("seeded-phase8-credential")
    account_hash = _hash("seeded-phase8-account")
    manifest_hash = _hash("seeded-phase8-manifest")
    implementation_hash = _hash("seeded-phase8-implementation")
    endpoint_hash = _hash("seeded-phase8-endpoints")
    reconciliation_hash = _hash("seeded-phase8-reconciliation")
    market_hash = _hash("seeded-phase8-market")
    metadata_hash = _hash("seeded-phase8-metadata")

    connection = psycopg.connect(**load_postgres_config().to_connection_kwargs())
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO execution.live_configuration_snapshots VALUES (%s,%s,'okx','production','seeded-phase8',true,true,false,%s,%s,%s)",
                    (configuration_id, configuration_hash, Jsonb({"provider_implementation_hash": implementation_hash, "endpoint_catalog_hash": endpoint_hash}), _hash("seed-config-record"), at),
                )
                cursor.execute(
                    "INSERT INTO execution.live_credential_references VALUES (%s,'okx','seeded-public','injected_local','seeded-phase8',false,%s,%s,%s,%s)",
                    (credential_id, at, Jsonb(["read", "spot_trade"]), credential_hash, at, ),
                )
                cursor.execute(
                    "INSERT INTO execution.live_account_snapshots VALUES (%s,%s,'seeded-phase8',%s,%s,1000,1000,0,0,'spot_cash',%s,%s)",
                    (account_id, run_id, at, at, Jsonb({"balances": {"USDT": {"available": "1000"}}, "positions": {}}), account_hash),
                )
                cursor.execute(
                    "INSERT INTO execution.live_preflight_reports VALUES (%s,%s,%s,%s,'seeded-commit',%s,%s,%s,%s,'passed','[]'::jsonb,'[]'::jsonb,%s)",
                    (report_id, run_id, configuration_hash, implementation_hash, endpoint_hash, credential_hash, account_hash, at, _hash("seed-report-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_preflight_checks VALUES (%s,%s,'seeded_check',true,true,%s,NULL,'seeded public-safe check',%s,%s)",
                    (check_id, report_id, at, _hash("seed-check-evidence"), _hash("seed-check-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_approvals VALUES (%s,%s,%s,%s,'seeded-phase8','okx','production',%s,%s,1000,0,%s,%s,'seed','seeded-nonce',%s,%s)",
                    (approval_id, run_id, report_id, configuration_hash, manifest_hash, _hash("seed-challenge"), at, datetime(2026, 1, 2, tzinfo=timezone.utc), Jsonb({"allowed_instruments": ["BTC-USDT"], "repository_commit_sha": "seeded-commit"}), _hash("seed-approval-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_run_manifests VALUES (%s,%s,%s,%s,%s,%s,%s,'seeded-commit',%s,%s,%s,true,false,%s,%s,%s)",
                    (manifest_id, run_id, approval_id, report_id, account_id, configuration_hash, implementation_hash, endpoint_hash, credential_hash, manifest_hash, Jsonb({"allowed_instruments": ["BTC-USDT"], "risk_limits": {}, "kill_switch_policy": {}, "parent_evidence_ids": []}), _hash("seed-manifest-record"), at),
                )
                cursor.execute(
                    "INSERT INTO execution.live_runs VALUES (%s,%s,'dry_run_running',true,false,%s,NULL,%s,0)",
                    (run_id, manifest_id, at, _hash("seed-run-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_kill_switches VALUES (%s,%s,'armed',NULL,%s,false,false,%s,%s,0)",
                    (kill_id, run_id, _hash("seed-kill-evidence"), at, _hash("seed-kill-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_order_intents VALUES (%s,%s,%s,'seededclient','BTC-USDT','buy','limit','spot',1,100,100,%s,%s,%s,%s,%s,%s,'dry_run_prepared',%s,%s)",
                    (intent_id, run_id, manifest_id, _id(815), market_hash, metadata_hash, account_hash, reconciliation_hash, _hash("seed-economic"), at, _hash("seed-intent-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_runtime_risk_decisions VALUES (%s,%s,true,'[]'::jsonb,100,100,100,100,100,0,%s,'seed-v1',%s,%s)",
                    (decision_id, intent_id, market_hash, at, _hash("seed-risk-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_reservations VALUES (%s,%s,'USDT',100,100,'projected',true,%s,%s,%s,0)",
                    (reservation_id, intent_id, at, at, _hash("seed-reservation-record")),
                )
                request = {"instId": "BTC-USDT", "tdMode": "cash", "clOrdId": "seededclient", "side": "buy", "ordType": "limit", "px": "100", "sz": "1"}
                cursor.execute(
                    "INSERT INTO execution.live_dispatch_outbox VALUES (%s,%s,'seededclient','dry_run_prepared',%s,%s,NULL,NULL,NULL,0,NULL,NULL,NULL,%s,%s,NULL,%s,0)",
                    (outbox_id, intent_id, _hash("seed-request"), Jsonb(request), at, at, _hash("seed-outbox-record")),
                )
                cursor.execute(
                    "INSERT INTO execution.live_dispatch_events VALUES (%s,%s,'prepared',%s,%s,%s)",
                    (event_id, outbox_id, Jsonb({"seeded": True}), at, _hash("seed-event-record")),
                )
        print(f"OK: seeded public-safe Phase 8A 0022 rows run={run_id}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
