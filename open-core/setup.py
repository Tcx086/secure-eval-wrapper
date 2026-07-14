"""Setuptools hooks for collector-derived immutable build identity."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithRepositoryIdentity(build_py):
    """Embed the checked-out Git HEAD in the built package, never a caller SHA."""

    def _metadata_output(self) -> Path:
        return (
            Path(self.build_lib)
            / "secure_eval_wrapper"
            / "live"
            / "_build_repository_identity.json"
        )

    def run(self) -> None:
        super().run()
        package_root = Path(__file__).resolve().parent
        repository_root = package_root.parent
        sys.path.insert(0, str(package_root / "src"))
        try:
            from secure_eval_wrapper.live.identity import collect_build_repository_metadata

            payload = collect_build_repository_metadata(source_root=repository_root)
        finally:
            sys.path.pop(0)
        output = self._metadata_output()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def get_outputs(self, include_bytecode: int = 1) -> list[str]:
        outputs = list(super().get_outputs(include_bytecode=include_bytecode))
        output = str(self._metadata_output())
        return outputs if output in outputs else [*outputs, output]

setup(
    cmdclass={"build_py": BuildPyWithRepositoryIdentity},
    options={
        "build": {
            "build_base": str(Path(__file__).resolve().parent.parent / "var" / "tmp" / "package-build")
        }
    },
)
