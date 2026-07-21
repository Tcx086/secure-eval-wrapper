from __future__ import annotations

import io
import json
import sys
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from secure_eval_wrapper.live import shadow_cli
from secure_eval_wrapper.live import shadow_runtime as shadow_runtime_module
from secure_eval_wrapper.live.shadow_models import (
    ShadowDataProvenance,
    ShadowRunSummary,
    ShadowSafetyFacts,
)
from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository
from secure_eval_wrapper.live.shadow_runtime import ShadowAssuranceRuntime, ShadowOperationFailure
from secure_eval_wrapper.data_collection import HttpResponse
from secure_eval_wrapper.live.shadow_scenarios import SHADOW_FIXTURE_TIME


def _response(payload: object) -> HttpResponse:
    return HttpResponse(200, json.dumps(payload).encode("utf-8"), {})


def _instrument_response() -> HttpResponse:
    return _response({
        "code": "0",
        "msg": "",
        "data": [{
            "instType": "SPOT",
            "instId": "BTC-USDT",
            "baseCcy": "BTC",
            "quoteCcy": "USDT",
            "settleCcy": "",
            "state": "live",
            "tickSz": "0.1",
            "lotSz": "0.0001",
            "minSz": "0.0001",
            "ctVal": "",
            "ctMult": "",
            "ctType": "",
            "ctValCcy": "",
            "instFamily": "",
            "uly": "",
            "listTime": "1609459200000",
            "expTime": "",
            "ruleType": "normal",
            "upcChg": [],
        }],
    })


def _trade_response() -> HttpResponse:
    return _response({
        "code": "0",
        "msg": "",
        "data": [{
            "instId": "BTC-USDT",
            "side": "buy",
            "sz": "0.001",
            "source": "0",
            "px": "50000",
            "tradeId": "phase8b-public-1",
            "ts": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        }],
    })


_PUBLIC_ENDPOINTS = (
    "GET /api/v5/public/instruments",
    "GET /api/v5/market/history-trades",
)


def _provenance(
    classification: str,
    reads: int,
    response_hashes: tuple[str, ...],
) -> ShadowDataProvenance:
    return ShadowDataProvenance(
        classification,
        _PUBLIC_ENDPOINTS[:reads],
        reads,
        response_hashes,
        "a" * 64,
        "b" * 64,
        None if classification == "public_network" else "connection_failure",
    )


def _public_failure(
    provenance: ShadowDataProvenance,
    *,
    stage: str,
    private_detail: str,
) -> ShadowOperationFailure:
    facts = ShadowSafetyFacts(provenance.network_read_count)

    def fail():
        raise RuntimeError(private_detail)

    try:
        shadow_runtime_module._run_public_operation(
            fail,
            safety_facts=facts,
            data_provenance=provenance,
            failure_stage=stage,
        )
    except ShadowOperationFailure as exc:
        return exc
    raise AssertionError("public operation failure was not raised")


def _public_summary() -> ShadowRunSummary:
    provenance = _provenance("public_network", 2, ("c" * 64, "d" * 64))
    return ShadowRunSummary(
        uuid4(),
        "public_network_okx_btc_usdt",
        "e" * 64,
        "f" * 64,
        None,
        True,
        (),
        1,
        "persisted",
        False,
        ShadowSafetyFacts(2),
        provenance,
    )


class QueueTransport:
    def __init__(self, *actions: object) -> None:
        self.actions = list(actions)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        action = self.actions[len(self.requests) - 1]
        if isinstance(action, BaseException):
            raise action
        return action


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _run_arguments() -> list[str]:
    return [
        "run",
        "--allow-public-network",
        "--postgres-database",
        "secure_eval_phase8b_shadow_cli_test",
        "--postgres-host",
        "127.0.0.1",
    ]


