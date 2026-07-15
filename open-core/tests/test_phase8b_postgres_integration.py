from __future__ import annotations

import hashlib
import os
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.durable_repository import (
    DurablePostgresLiveRepository,
    LiveConflictError,
    _public_payload,
)
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.models import LiveCredentialReference, live_uuid
from secure_eval_wrapper.live.readonly_preflight import build_authenticated_readonly_proof

from test_phase8_guarded_live import (
    ACCOUNT_FINGERPRINT,
    COMMIT,
    T0,
    config,
    exact_okx_bundle,
)

RUN = os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true"


@unittest.skipUnless(RUN, "requires real PostgreSQL 16")
class Phase8BPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row

        cls.connection = psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ["POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
            row_factory=dict_row,
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        self.connection.rollback()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE execution.live_authenticated_readonly_proofs,"
                "execution.live_okx_response_envelopes,execution.live_okx_response_bundles,"
                "execution.live_credential_references,execution.live_configuration_snapshots CASCADE"
            )
        self.connection.commit()
        self.repository = DurablePostgresLiveRepository(self.connection)
        self.configuration = replace(
            config(), credential_source_policy=("injected_local",)
        )
        payload = {
            name: getattr(self.configuration, name)
            for name in self.configuration.__dataclass_fields__
        }
        with self.repository.transaction():
            self.repository._strict_insert(
                "execution.live_configuration_snapshots",
                "configuration_snapshot_id",
                live_uuid("configuration", {"hash": self.configuration.configuration_hash}),
                (
                    "configuration_sha256", "provider", "environment", "account_fingerprint",
                    "dry_run", "read_only_preflight", "production_write_enabled",
                    "configuration_jsonb", "created_at_utc",
                ),
                (
                    self.configuration.configuration_hash, self.configuration.provider,
                    self.configuration.environment, self.configuration.account_fingerprint,
                    self.configuration.dry_run, self.configuration.read_only_preflight,
                    self.configuration.production_write_enabled, _public_payload(payload), T0,
                ),
                sha256_payload(payload),
            )

    def context(self):
        session = uuid4()
        bundle = exact_okx_bundle(
            session, "preflight", expected_account_fingerprint=ACCOUNT_FINGERPRINT
        )
        credential = LiveCredentialReference(
            "okx", f"phase8b-test-{session}", "injected_local",
            ACCOUNT_FINGERPRINT, True, T0, ("read",),
        )
        proof = build_authenticated_readonly_proof(
            proof_session_id=session,
            bundle=bundle,
            configuration=self.configuration,
            credential_reference=credential,
            expected_reviewed_sha=COMMIT,
            repository_identity=RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
            instrument="BTC-USDT",
            network_read_count=6,
            network_write_count=0,
        )
        return proof, bundle, credential

    def persist(self, proof, bundle, credential, **kwargs):
        return self.repository.persist_authenticated_readonly_proof(
            proof=proof,
            bundle=bundle,
            credential_reference=credential,
            configuration=self.configuration,
            created_at_utc=T0,
            **kwargs,
        )

    def count(self, table):
        with self.connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) AS count FROM execution.{table}")
            return cursor.fetchone()["count"]

    def test_migration_0026_catalog_and_phase8b_table_are_installed(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "db" / "migrations" / "0026_phase8b_authenticated_readonly_preflight.sql"
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT sha256 FROM audit.schema_migrations "
                "WHERE migration_id='0026_phase8b_authenticated_readonly_preflight'"
            )
            row = cursor.fetchone()
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='execution' AND table_name='live_authenticated_readonly_proofs'"
            )
            columns = {item["column_name"] for item in cursor.fetchall()}
        self.assertEqual(
            row["sha256"],
            hashlib.sha256(migration.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        )
        self.assertIn("public_proof_jsonb", columns)
        self.assertIn("record_sha256", columns)

    def test_atomic_persistence_exact_replay_reload_and_conflict(self):
        proof, bundle, credential = self.context()
        self.assertEqual(self.persist(proof, bundle, credential), proof.proof_id)
        self.assertEqual(self.persist(proof, bundle, credential), proof.proof_id)
        self.assertEqual(self.count("live_authenticated_readonly_proofs"), 1)
        reloaded = self.repository.load_authenticated_readonly_proof(proof.proof_id)
        self.assertEqual(reloaded.public_payload(), proof.public_payload())
        conflicting = replace(proof, warnings=("changed",), record_hash=None)
        with self.assertRaises(LiveConflictError):
            self.persist(conflicting, bundle, credential)
        self.connection.rollback()
        self.assertEqual(self.count("live_authenticated_readonly_proofs"), 1)

    def test_each_injected_failure_rolls_back_bundle_credential_and_proof(self):
        for failure in ("bundle", "credential", "proof"):
            with self.subTest(failure=failure):
                self.setUp()
                proof, bundle, credential = self.context()
                with self.assertRaises(RuntimeError):
                    self.persist(proof, bundle, credential, fail_at=failure)
                self.connection.rollback()
                self.assertEqual(self.count("live_authenticated_readonly_proofs"), 0)
                self.assertEqual(self.count("live_okx_response_bundles"), 0)
                self.assertEqual(self.count("live_credential_references"), 0)

    def test_direct_sql_cannot_promote_fake_transport_to_passed_proof(self):
        proof, bundle, credential = self.context()
        attacked = proof.public_payload()
        attacked["status"] = "passed"
        attacked["evidence_classification"] = "operational_collector"
        attacked["record_hash"] = sha256_payload(
            {key: value for key, value in attacked.items() if key != "record_hash"}
        )
        with self.assertRaises(Exception):
            with self.repository.transaction():
                self.repository._persist_okx_bundle(bundle)
                self.repository._strict_insert(
                    "execution.live_credential_references", "credential_reference_id",
                    credential.reference_id,
                    (
                        "provider", "alias", "source_type", "account_fingerprint", "loaded",
                        "verified_at_utc", "permission_summary_jsonb", "created_at_utc",
                    ),
                    (
                        credential.provider, credential.alias, credential.source_type,
                        credential.account_fingerprint, credential.loaded,
                        credential.verified_at_utc, _public_payload(credential.permission_summary), T0,
                    ),
                    credential.record_hash,
                )
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO execution.live_authenticated_readonly_proofs ("
                        "proof_id,proof_session_id,response_bundle_id,configuration_sha256,"
                        "credential_reference_id,expected_reviewed_sha,observed_repository_sha,"
                        "repository_identity_source,account_fingerprint,credential_source,"
                        "provider_permissions_jsonb,normalized_permissions_jsonb,instrument_id,"
                        "query_started_at_utc,query_completed_at_utc,venue_time_at_utc,"
                        "clock_skew_milliseconds,network_read_count,evidence_classification,status,"
                        "public_proof_jsonb,record_sha256,created_at_utc) VALUES ("
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            proof.proof_id, proof.proof_session_id, proof.response_bundle_id,
                            proof.configuration_hash, proof.credential_reference_id,
                            proof.expected_reviewed_sha, proof.observed_repository_sha,
                            proof.repository_identity_source, proof.account_fingerprint,
                            proof.credential_source, _public_payload(proof.provider_permissions),
                            _public_payload(proof.normalized_permissions), proof.instrument_id,
                            proof.query_started_at_utc, proof.query_completed_at_utc,
                            proof.venue_time_at_utc, proof.clock_skew_milliseconds,
                            proof.network_read_count, "operational_collector", "passed",
                            _public_payload(attacked), attacked["record_hash"], T0,
                        ),
                    )
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self.connection.rollback()
        self.assertEqual(self.count("live_authenticated_readonly_proofs"), 0)

    def test_public_proof_contains_only_aggregates_while_raw_evidence_stays_private(self):
        proof, bundle, credential = self.context()
        self.persist(proof, bundle, credential)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT public_proof_jsonb FROM execution.live_authenticated_readonly_proofs "
                "WHERE proof_id=%s", (proof.proof_id,)
            )
            public = cursor.fetchone()["public_proof_jsonb"]
            cursor.execute(
                "SELECT raw_response_jsonb FROM execution.live_okx_response_envelopes "
                "WHERE response_bundle_id=%s AND endpoint_kind='balances'", (bundle.bundle_id,)
            )
            private = cursor.fetchone()["raw_response_jsonb"]
        encoded_public = str(public)
        self.assertNotIn("availEq", encoded_public)
        self.assertNotIn("frozenBal", encoded_public)
        self.assertIn("availEq", str(private))
        self.assertEqual(public["balance_currencies"], ["BTC", "USDT"])


if __name__ == "__main__":
    unittest.main()