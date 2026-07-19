from __future__ import annotations

import io
import json
import sys
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from secure_eval_wrapper.live import shadow_cli
from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository
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

    def _exercise_cli(self, transport, *, persistence_failure=False, serialization_failure=False):
        connection = FakeConnection()
        repository = MemoryShadowRepository()
        if persistence_failure:
            repository.persist_bundle = Mock(side_effect=RuntimeError("private persistence detail"))
        output = io.StringIO()
        patches = (
            patch.object(
                shadow_cli,
                "_connect",
                return_value=("secure_eval_phase8b_shadow_cli_test", "127.0.0.1", connection),
            ),
            patch.object(shadow_cli, "PostgresShadowRepository", return_value=repository),
            patch(
                "secure_eval_wrapper.live.shadow_runtime.UrlLibHttpTransport",
                return_value=transport,
            ),
        )
        with patches[0], patches[1], patches[2], redirect_stdout(output):
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

    def test_success_source_runtime_persistence_and_serialization_paths_preserve_six_facts(self):
        cases = (
            ("success", QueueTransport(_instrument_response(), _trade_response()), False, False, 0, 2),
            ("source_failure", QueueTransport(TimeoutError("private source detail")), False, False, 2, 1),
            ("persistence_failure", QueueTransport(_instrument_response(), _trade_response()), True, False, 2, 2),
            ("serialization_failure", QueueTransport(_instrument_response(), _trade_response()), False, True, 2, 2),
        )
        for name, transport, persistence_failure, serialization_failure, expected_code, reads in cases:
            with self.subTest(name=name):
                result, payload, raw = self._exercise_cli(
                    transport,
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
