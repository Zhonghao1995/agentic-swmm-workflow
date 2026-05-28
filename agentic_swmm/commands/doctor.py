from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_quiet_flag,
)
from agentic_swmm.commands.doctor_extension import (
    apply_fix_actions,
    collect_fix_actions,
    collect_llm_provider_status,
    collect_memory_store_status,
    collect_optout_status,
    collect_sessions_db_status,
    fix_action_to_dict,
    group_identical_warns,
    grouped_warn_to_dict,
    llm_provider_status_to_dict,
    memory_store_status_to_dict,
    optout_status_to_dict,
    render_grouped_warns_section,
    render_llm_provider_section,
    render_memory_stores_section,
    render_runtime_knobs_section,
)
from agentic_swmm.config import mcp_registry_path
from agentic_swmm.utils.paths import repo_root


_DOCTOR_EXAMPLE = "aiswmm doctor --fix --yes"


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
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit the full doctor report as JSON on stdout instead of "
            "the human-readable sections. Useful for CI integration."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "After printing the report, walk through the suggested "
            "remediations (mcp.json refresh, bootstrap memory). Each "
            "action prompts y/N unless --yes is set."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "When combined with --fix, apply remediations without "
            "asking for confirmation. Safe for CI/automation."
        ),
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_DOCTOR_EXAMPLE)
    parser.set_defaults(func=main)


def _memory_dir(root: Path) -> Path:
    """Resolve the active memory directory.

    Honours ``AISWMM_MEMORY_DIR`` so a user who redirected memory sees
    the redirected location in the doctor report.
    """
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    return root / "memory" / "modeling-memory"


def _runs_dir(root: Path) -> Path:
    """Resolve the active runs root.

    Honours ``AISWMM_RUNS_ROOT`` for parity with ``aiswmm memory`` so a
    user who redirects their runs directory sees the redirected
    sessions.sqlite location in the doctor report.
    """
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override)
    return root / "runs"


def _build_install_checks(root: Path) -> list[tuple[str, bool, str, bool]]:
    """The historical install-checks block, factored so the JSON path
    and the text path share one source of truth."""
    checks: list[tuple[str, bool, str, bool]] = []
    checks.append(("repo root", root.exists(), str(root), True))
    worktree_detail = _worktree_install_detail(root)
    if worktree_detail is not None:
        checks.append(("editable install", False, worktree_detail, False))
    for server_name, drift_detail in _mcp_json_drift(root):
        checks.append(
            (f"mcp.json: {server_name}", False, drift_detail, False)
        )
    checks.append(
        (
            "OPENAI_API_KEY",
            bool(os.environ.get("OPENAI_API_KEY")),
            "set"
            if os.environ.get("OPENAI_API_KEY")
            else "not set; needed for OpenAI agent planner mode",
            False,
        )
    )
    claude = shutil.which("claude")
    checks.append(
        (
            "claude code CLI",
            claude is not None,
            claude or "not found; optional future provider",
            False,
        )
    )
    node = shutil.which("node")
    checks.append(
        (
            "node executable",
            node is not None,
            node or "not found; needed for MCP server launchers",
            True,
        )
    )
    swmm = _which_swmm5()
    swmm_detail = (
        f"{swmm}; {_swmm_version() or 'version unavailable'}"
        if swmm
        else "not found on PATH or repo .local/bin"
    )
    checks.append(("swmm5 executable", swmm is not None, swmm_detail, True))
    for module in ("numpy", "matplotlib", "swmmtoolbox"):
        checks.append(
            (
                f"python module: {module}",
                _module_available(module),
                "importable" if _module_available(module) else "missing",
                True,
            )
        )
    # Optional [anywhere] extra — swmm-anywhere skill is callable iff
    # swmmanywhere is importable. We deliberately treat absence as INFO,
    # not WARN, since the default pip install aiswmm intentionally omits
    # the 27 heavy geo deps. Upstream attribution: SWMManywhere is © Imperial
    # College London, BSD-3-Clause.
    anywhere_installed = _module_available("swmmanywhere")
    checks.append(
        (
            "swmm-anywhere extra",
            anywhere_installed,
            "installed (SWMManywhere by Imperial College London, BSD-3)"
            if anywhere_installed
            else (
                "not installed; install with: pip install aiswmm[anywhere] "
                "(wraps SWMManywhere by Imperial College London, BSD-3-Clause; "
                "only needed if you want to synthesise networks from a bbox)"
            ),
            False,
        )
    )
    for path in (
        Path("skills/swmm-runner/scripts/swmm_runner.py"),
        Path("skills/swmm-experiment-audit/scripts/audit_run.py"),
        Path("skills/swmm-plot/scripts/plot_rain_runoff_si.py"),
        Path("skills/swmm-modeling-memory/scripts/summarize_memory.py"),
    ):
        full = root / path
        checks.append((str(path), full.exists(), str(full), True))
    return checks


