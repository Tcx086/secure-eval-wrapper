"""Cross-platform orchestration for the public repository's offline validation gates."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


OPEN_CORE = Path(__file__).resolve().parents[2]
ROOT = OPEN_CORE.parent


def _run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def validate_status() -> None:
    status = json.loads((ROOT / ".project" / "implementation_status.json").read_text(encoding="utf-8-sig"))
    required = {"schema_version", "project", "repository", "status_source", "schema", "updated_at_utc", "current_phase", "rules", "storage", "live_trading", "privacy", "control_files", "phases"}
    if set(status) != required:
        raise RuntimeError("implementation status top-level schema mismatch")
    if status["storage"]["authoritative_storage"] != "PostgreSQL" or status["storage"]["sqlite_authoritative_storage_allowed"] is not False:
        raise RuntimeError("PostgreSQL-only status boundary changed")
    if status["live_trading"]["enabled_by_default"] is not False:
        raise RuntimeError("live trading must remain disabled")
    phases = {row["id"]: row for row in status["phases"]}
    if status["current_phase"] not in phases:
        raise RuntimeError("current_phase does not identify a declared phase")
    print("OK: implementation status JSON structure and fixed boundaries")


def boundary_scan() -> None:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", "var", ".venv"} for part in path.parts):
            continue
        files.append(path)
    forbidden_content = {
        "authoritative SQLite runtime": re.compile(r"(^|\n)\s*(import sqlite3|from sqlite3)", re.I),
        "SQLite connection URI": re.compile(r"sqlite(?:\+\w+)?://", re.I),
        "paper broker implementation": re.compile(r"class\s+PaperBroker\b"),
        "live broker implementation": re.compile(r"class\s+LiveBroker\b"),
        "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    }
    findings = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in forbidden_content.items():
            if pattern.search(text):
                findings.append(f"{label}: {path.relative_to(ROOT)}")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    bad_paths = [name for name in tracked if name.replace("\\", "/").startswith(("var/cache/", "var/raw/", "var/tmp/", "var/logs/", "var/postgres/", "var/private/"))]
    findings.extend(f"tracked generated/private path: {name}" for name in bad_paths)
    if findings:
        raise RuntimeError("boundary scan failed:\n" + "\n".join(findings))
    print(f"OK: boundary scan inspected {len(files)} files")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run cross-platform secure-eval validation.")
    parser.add_argument("--phase5-only", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    python = sys.executable
    if not args.skip_tests:
        pattern = "test_phase5*.py" if args.phase5_only else "test_*.py"
        _run(python, "-m", "unittest", "discover", "-s", str(OPEN_CORE / "tests"), "-p", pattern)
    _run(python, "-m", "compileall", "-q", str(OPEN_CORE / "src"), str(OPEN_CORE / "scripts"), str(OPEN_CORE / "tests"))
    _run(python, str(OPEN_CORE / "scripts" / "run_public_ohlcv_pipeline.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_public_market_data_pipeline.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_public_alpha_signal_pipeline.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_public_backtest_pipeline.py"))
    _run(python, str(OPEN_CORE / "scripts" / "verify_postgres_schema.py"), "--migration-only")
    validate_status()
    boundary_scan()
    print("OK: cross-platform validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
