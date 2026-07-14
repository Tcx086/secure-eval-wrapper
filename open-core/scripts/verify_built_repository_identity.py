"""Verify that one built wheel contains collector-derived repository identity."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


WHEEL_MEMBER = "secure_eval_wrapper/live/_build_repository_identity.json"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_built_repository_identity.py WHEEL_DIRECTORY")
    wheel_directory = Path(sys.argv[1]).resolve()
    wheels = tuple(wheel_directory.glob("secure_eval_wrapper-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one secure-eval-wrapper wheel")

    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root / "open-core" / "src"))
    try:
        from secure_eval_wrapper.live.identity import collect_build_repository_metadata

        expected = collect_build_repository_metadata(source_root=repository_root)
    finally:
        sys.path.pop(0)

    with zipfile.ZipFile(wheels[0]) as archive:
        try:
            actual = json.loads(archive.read(WHEEL_MEMBER).decode("utf-8"))
        except KeyError as exc:
            raise SystemExit("wheel is missing immutable repository identity metadata") from exc
    if actual != expected:
        raise SystemExit("wheel repository identity does not match checked-out Git HEAD")
    print(
        json.dumps(
            {
                "commit_sha": actual["commit_sha"],
                "resolver_version": actual["resolver_version"],
                "wheel_member": WHEEL_MEMBER,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
