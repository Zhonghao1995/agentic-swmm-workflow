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
        # What a person opens the run to find.
        "README.md",
        "manifest.json",
        "session.yaml",
        "final_report.md",
        "report.docx",
        "acceptance_report.json",
        "acceptance_report.md",
        "chat_note.md",
        "tool_results",
        # What the audit -> memory pipeline leaves beside them: the per-run
        # memory card the RAG library reads, the failure advice the CLI
        # verb writes, and the command trace. The CLI path has always
        # written these at the root; the agent path joined it on
        # 2026-09-02 (finding F-35), which is when the fresh-run guard
        # first met them.
        "memory_summary.json",
        "failure_advice.json",
        "failure_advice.md",
        "command_trace.json",
        # Audience directories.
        "_agent",
        "_obsidian",
        # Legacy: these moved under _agent/ and stay readable at the root
        # forever (ADR-0004 section 3), so old runs do not become invalid.
        "agent_snapshot.json",
        "agent_trace.jsonl",
        "memory_trace.jsonl",
    }
)

#: Machine-facing sidecars live under this directory, not at the run root.
#: A run root is what a person opens first, and it was showing them nine loose
#: files with names like ``aiswmm_state.json`` next to the one report they
#: wanted. The leading underscore sorts it below the numbered stages in both
#: Explorer and ``ls``.
AGENT_DIR = "_agent"

#: What gets exported to an Obsidian vault, kept out of the same eyeline.
OBSIDIAN_DIR = "_obsidian"

#: Files that belong to the agent, not to the reader of the run. These are
#: session ground truth (``agent_trace.jsonl`` is the record the sqlite session
#: DB is rebuilt from), not deliverables.
AGENT_FILES: frozenset[str] = frozenset(
    {
        "agent_trace.jsonl",
        "memory_trace.jsonl",
        "session_state.json",
        "aiswmm_state.json",
        "agent_snapshot.json",
        "context_summary.md",
    }
)


def agent_file(run_dir: "Path | str", name: str) -> "Path":
    """Path for one agent sidecar, new location first, legacy root second.

    One rule for readers and writers, because a split-brain run is worse than
    an untidy one:

    * fresh run -> ``<run>/_agent/<name>``;
    * legacy run that already has ``<run>/<name>`` -> that path, so an append
      keeps landing in the file the run already has;
    * legacy run being read -> the root copy is found without a migration.

    Pure: it touches nothing. An earlier version created ``_agent/`` here, and
    a caller that only wanted to test ``.exists()`` left a stray directory
    behind at whatever path it was handed, including the runs root. Writers
    call :func:`agent_file_for_write`.

    ADR-0004 keeps legacy layouts readable forever; this follows the same
    rule as ``LEGACY_ALIASES``, one level up.
    """
    from pathlib import Path as _Path

    root = _Path(run_dir)
    new_path = root / AGENT_DIR / name
    if new_path.exists():
        return new_path
    legacy = root / name
    if legacy.exists():
        return legacy
    return new_path


def agent_file_for_write(run_dir: "Path | str", name: str) -> "Path":
    """:func:`agent_file`, with the parent directory guaranteed to exist."""
    path = agent_file(run_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


#: Upstream boxes get a named sub-box under 10_upstream.
UPSTREAM_SWMMANYWHERE = "swmmanywhere"
UPSTREAM_SWMMCANADA = "swmmcanada"

# --- legacy read-tolerance (ADR-0004 section 3) ---------------------------
# canonical stage -> names old runs may carry for the same concept.
# READ-ONLY: resolvers consult these; writers never produce them.

LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    BUILDER: ("04_builder", "builder"),
    RUNNER: ("05_runner", "runner", "01_runner"),
    QA: ("06_qa",),
    PLOT: ("07_plots",),
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
