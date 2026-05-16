from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

from agentic_swmm.config import mcp_registry_path
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


def _worktree_install_detail(root: Path) -> str | None:
    """Return a WARN detail string when ``root`` looks like a worktree.

    Two signals (either is enough):

    * The path contains ``.claude/worktrees/`` — Claude Code's worktree
      layout. This is the common footgun: ``pip install -e .`` was run
      from inside a temporary worktree and the runtime stays pinned to
      that branch's snapshot.
    * ``<root>/.git`` is a file (not a directory) — the canonical git
      worktree marker that points into another ``.git`` directory.

    Returns ``None`` for a normal checkout. Returns the WARN detail
    string (with remediation) otherwise.
    """

    posix = root.as_posix()
    if ".claude/worktrees/" in posix or _is_git_worktree(root):
        return (
            f"editable install points to a worktree at {root}. "
            "Re-run 'pip install -e .' from the main checkout to sync "
            "fixes."
        )
    return None


def _mcp_json_drift(root: Path) -> list[tuple[str, str]]:
    """Yield ``(server_name, detail)`` pairs for drifted MCP servers.

    Reads ``~/.aiswmm/mcp.json`` (or whatever ``AISWMM_CONFIG_DIR``
    overrides to) and for each server entry resolves the embedded
    launcher path. If the launcher is **not** under the active repo
    root, that server has drifted and gets a WARN row.

    Returns an empty list when mcp.json is absent or unreadable —
    that's the typical pre-``aiswmm setup`` state, not a drift.
    """

    try:
        path = mcp_registry_path()
    except Exception:  # pragma: no cover - defensive
        return []
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("mcp_servers")
    if not isinstance(records, list):
        return []
    try:
        active_root = root.resolve()
    except OSError:  # pragma: no cover - defensive
        active_root = root
    drifted: list[tuple[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name", "?"))
        launcher = _record_launcher(record)
        if launcher is None:
            continue
        try:
            launcher_resolved = launcher.resolve()
        except OSError:
            launcher_resolved = launcher
        if _is_under(launcher_resolved, active_root):
            continue
        drifted.append(
            (
                name,
                (
                    f"mcp.json routes {name} to a different checkout "
                    f"({launcher_resolved}). Re-run "
                    f"'aiswmm setup --refresh-mcp' to align with the "
                    f"active install, or sync that checkout manually."
                ),
            )
        )
    return drifted


def _record_launcher(record: dict) -> Path | None:
    """Best-effort extraction of an MCP server's launcher path.

    Prefers the explicit ``launcher`` key (set by ``discover_mcp_servers``
    today), then falls back to ``args[0]`` per the PRD's "embedded
    absolute path" description.
    """

    raw = record.get("launcher")
    if isinstance(raw, str) and raw:
        return Path(raw)
    args = record.get("args")
    if isinstance(args, list) and args:
        first = args[0]
        if isinstance(first, str) and first:
            return Path(first)
    return None


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_git_worktree(root: Path) -> bool:
    git_marker = root / ".git"
    # A normal checkout has ``.git`` as a directory; a worktree has it
    # as a file containing ``gitdir: <path-to-main-.git/worktrees/...>``.
    return git_marker.is_file()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("doctor", help="Check local runtime dependencies.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    root = repo_root()
    checks: list[tuple[str, bool, str, bool]] = []
    checks.append(("repo root", root.exists(), str(root), True))
    # Issue #113: warn when the editable install resolves into a
    # Claude Code worktree. pip install -e . inside .claude/worktrees/
    # pins the runtime to that branch's snapshot; main can move forward
    # while the user keeps running stale code. Non-fatal — the user
    # decides when to re-install.
    worktree_detail = _worktree_install_detail(root)
    if worktree_detail is not None:
        checks.append(("editable install", False, worktree_detail, False))
    # Issue #114: warn when ~/.aiswmm/mcp.json routes any MCP server to
    # a launcher path outside the active editable install. Each drifted
    # server gets its own WARN row so the user can see which servers
    # the runtime is actually loading from elsewhere.
    for server_name, drift_detail in _mcp_json_drift(root):
        checks.append(
            (f"mcp.json: {server_name}", False, drift_detail, False)
        )
    checks.append(("OPENAI_API_KEY", bool(os.environ.get("OPENAI_API_KEY")), "set" if os.environ.get("OPENAI_API_KEY") else "not set; needed for OpenAI agent planner mode", False))
    claude = shutil.which("claude")
    checks.append(("claude code CLI", claude is not None, claude or "not found; optional future provider", False))
    node = shutil.which("node")
    checks.append(("node executable", node is not None, node or "not found; needed for MCP server launchers", True))
    swmm = _which_swmm5()
    swmm_detail = f"{swmm}; {_swmm_version() or 'version unavailable'}" if swmm else "not found on PATH or repo .local/bin"
    checks.append(("swmm5 executable", swmm is not None, swmm_detail, True))
    for module in ("numpy", "matplotlib", "swmmtoolbox"):
        checks.append((f"python module: {module}", _module_available(module), "importable" if _module_available(module) else "missing", True))
    for path in (
        Path("skills/swmm-runner/scripts/swmm_runner.py"),
        Path("skills/swmm-experiment-audit/scripts/audit_run.py"),
        Path("skills/swmm-plot/scripts/plot_rain_runoff_si.py"),
        Path("skills/swmm-modeling-memory/scripts/summarize_memory.py"),
    ):
        full = root / path
        checks.append((str(path), full.exists(), str(full), True))

    ok = True
    for name, passed, detail, required in checks:
        ok = ok and (passed or not required)
        status = "OK" if passed else ("MISSING" if required else "WARN")
        print(f"{status:7} {name} - {detail}")
    return 0 if ok else 1