def _checks_to_dicts(
    checks: list[tuple[str, bool, str, bool]],
) -> list[dict]:
    return [
        {
            "name": name,
            "passed": passed,
            "detail": detail,
            "required": required,
        }
        for (name, passed, detail, required) in checks
    ]


def main(args: argparse.Namespace) -> int:
    root = repo_root()
    install_checks = _build_install_checks(root)
    install_check_dicts = _checks_to_dicts(install_checks)

    memory_dir = _memory_dir(root)
    memory_stores = collect_memory_store_status(memory_dir)
    # Append the cross-session SQLite store row (issue #204). It lives
    # under runs/, not memory/modeling-memory/, so it has its own
    # collector — but it renders in the same Memory stores section.
    memory_stores.append(collect_sessions_db_status(_runs_dir(root)))
    optout_flags = collect_optout_status()
    llm_provider = collect_llm_provider_status()

    # Pull the non-passing rows into a WARN/MISSING bucket so the
    # grouping can collapse identical-cause WARNs (PRD-08 audit #28).
    warn_or_missing = [
        d for d in install_check_dicts if not d["passed"]
    ]
    # Issue #212: CORRUPT / UNREADABLE memory stores must appear in
    # the Issues section AND drive a non-zero exit code so CI health
    # checks don't miss data-loss conditions. Project each into the
    # install-check shape so the existing renderer / exit-code logic
    # picks them up without a special case.
    severe_memory_stores = [
        s for s in memory_stores
        if s.severity in {"CORRUPT", "UNREADABLE"}
    ]
    for store in severe_memory_stores:
        warn_or_missing.append(
            {
                "name": f"memory store: {store.name}",
                "passed": False,
                "detail": (
                    store.remediation
                    or f"{store.severity.lower()} — run aiswmm memory repair-sessions"
                ),
                "required": True,
            }
        )
    grouped = group_identical_warns(warn_or_missing)

    report = {
        "checks": install_check_dicts,
        "memory_stores": memory_stores,
        "optout_status": optout_flags,
        "llm_provider": llm_provider,
        "grouped_warns": grouped,
    }

    if getattr(args, "json", False):
        payload = {
            "checks": install_check_dicts,
            "memory_stores": [
                memory_store_status_to_dict(s) for s in memory_stores
            ],
            "optout_status": [
                optout_status_to_dict(s) for s in optout_flags
            ],
            "llm_provider": llm_provider_status_to_dict(llm_provider),
            "grouped_warns": [grouped_warn_to_dict(r) for r in grouped],
        }
        # When --fix is set we still print the fix-action candidates
        # so a CI consumer can decide what to run.
        if getattr(args, "fix", False):
            payload["fix_actions"] = [
                fix_action_to_dict(a) for a in collect_fix_actions(report)
            ]
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        # Section 1 — Install.
        print("Install:")
        for name, passed, detail, required in install_checks:
            status = "OK" if passed else ("MISSING" if required else "WARN")
            # Skip rows that have been absorbed into a grouped WARN; the
            # grouped section will display them.
            if not passed:
                continue
            print(f"  {status:7} {name} - {detail}")
        # Section 2 — Memory stores.
        print()
        print(render_memory_stores_section(memory_stores))
        # Section 3 — Runtime knobs.
        print()
        print(render_runtime_knobs_section(optout_flags))
        # Section 3b — LLM provider (PRD-09).
        print()
        print(render_llm_provider_section(llm_provider))
        # Section 4 — Issues (grouped).
        body = render_grouped_warns_section(grouped)
        if body:
            print()
            print(body)
        # Section 5 — Suggested actions.
        fix_actions = collect_fix_actions(report)
        if fix_actions and not getattr(args, "fix", False):
            print()
            print("Suggested actions (run `aiswmm doctor --fix` to apply):")
            for action in fix_actions:
                print(f"  - {action.label}: {' '.join(action.command)}")

    # ---- --fix interactive remediation
    if getattr(args, "fix", False):
        actions = collect_fix_actions(report)
        if not actions:
            print("\nno remediable actions available.")
        else:
            print("\nApplying fixes:")
            apply_fix_actions(actions, yes=getattr(args, "yes", False))

    # Overall exit code: 0 iff every required install check passed AND
    # no memory store is CORRUPT or UNREADABLE (issue #212 — CI health
    # checks must fail on data-loss conditions, not just missing
    # binaries). MISSING memory stores stay advisory because the
    # bootstrap verbs create them lazily; only the destructive
    # CORRUPT / UNREADABLE states demand operator action.
    install_ok = all(
        passed or not required
        for (_, passed, _, required) in install_checks
    )
    memory_severe = bool(severe_memory_stores)
    return 0 if install_ok and not memory_severe else 1
