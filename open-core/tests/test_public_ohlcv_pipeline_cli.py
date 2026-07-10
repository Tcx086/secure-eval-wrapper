"""Offline safety tests for the public OHLCV pipeline CLI."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "open-core" / "scripts" / "run_public_ohlcv_pipeline.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("public_ohlcv_pipeline_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublicOhlcvPipelineCliTests(unittest.TestCase):
    def test_default_offline_fixture_mode_runs_without_network(self) -> None:
        cli = _load_cli()
        output = io.StringIO()
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            with redirect_stdout(output):
                exit_code = cli.main([])

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("status=succeeded", rendered)
        self.assertIn("provider=binance", rendered)
        self.assertIn("provider=okx", rendered)
        self.assertIn("reconciliation=passed", rendered)
        self.assertIn("hashes_valid=True", rendered)
        self.assertNotIn("provider_payload", rendered)

    def test_public_network_mode_requires_explicit_environment_gate(self) -> None:
        cli = _load_cli()
        output = io.StringIO()
        environment = dict(os.environ)
        environment.pop(cli.PUBLIC_NETWORK_FLAG, None)
        with patch.dict(os.environ, environment, clear=True):
            with patch("socket.socket", side_effect=AssertionError("network access attempted")):
                with redirect_stdout(output):
                    exit_code = cli.main(["--mode", "public-network"])

        self.assertEqual(exit_code, 2)
        self.assertIn("disabled", output.getvalue())

    def test_postgres_requires_both_cli_and_environment_flags(self) -> None:
        cli = _load_cli()
        output = io.StringIO()
        environment = dict(os.environ)
        environment.pop(cli.POSTGRES_PERSISTENCE_FLAG, None)
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(
                cli,
                "_connect_postgres",
                side_effect=AssertionError("database connection attempted"),
            ):
                with redirect_stdout(output):
                    exit_code = cli.main(["--persist"])

        self.assertEqual(exit_code, 2)
        self.assertIn("PostgreSQL persistence is disabled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
