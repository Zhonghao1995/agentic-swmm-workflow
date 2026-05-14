"""Run-folder layout invariant for the audit layer.

PRD: ``.claude/prds/PRD_audit.md``.

Audit invariant (only this is enforced; nothing else about a run dir is):

    <run-dir>/09_audit/experiment_note.md          # required
    <run-dir>/09_audit/experiment_provenance.json  # required

This module is intentionally pure: ``Path.exists()`` / ``Path.iterdir()``
only, no JSON parsing, no schema validation beyond presence. Schema-version
checks live in ``skills/swmm-experiment-audit/scripts/audit_run.py``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator


CHAT_REQUIRED_FILES = ("session_state.json", "agent_trace.jsonl", "chat_note.md")
SWMM_REQUIRED_AUDIT_FILES = (
    "09_audit/experiment_note.md",
    "09_audit/experiment_provenance.json",
)
# A dir is treated as a chat session when ``session_state.json`` is present.
# ``agent_trace.jsonl`` alone is too weak (zombie ``runs/agent/agent-<ts>/``
# dirs also carry one), so we anchor on the chat-only state file.
_CHAT_HINT_FILES = ("session_state.json",)
# Filenames/dirnames that, when present, hint a dir is a SWMM run.
_SWMM_HINT_DIRS = (
    "00_inputs",
    "01_gis",
    "02_params",
    "03_climate",
    "04_network",
    "04_network_prep",
    "04_builder",
    "05_builder",
    "05_runner",
    "06_runner",
    "06_qa",
    "07_qa",
    "08_plot",
    "09_audit",
    "10_direct",
    "audit",
    "06_audit",
)
_SWMM_HINT_FILES = ("manifest.json", "acceptance_report.json")


class RunKind(Enum):
    """Discriminator for a run-dir-shaped path."""

    CHAT = "chat"
    SWMM = "swmm"
    ZOMBIE = "zombie"


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating one run dir against its expected contract."""

    kind: RunKind
    ok: bool
    missing: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - thin convenience
        return self.ok


@dataclass(frozen=True)
class RunFolder:
    """A discovered run dir plus its discriminator."""

    path: Path
    kind: RunKind


def _looks_like_chat(path: Path) -> bool:
    return any((path / name).exists() for name in _CHAT_HINT_FILES)


def _looks_like_swmm(path: Path) -> bool:
    if any((path / name).is_dir() for name in _SWMM_HINT_DIRS):
        return True
    return any((path / name).is_file() for name in _SWMM_HINT_FILES)


def _looks_like_legacy_swmm_root(path: Path) -> bool:
    # P1/P2/P3 legacy audit files at run-dir root.
    return (path / "experiment_note.md").exists() or (
        path / "experiment_provenance.json"
    ).exists()


def classify(run_dir: Path) -> RunKind:
    """Best-effort discriminator for one run dir.

    Order matters: a chat layout shadowed by run-dir hints is still chat
    because it carries the chat-specific JSON files.
    """
    if _looks_like_chat(run_dir):
        return RunKind.CHAT
    if _looks_like_swmm(run_dir) or _looks_like_legacy_swmm_root(run_dir):
        return RunKind.SWMM
    return RunKind.ZOMBIE


def validate(run_dir: Path) -> ValidationResult:
    """Validate a run dir against its kind-specific required files.

    For SWMM, the only invariant is the canonical ``09_audit/`` pair.
    Stage-dir presence is intentionally not asserted (PRD D7).
    """
    kind = classify(run_dir)
    missing: list[str] = []
    if kind is RunKind.CHAT:
        for name in CHAT_REQUIRED_FILES:
            if not (run_dir / name).exists():
                missing.append(name)
    elif kind is RunKind.SWMM:
        for rel in SWMM_REQUIRED_AUDIT_FILES:
            if not (run_dir / rel).exists():
                missing.append(rel)
    else:
        # ZOMBIE: nothing required, but cannot be considered "ok" either.
        missing.append("(no run/chat content)")
    return ValidationResult(kind=kind, ok=not missing, missing=missing)


def _is_run_dir_candidate(path: Path) -> bool:
    """True if this dir is itself a leaf run dir worth yielding.

    A leaf is a dir that either (a) carries chat content, (b) carries a
    SWMM hint (stage dir or top-level manifest), or (c) carries legacy
    root-level audit files. We do not recurse below it.
    """
    if _looks_like_chat(path):
        return True
    if _looks_like_swmm(path):
        return True
    if _looks_like_legacy_swmm_root(path):
        return True
    return False


def discover(runs_root: Path) -> Iterator[RunFolder]:
    """BFS-walk ``runs_root`` and yield every discovered run/chat dir.

    - Walks unlimited depth so nested cases (e.g.
      ``external-case-candidates/zenodo-tecnopolo/month-199401/runner-fixed/``)
      are picked up without hardcoded globs.
    - Skips ``.archive/`` (PRD D5) and any other hidden top-level dir.
    - Once a dir is yielded as a run-dir candidate, its children are not
      explored, so stage subdirs like ``05_builder/`` are not mistakenly
      treated as separate runs.
    """
    if not runs_root.exists():
        return
    queue: deque[Path] = deque([runs_root])
    while queue:
        current = queue.popleft()
        if not current.is_dir():
            continue
        for entry in sorted(current.iterdir()):
            if not entry.is_dir():
                continue
            # Skip hidden subtrees: .archive/, .git/, ._foo, etc.
            if entry.name.startswith("."):
                continue
            if _is_run_dir_candidate(entry):
                yield RunFolder(path=entry, kind=classify(entry))
                # Do not descend into a yielded run dir.
                continue
            queue.append(entry)
