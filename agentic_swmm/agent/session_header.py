"""Session header + auto-derived agent snapshot (ADR-0003, layer 1).

Every agent session directory becomes self-describing through two files
written at session start:

* ``session.yaml`` — the who/where/what header: the user's verbatim
  goal, the aiswmm version, a content hash of the agent snapshot, an
  inline environment fingerprint, and a ``status`` that is finalized to
  ``completed`` / ``failed`` / ``interrupted`` at session end.
* ``agent_snapshot.json`` — an EXPORT of the live agent surface (model
  provider, system-prompt hash, sorted tool names + schema hash, per
  SKILL.md hashes, intent-map hash, permission profile).

Design rule (ADR-0003): the snapshot is derived from what IS, never a
prescriptive config. A hand-written agent manifest would drift from the
registry the same way SKILL.md tool names rotted before the #326
contract guard; deriving it at session start makes drift impossible.

Versioning anchors on ``agentic_swmm.__version__`` plus content hashes;
there is deliberately no second version counter.

All writers are best-effort at the call sites: a header failure must
never break a modelling session. The builders themselves raise normally
so tests can assert on real errors.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentic_swmm import __version__ as _AISWMM_VERSION
from agentic_swmm.utils.paths import repo_root

SESSION_HEADER_NAME = "session.yaml"
AGENT_SNAPSHOT_NAME = "agent_snapshot.json"

# Env var set by the Docker entrypoint (ADR-0003 layer 3, wired in the
# environment-fingerprint PR); absent means a bare-metal run.
CONTAINER_DIGEST_ENV = "AISWMM_CONTAINER_DIGEST"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_commit() -> str | None:
    """Current checkout commit, or None outside a git checkout (pip install)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else None


def build_agent_snapshot(
    *,
    registry: Any = None,
    provider: str | None = None,
    model: str | None = None,
    planner: str | None = None,
    profile: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Export the live agent surface as a JSON-able dict.

    ``registry`` defaults to a fresh ``AgentToolRegistry`` (imported
    lazily to stay off the module-load cycle). ``system_prompt`` is the
    resolved prompt TEXT when the caller has one (LLM planner); the rule
    planner has no prompt and records ``None``.
    """
    if registry is None:
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()

    schemas = sorted(registry.schemas(), key=lambda schema: schema["name"])
    tool_names = [schema["name"] for schema in schemas]
    tools_sha256 = _sha256_text(json.dumps(schemas, sort_keys=True, default=str))

    skills: dict[str, str] = {}
    skills_root = repo_root() / "skills"
    if skills_root.is_dir():
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            digest = _sha256_file(skill_md)
            if digest is not None:
                skills[skill_md.parent.name] = digest

    intent_map_sha256 = _sha256_file(repo_root() / "agent" / "config" / "intent_map.json")

    return {
        "aiswmm_version": _AISWMM_VERSION,
        "planner": planner,
        "model_provider": provider,
        "model": model,
        "permission_profile": profile,
        "system_prompt_sha256": _sha256_text(system_prompt) if system_prompt else None,
        "tools": tool_names,
        "tools_schema_sha256": tools_sha256,
        "skills": skills,
        "intent_map_sha256": intent_map_sha256,
    }


def environment_fingerprint() -> dict[str, Any]:
    """Where this session actually runs: captured, not prescribed.

    The provenance-side extension (swmm5 version as invoked, key package
    versions) is ADR-0003 layer 3; this block is the session-level core
    shared by both.
    """
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "aiswmm_version": _AISWMM_VERSION,
        "git_commit": _git_commit(),
        "container_image_digest": os.environ.get(CONTAINER_DIGEST_ENV) or None,
    }


def write_session_header(
    session_dir: Path,
    *,
    goal: str,
    planner: str,
    profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    registry: Any = None,
    system_prompt: str | None = None,
) -> Path:
    """Write ``agent_snapshot.json`` + ``session.yaml`` into ``session_dir``.

    Returns the header path. Raises on I/O errors: call sites wrap this
    best-effort (see ``try_write_session_header``).
    """
    snapshot = build_agent_snapshot(
        registry=registry,
        provider=provider,
        model=model,
        planner=planner,
        profile=profile,
        system_prompt=system_prompt,
    )
    snapshot_text = json.dumps(snapshot, indent=2, sort_keys=True)
    (session_dir / AGENT_SNAPSHOT_NAME).write_text(snapshot_text + "\n", encoding="utf-8")

    env = environment_fingerprint()
    header = {
        "session_id": session_dir.name,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "goal": goal,
        "planner": planner,
        "agent": {
            "aiswmm_version": _AISWMM_VERSION,
            "snapshot_sha256": _sha256_text(snapshot_text),
        },
        "environment": {
            "fingerprint_sha256": _sha256_text(json.dumps(env, sort_keys=True)),
            **env,
        },
        "status": "running",
    }
    header_path = session_dir / SESSION_HEADER_NAME
    header_path.write_text(
        yaml.safe_dump(header, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return header_path


def finalize_session_header(session_dir: Path, status: str) -> None:
    """Stamp the terminal ``status`` (+ ``completed_utc``). Best-effort:
    a finalize failure must never mask the session's own outcome."""
    header_path = session_dir / SESSION_HEADER_NAME
    try:
        header = yaml.safe_load(header_path.read_text(encoding="utf-8"))
        if not isinstance(header, dict):
            return
        header["status"] = status
        header["completed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        header_path.write_text(
            yaml.safe_dump(header, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    except Exception:  # pragma: no cover - never break a session on finalize
        return


def try_write_session_header(session_dir: Path, **kwargs: Any) -> Path | None:
    """Best-effort wrapper for the session start path."""
    try:
        return write_session_header(session_dir, **kwargs)
    except Exception:  # pragma: no cover - header must never break a session
        return None


__all__ = [
    "AGENT_SNAPSHOT_NAME",
    "CONTAINER_DIGEST_ENV",
    "SESSION_HEADER_NAME",
    "build_agent_snapshot",
    "environment_fingerprint",
    "finalize_session_header",
    "try_write_session_header",
    "write_session_header",
]
