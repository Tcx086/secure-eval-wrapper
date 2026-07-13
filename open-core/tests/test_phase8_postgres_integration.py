from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.authorities import FixtureOnlyPreflightEvidence
from secure_eval_wrapper.live.broker import GuardedLiveBroker
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository, LiveClaimError, LiveConflictError
from secure_eval_wrapper.live.models import LiveObservationBundle, LiveRecoveryOutcome
from secure_eval_wrapper.live.restart import ReconstructedLiveRuntime, reconstruct_live_runtime
from secure_eval_wrapper.live.risk_summary import build_post_run_summary, build_pre_run_summary
from secure_eval_wrapper.live.venues.fake_live import FakeLiveVenue
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter
from test_phase8_guarded_live import T0, account, live_intent, passed_authority

RUN = os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true"
TABLES = (
    "live_post_run_summaries", "live_pre_run_summaries", "live_lifecycle_events", "live_recovery_records",
    "live_reconciliation_differences", "live_reconciliations", "live_fill_observations", "live_order_projections",
    "live_order_observations", "live_transport_attempts", "live_cancel_outbox", "live_dispatch_events",
    "live_dispatch_outbox", "live_reservations", "live_runtime_risk_decisions", "live_order_intents",
    "live_run_risk_state", "live_kill_events", "live_kill_switches", "live_runs", "live_run_manifests",
    "live_approvals", "live_preflight_check_sources", "live_preflight_checks", "live_preflight_reports",
    "live_preflight_sources", "live_account_snapshots", "live_credential_references", "live_configuration_snapshots",
)