class Phase8BShadowCliBoundaryTests(unittest.TestCase):
    def test_remote_localhost_operator_and_unrelated_targets_are_rejected_preconnect(self):
        cases = (
            ("localhost", "secure_eval_phase8b_shadow_cli_test"),
            ("db.example.com", "secure_eval_phase8b_shadow_cli_test"),
            ("127.0.0.1", "postgres"),
            ("127.0.0.1", "operator_database"),
            ("127.0.0.1", "secure_eval_phase8b_shadow_BAD"),
        )
        fake_psycopg = SimpleNamespace(connect=Mock(side_effect=AssertionError("connect called")))
        for host, database in cases:
            with self.subTest(host=host, database=database):
                output = io.StringIO()
                with patch.dict(sys.modules, {"psycopg": fake_psycopg}), redirect_stdout(output):
                    result = shadow_cli.main([
                        "run",
                        "--postgres-database",
                        database,
                        "--postgres-host",
                        host,
                    ])
                payload = json.loads(output.getvalue())
                self.assertEqual(result, 2)
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["network_read_count"], 0)
        fake_psycopg.connect.assert_not_called()

    def test_password_flag_is_absent_and_valid_connect_uses_libpq_auth_only(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            shadow_cli._parser().parse_args(_run_arguments() + ["--postgres-password", "secret"])

        connection = FakeConnection()
        connect = Mock(return_value=connection)
        args = SimpleNamespace(
            postgres_database="secure_eval_phase8b_shadow_cli_test",
            postgres_host="::1",
            postgres_port=5432,
            postgres_user="shadow_user",
            postgres_sslmode="disable",
        )
        with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
            database, host, observed = shadow_cli._connect(args)
        self.assertEqual(database, args.postgres_database)
        self.assertEqual(host, "::1")
        self.assertIs(observed, connection)
        kwargs = connect.call_args.kwargs
        self.assertNotIn("password", kwargs)
        self.assertEqual(kwargs["host"], "::1")
        self.assertEqual(kwargs["dbname"], args.postgres_database)

    def _exercise_cli(
        self,
        *,
        public=False,
        source_failure=False,
        persistence_failure=False,
        serialization_failure=False,
    ):
        connection = FakeConnection()
        repository = MemoryShadowRepository()
        if persistence_failure:
            repository.persist_bundle = Mock(side_effect=RuntimeError("private persistence detail"))
        output = io.StringIO()
        arguments = (
            _run_arguments()
            if public
            else [
                "run",
                "--postgres-database",
                "secure_eval_phase8b_shadow_cli_test",
                "--postgres-host",
                "127.0.0.1",
            ]
        )
        source_context = (
            patch(
                "secure_eval_wrapper.data_collection.okx_v5_public."
                "OkxPublicProvider.fetch_instruments",
                side_effect=TimeoutError("private source detail"),
            )
            if source_failure
            else patch.dict({}, {})
        )
        with (
            patch.object(
                shadow_cli,
                "_connect",
                return_value=("secure_eval_phase8b_shadow_cli_test", "127.0.0.1", connection),
            ),
            patch.object(shadow_cli, "PostgresShadowRepository", return_value=repository),
            source_context,
            redirect_stdout(output),
        ):
            if serialization_failure:
                failing_json = SimpleNamespace(
                    dumps=Mock(side_effect=TypeError("private json detail")),
                    JSONEncoder=json.JSONEncoder,
                )
                with patch.object(shadow_cli, "json", failing_json):
                    result = shadow_cli.main(arguments)
            else:
                result = shadow_cli.main(arguments)
        self.assertTrue(connection.closed)
        return result, json.loads(output.getvalue()), output.getvalue()

    def _exercise_patched_public(self, outcome, *, serialization_failure=False):
        connection = FakeConnection()
        output = io.StringIO()
        runtime_patch = (
            patch.object(ShadowAssuranceRuntime, "run_public", side_effect=outcome)
            if isinstance(outcome, BaseException)
            else patch.object(ShadowAssuranceRuntime, "run_public", return_value=outcome)
        )
        with (
            patch.object(
                shadow_cli,
                "_connect",
                return_value=("secure_eval_phase8b_shadow_cli_test", "127.0.0.1", connection),
            ),
            patch.object(
                shadow_cli,
                "PostgresShadowRepository",
                return_value=MemoryShadowRepository(),
            ),
            runtime_patch,
            redirect_stdout(output),
        ):
            if serialization_failure:
                failing_json = SimpleNamespace(
                    dumps=Mock(side_effect=TypeError("private json detail")),
                    JSONEncoder=json.JSONEncoder,
                )
                with patch.object(shadow_cli, "json", failing_json):
                    result = shadow_cli.main(_run_arguments())
            else:
                result = shadow_cli.main(_run_arguments())
        self.assertTrue(connection.closed)
        return result, json.loads(output.getvalue()), output.getvalue()

    def test_public_reads_succeeded_but_persistence_failed_keeps_truthful_two(self):
        provenance = _provenance("public_network", 2, ("4" * 64, "5" * 64))
        facts = ShadowSafetyFacts(2)
        repository = MemoryShadowRepository()
        repository.persist_bundle = Mock(
            side_effect=RuntimeError("private persistence connection and local path detail")
        )
        runtime = ShadowAssuranceRuntime(
            repository=repository,
            market_source=shadow_runtime_module.FixtureShadowMarketSource(),
        )
        decision = SimpleNamespace(
            safety_facts=facts,
            data_provenance=provenance,
        )

        with self.assertRaises(ShadowOperationFailure) as raised:
            runtime._persist_and_summarize(decision, crash_at=None)

        failure = raised.exception
        self.assertEqual(failure.failure_stage, "persistence")
        self.assertEqual(failure.failure_code, "shadow_public_persistence_failed")
        self.assertEqual(failure.safety_facts.network_read_count, 2)
        self.assertIs(failure.data_provenance, provenance)
        self.assertNotIn("private", str(failure))
        self.assertIsNone(failure.__cause__)
        self.assertIsNone(failure.__context__)
        repository.persist_bundle.assert_called_once_with(decision, crash_at=None)

    def test_public_persistence_failure_keeps_two_reads_and_never_leaks_detail(self):
        failure = _public_failure(
            _provenance("public_network", 2, ("c" * 64, "d" * 64)),
            stage="persistence",
            private_detail="private repository path and database detail",
        )
        result, payload, raw = self._exercise_patched_public(failure)

        self.assertEqual(result, 2)
        self.assertEqual(payload["blockers"], ["shadow_public_persistence_failed"])
        self.assertEqual(payload["failure_stage"], "persistence")
        self.assertEqual(payload["network_read_count"], 2)
        for key in (
            "network_write_count",
            "production_transport_call_count",
            "authenticated_endpoint_call_count",
            "credential_read_count",
            "production_write_count",
        ):
            self.assertEqual(payload[key], 0)
        self.assertNotIn("private", raw)
        self.assertIsNone(failure.__cause__)
        self.assertIsNone(failure.__context__)
        with self.assertRaises(FrozenInstanceError):
            failure.failure_stage = "runtime"

    def test_runtime_and_unavailable_downstream_failures_keep_authoritative_provenance(self):
        cases = (
            ("runtime", _provenance("public_network", 2, ("1" * 64, "2" * 64))),
            ("persistence", _provenance("unavailable", 1, ())),
            ("persistence", _provenance("unavailable", 2, ("3" * 64,))),
        )
        for stage, provenance in cases:
            with self.subTest(stage=stage, reads=provenance.network_read_count):
                failure = _public_failure(
                    provenance,
                    stage=stage,
                    private_detail="private downstream detail",
                )
                self.assertEqual(
                    failure.safety_facts.network_read_count,
                    provenance.network_read_count,
                )
                self.assertIs(failure.data_provenance, provenance)
                self.assertEqual(failure.data_provenance.response_source_hashes, provenance.response_source_hashes)
                self.assertEqual(failure.safety_facts.network_write_count, 0)
                self.assertEqual(failure.safety_facts.production_transport_call_count, 0)
                self.assertEqual(failure.safety_facts.authenticated_endpoint_call_count, 0)
                self.assertEqual(failure.safety_facts.credential_read_count, 0)
                self.assertEqual(failure.safety_facts.production_write_count, 0)
                self.assertNotIn("private", str(failure))

    def test_serialization_failure_after_public_summary_keeps_two_reads(self):
        result, payload, raw = self._exercise_patched_public(
            _public_summary(), serialization_failure=True
        )
        self.assertEqual(result, 2)
        self.assertEqual(payload["blockers"], ["shadow_result_serialization_failed"])
        self.assertEqual(payload["network_read_count"], 2)
        for key in (
            "network_write_count",
            "production_transport_call_count",
            "authenticated_endpoint_call_count",
            "credential_read_count",
            "production_write_count",
        ):
            self.assertEqual(payload[key], 0)
        self.assertNotIn("private", raw)

    def test_success_source_runtime_persistence_and_serialization_paths_preserve_six_facts(self):
        cases = (
            ("fixture_success", False, False, False, False, 0, 0),
            ("public_local_failure", True, True, False, False, 2, 0),
            ("fixture_persistence_failure", False, False, True, False, 2, 0),
            ("fixture_serialization_failure", False, False, False, True, 2, 0),
        )
        for (
            name,
            public,
            source_failure,
            persistence_failure,
            serialization_failure,
            expected_code,
            reads,
        ) in cases:
            with self.subTest(name=name):
                result, payload, raw = self._exercise_cli(
                    public=public,
                    source_failure=source_failure,
                    persistence_failure=persistence_failure,
                    serialization_failure=serialization_failure,
                )
                self.assertEqual(result, expected_code)
                self.assertEqual(payload["network_read_count"], reads)
                for key in (
                    "network_write_count",
                    "production_transport_call_count",
                    "authenticated_endpoint_call_count",
                    "credential_read_count",
                    "production_write_count",
                ):
                    self.assertEqual(payload[key], 0)
                self.assertNotIn("private", raw)


if __name__ == "__main__":
    unittest.main()
