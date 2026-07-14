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
    if phases["phase_6_monitoring_simulated_fix_api"]["status"] not in {"in_progress", "completed"}:
        raise RuntimeError("Phase 6 must be in progress or completed during the Phase 6 milestone")
    if phases["phase_7_paper_trading"]["status"] not in {"in_progress", "completed"}:
        raise RuntimeError("Phase 7 must be in progress or completed during the paper milestone")
    if phases["phase_8_guarded_live_execution"]["status"] != "in_progress":
        raise RuntimeError("Phase 8A must remain in progress")
    if status["current_phase"] != "phase_8_guarded_live_execution" or status["rules"]["runtime_features_allowed_in_current_phase"] is not True:
        raise RuntimeError("Phase 8A runtime status fields are not synchronized")
    print("OK: implementation status JSON structure and fixed boundaries")


_STATIC_FORBIDDEN = {
    "authoritative SQLite runtime": re.compile(r"(^|\n)\s*(import sqlite3|from sqlite3)", re.I),
    "SQLite connection URI": re.compile(r"sqlite(?:\+\w+)?://", re.I),
    "live broker implementation": re.compile(r"class\s+LiveBroker\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
}
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(?:[A-Z0-9_]*(?:API_?KEY|API_?SECRET|ACCESS_?TOKEN|AUTH_?TOKEN|SECRET_?KEY|PRIVATE_?KEY))[ \t]*[:=][ \t]*(?:\"([^\"]+)\"|'([^']+)'|([A-Z0-9_./+=:-]+))[ \t]*(?:#.*)?$"
)
_EXCHANGE_CREDENTIAL = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(?:BINANCE|OKX|BYBIT|COINBASE)_(?:API_KEY|API_SECRET|SECRET_KEY|PASSPHRASE)[ \t]*[:=][ \t]*(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))[ \t]*(?:#.*)?$"
)
_AUTHENTICATED_ENDPOINT = re.compile(
    r"/(?:api/v3/(?:account|order)|fapi/v1/(?:account|order)|api/v5/(?:account|trade)/(?:account|order|orders?))\b",
    re.I,
)
_PLACEHOLDER = re.compile(r"(?:placeholder|example|dummy|test|changeme|not[_-]?set|phase5_ci_only|\$\{|\$env:|os\.environ|none|null)", re.I)
_TEXT_SCAN_SUFFIXES = {".py", ".ps1", ".sh", ".yml", ".yaml", ".toml", ".json", ".env", ".ini", ".cfg"}


def _non_placeholder(value: str) -> bool:
    cleaned = value.strip().strip("\"'")
    return bool(cleaned) and _PLACEHOLDER.search(cleaned) is None


def content_boundary_findings(path: Path, text: str) -> list[str]:
    findings = []
    for label, pattern in _STATIC_FORBIDDEN.items():
        if pattern.search(text):
            findings.append(label)
    if path.suffix.lower() in _TEXT_SCAN_SUFFIXES or path.name.startswith(".env"):
        for match in _CREDENTIAL_ASSIGNMENT.finditer(text):
            value = next(group for group in match.groups() if group is not None)
            if _non_placeholder(value):
                findings.append("non-placeholder token/API-key/secret assignment")
                break
        for match in _EXCHANGE_CREDENTIAL.finditer(text):
            value = next(group for group in match.groups() if group is not None)
            if _non_placeholder(value):
                findings.append("non-placeholder exchange credential assignment")
                break
        approved_endpoint_contract = path.as_posix().endswith((
            "paper/endpoints.py", "paper/venues/official_sandbox.py",
            "live/endpoints.py", "live/collector_evidence.py", "live/venues/okx_live.py", "live/broker.py",
        ))
        endpoint_scan_text = text
        if path.as_posix().endswith("live/durable_repository.py"):
            endpoint_scan_text = endpoint_scan_text.replace("/api/v5/trade/order", "")
        if "tests" not in {part.lower() for part in path.parts} and not approved_endpoint_contract and _AUTHENTICATED_ENDPOINT.search(endpoint_scan_text):
            findings.append("authenticated exchange account/order endpoint")
    return findings


def tracked_path_boundary_findings(names: list[str]) -> list[str]:
    findings = []
    for raw in names:
        name = raw.replace("\\", "/")
        lowered = name.lower()
        parts = lowered.split("/")
        basename = parts[-1]
        if lowered.startswith(("var/cache/", "var/raw/", "var/tmp/", "var/logs/", "var/postgres/", "var/private/")):
            findings.append(f"tracked generated/private path: {name}")
        if any(re.fullmatch(r"(?:private[_-]?strateg(?:y|ies)|proprietary|confidential)", part) for part in parts):
            findings.append(f"tracked private strategy path: {name}")
        if re.search(r"(?:real[_-]?)?(?:account|orders?|fills?|trades?)[_-](?:export|dump|logs?)(?:\.|_)", basename):
            findings.append(f"tracked account/trade export or log: {name}")
        if basename.endswith((".dump", ".backup", ".bak")) or re.match(r"(?:pg|postgres(?:ql)?)[_-]?dump.*\.sql$", basename):
            findings.append(f"tracked database dump: {name}")
        if basename == ".env" or (basename.startswith(".env.") and basename not in {".env.example", ".env.template"}):
            findings.append(f"tracked environment credential file: {name}")
        if re.search(r"(?:credentials?|secrets?|api[_-]?keys?)\.(?:json|ya?ml|toml|ini|txt)$", basename):
            findings.append(f"tracked credential file: {name}")
        if any(part in {"pgdata", "postgres-data", ".postgres"} for part in parts) or basename == "pg_version":
            findings.append(f"tracked generated PostgreSQL state: {name}")
    return findings


def boundary_scan() -> None:
    files = []
    findings = []
    this_file = Path(__file__).resolve()
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", "var", ".venv"} for part in path.parts):
            continue
        files.append(path)
        if path.resolve() == this_file:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(f"{label}: {path.relative_to(ROOT)}" for label in content_boundary_findings(path, text))
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    findings.extend(tracked_path_boundary_findings(tracked))
    if findings:
        raise RuntimeError("boundary scan failed:\n" + "\n".join(sorted(set(findings))))
    print(f"OK: strengthened boundary scan inspected {len(files)} files and {len(tracked)} tracked paths")


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
    _run(python, str(OPEN_CORE / "scripts" / "run_public_monitoring.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_simulated_fix.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_paper_preflight.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_internal_paper.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_paper_sandbox.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_paper_status.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_paper_kill.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_paper_reconcile.py"))
    _run(python, str(OPEN_CORE / "scripts" / "run_live_preflight.py"), "--help")
    _run(python, str(OPEN_CORE / "scripts" / "run_live_dry_run.py"), "--help")
    _run(python, str(OPEN_CORE / "scripts" / "run_live_status.py"), "--help")
    _run(python, str(OPEN_CORE / "scripts" / "run_live_reconcile.py"), "--help")
    _run(python, str(OPEN_CORE / "scripts" / "run_live_kill.py"), "--help")
    _run(python, str(OPEN_CORE / "scripts" / "verify_postgres_schema.py"), "--migration-only")
    validate_status()
    boundary_scan()
    print("OK: cross-platform validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
