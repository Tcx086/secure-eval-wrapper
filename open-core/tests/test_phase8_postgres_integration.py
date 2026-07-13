from __future__ import annotations

import os
import unittest
import runpy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository, LiveClaimError, LiveConflictError
from secure_eval_wrapper.live.kill_switch import arm_kill_switch
from secure_eval_wrapper.live.models import LiveKillState, LiveObservationBundle, LiveReconciliationStatus
from secure_eval_wrapper.live.reconciliation import reconcile_live
from secure_eval_wrapper.live.risk import LiveRiskState, evaluate_live_risk
from secure_eval_wrapper.live.risk_summary import build_post_run_summary, build_pre_run_summary
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter
from test_phase8_guarded_live import T0, account, config, live_intent, market_evidence, passed_authority

RUN = os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true"
TABLES = ("live_post_run_summaries","live_pre_run_summaries","live_lifecycle_events","live_recovery_records","live_reconciliation_differences","live_reconciliations","live_fill_observations","live_order_projections","live_order_observations","live_transport_attempts","live_cancel_outbox","live_dispatch_events","live_dispatch_outbox","live_reservations","live_runtime_risk_decisions","live_order_intents","live_kill_events","live_kill_switches","live_runs","live_run_manifests","live_approvals","live_preflight_checks","live_preflight_reports","live_account_snapshots","live_credential_references","live_configuration_snapshots")


