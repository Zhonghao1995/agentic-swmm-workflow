"""Canonical run-directory layout (ADR-0004): the single source of truth.

Five layouts coexisted for "one modelling run" (two shifted numbering
generations, a flat agent path, and two upstream shapes). This module is
the one place stage names live; every writer and reader imports from
here. A literal ``"0X_..."`` string anywhere else is a review smell and,
for writers, a guard-test failure (``tests/test_run_layout_guard.py``).

Numbers are RESERVED PARKING SPOTS, not per-run sequences: a stage a run
does not use creates no directory, but a stage that runs lands in its
number on every path (CLI verb, agent tools, synth, canada).

``10_upstream`` follows ADR-0001: upstream internals (SWMManywhere's
``bbox_1`` workspace, SWMMCanada's ``swmm_model.zip``) are provenance
evidence. aiswmm dictates only WHERE the box sits, never what is inside.

Legacy names (Generation B, flat runner outputs, ``09_review``) stay
readable forever through ``LEGACY_ALIASES`` and the manifest-first
resolvers; nothing ever WRITES a legacy name again.
"""

from __future__ import annotations

from pathlib import Path

# --- canonical stage registry (ADR-0004 section 1) -----------------------

RAW = "00_raw"
GIS = "01_gis"
PARAMS = "02_params"
CLIMATE = "03_climate"
NETWORK = "04_network"
BUILDER = "05_builder"
RUNNER = "06_runner"
QA = "07_qa"
PLOT = "08_plot"
AUDIT = "09_audit"
UPSTREAM = "10_upstream"
REVIEW = "11_review"

#: Every canonical stage directory a run may contain, in pipeline order.
CANONICAL_STAGES: tuple[str, ...] = (
    RAW, GIS, PARAMS, CLIMATE, NETWORK, BUILDER,
    RUNNER, QA, PLOT, AUDIT, UPSTREAM, REVIEW,
)

#: Non-stage entries that legitimately live at a run/session dir root.
CANONICAL_ROOT_FILES: frozenset[str] = frozenset(
    {
        "manifest.json",
        "session.yaml",
        "agent_snapshot.json",
        "agent_trace.jsonl",
        "memory_trace.jsonl",
        "final_report.md",
        "acceptance_report.json",
        "acceptance_report.md",
        "tool_results",
        "chat_note.md",
    }
)

#: Upstream boxes get a named sub-box under 10_upstream.
UPSTREAM_SWMMANYWHERE = "swmmanywhere"
UPSTREAM_SWMMCANADA = "swmmcanada"

# --- legacy read-tolerance (ADR-0004 section 3) ---------------------------
# canonical stage -> names old runs may carry for the same concept.
# READ-ONLY: resolvers consult these; writers never produce them.

LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    BUILDER: ("04_builder", "builder"),
    RUNNER: ("05_runner", "runner", "01_runner"),
    QA: ("06_qa", "07_qa"),
    PLOT: ("07_plots", "08_plot"),
    AUDIT: ("06_audit",),
    REVIEW: ("09_review",),
    UPSTREAM: ("10_swmmanywhere",),
}


def stage_dir(run_dir: Path, stage: str, *, create: bool = False) -> Path:
    """The canonical directory for ``stage`` under ``run_dir``.

    ``stage`` must be one of ``CANONICAL_STAGES`` (typo-proofing: passing
    a raw string that is not registered raises immediately rather than
    minting a sixth scheme).
    """
    if stage not in CANONICAL_STAGES:
        raise ValueError(
            f"unknown run-layout stage {stage!r}; canonical stages: {', '.join(CANONICAL_STAGES)}"
        )
    path = run_dir / stage
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def upstream_dir(run_dir: Path, source: str, *, create: bool = False) -> Path:
    """The opaque upstream box for ``source`` (e.g. ``swmmcanada``)."""
    path = run_dir / UPSTREAM / source
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def find_stage(run_dir: Path, stage: str) -> Path | None:
    """Resolve ``stage`` in ``run_dir``, canonical first, then legacy names.

    Read-side tolerance for historical runs; returns the first existing
    directory or None. Writers must use :func:`stage_dir` instead.
    """
    canonical = run_dir / stage
    if canonical.is_dir():
        return canonical
    for alias in LEGACY_ALIASES.get(stage, ()):
        candidate = run_dir / alias
        if candidate.is_dir():
            return candidate
    return None


__all__ = [
    "AUDIT", "BUILDER", "CANONICAL_ROOT_FILES", "CANONICAL_STAGES", "CLIMATE",
    "GIS", "LEGACY_ALIASES", "NETWORK", "PARAMS", "PLOT", "QA", "RAW",
    "REVIEW", "RUNNER", "UPSTREAM", "UPSTREAM_SWMMANYWHERE",
    "UPSTREAM_SWMMCANADA", "find_stage", "stage_dir", "upstream_dir",
]
