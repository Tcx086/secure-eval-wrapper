from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_code_hashes(project_root: Path, rel_paths: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel in rel_paths:
        p = project_root / rel
        if p.exists():
            out[rel] = file_sha256(p)
    return out


def build_manifest(
    strategy: str,
    input_path: Path,
    config: Dict[str, object],
    code_hashes: Dict[str, str],
) -> Dict[str, object]:
    config_bytes = json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_name": strategy,
        "input_file": str(input_path),
        "input_sha256": file_sha256(input_path),
        "config": config,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "code_hashes": code_hashes,
        "repro_note": (
            "Use the same input snapshot, config, and code hashes to reproduce "
            "the same deterministic demo results."
        ),
    }

