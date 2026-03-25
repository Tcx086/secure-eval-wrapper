from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class Stage:
    name: str
    command: List[str]
    cwd: Path
    retryable: bool = True


def build_quant_pipeline(repo_root: Path, python_exe: str) -> List[Stage]:
    open_core = repo_root / "open-core"
    return [
        Stage(
            name="signal_demo",
            command=[
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(open_core / "scripts" / "run_demo.ps1"),
                "-PythonExe",
                python_exe,
            ],
            cwd=repo_root,
        ),
        Stage(
            name="eval_quant",
            command=[
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(open_core / "scripts" / "run_eval.ps1"),
                "-PythonExe",
                python_exe,
            ],
            cwd=repo_root,
        ),
        Stage(
            name="package_quant",
            command=[
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(open_core / "scripts" / "run_all.ps1"),
                "-PythonExe",
                python_exe,
            ],
            cwd=repo_root,
        ),
    ]


def build_generic_pipeline(repo_root: Path, python_exe: str) -> List[Stage]:
    open_core = repo_root / "open-core"
    return [
        Stage(
            name="eval_generic",
            command=[
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(open_core / "scripts" / "run_generic_demo.ps1"),
                "-PythonExe",
                python_exe,
            ],
            cwd=repo_root,
        )
    ]


def build_all_pipeline(repo_root: Path, python_exe: str) -> List[Stage]:
    return build_quant_pipeline(repo_root, python_exe) + build_generic_pipeline(repo_root, python_exe)
