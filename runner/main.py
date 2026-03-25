from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from system.orchestrator import Orchestrator, RunConfig
from system.pipeline import build_all_pipeline, build_generic_pipeline, build_quant_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Top-level system runner")
    parser.add_argument("--mode", choices=["quant", "generic", "all"], default="all")
    parser.add_argument("--python-exe", default=r"D:\qt\.python\python.exe")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-backoff-sec", type=float, default=1.0)
    parser.add_argument("--timeout-sec", type=int, default=1200)
    args = parser.parse_args()

    repo_root = REPO_ROOT

    if args.mode == "quant":
        stages = build_quant_pipeline(repo_root, args.python_exe)
    elif args.mode == "generic":
        stages = build_generic_pipeline(repo_root, args.python_exe)
    else:
        stages = build_all_pipeline(repo_root, args.python_exe)

    orchestrator = Orchestrator(
        repo_root=repo_root,
        config=RunConfig(
            retries=args.retries,
            retry_backoff_sec=args.retry_backoff_sec,
            timeout_sec=args.timeout_sec,
        ),
    )
    return orchestrator.run(stages)


if __name__ == "__main__":
    raise SystemExit(main())