@unittest.skipUnless(RUN, "requires real PostgreSQL 16")
class Phase8PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.connection = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"), row_factory=dict_row)

    @classmethod
    def tearDownClass(cls): cls.connection.close()

    def setUp(self):
        with self.connection.cursor() as cursor:
            cursor.execute("TRUNCATE " + ",".join("execution." + name for name in TABLES) + " CASCADE")
        self.connection.commit(); self.repo = DurablePostgresLiveRepository(self.connection)

    def context(self):
        cfg, snap, cred, report, approval, manifest = passed_authority(); kill = arm_kill_switch(live_run_id=manifest.live_run_id, at_utc=T0)
        projection = {"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}
        reconciliation = reconcile_live(live_run_id=manifest.live_run_id, local_projection=projection, venue_observation=projection, evaluated_at_utc=T0)
        market = market_evidence(); intent = live_intent(manifest, snap, reconciliation, market, limit=Decimal("101"))
        state = LiveRiskState(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, LiveReconciliationStatus.RECONCILED, LiveKillState.ARMED)
        risk = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=state, approval=approval, evaluated_at_utc=T0)
        body = OkxProductionSpotAdapter.build_limit_order_body(instrument="BTC-USDT", side="buy", quantity=intent.quantity, limit_price=intent.limit_price, client_order_id=intent.client_order_id, tick_size=Decimal("0.01"), lot_size=Decimal("0.001")); request_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/order", "body": body})
        return locals()

    def start(self, ctx, **kwargs):
        return self.repo.persist_start_bundle(configuration=ctx["cfg"], credential_reference=ctx["cred"], account_snapshot=ctx["snap"], report=ctx["report"], approval=ctx["approval"], manifest=ctx["manifest"], kill_switch=ctx["kill"], created_at_utc=T0, **kwargs)

    def count(self, table):
        with self.connection.cursor() as cursor: cursor.execute(f"SELECT count(*) AS count FROM execution.{table}"); return cursor.fetchone()["count"]

    def test_catalog_contains_all_phase8a_tables_and_write_checks(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='execution' AND table_name LIKE 'live_%'"); names = {row["table_name"] for row in cursor.fetchall()}
        self.assertEqual(names, set(TABLES)); self.assertGreaterEqual(len(names), 26)

    def test_migration_0022_idempotency_and_failure_rollback(self):
        import psycopg

        root = Path(__file__).resolve().parents[2]
        migration_runner = runpy.run_path(str(root / "open-core" / "scripts" / "apply_postgres_migrations.py"))
        apply_one = migration_runner["_apply_migration"]
        connection = psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ["POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
            autocommit=True,
        )
        try:
            migration = root / "open-core" / "db" / "migrations" / "0022_phase8_guarded_live_foundation.sql"
            digest, applied = apply_one(connection, migration)
            self.assertFalse(applied)
            self.assertEqual(digest, "b01c0c0c7801247594ee75009055f899c8902b6cfa1b44ed91ad8451e478e434")
            with TemporaryDirectory() as temporary:
                probe = Path(temporary) / "9998_phase8_rollback_probe.sql"
                probe.write_text(
                    "CREATE TABLE execution.phase8_rollback_probe (id integer); "
                    "SELECT phase8_intentional_missing_function();",
                    encoding="utf-8",
                )
                with self.assertRaises(Exception):
                    apply_one(connection, probe)
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('execution.phase8_rollback_probe')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT count(*) FROM audit.schema_migrations WHERE migration_id='9998_phase8_rollback_probe'"
                )
                self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            connection.close()


    def test_start_bundle_atomicity_and_exact_replay(self):
        for failure in ("configuration", "credential", "account", "preflight", "approval", "manifest", "kill_switch"):
            with self.subTest(failure=failure):
                self.setUp(); ctx = self.context()
                with self.assertRaises(RuntimeError): self.start(ctx, fail_at=failure)
                self.assertEqual(sum(self.count(name) for name in TABLES), 0)
        self.setUp(); ctx = self.context(); self.assertTrue(self.start(ctx)); self.assertTrue(self.start(ctx)); self.assertEqual(self.count("live_runs"), 1); self.assertEqual(self.count("live_preflight_checks"), len(ctx["report"].checks))

    def test_intent_reservation_outbox_atomicity_and_approval_consumption(self):
        for failure in ("intent", "risk", "reservation", "outbox"):
            with self.subTest(failure=failure):
                self.setUp(); ctx = self.context(); self.start(ctx)
                with self.assertRaises(RuntimeError): self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT", fail_at=failure)
                self.assertEqual(self.count("live_order_intents"), 0); self.assertEqual(self.count("live_dispatch_outbox"), 0)
                with self.connection.cursor() as cursor: cursor.execute("SELECT consumed_notional FROM execution.live_approvals"); self.assertEqual(Decimal(cursor.fetchone()["consumed_notional"]), Decimal(0))
        self.setUp(); ctx = self.context(); self.start(ctx); outbox = self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT")
        with self.connection.cursor() as cursor: cursor.execute("SELECT consumed_notional FROM execution.live_approvals"); self.assertEqual(Decimal(cursor.fetchone()["consumed_notional"]), ctx["risk"].risk_notional)
        self.assertEqual(self.count("live_order_intents"), 1); self.assertEqual(self.count("live_reservations"), 1); self.assertEqual(self.count("live_dispatch_outbox"), 1); self.assertIsNotNone(outbox)
        replayed = self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT")
        self.assertEqual(replayed, outbox)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT consumed_notional FROM execution.live_approvals")
            self.assertEqual(Decimal(cursor.fetchone()["consumed_notional"]), ctx["risk"].risk_notional)

    def test_claim_lease_exclusion_suppression_and_monotonicity(self):
        ctx = self.context(); self.start(ctx); outbox = self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT")
        first = self.repo.claim_dispatch(worker_identity="worker-a", at_utc=T0, outbox_id=outbox); self.assertIsNotNone(first)
        self.assertIsNone(DurablePostgresLiveRepository(self.connection).claim_dispatch(worker_identity="worker-b", at_utc=T0, outbox_id=outbox))
        with self.assertRaises(LiveClaimError): self.repo.suppress_claimed_dispatch(outbox_id=outbox, claim_token=first[1], worker_identity="worker-b", at_utc=T0 + timedelta(seconds=1))
        self.repo.suppress_claimed_dispatch(outbox_id=outbox, claim_token=first[1], worker_identity="worker-a", at_utc=T0 + timedelta(seconds=1))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT state,successful_write,external_write_attempted FROM execution.live_dispatch_outbox d JOIN execution.live_transport_attempts t ON t.order_intent_id=d.order_intent_id"); row = cursor.fetchone(); self.assertEqual(row["state"], "dry_run_suppressed"); self.assertFalse(row["successful_write"]); self.assertFalse(row["external_write_attempted"])
        with self.assertRaises(Exception):
            with self.connection.cursor() as cursor: cursor.execute("UPDATE execution.live_dispatch_outbox SET state='dry_run_prepared',version=version+1 WHERE dispatch_outbox_id=%s", (outbox,))
        self.connection.rollback()
    def test_recovery_claim_observation_idempotency_and_conflict(self):
        ctx = self.context(); self.start(ctx)
        outbox = self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT")
        dispatch = self.repo.claim_dispatch(worker_identity="dispatch-a", at_utc=T0, outbox_id=outbox)
        self.repo.mark_pending_recovery(outbox_id=outbox, claim_token=dispatch[1], worker_identity="dispatch-a", at_utc=T0 + timedelta(seconds=1))
        recovery = self.repo.claim_recovery(worker_identity="recovery-a", at_utc=T0 + timedelta(seconds=2), outbox_id=outbox)
        self.assertIsNotNone(recovery)
        self.assertIsNone(self.repo.claim_recovery(worker_identity="recovery-b", at_utc=T0 + timedelta(seconds=2), outbox_id=outbox))
        bundle = LiveObservationBundle(
            ctx["manifest"].live_run_id,
            ctx["intent"].client_order_id,
            {"ordId": "dry-run-observation", "clOrdId": ctx["intent"].client_order_id, "state": "live"},
            (),
            (),
            (),
            {"config": {"acctLv": "1"}, "balances": [], "positions": []},
            T0 + timedelta(seconds=3),
            True,
        )
        observation_id = self.repo.persist_recovery_observation(
            outbox_id=outbox,
            claim_token=recovery[1],
            worker_identity="recovery-a",
            observation_bundle=bundle,
            at_utc=T0 + timedelta(seconds=4),
        )
        self.assertEqual(
            self.repo.persist_recovery_observation(
                outbox_id=outbox,
                claim_token=recovery[1],
                worker_identity="recovery-a",
                observation_bundle=bundle,
                at_utc=T0 + timedelta(seconds=4),
            ),
            observation_id,
        )
        self.assertEqual(self.count("live_recovery_records"), 1)
        self.assertEqual(self.count("live_order_observations"), 1)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT state,recovery_generation FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s", (outbox,))
            row = cursor.fetchone()
        self.assertEqual(row["state"], "dry_run_suppressed")
        self.assertEqual(row["recovery_generation"], 1)
        conflicting = LiveObservationBundle(
            ctx["manifest"].live_run_id,
            ctx["intent"].client_order_id,
            {"ordId": "changed", "clOrdId": ctx["intent"].client_order_id, "state": "live"},
            (),
            (),
            (),
            {"config": {"acctLv": "1"}, "balances": [], "positions": []},
            bundle.queried_at_utc,
            True,
        )
        with self.assertRaises(LiveConflictError):
            self.repo.persist_recovery_observation(
                outbox_id=outbox,
                claim_token=recovery[1],
                worker_identity="recovery-a",
                observation_bundle=conflicting,
                at_utc=T0 + timedelta(seconds=4),
            )

    def test_cancel_outbox_is_durable_and_suppressed(self):
        ctx = self.context(); self.start(ctx)
        self.repo.prepare_dry_run_bundle(intent=ctx["intent"], risk_decision=ctx["risk"], request_body=ctx["body"], provider_request_hash=ctx["request_hash"], created_at_utc=T0, reservation_currency="USDT")
        body = {"instId": "BTC-USDT", "clOrdId": ctx["intent"].client_order_id}
        request_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/cancel-order", "body": body})
        cancel_id = self.repo.prepare_cancel_dry_run(
            live_run_id=ctx["manifest"].live_run_id,
            order_intent_id=ctx["intent"].order_intent_id,
            client_order_id=ctx["intent"].client_order_id,
            request_body=body,
            provider_request_hash=request_hash,
            created_at_utc=T0,
        )
        self.assertIsNotNone(cancel_id)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.state,t.external_write_attempted,t.successful_write "
                "FROM execution.live_cancel_outbox c "
                "JOIN execution.live_transport_attempts t ON t.order_intent_id=c.order_intent_id "
                "WHERE t.operation='cancel_order'"
            )
            row = cursor.fetchone()
        self.assertEqual(row["state"], "dry_run_suppressed")
        self.assertFalse(row["external_write_attempted"])
        self.assertFalse(row["successful_write"])

    def test_restart_reconstruction_is_dry_run_only(self):
        ctx = self.context(); self.start(ctx); state = DurablePostgresLiveRepository(self.connection).reconstruct(ctx["manifest"].live_run_id)
        self.assertTrue(state["run"]["dry_run"]); self.assertFalse(state["run"]["production_write_enabled"]); self.assertEqual(state["kill_switch"]["state"], "armed"); self.assertEqual(state["pending_recovery_count"], 0)

    def test_reconciliation_and_summary_lineage_are_atomic_and_public_safe(self):
        ctx = self.context(); self.start(ctx); self.repo.persist_reconciliation(ctx["reconciliation"], exact_input={"local": {}, "venue": {}})
        pre = build_pre_run_summary(manifest=ctx["manifest"], approval=ctx["approval"], account_snapshot=ctx["snap"], proposed_decisions=(ctx["risk"],), reconciliation=ctx["reconciliation"], kill_switch=ctx["kill"], market_evidence_age_seconds=0, generated_at_utc=T0)
        post = build_post_run_summary(manifest=ctx["manifest"], generated_at_utc=T0 + timedelta(seconds=1), suppressed=True, transport_attempts=0, order_observations=0, fills=(), fees=Decimal(0), ending_balances={}, ending_positions={}, realized_pnl=Decimal(0), maximum_exposure=ctx["risk"].risk_notional, reconciliation=ctx["reconciliation"], kill_switch=ctx["kill"], unresolved_recovery_items=(), evidence_ids=(ctx["manifest"].manifest_id, ctx["reconciliation"].reconciliation_id))
        self.repo.persist_summary(pre); self.repo.persist_summary(post); self.assertEqual(self.count("live_pre_run_summaries"), 1); self.assertEqual(self.count("live_post_run_summaries"), 1)
        with self.connection.cursor() as cursor: cursor.execute("SELECT string_agg(row_to_json(t)::text,' ') AS text FROM execution.live_credential_references t"); text = cursor.fetchone()["text"] or ""
        for secret in ("placeholder-secret", "placeholder-passphrase", "OK-ACCESS-SIGN", "authorization"):
            self.assertNotIn(secret, text)


if __name__ == "__main__": unittest.main()