@unittest.skipUnless(RUN, "requires real PostgreSQL 16")
class Phase8PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.kwargs = dict(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))
        cls.connection = psycopg.connect(**cls.kwargs, row_factory=dict_row)

    @classmethod
    def tearDownClass(cls): cls.connection.close()

    def setUp(self):
        with self.connection.cursor() as cursor: cursor.execute("TRUNCATE " + ",".join("execution." + name for name in TABLES) + " CASCADE")
        self.connection.commit(); self.repo = DurablePostgresLiveRepository(self.connection)

    def context(self, *, at=T0):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(at=at)
        intent = live_intent(manifest, snap, reconciliation, market, limit=Decimal("100"), at=at)
        body = OkxProductionSpotAdapter.build_limit_order_body(instrument="BTC-USDT", side=intent.side.value, quantity=intent.quantity, limit_price=intent.limit_price, client_order_id=intent.client_order_id, tick_size=Decimal("0.01"), lot_size=Decimal("0.001"))
        request_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/order", "body": body})
        return locals()

    def start(self, ctx, **kwargs):
        self.repo.persist_reconciliation(ctx["reconciliation"], exact_input={"local": {"source": "postgresql"}, "venue": {"source": "typed_exact_bundle"}})
        return self.repo.persist_start_bundle(configuration=ctx["cfg"], credential_reference=ctx["cred"], account_snapshot=ctx["snap"], report=ctx["report"], approval=ctx["approval"], manifest=ctx["manifest"], kill_switch=ctx["kill"], evidence=ctx["evidence"], created_at_utc=ctx["at"], **kwargs)

    def prepare(self, ctx, *, repo=None, intent=None, configuration=None, approval=None, **kwargs):
        repo = repo or self.repo; intent = intent or ctx["intent"]
        configuration = configuration or ctx["cfg"]
        approval = approval or ctx["approval"]
        body = OkxProductionSpotAdapter.build_limit_order_body(instrument="BTC-USDT", side=intent.side.value, quantity=intent.quantity, limit_price=intent.limit_price, client_order_id=intent.client_order_id, tick_size=Decimal("0.01"), lot_size=Decimal("0.001"))
        request_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/order", "body": body})
        return repo.prepare_operational_dry_run(intent=intent, configuration=configuration, approval=approval, market_evidence=ctx["market"], request_body=body, provider_request_hash=request_hash, created_at_utc=ctx["at"], **kwargs)

    def count(self, table):
        with self.connection.cursor() as cursor: cursor.execute(f"SELECT count(*) AS count FROM execution.{table}"); return cursor.fetchone()["count"]

    def test_catalog_and_migration_0023_are_installed(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='execution' AND table_name LIKE 'live_%'"); names = {row["table_name"] for row in cursor.fetchall()}
            cursor.execute("SELECT sha256 FROM audit.schema_migrations WHERE migration_id='0023_phase8a_authority_recovery_and_cli_integrity'"); catalog_hash = cursor.fetchone()["sha256"]
        self.assertEqual(names, set(TABLES)); migration = Path(__file__).resolve().parents[1] / "db" / "migrations" / "0023_phase8a_authority_recovery_and_cli_integrity.sql"
        self.assertEqual(catalog_hash, hashlib.sha256(migration.read_bytes().replace(b"\r\n", b"\n")).hexdigest())

    def test_start_bundle_rolls_back_each_child_and_replays_exactly(self):
        for failure in ("configuration", "credential", "account", "preflight", "approval", "manifest", "kill_switch", "risk_state"):
            with self.subTest(failure=failure):
                self.setUp(); ctx = self.context(); self.repo.persist_reconciliation(ctx["reconciliation"], exact_input={"seed": True})
                with self.assertRaises(RuntimeError): self.repo.persist_start_bundle(configuration=ctx["cfg"], credential_reference=ctx["cred"], account_snapshot=ctx["snap"], report=ctx["report"], approval=ctx["approval"], manifest=ctx["manifest"], kill_switch=ctx["kill"], evidence=ctx["evidence"], created_at_utc=T0, fail_at=failure)
                self.assertEqual(self.count("live_runs"), 0); self.assertEqual(self.count("live_preflight_reports"), 0)
        self.setUp(); ctx = self.context(); self.assertTrue(self.start(ctx)); self.assertTrue(self.start(ctx)); self.assertEqual(self.count("live_runs"), 1)

    def test_database_rejects_unsafe_permission_spoof(self):
        ctx = self.context(); self.start(ctx)
        unsafe_id = uuid4(); unsafe_hash = "a" * 64; report_id = uuid4()
        with self.assertRaises(Exception):
            with self.connection.cursor() as cursor:
                cursor.execute("INSERT INTO execution.live_credential_references (credential_reference_id,provider,alias,source_type,account_fingerprint,loaded,verified_at_utc,permission_summary_jsonb,record_sha256,created_at_utc) VALUES (%s,'okx','unsafe','injected_local',%s,false,%s,'[\"read\",\"withdraw\"]'::jsonb,%s,%s)", (unsafe_id, ctx["snap"].account_fingerprint, T0, unsafe_hash, T0))
                cursor.execute("INSERT INTO execution.live_preflight_reports (preflight_report_id,live_run_id,configuration_sha256,implementation_sha256,repository_commit_sha,endpoint_catalog_sha256,credential_reference_sha256,account_snapshot_sha256,evaluated_at_utc,status,blockers_jsonb,warnings_jsonb,record_sha256,credential_reference_id,account_snapshot_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'passed','[]'::jsonb,'[]'::jsonb,%s,%s,%s)", (report_id, ctx["manifest"].live_run_id, ctx["cfg"].configuration_hash, ctx["cfg"].provider_implementation_hash, ctx["manifest"].repository_commit_sha, ctx["cfg"].endpoint_catalog_hash, unsafe_hash, ctx["snap"].record_hash, T0, "b" * 64, unsafe_id, ctx["snap"].snapshot_id))
        self.connection.rollback(); self.assertEqual(self.count("live_credential_references"), 1)

    def test_cross_run_report_approval_manifest_and_intent_attacks_fail(self):
        a = self.context(); self.start(a); b = self.context(); self.start(b)
        with self.assertRaises(Exception):
            with self.connection.cursor() as cursor:
                cursor.execute("INSERT INTO execution.live_approvals (approval_id,live_run_id,preflight_report_id,configuration_sha256,account_fingerprint,provider,environment,manifest_sha256,confirmation_challenge_sha256,maximum_total_approved_notional,consumed_notional,created_at_utc,expires_at_utc,approving_actor,nonce,approval_jsonb,record_sha256) VALUES (%s,%s,%s,%s,%s,'okx','production',%s,%s,100,0,%s,%s,'attack','cross-run','{}'::jsonb,%s)", (uuid4(), b["manifest"].live_run_id, a["report"].report_id, b["cfg"].configuration_hash, b["snap"].account_fingerprint, "c" * 64, "d" * 64, T0, T0 + timedelta(seconds=30), "e" * 64))
        self.connection.rollback()
        attack = live_intent(b["manifest"], b["snap"], b["reconciliation"], b["market"], limit=Decimal("101"))
        with self.assertRaises(Exception):
            with self.connection.cursor() as cursor:
                cursor.execute("INSERT INTO execution.live_order_intents (order_intent_id,live_run_id,manifest_id,client_order_id,instrument_id,side,order_type,accounting_mode,quantity,limit_price,reference_price,market_evidence_id,market_evidence_sha256,instrument_metadata_sha256,account_snapshot_sha256,reconciliation_sha256,economic_sha256,state,created_at_utc,record_sha256) VALUES (%s,%s,%s,%s,'BTC-USDT','buy','limit','spot',1,101,100,%s,%s,%s,%s,%s,%s,'dry_run_prepared',%s,%s)", (attack.order_intent_id, b["manifest"].live_run_id, a["manifest"].manifest_id, attack.client_order_id, attack.market_evidence_id, attack.market_evidence_hash, attack.instrument_metadata_hash, attack.account_snapshot_hash, attack.reconciliation_hash, attack.economic_hash, T0, attack.record_hash))
        self.connection.rollback()

    def test_transactional_risk_ignores_caller_zero_and_stopped_kill(self):
        ctx = self.context(); self.start(ctx)
        with self.connection.cursor() as cursor: cursor.execute("UPDATE execution.live_run_risk_state SET daily_submitted_notional=%s,version=version+1 WHERE live_run_id=%s", (ctx["cfg"].maximum_daily_submitted_notional, ctx["manifest"].live_run_id))
        self.connection.commit(); prepared = self.prepare(ctx, caller_risk_state={"daily_submitted_notional": 0})
        self.assertFalse(prepared["risk_decision"].accepted); self.assertIn("maximum_daily_submitted_notional", prepared["risk_decision"].reasons)
        self.setUp(); ctx = self.context(); self.start(ctx); self.repo.trigger_kill(live_run_id=ctx["manifest"].live_run_id, reason="manual", evidence={}, at_utc=T0 + timedelta(seconds=1))
        with self.assertRaises(PermissionError): self.prepare(ctx)

    def test_old_preflight_and_approval_cannot_reset_kill(self):
        ctx = self.context(); self.start(ctx); self.repo.trigger_kill(live_run_id=ctx["manifest"].live_run_id, reason="manual", evidence={}, at_utc=T0 + timedelta(seconds=1))
        with self.assertRaises(PermissionError): self.repo.reset_kill(live_run_id=ctx["manifest"].live_run_id, fresh_preflight_report_id=ctx["report"].report_id, new_approval_id=ctx["approval"].approval_id, at_utc=T0 + timedelta(seconds=2))

    def test_buy_sell_fee_reservations_and_duplicate_replay(self):
        ctx = self.context(); self.start(ctx); result = self.prepare(ctx); reservation = result["reservation"]
        self.assertEqual(reservation.currency, "USDT"); self.assertEqual(reservation.original_amount, reservation.risk_notional + reservation.maximum_fee_amount)
        replay = self.prepare(ctx); self.assertTrue(replay["replayed"]); self.assertEqual(self.count("live_reservations"), 1)
        self.setUp(); ctx = self.context(); sell = live_intent(ctx["manifest"], ctx["snap"], ctx["reconciliation"], ctx["market"], side="sell", quantity=Decimal("2"))
        self.start(ctx)
        with self.connection.cursor() as cursor: cursor.execute("UPDATE execution.live_run_risk_state SET positions_jsonb='{" + '"BTC-USDT":{"notional":"500"}' + "}'::jsonb,version=version+1 WHERE live_run_id=%s", (ctx["manifest"].live_run_id,))
        self.connection.commit(); result = self.prepare(ctx, intent=sell); self.assertEqual(result["reservation"].currency, "BTC"); self.assertEqual(result["reservation"].original_amount, Decimal("2"))

    def test_insufficient_quote_and_base_balances_block(self):
        for side, currency in (("buy", "USDT"), ("sell", "BTC")):
            with self.subTest(side=side):
                self.setUp(); ctx = self.context(); self.start(ctx)
                balances = {"USDT": {"available": "0"}, "BTC": {"available": "0"}}
                positions = {"BTC-USDT": {"notional": "500"}}
                with self.connection.cursor() as cursor: cursor.execute("UPDATE execution.live_run_risk_state SET balances_jsonb=%s,positions_jsonb=%s,version=version+1 WHERE live_run_id=%s", (json.dumps(balances), json.dumps(positions), ctx["manifest"].live_run_id))
                self.connection.commit(); intent = live_intent(ctx["manifest"], ctx["snap"], ctx["reconciliation"], ctx["market"], side=side)
                result = self.prepare(ctx, intent=intent); self.assertFalse(result["risk_decision"].accepted); self.assertIn(f"insufficient_{currency.lower()}_balance", result["risk_decision"].reasons)

    def test_partial_projected_consumption_and_fee_ceiling(self):
        ctx = self.context(); self.start(ctx); result = self.prepare(ctx); reservation = result["reservation"]
        remaining = self.repo.consume_reservation(reservation_id=reservation.reservation_id, amount=reservation.original_amount / 2, quantity=reservation.original_quantity / 2, at_utc=T0 + timedelta(seconds=1))
        self.assertEqual(remaining, (reservation.original_amount / 2, reservation.original_quantity / 2))
        with self.assertRaises(PermissionError): self.repo.consume_reservation(reservation_id=reservation.reservation_id, amount=reservation.original_amount, quantity=Decimal(0), at_utc=T0 + timedelta(seconds=2))
        self.assertEqual(reservation.maximum_fee_amount, reservation.risk_notional * ctx["cfg"].maximum_fee_bps / Decimal(10000))

    def test_concurrent_intents_compete_for_same_balance(self):
        import psycopg
        from psycopg.rows import dict_row
        ctx = self.context(); self.start(ctx)
        balances = {"USDT": {"available": "150"}, "BTC": {"available": "10"}}
        with self.connection.cursor() as cursor: cursor.execute("UPDATE execution.live_run_risk_state SET balances_jsonb=%s,version=version+1 WHERE live_run_id=%s", (json.dumps(balances), ctx["manifest"].live_run_id))
        self.connection.commit()
        intents = [ctx["intent"], live_intent(ctx["manifest"], ctx["snap"], ctx["reconciliation"], ctx["market"], limit=Decimal("101"))]
        def worker(intent):
            connection = psycopg.connect(**self.kwargs, row_factory=dict_row)
            try: return self.prepare(ctx, repo=DurablePostgresLiveRepository(connection), intent=intent)["risk_decision"].accepted
            finally: connection.close()
        with ThreadPoolExecutor(max_workers=2) as executor: outcomes = list(executor.map(worker, intents))
        self.assertEqual(sorted(outcomes), [False, True]); self.assertEqual(self.count("live_reservations"), 1)

    def _pending_recovery(self, ctx):
        prepared = self.prepare(ctx); outbox = prepared["outbox_id"]; dispatch = self.repo.claim_dispatch(worker_identity="dispatch", at_utc=T0, outbox_id=outbox)
        self.repo.mark_pending_recovery(outbox_id=outbox, claim_token=dispatch[1], worker_identity="dispatch", at_utc=T0 + timedelta(seconds=1))
        recovery = self.repo.claim_recovery(worker_identity="recovery", at_utc=T0 + timedelta(seconds=2), outbox_id=outbox)
        return outbox, recovery

    def observation(self, ctx, *, order=None, fills=(), extra_account=None, declared=LiveRecoveryOutcome.CONFIRMED_ABSENT):
        queried_at = T0 + timedelta(seconds=3)
        recent = (order,) if order is not None else ()
        open_orders = recent if order is not None and order.get("state") in {"live", "partially_filled"} else ()
        fills = tuple(fills)
        account_observation = {"account_fingerprint": ctx["snap"].account_fingerprint, "query_timestamp_utc": queried_at}
        if extra_account:
            account_observation.update(extra_account)
        account_observation["response_hashes"] = {
            "queried_order": sha256_payload(order),
            "recent_orders": sha256_payload(recent),
            "open_orders": sha256_payload(open_orders),
            "fills": sha256_payload(fills),
            "account": sha256_payload(account_observation),
        }
        return LiveObservationBundle(
            ctx["manifest"].live_run_id, ctx["intent"].client_order_id, order, recent, open_orders,
            fills, account_observation, queried_at, declared,
        )

    def test_observed_external_order_and_fill_create_incident_and_stop_kill(self):
        for outcome in (LiveRecoveryOutcome.OBSERVED_EXTERNAL_ORDER, LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL):
            with self.subTest(outcome=outcome):
                self.setUp(); ctx = self.context(); self.start(ctx); outbox, recovery = self._pending_recovery(ctx)
                order = {"ordId": "o1", "clOrdId": ctx["intent"].client_order_id, "instId": "BTC-USDT", "state": "live", "side": "buy", "sz": "1", "px": "100"}
                fills = ({"tradeId": "f1", "ordId": "o1", "clOrdId": ctx["intent"].client_order_id, "instId": "BTC-USDT", "side": "buy", "fillSz": "1", "fillPx": "100", "fee": "-0.1", "feeCcy": "USDT"},) if outcome is LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL else ()
                bundle = self.observation(ctx, order=order, fills=fills, declared=LiveRecoveryOutcome.CONFIRMED_ABSENT)
                self.repo.persist_recovery_observation(outbox_id=outbox, claim_token=recovery[1], worker_identity="recovery", observation_bundle=bundle, at_utc=T0 + timedelta(seconds=4))
                with self.connection.cursor() as cursor:
                    cursor.execute("SELECT d.state,p.state AS projection,k.state AS kill FROM execution.live_dispatch_outbox d JOIN execution.live_order_projections p ON p.order_intent_id=d.order_intent_id JOIN execution.live_kill_switches k ON k.live_run_id=d.live_run_id WHERE d.dispatch_outbox_id=%s", (outbox,)); row = cursor.fetchone()
                self.assertEqual((row["state"], row["projection"], row["kill"]), ("unexpected_external_side_effect", "incident_blocked", "stopped"))

    def test_recovery_conflict_and_two_worker_claim_exclusion(self):
        ctx = self.context(); self.start(ctx); outbox, recovery = self._pending_recovery(ctx)
        self.assertIsNone(self.repo.claim_recovery(worker_identity="other", at_utc=T0 + timedelta(seconds=2), outbox_id=outbox))
        bundle = self.observation(ctx)
        observation_id = self.repo.persist_recovery_observation(outbox_id=outbox, claim_token=recovery[1], worker_identity="recovery", observation_bundle=bundle, at_utc=T0 + timedelta(seconds=4))
        conflicting = self.observation(ctx, extra_account={"changed": True})
        with self.assertRaises(LiveConflictError): self.repo.persist_recovery_observation(outbox_id=outbox, claim_token=recovery[1], worker_identity="recovery", observation_bundle=conflicting, at_utc=T0 + timedelta(seconds=4))
        self.assertIsNotNone(observation_id)

    def test_direct_sql_economic_manifest_request_and_success_mutations_fail(self):
        ctx = self.context()
        self.start(ctx)
        result = self.prepare(ctx)
        outbox = result["outbox_id"]
        claimed = self.repo.claim_dispatch(worker_identity="immutability", at_utc=T0, outbox_id=outbox)
        self.repo.suppress_claimed_dispatch(
            outbox_id=outbox,
            claim_token=claimed[1],
            worker_identity="immutability",
            at_utc=T0 + timedelta(seconds=1),
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT transport_attempt_id FROM execution.live_transport_attempts WHERE order_intent_id=%s",
                (ctx["intent"].order_intent_id,),
            )
            attempt_id = cursor.fetchone()["transport_attempt_id"]
        attacks = [
            ("UPDATE execution.live_order_intents SET quantity=quantity+1 WHERE order_intent_id=%s", (ctx["intent"].order_intent_id,)),
            ("UPDATE execution.live_order_intents SET limit_price=limit_price+1 WHERE order_intent_id=%s", (ctx["intent"].order_intent_id,)),
            ("UPDATE execution.live_order_intents SET market_evidence_sha256=%s WHERE order_intent_id=%s", ("f" * 64, ctx["intent"].order_intent_id)),
            ("UPDATE execution.live_order_intents SET account_snapshot_sha256=%s WHERE order_intent_id=%s", ("f" * 64, ctx["intent"].order_intent_id)),
            ("UPDATE execution.live_order_intents SET reconciliation_sha256=%s WHERE order_intent_id=%s", ("f" * 64, ctx["intent"].order_intent_id)),
            ("UPDATE execution.live_order_intents SET live_run_id=%s WHERE order_intent_id=%s", (uuid4(), ctx["intent"].order_intent_id)),
            ("UPDATE execution.live_approvals SET maximum_total_approved_notional=maximum_total_approved_notional+1 WHERE approval_id=%s", (ctx["approval"].approval_id,)),
            ("UPDATE execution.live_run_manifests SET manifest_sha256=%s WHERE manifest_id=%s", ("f" * 64, ctx["manifest"].manifest_id)),
            ("UPDATE execution.live_reservations SET original_amount=original_amount+1 WHERE reservation_id=%s", (result["reservation"].reservation_id,)),
            ("UPDATE execution.live_dispatch_outbox SET request_jsonb='{}'::jsonb WHERE dispatch_outbox_id=%s", (outbox,)),
            ("UPDATE execution.live_dispatch_outbox SET provider_request_sha256=%s WHERE dispatch_outbox_id=%s", ("f" * 64, outbox)),
            ("UPDATE execution.live_dispatch_outbox SET request_method='GET' WHERE dispatch_outbox_id=%s", (outbox,)),
            ("UPDATE execution.live_dispatch_outbox SET request_path='/api/v5/trade/cancel-order' WHERE dispatch_outbox_id=%s", (outbox,)),
            ("UPDATE execution.live_transport_attempts SET external_write_attempted=true,successful_write=true WHERE transport_attempt_id=%s", (attempt_id,)),
        ]
        for sql, params in attacks:
            with self.subTest(sql=sql):
                with self.assertRaises(Exception):
                    with self.connection.cursor() as cursor:
                        cursor.execute(sql, params)
                self.connection.rollback()
        with self.assertRaises(Exception):
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO execution.live_transport_attempts (transport_attempt_id,live_run_id,order_intent_id,operation,provider_request_sha256,result,external_write_attempted,successful_write,attempted_at_utc,record_sha256) VALUES (%s,%s,%s,'attack',%s,'write_suppressed',true,true,%s,%s)",
                    (uuid4(), ctx["manifest"].live_run_id, ctx["intent"].order_intent_id, "a" * 64, T0, "b" * 64),
                )
        self.connection.rollback()

    def test_restart_reconstructs_typed_operational_dry_run(self):
        ctx = self.context(); self.start(ctx); runtime = reconstruct_live_runtime(repository=DurablePostgresLiveRepository(self.connection), live_run_id=ctx["manifest"].live_run_id)
        self.assertIsInstance(runtime, ReconstructedLiveRuntime); self.assertFalse(runtime.configuration.production_write_enabled); self.assertFalse(runtime.manifest.production_write_enabled); self.assertEqual(runtime.kill_switch.state.value, "armed")

    def test_all_five_operational_clis_use_postgresql_and_remain_write_free(self):
        now = datetime.now(timezone.utc); ctx = self.context(at=now); self.start(ctx); root = Path(__file__).resolve().parents[1]; env = dict(os.environ); env["PYTHONPATH"] = str(root / "src")
        commands = {
            "preflight": [root / "scripts" / "run_live_preflight.py", "--live-run-id", str(ctx["manifest"].live_run_id)],
            "dry-run": [root / "scripts" / "run_live_dry_run.py", "--live-run-id", str(ctx["manifest"].live_run_id), "--side", "buy", "--quantity", "1", "--limit-price", "100", "--tick-size", "0.01", "--lot-size", "0.001"],
            "status": [root / "scripts" / "run_live_status.py", "--live-run-id", str(ctx["manifest"].live_run_id)],
        }
        for name, command in commands.items():
            completed = subprocess.run([sys.executable, *map(str, command)], env=env, capture_output=True, text=True); self.assertIn(completed.returncode, (0, 1), (name, completed.stdout, completed.stderr)); payload = json.loads(completed.stdout); self.assertFalse(payload["network_writes_occurred"])
        projection = self.repo.build_local_projection(ctx["manifest"].live_run_id, observed_at_utc=now)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "venue.json"; path.write_text(json.dumps({"account_fingerprint": projection["account_fingerprint"], "orders": projection["orders"], "fills": projection["fills"], "balances": projection["balances"], "positions": projection["positions"], "sequence": projection["sequence"], "observed_at_utc": projection["timestamp_utc"], "response_hashes": ["a" * 64]}, default=str), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(root / "scripts" / "run_live_reconcile.py"), "--live-run-id", str(ctx["manifest"].live_run_id), "--venue-observation-json", str(path)], env=env, capture_output=True, text=True); self.assertIn(completed.returncode, (0, 1)); self.assertFalse(json.loads(completed.stdout)["network_writes_occurred"])
        completed = subprocess.run([sys.executable, str(root / "scripts" / "run_live_kill.py"), "--live-run-id", str(ctx["manifest"].live_run_id)], env=env, capture_output=True, text=True); self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr); self.assertFalse(json.loads(completed.stdout)["network_writes_occurred"])

    def _restart_process(self, live_run_id, action):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src")
        code = """
import json
import os
import sys
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository
from secure_eval_wrapper.live.restart import reconstruct_live_runtime

connection = psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=int(os.environ["POSTGRES_PORT"]),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
    row_factory=dict_row,
)
repository = DurablePostgresLiveRepository(connection)
run_id = sys.argv[1]
action = sys.argv[2]
runtime = reconstruct_live_runtime(repository=repository, live_run_id=run_id)
now = datetime.now(timezone.utc)
result = {
    "write_enabled": runtime.configuration.production_write_enabled or runtime.manifest.production_write_enabled,
    "kill": runtime.kill_switch.state.value,
    "dispatch": [row.state for row in runtime.dispatch_outboxes],
    "cancel_count": len(runtime.cancel_outboxes),
    "recovery_count": len(runtime.recovery_claims),
    "reconciliation_count": len(runtime.reconciliations),
    "reservation_count": len(runtime.reservations),
    "summary_count": len(runtime.summaries),
    "venue_writes": runtime.venue.write_attempt_count,
}
if action == "suppress":
    outbox_id = runtime.dispatch_outboxes[0].record_id
    claimed = repository.claim_dispatch(worker_identity="restart-process", at_utc=now, outbox_id=outbox_id)
    if claimed is None:
        raise RuntimeError("restart process could not claim unresolved dispatch")
    repository.suppress_claimed_dispatch(
        outbox_id=outbox_id,
        claim_token=claimed[1],
        worker_identity="restart-process",
        at_utc=now + timedelta(seconds=1),
    )
    result["continued_state"] = repository.dispatch_state(outbox_id)
elif action == "claim_recovery":
    outbox_id = runtime.dispatch_outboxes[0].record_id
    claimed = repository.claim_recovery(worker_identity="restart-recovery", at_utc=now, outbox_id=outbox_id)
    result["recovery_claimed"] = claimed is not None
connection.close()
print(json.dumps(result, sort_keys=True))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code, str(live_run_id), action],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def test_all_true_fixture_preflight_spoof_never_reaches_postgresql(self):
        ctx = self.context()
        fixture = FixtureOnlyPreflightEvidence(
            ctx["manifest"].live_run_id,
            {"repository": True, "postgresql": True, "permissions_safe": True, "kill_armed": True},
        )
        with self.assertRaises(TypeError):
            self.repo.persist_start_bundle(
                configuration=ctx["cfg"],
                credential_reference=ctx["cred"],
                account_snapshot=ctx["snap"],
                report=ctx["report"],
                approval=ctx["approval"],
                manifest=ctx["manifest"],
                kill_switch=ctx["kill"],
                evidence=fixture,
                created_at_utc=T0,
            )
        self.assertEqual(self.count("live_configuration_snapshots"), 0)
        self.assertEqual(self.count("live_preflight_reports"), 0)

    def test_mismatched_configuration_is_rejected_by_postgresql_broker(self):
        ctx = self.context()
        self.start(ctx)
        mismatched = replace(
            ctx["cfg"],
            maximum_order_notional=ctx["cfg"].maximum_order_notional + Decimal("1"),
        )
        with self.assertRaises(PermissionError):
            GuardedLiveBroker(
                configuration=mismatched,
                manifest=ctx["manifest"],
                approval=ctx["approval"],
                preflight_report=ctx["report"],
                repository=self.repo,
                venue=FakeLiveVenue(),
            )

    def test_mismatched_approval_is_rejected_by_postgresql_broker(self):
        ctx = self.context()
        other = self.context()
        self.start(ctx)
        with self.assertRaises(PermissionError):
            GuardedLiveBroker(
                configuration=ctx["cfg"],
                manifest=ctx["manifest"],
                approval=other["approval"],
                preflight_report=ctx["report"],
                repository=self.repo,
                venue=FakeLiveVenue(),
            )

    def test_database_rejects_cross_run_membership_mutations_for_critical_authorities(self):
        first = self.context()
        self.start(first)
        second = self.context()
        self.start(second)
        prepared = self.prepare(first)
        target_run = second["manifest"].live_run_id
        attacks = (
            ("UPDATE execution.live_account_snapshots SET live_run_id=%s WHERE account_snapshot_id=%s", (target_run, first["snap"].snapshot_id)),
            ("UPDATE execution.live_preflight_reports SET live_run_id=%s WHERE preflight_report_id=%s", (target_run, first["report"].report_id)),
            ("UPDATE execution.live_approvals SET live_run_id=%s WHERE approval_id=%s", (target_run, first["approval"].approval_id)),
            ("UPDATE execution.live_run_manifests SET live_run_id=%s WHERE manifest_id=%s", (target_run, first["manifest"].manifest_id)),
            ("UPDATE execution.live_order_intents SET live_run_id=%s WHERE order_intent_id=%s", (target_run, first["intent"].order_intent_id)),
            ("UPDATE execution.live_dispatch_outbox SET live_run_id=%s WHERE dispatch_outbox_id=%s", (target_run, prepared["outbox_id"])),
            ("UPDATE execution.live_reconciliations SET live_run_id=%s WHERE reconciliation_id=%s", (target_run, first["reconciliation"].reconciliation_id)),
        )
        for sql, params in attacks:
            with self.subTest(sql=sql):
                with self.assertRaises(Exception):
                    with self.connection.cursor() as cursor:
                        cursor.execute(sql, params)
                self.connection.rollback()

    def test_two_workers_cannot_claim_the_same_dispatch(self):
        import psycopg
        from psycopg.rows import dict_row

        ctx = self.context()
        self.start(ctx)
        outbox_id = self.prepare(ctx)["outbox_id"]

        def worker(identity):
            connection = psycopg.connect(**self.kwargs, row_factory=dict_row)
            try:
                repository = DurablePostgresLiveRepository(connection)
                return repository.claim_dispatch(
                    worker_identity=identity,
                    at_utc=T0 + timedelta(seconds=1),
                    outbox_id=outbox_id,
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(worker, ("worker-a", "worker-b")))
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_cancel_request_is_exact_immutable_and_reconstructed(self):
        ctx = self.context()
        self.start(ctx)
        self.prepare(ctx)
        body = OkxProductionSpotAdapter.build_cancel_body(
            instrument="BTC-USDT",
            client_order_id=ctx["intent"].client_order_id,
        )
        request_hash = sha256_payload(
            {"method": "POST", "path": "/api/v5/trade/cancel-order", "body": body}
        )
        with self.assertRaises(ValueError):
            self.repo.prepare_cancel_dry_run(
                live_run_id=ctx["manifest"].live_run_id,
                order_intent_id=ctx["intent"].order_intent_id,
                client_order_id=ctx["intent"].client_order_id,
                request_body={**body, "instId": "ETH-USDT"},
                provider_request_hash=request_hash,
                created_at_utc=T0 + timedelta(seconds=1),
            )
        cancel_id = self.repo.prepare_cancel_dry_run(
            live_run_id=ctx["manifest"].live_run_id,
            order_intent_id=ctx["intent"].order_intent_id,
            client_order_id=ctx["intent"].client_order_id,
            request_body=body,
            provider_request_hash=request_hash,
            created_at_utc=T0 + timedelta(seconds=1),
        )
        attacks = (
            ("UPDATE execution.live_cancel_outbox SET request_jsonb='{}'::jsonb WHERE cancel_outbox_id=%s", (cancel_id,)),
            ("UPDATE execution.live_cancel_outbox SET provider_request_sha256=%s WHERE cancel_outbox_id=%s", ("f" * 64, cancel_id)),
            ("UPDATE execution.live_cancel_outbox SET request_method='GET' WHERE cancel_outbox_id=%s", (cancel_id,)),
            ("UPDATE execution.live_cancel_outbox SET request_path='/api/v5/trade/order' WHERE cancel_outbox_id=%s", (cancel_id,)),
            ("UPDATE execution.live_cancel_outbox SET live_run_id=%s WHERE cancel_outbox_id=%s", (uuid4(), cancel_id)),
        )
        for sql, params in attacks:
            with self.subTest(sql=sql):
                with self.assertRaises(Exception):
                    with self.connection.cursor() as cursor:
                        cursor.execute(sql, params)
                self.connection.rollback()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state,request_method,request_path FROM execution.live_cancel_outbox WHERE cancel_outbox_id=%s",
                (cancel_id,),
            )
            row = cursor.fetchone()
        self.assertEqual(
            (row["state"], row["request_method"], row["request_path"]),
            ("dry_run_suppressed", "POST", "/api/v5/trade/cancel-order"),
        )
        restarted = self._restart_process(ctx["manifest"].live_run_id, "inspect")
        self.assertEqual(restarted["cancel_count"], 1)
        self.assertFalse(restarted["write_enabled"])

    def test_direct_sql_reservation_economics_mutation_fails(self):
        ctx = self.context()
        self.start(ctx)
        reservation = self.prepare(ctx)["reservation"]
        for column in ("original_amount", "worst_case_price", "maximum_fee_amount", "risk_notional"):
            with self.subTest(column=column):
                with self.assertRaises(Exception):
                    with self.connection.cursor() as cursor:
                        cursor.execute(
                            f"UPDATE execution.live_reservations SET {column}={column}+1 WHERE reservation_id=%s",
                            (reservation.reservation_id,),
                        )
                self.connection.rollback()

    def test_new_process_continues_unresolved_dispatch_to_dry_run_suppression(self):
        ctx = self.context()
        self.start(ctx)
        self.prepare(ctx)
        restarted = self._restart_process(ctx["manifest"].live_run_id, "suppress")
        self.assertEqual(restarted["dispatch"], ["dry_run_prepared"])
        self.assertEqual(restarted["continued_state"], "dry_run_suppressed")
        self.assertEqual(restarted["reservation_count"], 1)
        self.assertEqual(restarted["venue_writes"], 0)
        self.assertFalse(restarted["write_enabled"])

    def test_new_process_continues_pending_recovery_with_stopped_kill(self):
        ctx = self.context()
        self.start(ctx)
        prepared = self.prepare(ctx)
        dispatch = self.repo.claim_dispatch(
            worker_identity="crashed-dispatch",
            at_utc=T0,
            outbox_id=prepared["outbox_id"],
        )
        self.repo.mark_pending_recovery(
            outbox_id=prepared["outbox_id"],
            claim_token=dispatch[1],
            worker_identity="crashed-dispatch",
            at_utc=T0 + timedelta(seconds=1),
        )
        self.repo.trigger_kill(
            live_run_id=ctx["manifest"].live_run_id,
            reason="restart-audit",
            evidence={"source": "postgresql"},
            at_utc=T0 + timedelta(seconds=2),
        )
        restarted = self._restart_process(ctx["manifest"].live_run_id, "claim_recovery")
        self.assertEqual(restarted["kill"], "stopped")
        self.assertEqual(restarted["dispatch"], ["pending_recovery"])
        self.assertTrue(restarted["recovery_claimed"])
        self.assertFalse(restarted["write_enabled"])

    def test_new_process_reconstructs_suppression_reconciliation_and_summaries(self):
        ctx = self.context()
        self.start(ctx)
        prepared = self.prepare(ctx)
        claimed = self.repo.claim_dispatch(
            worker_identity="summary-worker",
            at_utc=T0,
            outbox_id=prepared["outbox_id"],
        )
        self.repo.suppress_claimed_dispatch(
            outbox_id=prepared["outbox_id"],
            claim_token=claimed[1],
            worker_identity="summary-worker",
            at_utc=T0 + timedelta(seconds=1),
        )
        pre_run = build_pre_run_summary(
            manifest=ctx["manifest"],
            approval=ctx["approval"],
            account_snapshot=ctx["snap"],
            proposed_decisions=(prepared["risk_decision"],),
            reconciliation=ctx["reconciliation"],
            kill_switch=ctx["kill"],
            market_evidence_age_seconds=0,
            generated_at_utc=T0 + timedelta(seconds=2),
        )
        post_run = build_post_run_summary(
            manifest=ctx["manifest"],
            generated_at_utc=T0 + timedelta(seconds=3),
            suppressed=True,
            transport_attempts=1,
            order_observations=0,
            fills=(),
            fees=Decimal("0"),
            ending_balances=ctx["snap"].balances,
            ending_positions=ctx["snap"].positions,
            realized_pnl=Decimal("0"),
            maximum_exposure=prepared["risk_decision"].risk_notional,
            reconciliation=ctx["reconciliation"],
            kill_switch=ctx["kill"],
            unresolved_recovery_items=(),
            evidence_ids=(ctx["manifest"].manifest_id, ctx["reconciliation"].reconciliation_id),
        )
        self.repo.persist_summary(pre_run)
        self.repo.persist_summary(post_run)
        restarted = self._restart_process(ctx["manifest"].live_run_id, "inspect")
        self.assertEqual(restarted["dispatch"], ["dry_run_suppressed"])
        self.assertGreaterEqual(restarted["reconciliation_count"], 1)
        self.assertEqual(restarted["reservation_count"], 1)
        self.assertEqual(restarted["summary_count"], 2)
        self.assertEqual(restarted["venue_writes"], 0)
        self.assertFalse(restarted["write_enabled"])

if __name__ == "__main__": unittest.main()
