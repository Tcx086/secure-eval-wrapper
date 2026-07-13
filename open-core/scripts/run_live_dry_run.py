import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from secure_eval_wrapper.live.cli import dry_run_main

if __name__ == "__main__":
    raise SystemExit(dry_run_main())
