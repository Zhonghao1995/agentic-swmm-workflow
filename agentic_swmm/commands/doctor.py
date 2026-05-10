from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

from agentic_swmm.utils.paths import repo_root


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _swmm_version() -> str | None:
    exe = _which_swmm5()
    if not exe:
        return None
    env = os.environ.copy()
    env["PATH"] = f"{Path(exe).parent}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, env=env)
    text = (proc.stdout + "\n" + proc.stderr).strip()
    return text or "available"


def _which_swmm5() -> str | None:
    path_hit = shutil.which("swmm5")
    if path_hit:
        return path_hit
    local_bin = repo_root() / ".local" / "bin"
    for name in ("swmm5.exe", "runswmm.exe", "swmm5.cmd"):
        candidate = local_bin / name
        if candidate.exists():
            return str(candidate)
    return None


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("doctor", help="Check local runtime dependencies.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    root = repo_root()
    checks: list[tuple[str, bool, str]] = []
    checks.append(("repo root", root.exists(), str(root)))
    swmm = _which_swmm5()
    swmm_detail = f"{swmm}; {_swmm_version() or 'version unavailable'}" if swmm else "not found on PATH or repo .local/bin"
    checks.append(("swmm5 executable", swmm is not None, swmm_detail))
    for module in ("numpy", "matplotlib", "swmmtoolbox"):
        checks.append((f"python module: {module}", _module_available(module), "importable" if _module_available(module) else "missing"))
    for path in (
        Path("skills/swmm-runner/scripts/swmm_runner.py"),
        Path("skills/swmm-experiment-audit/scripts/audit_run.py"),
        Path("skills/swmm-plot/scripts/plot_rain_runoff_si.py"),
        Path("skills/swmm-modeling-memory/scripts/summarize_memory.py"),
    ):
        full = root / path
        checks.append((str(path), full.exists(), str(full)))

    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        status = "OK" if passed else "MISSING"
        print(f"{status:7} {name} - {detail}")
    return 0 if ok else 1
