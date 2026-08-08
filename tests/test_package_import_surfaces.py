"""Pin the allowed import surface of the facade packages (issue #359).

Three packages ship a deliberate boundary; each got a different, honest
treatment and this test makes all three enforceable:

* ``hitl`` is fully facade-routed: its ``__init__`` re-exports every
  public name, so production code outside the package must never import
  from a ``hitl`` submodule (that is how two silent bypasses lived).
* ``gap_fill`` exposes a documented submodule list; external
  module-object imports must stay inside it.
* ``memory`` is a wide domain package with an intentional two-tier
  surface: the 4-function facade plus named submodules for first-party
  code. The submodule tier is a RATCHET: growing it is fine, but it
  must be a conscious edit to the pinned list here in the same PR
  (ADR-0006 D3 style, same as the CLI verb pin).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "agentic_swmm"

# The documented gap_fill surface (package docstring lists these).
GAP_FILL_ALLOWED = {
    "protocol",
    "preflight",
    "proposer",
    "recorder",
    "ui",
    "ui_per_gap",
    "llm_enumerator",
}

# Ratchet: memory submodules that first-party code outside the package
# imports today. Adding a NEW submodule to the external surface is a
# deliberate act: extend this list in the same PR and say why.
MEMORY_SUBMODULE_RATCHET = {
    "benchmark_resolver",
    "calibration_memory",
    "card",
    "case_inference",
    "citations",
    "context_budget",
    "context_fence",
    "cross_watershed_transfer",
    "facts",
    "lessons_lifecycle",
    "lessons_metadata",
    "memory_archive",
    "memory_outcomes",
    "negative_lessons_markdown",
    "parametric_memory",
    "reference_benchmarks",
    "run_failures",
    "run_progress",
    "session_db",
    "session_repair",
    "session_sync",
    "storm_library",
    "user_baseline",
}


def _external_files(package: str):
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts and relative.parts[0] == package:
            continue
        yield path


def _submodule_names(package: str) -> set[str]:
    return {
        p.stem for p in (PACKAGE_ROOT / package).glob("*.py") if p.stem != "__init__"
    }


def _external_surface(package: str) -> dict[str, set[str]]:
    """Map submodule name -> set of importing files (repo-relative)."""
    symbol_import = re.compile(rf"from agentic_swmm\.{package}\.([a-zA-Z_]+) import")
    module_import = re.compile(rf"from agentic_swmm\.{package} import ([a-zA-Z_ ,]+)")
    modules = _submodule_names(package)
    surface: dict[str, set[str]] = {}
    for path in _external_files(package):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(REPO_ROOT))
        for match in symbol_import.finditer(text):
            surface.setdefault(match.group(1), set()).add(rel)
        for match in module_import.finditer(text):
            for name in match.group(1).split(","):
                name = name.strip().split(" as ")[0]
                if name in modules:
                    surface.setdefault(name, set()).add(rel)
    return surface


# The one sanctioned hitl submodule import: the function
# ``request_expert_review`` collides with its own submodule's name, so
# Python's submodule binding would shadow a package-level re-export
# (order-dependent module-vs-function). Facade routing is unsafe there.
HITL_SANCTIONED = {"request_expert_review"}


def test_hitl_is_facade_routed_except_the_name_collision() -> None:
    surface = _external_surface("hitl")
    offenders = {
        sub: sorted(files)
        for sub, files in surface.items()
        if sub not in HITL_SANCTIONED
    }
    assert offenders == {}, (
        "hitl re-exports its public surface from the package; import "
        f"from agentic_swmm.hitl instead of a submodule: {offenders}"
    )


def test_gap_fill_external_surface_is_the_documented_set() -> None:
    surface = _external_surface("gap_fill")
    undeclared = {
        sub: sorted(files)
        for sub, files in surface.items()
        if sub not in GAP_FILL_ALLOWED
    }
    assert undeclared == {}, (
        "gap_fill submodule used outside the documented surface; either "
        "route through the package or grow the documented list (package "
        f"docstring + GAP_FILL_ALLOWED) in the same PR: {undeclared}"
    )


def test_memory_submodule_surface_matches_the_ratchet() -> None:
    surface = set(_external_surface("memory"))
    new = surface - MEMORY_SUBMODULE_RATCHET
    gone = MEMORY_SUBMODULE_RATCHET - surface
    assert not new, (
        "memory's external submodule surface grew; if deliberate, add to "
        f"MEMORY_SUBMODULE_RATCHET in the same PR and say why: {sorted(new)}"
    )
    assert not gone, (
        "memory submodules no longer imported externally; shrink the "
        f"ratchet to keep it honest: {sorted(gone)}"
    )
