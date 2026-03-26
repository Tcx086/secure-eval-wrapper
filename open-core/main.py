from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OPEN_CORE_ROOT = Path(__file__).resolve().parent
if str(OPEN_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(OPEN_CORE_ROOT))

from src.data.loader import load_feature_file
from src.eval_cli import run_eval
from src.generic_eval_cli import main as _generic_main  # noqa: F401
from src.registry import get_strategy


def run_quant(repo_root: Path, python_exe_hint: str = "") -> dict:
    open_core = repo_root / "open-core"
    delivery = repo_root / "delivery"

    input_path = open_core / "data" / "sample" / "features.json"
    strategy = get_strategy("demo")
    signal = strategy.generate_signal(load_feature_file(str(input_path)))

    eval_result = run_eval(
        input_path=str(input_path),
        strategy_name="demo",
        out_dir=str(delivery / "demo-run"),
        seed=20260325,
        bars=280,
        mc_paths=200,
        mc_path_len=220,
    )

    signal_out = {
        "action": signal.action,
        "score": signal.score,
        "confidence": signal.confidence,
        "meta": signal.meta,
    }
    (delivery / "demo-run" / "signal_output.json").write_text(
        json.dumps(signal_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "mode": "quant",
        "signal": signal_out,
        "eval": eval_result,
        "python_exe_hint": python_exe_hint,
    }


def run_generic(repo_root: Path, python_exe_hint: str = "") -> dict:
    import subprocess
    import sys

    open_core = repo_root / "open-core"
    script = open_core / "scripts" / "run_generic_demo.ps1"
    cmd = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    if python_exe_hint:
        cmd.extend(["-PythonExe", python_exe_hint])

    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"generic demo failed: {proc.stderr.strip()}")

    return {
        "mode": "generic",
        "stdout": proc.stdout,
        "python_exe_hint": python_exe_hint or sys.executable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="System entrypoint for full evaluation pipeline")
    parser.add_argument("--mode", choices=["quant", "generic", "all"], default="all")
    parser.add_argument("--python-exe", default=sys.executable)
    args = parser.parse_args()

    open_core_root = OPEN_CORE_ROOT
    repo_root = open_core_root.parent
    out_dir = repo_root / "delivery" / "system-run"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "results": [],
    }

    if args.mode in {"quant", "all"}:
        summary["results"].append(run_quant(repo_root, args.python_exe))
    if args.mode in {"generic", "all"}:
        summary["results"].append(run_generic(repo_root, args.python_exe))

    summary_path = out_dir / "system_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
