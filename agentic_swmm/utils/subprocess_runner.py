from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root


@dataclass
class CommandResult:
    command: list[str]
    return_code: int
    started_at_utc: str
    finished_at_utc: str
    stdout: str
    stderr: str

    def as_trace(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "return_code": self.return_code,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
        }


class CommandFailed(RuntimeError):
    def __init__(self, result: CommandResult):
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        super().__init__(f"command failed with exit code {result.return_code}: {detail}")


def python_command(script: Path, *args: str) -> list[str]:
    return [sys.executable, str(script), *args]


def run_command(command: list[str], *, check: bool = True) -> CommandResult:
    started = datetime.now(timezone.utc)
    proc = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, env=runtime_env())
    finished = datetime.now(timezone.utc)
    result = CommandResult(
        command=command,
        return_code=proc.returncode,
        started_at_utc=started.isoformat(timespec="seconds"),
        finished_at_utc=finished.isoformat(timespec="seconds"),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and proc.returncode != 0:
        raise CommandFailed(result)
    return result


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    local_bin = repo_root() / ".local" / "bin"
    if local_bin.exists():
        env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def append_trace(path: Path, result: CommandResult, *, stage: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    entries = payload.get("commands")
    if not isinstance(entries, list):
        entries = []
    trace = result.as_trace()
    trace["stage"] = stage
    entries.append(trace)
    payload["schema_version"] = "1.0"
    payload["commands"] = entries
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
