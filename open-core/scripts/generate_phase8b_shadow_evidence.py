"""Generate the fixed-allowlist Phase 8B public shadow assurance artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from secure_eval_wrapper.live.shadow_evidence import build_public_shadow_evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-sha", required=True)
    parser.add_argument(
        "--output",
        default="docs/evidence/phase8b_shadow_assurance_public.json",
    )
    args = parser.parse_args(argv)
    payload = build_public_shadow_evidence(repository_sha=args.repository_sha)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"OK: wrote {path} sha256={payload['evidence_payload_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
