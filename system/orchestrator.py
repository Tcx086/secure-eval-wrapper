from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from system.pipeline import Stage


@dataclass
class RunConfig:
    retries: int = 1
    retry_backoff_sec: float = 1.0
    timeout_sec: int = 1200


class Orchestrator:
    def __init__(self, repo_root: Path, config: RunConfig):
        self.repo_root = repo_root
        self.config = config
        self.logs_dir = repo_root / "delivery" / "orchestrator"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _run_once(self, stage: Stage) -> Dict[str, object]:
        started = time.time()
        proc = subprocess.run(
            stage.command,
            cwd=str(stage.cwd),
            capture_output=True,
            text=True,
            timeout=self.config.timeout_sec,
            check=False,
        )
        elapsed = time.time() - started
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_sec": round(elapsed, 3),
        }

    def run(self, stages: List[Stage]) -> int:
        summary: Dict[str, object] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "stages": [],
        }

        for stage in stages:
            attempts = 0
            success = False
            last_result: Dict[str, object] = {}

            max_attempts = 1 + max(0, self.config.retries if stage.retryable else 0)
            while attempts < max_attempts:
                attempts += 1
                result = self._run_once(stage)
                last_result = result

                log_name = f"{self._timestamp()}_{stage.name}_attempt{attempts}.log"
                log_path = self.logs_dir / log_name
                log_path.write_text(
                    "\n".join(
                        [
                            f"stage={stage.name}",
                            f"attempt={attempts}",
                            f"returncode={result['returncode']}",
                            f"elapsed_sec={result['elapsed_sec']}",
                            "--- stdout ---",
                            str(result["stdout"]),
                            "--- stderr ---",
                            str(result["stderr"]),
                        ]
                    ),
                    encoding="utf-8",
                )

                if int(result["returncode"]) == 0:
                    success = True
                    break

                if attempts < max_attempts:
                    time.sleep(self.config.retry_backoff_sec * attempts)

            summary["stages"].append(
                {
                    "name": stage.name,
                    "attempts": attempts,
                    "success": success,
                    "elapsed_sec": last_result.get("elapsed_sec", None),
                    "last_returncode": last_result.get("returncode", None),
                }
            )

            if not success:
                summary_path = self.logs_dir / f"run_summary_{self._timestamp()}.json"
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"status": "failed", "summary": str(summary_path)}, ensure_ascii=False, indent=2))
                return 1

        summary_path = self.logs_dir / f"run_summary_{self._timestamp()}.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "ok", "summary": str(summary_path)}, ensure_ascii=False, indent=2))
        return 0
