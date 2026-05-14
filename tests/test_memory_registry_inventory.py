"""Inventory test for the startup memory registry (P1-1 in #79).

The README under ``agent/memory/`` advertises seven LLM-readable memory
files that an agent runtime (Claude Code, OpenClaw, Hermes, …) is expected
to load on startup as the warm-identity layer. The registry in
``agentic_swmm.runtime.registry.LONG_TERM_MEMORY_FILES`` is the single
source of truth that wires those files into the runtime. The two had
silently drifted (registry: 3 files, README: 7 files); this test locks
them together so future drift trips immediately.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.runtime.registry import (
    LONG_TERM_MEMORY_FILES,
    discover_memory_files,
)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_MEMORY_DIR = _REPO_ROOT / "agent" / "memory"

_EXPECTED_FILES = frozenset(
    {
        "agent/memory/identification_memory.md",
        "agent/memory/operational_memory.md",
        "agent/memory/evidence_memory.md",
        "agent/memory/soul.md",
        "agent/memory/modeling_workflow_memory.md",
        "agent/memory/user_bridge_memory.md",
        "agent/memory/README.md",
    }
)


def test_long_term_memory_registry_size_is_seven() -> None:
    assert len(LONG_TERM_MEMORY_FILES) == 7, (
        f"LONG_TERM_MEMORY_FILES has {len(LONG_TERM_MEMORY_FILES)} entries; "
        f"expected 7 to match agent/memory/README.md. See #79 P1-1."
    )


def test_long_term_memory_registry_contents_match_expected() -> None:
    registered = frozenset(preferred for preferred, _fallback in LONG_TERM_MEMORY_FILES)
    missing = _EXPECTED_FILES - registered
    extra = registered - _EXPECTED_FILES
    assert not missing, f"registry missing required entries: {sorted(missing)}"
    assert not extra, f"registry has unexpected entries: {sorted(extra)}"


def test_each_registered_memory_file_exists_on_disk() -> None:
    for preferred, _fallback in LONG_TERM_MEMORY_FILES:
        path = _REPO_ROOT / preferred
        assert path.is_file(), f"registered memory file missing on disk: {path}"


def test_discover_memory_files_reports_seven_startup_records() -> None:
    records = discover_memory_files()
    startup = [r for r in records if r.get("load_at_startup")]
    assert len(startup) == 7, (
        f"discover_memory_files reports {len(startup)} startup files; "
        "expected 7."
    )


def test_registry_matches_agent_memory_markdown_inventory() -> None:
    """Cross-check the registry against the actual ``agent/memory/*.md``
    files on disk so that a new tracked memory file fails this test until
    the registry is updated to include it."""

    on_disk = {
        f"agent/memory/{p.name}"
        for p in _AGENT_MEMORY_DIR.glob("*.md")
    }
    registered = {preferred for preferred, _fallback in LONG_TERM_MEMORY_FILES}
    assert on_disk == registered, (
        "Registry vs. on-disk agent/memory/*.md drift; on_disk-only="
        f"{sorted(on_disk - registered)} registry-only="
        f"{sorted(registered - on_disk)}"
    )
