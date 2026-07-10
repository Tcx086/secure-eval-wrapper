"""Offline CLI and security-boundary tests for the bundled public data layer."""

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
SCRIPT = ROOT / "open-core/scripts/run_public_market_data_pipeline.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("phase2_complete_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublicMarketDataCliTests(unittest.TestCase):
    def test_default_fixture_runs_complete_vertical_without_socket(self):
        cli = _load_cli()
        output = io.StringIO()
        with patch("socket.socket", side_effect=AssertionError("socket attempted")):
            with redirect_stdout(output):
                code = cli.main([])
        rendered = output.getvalue()
        self.assertEqual(code, 0)
        for data_type in ("ohlcv", "trades", "funding_rates", "instruments"):
            self.assertIn(f"data_type={data_type}", rendered)
        self.assertIn("provider=binance_usdm", rendered)
        self.assertIn("accepted=2", rendered)
        self.assertNotIn("provider_payload", rendered)
        self.assertNotIn("POSTGRES_PASSWORD", rendered)

    def test_public_network_and_postgres_are_independently_gated(self):
        cli = _load_cli()
        environment = dict(os.environ)
        environment.pop(cli.PUBLIC_NETWORK_FLAG, None)
        environment.pop(cli.POSTGRES_PERSISTENCE_FLAG, None)
        with patch.dict(os.environ, environment, clear=True):
            with patch("socket.socket", side_effect=AssertionError("socket attempted")):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--mode", "public-network"]), 2)
            with patch.object(cli, "_connect_postgres", side_effect=AssertionError("DB attempted")):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--persist"]), 2)

    def test_fixture_is_bounded_synthetic_and_contains_no_credentials(self):
        fixture_path = ROOT / "open-core/data/sample/public_market_data_bundle_sample.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(fixture["classification"], "synthetic_public_safe")
        text = fixture_path.read_text(encoding="utf-8-sig").lower()
        for forbidden in ("api_key", "secretkey", "passphrase", "private_key"):
            self.assertNotIn(forbidden, text)

    def test_public_provider_modules_contain_no_forbidden_http_dependencies(self):
        source_root = ROOT / "open-core/src/secure_eval_wrapper/data_collection"
        combined = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in source_root.glob("*.py")
        ).lower()
        for dependency in ("import requests", "import httpx", "import aiohttp", "import ccxt"):
            self.assertNotIn(dependency, combined)
        self.assertNotIn("x-mbx-apikey", combined)


if __name__ == "__main__":
    unittest.main()
