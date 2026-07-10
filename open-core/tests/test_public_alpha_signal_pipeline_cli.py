"""Offline CLI and Phase 3-4 security-boundary tests."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "open-core/scripts/run_public_alpha_signal_pipeline.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("public_alpha_signal_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublicAlphaSignalCliTests(unittest.TestCase):
    def test_default_fixture_runs_without_socket_or_database(self):
        cli = _load_cli()
        output = io.StringIO()
        with patch("socket.socket", side_effect=AssertionError("socket attempted")):
            with patch.object(cli, "_connect_postgres", side_effect=AssertionError("database attempted")):
                with redirect_stdout(output):
                    code = cli.main([])
        rendered = output.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("alpha=momentum@1.1.0", rendered)
        self.assertIn("alpha=funding_rate_contrarian@1.1.0", rendered)
        self.assertIn("signals=combined status=completed", rendered)
        self.assertIn("persistence=False", rendered)
        self.assertNotIn("source_observation_ids", rendered)
        self.assertNotIn("POSTGRES_PASSWORD", rendered)

    def test_persistence_requires_both_flag_and_environment_gate(self):
        cli = _load_cli()
        environment = dict(os.environ)
        environment.pop(cli.POSTGRES_PERSISTENCE_FLAG, None)
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(cli, "_connect_postgres", side_effect=AssertionError("database attempted")):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--persist"]), 2)

    def test_fixture_is_public_safe_and_has_required_regimes(self):
        path = ROOT / "open-core/data/sample/public_alpha_signal_sample.json"
        fixture = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(fixture["classification"], "synthetic_public_safe")
        self.assertEqual({row["symbol"] for row in fixture["ohlcv"]}, {"BTC-USDT", "ETH-USDT"})
        self.assertTrue(any(DecimalText == "0" for DecimalText in (row["rate"] for row in fixture["funding_rates"])))
        self.assertTrue(all(row["funding_interval_source"] == "provider_reported" for row in fixture["funding_rates"]))
        text = path.read_text(encoding="utf-8-sig").lower()
        for forbidden in ("api_key", "secretkey", "passphrase", "private_key"):
            self.assertNotIn(forbidden, text)

    def test_runtime_has_no_heavy_quant_or_execution_imports(self):
        roots = (
            ROOT / "open-core/src/secure_eval_wrapper/alpha",
            ROOT / "open-core/src/secure_eval_wrapper/signals",
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for root in roots
            for path in root.rglob("*.py")
        ).lower()
        for dependency in ("import pandas", "import numpy", "import scipy", "import talib", "import ccxt"):
            self.assertNotIn(dependency, combined)
        for boundary in ("secure_eval_wrapper.execution", "simulatedbroker", "broker api", "account snapshot"):
            self.assertNotIn(boundary, combined)


if __name__ == "__main__":
    unittest.main()
