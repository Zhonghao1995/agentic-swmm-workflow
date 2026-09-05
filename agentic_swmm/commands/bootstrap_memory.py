"""``aiswmm bootstrap memory`` — scaffold a project memory directory (PRD-06 Phase D.4).

A fresh project has no ``memory/modeling-memory/`` directory; the
existing memory stores (``parametric_memory.jsonl``,
``calibration_memory.jsonl``, ``negative_lessons.jsonl``) are created
lazily by the audit hook the first time it tries to append a row. For
human onboarding that lazy-create flow is opaque — the user opens the
project and sees nothing memory-related until after the first SWMM
run.

This command creates the skeleton ahead of time so the user can:

    * grep for the empty JSONL files and confirm where memory lives;
    * paste-edit ``project_overrides.yaml`` before the first run;
    * read the bundled ``README.md`` and follow the link to
      ``docs/memory_runtime.md`` for the substrate's contract.

Idempotent
----------
Re-running the command never overwrites an existing file. Files that
already exist appear in the ``skipped`` list of :class:`BootstrapResult`;
files that did not exist appear in ``created``. This means
``aiswmm bootstrap memory`` is safe to run in CI as a "ensure-present"
step.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from agentic_swmm.utils.paths import resolve_memory_dir

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_json_flag,
    register_quiet_flag,
)


_BOOTSTRAP_EXAMPLE = "aiswmm bootstrap memory --dir memory/modeling-memory"


# Default target directory. Matches the layout the rest of the package
# uses (``memory/modeling-memory/``) so the bootstrap output lands
# where the audit hook will later append to it.
_DEFAULT_DIR = Path("memory") / "modeling-memory"


# Filenames the skeleton creates. Kept as a module-level tuple so the
# CLI help text and the test suite can both reference the same list
# without drifting.
_SKELETON_FILES: tuple[str, ...] = (
    "parametric_memory.jsonl",
    "calibration_memory.jsonl",
    "negative_lessons.jsonl",
    "project_overrides.yaml",
    # Live finding F-132 (2026-09-04): doctor told a pip user these three were
    # MISSING with the remedy "ship from package or copy from repo", and a pip
    # user has no repo. The skeletons below are the checkout's placeholder
    # files (null leaves, pending-verification entries); bootstrap never
    # overwrites an existing one, so a curated copy survives upgrades.
    "reference_benchmarks.yaml",
    "citations.yaml",
    "storm_library.yaml",
    "README.md",
)


# Header for the project_overrides.yaml file. The schema_version line
# is required by :mod:`agentic_swmm.memory.benchmark_resolver` —
# without it the overrides file would be rejected on first read.
_PROJECT_OVERRIDES_HEADER = (
    "# project_overrides.yaml — per-project overlay on reference_benchmarks.yaml.\n"
    "#\n"
    "# Any key under the same dotted path as the library benchmark wins\n"
    "# when present here. Leave empty (just the schema_version line) to\n"
    "# fall through to library defaults.\n"
    "schema_version: \"1.0\"\n"
)


# README content. Single source of truth for the link to the
# engineering doc — the bootstrap target dir is the first place a new
# user looks, so the README should point them at the substrate doc
# rather than at PR numbers.
_README_CONTENT = (
    "# Modeling memory\n"
    "\n"
    "This directory holds the project's modeling memory:\n"
    "\n"
    "* `parametric_memory.jsonl` — append-only log of run-level\n"
    "  parameters and QA metrics.\n"
    "* `calibration_memory.jsonl` — append-only log of accepted\n"
    "  calibrations and goodness-of-fit metrics.\n"
    "* `negative_lessons.jsonl` — append-only log of known-bad\n"
    "  parameter regions and failure codes.\n"
    "* `project_overrides.yaml` — per-project overlay on the library\n"
    "  reference benchmarks.\n"
    "\n"
    "See [docs/memory_runtime.md](../../docs/memory_runtime.md) for\n"
    "the substrate contract and the four confidence quadrants the\n"
    "runtime uses to decide between auto-complete, memory-informed,\n"
    "LLM, and HITL.\n"
)


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of one ``bootstrap memory`` invocation.

    The dataclass is frozen so tests can compare two results by value
    without worrying about post-construction mutation. Both ``created``
    and ``skipped`` are :class:`list` for ordering predictability —
    the order matches the iteration order over :data:`_SKELETON_FILES`.

    Attributes:
        target_dir: The directory the skeleton landed in. Resolved
            from the user's ``--dir`` flag (or the default) before
            any file is touched.
        created: Files that did not exist and were created.
        skipped: Files that already existed and were left alone.
    """

    target_dir: Path
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)



_YAML_SKELETONS: dict[str, str] = {
    'reference_benchmarks.yaml': '# ===========================================================================\n# WARNING — UN-CITED PLACEHOLDERS\n# ---------------------------------------------------------------------------\n# Most numeric leaves in this file are deliberately ``null``. Each null leaf\n# has a sibling ``citation`` key pointing into ``citations.yaml`` (also under\n# this directory). A numeric leaf lands ONLY AFTER the matching citation\n# entry has been verified by the maintainer (``verified_by`` and\n# ``verified_on`` populated) — schema-only placeholder entries are not\n# sufficient. See ``agentic_swmm/memory/citations.py`` for the typed reader.\n#\n# Two consequences for runtime:\n#  * ``classify_metric`` returns ``"UNKNOWN"`` for any metric whose threshold\n#    leaf is ``null`` (see agentic_swmm/memory/reference_benchmarks.py).\n#  * ``recall_reference_benchmark(path, dotted_key, default)`` always returns\n#    the caller\'s ``default`` for a ``null`` leaf — callers must pass a\n#    safe-conservative numeric default.\n#\n# The only block that ships with concrete numbers is\n# ``continuity_thresholds_pct`` — those are the SWMM User Manual\'s own\n# magnitude bands for the runoff / flow / mass-balance continuity printout,\n# not a literature recall. A later phase will move them under a citation\n# key too, for consistency.\n# ===========================================================================\n\nschema_version: "1.0"\n\n# ---------------------------------------------------------------------------\n# Goodness-of-fit thresholds for calibrated runs. NSE = Nash-Sutcliffe.\n# Use case keys match ``model_structure.use_case`` in parametric_memory.\n#\n# Leaves are intentionally ``null`` — populate from the project\'s\n# citation library in Phase B (Moriasi-class watershed-modeling guidance\n# for streamflow; stormwater-event guidance is separate literature).\n# ---------------------------------------------------------------------------\nnse_acceptable_thresholds:\n  stormwater_event:\n    acceptable: null\n    good: null\n    excellent: null\n    citation: null  # lands when matching citations.yaml entry is verified\n  stormwater_continuous:\n    acceptable: null\n    good: null\n    excellent: null\n    citation: null  # lands when matching citations.yaml entry is verified\n  baseflow_low_flow:\n    acceptable: null\n    good: null\n    excellent: null\n    citation: null  # lands when matching citations.yaml entry is verified\n\n# ---------------------------------------------------------------------------\n# Continuity error thresholds read from the .rpt by postflight_qa.\n# WARN bumps the run to manual review; FAIL gates downstream acceptance.\n# Magnitudes — see classify_metric() semantics.\n#\n# These bands track the SWMM User Manual\'s own continuity-error printout\n# convention (small percentages indicate a numerically sound run; double-digit\n# percentages indicate the simulation has gone off the rails). Treat them as\n# the project\'s *default* gate; project-local overrides should pass an\n# explicit ``benchmarks_path`` to ``postflight_qa``.\n# ---------------------------------------------------------------------------\ncontinuity_thresholds_pct:\n  runoff:\n    warn: 5.0\n    fail: 10.0\n  flow:\n    warn: 1.0\n    fail: 5.0\n  mass_balance:\n    warn: 2.0\n    fail: 5.0\n\n# ---------------------------------------------------------------------------\n# Manning\'s n for overland flow (SWMM SUBCATCHMENT N-IMPERV / N-PERV).\n#\n# Leaves are intentionally ``null`` pending Phase B citations.yaml.\n# Citation keys are kept inline so the Phase B migration is a value-only\n# fill, not a schema change.\n# ---------------------------------------------------------------------------\nmanning_n_overland:\n  asphalt:\n    min: null\n    typical: null\n    max: null\n    citation: null  # populate together with values in Phase B\n  concrete:\n    min: null\n    typical: null\n    max: null\n    citation: null\n  grass_short:\n    min: null\n    typical: null\n    max: null\n    citation: null\n\n# ---------------------------------------------------------------------------\n# Manning\'s n for closed-conduit roughness (SWMM CONDUIT ROUGHNESS).\n# Leaves intentionally ``null`` pending Phase B citations.yaml.\n# ---------------------------------------------------------------------------\nmanning_n_pipes:\n  concrete_smooth:\n    min: null\n    typical: null\n    max: null\n    citation: null\n  hdpe:\n    min: null\n    typical: null\n    max: null\n    citation: null\n',
    'citations.yaml': '# ===========================================================================\n# CITATION LIBRARY — PRD-06 Phase B.2\n# ---------------------------------------------------------------------------\n# This file is HAND-EDITED. Each entry is a citation token (the dictionary\n# key) plus the bibliographic fields needed to look up the original source\n# manually. The library is the substrate behind two things:\n#\n#   1. The ``citation`` leaf next to every numeric range in\n#      ``reference_benchmarks.yaml``. A reference-benchmark range is only\n#      populated AFTER the matching citations.yaml entry has been verified\n#      against the original work — bibliographic placeholder entries do not\n#      authorise populating numeric leaves.\n#\n#   2. The ``aiswmm cite <citation_key>`` CLI surface, which prints the entry\n#      so a human (or agent in transparency mode) can audit which work backs\n#      a parameter choice.\n#\n# Verification contract:\n#   * Every entry MUST be hand-verified by the maintainer before it is used\n#     to backfill a numeric leaf in ``reference_benchmarks.yaml``.\n#   * The ``verified_by`` and ``verified_on`` fields record who and when.\n#   * Unverified entries are useful as schema placeholders only.\n#\n# Schema (every entry must follow this shape):\n#   <citation_key>:\n#     authors: "..."                 # full author list as printed on source\n#     year: <int>                    # publication year\n#     title: "..."                   # title of the work\n#     work: "..."                    # journal / book / report container\n#     locator: "..."                 # page / table / section locator\n#     url: ""                        # optional, may be empty\n#     verified_by: "..."             # maintainer who verified the entry\n#     verified_on: "YYYY-MM-DD"      # ISO date of verification\n#\n# Keys are snake_case. Use the convention ``<lead-author>_<year>_<short>`` so\n# the token reads cleanly in code comments and audit notes.\n# ===========================================================================\n\nschema_version: "1.0"\n\n# ---------------------------------------------------------------------------\n# Worked-example entry — schema demonstration only.\n# Replace these placeholder fields once the maintainer has verified the\n# underlying source. Do NOT cite this entry from reference_benchmarks.yaml\n# until the placeholder text has been replaced with verified bibliographic\n# data.\n# ---------------------------------------------------------------------------\nworked_example_pending_verification:\n  authors: "<author-list-pending-verification>"\n  year: 0\n  title: "<title-pending-verification>"\n  work: "<container-pending-verification>"\n  locator: "<page-or-table-pending-verification>"\n  url: ""\n  verified_by: ""\n  verified_on: ""\n',
    'storm_library.yaml': '# ===========================================================================\n# STORM LIBRARY — curated design-storm specifications.\n# ---------------------------------------------------------------------------\n# This file is HAND-EDITED by the maintainer. The four blocks below cover\n# Chicago, Huff, SCS Type II, and free-form user-curated events. Schema-only\n# placeholder entries are preserved so the reader can validate keys before\n# the maintainer fills in real values.\n#\n# Three runtime consumers:\n#\n#   1. ``aiswmm storm --from-library <key>`` — looks up a chicago_hyetographs\n#      entry and constructs the Chicago hyetograph from its idf_params /\n#      peak_position. Numeric placeholder leaves cause the CLI to skip the\n#      entry with a clear "library entry not populated" message.\n#\n#   2. The Huff and SCS blocks let a maintainer add project-local overrides\n#      of the in-code dimensionless tables (typically not needed; the\n#      in-code defaults are the source of truth).\n#\n#   3. ``user_curated`` holds free-form historical / recorded events with\n#      a ``timeseries_csv`` reference. Currently consumed only by the\n#      reader; future CLI verbs will surface these directly.\n#\n# Loader: ``agentic_swmm/memory/storm_library.py``.\n# ===========================================================================\n\nschema_version: "1.0"\n\n# ---------------------------------------------------------------------------\n# Chicago hyetograph specifications keyed by region+return-period+duration.\n# Each entry\'s ``idf_params`` provides {a, b, c} for the IDF formula\n# ``i = a / (t + b)^c``. ``peak_position`` is the fractional location of\n# the peak (0.0..1.0).\n# ---------------------------------------------------------------------------\nchicago_hyetographs:\n  # Schema-demonstration entry. Replace placeholder fields with verified\n  # IDF parameters and add a ``citation`` key pointing into citations.yaml\n  # before relying on this entry at runtime.\n  example_region_100yr_3hr_5min:\n    idf_params:\n      a: null\n      b: null\n      c: null\n    peak_position: null\n    duration_min: 180\n    interval_min: 5\n    citation: null\n\n# ---------------------------------------------------------------------------\n# Huff quartile distributions are computed in code from embedded tables;\n# this block lets the maintainer override / add user-curated regional\n# variants. Each override entry mirrors the in-code shape:\n#   quartile: 1..4\n#   cumulative: [0.1, 0.2, ..., 1.0]  # 10-point monotone-increasing\n# ---------------------------------------------------------------------------\nhuff_user_overrides: {}\n\n# ---------------------------------------------------------------------------\n# SCS Type II is computed in code; same override mechanism here. Each\n# override entry holds:\n#   total_hours: 24\n#   cumulative: [[hours, fraction], ...]\n# ---------------------------------------------------------------------------\nscs_user_overrides: {}\n\n# ---------------------------------------------------------------------------\n# Free-form user-curated events (one entry per historical or design storm).\n# ---------------------------------------------------------------------------\nuser_curated:\n  # Schema-demonstration entry. ``timeseries_csv`` is the relative path to\n  # a two-column CSV (timestamp, intensity_mm_per_hr).\n  example_recorded_event:\n    source: null\n    timeseries_csv: null\n    notes: null\n',
}

def _content_for(filename: str) -> str:
    if filename in _YAML_SKELETONS:
        return _YAML_SKELETONS[filename]
    """Return the initial content for ``filename``.

    JSONL stores get an empty string (the file just needs to exist
    so audit-hook appends find it). The YAML and README files get
    static content authored above.
    """
    if filename == "project_overrides.yaml":
        return _PROJECT_OVERRIDES_HEADER
    if filename == "README.md":
        return _README_CONTENT
    return ""


def bootstrap_memory_dir(target_dir: Path | None = None) -> BootstrapResult:
    """Create the memory skeleton under ``target_dir`` and return the result.

    Arguments:
        target_dir: Directory to scaffold. ``None`` defaults to the
            directory the runtime and doctor read (``resolve_memory_dir()``:
            ``AISWMM_MEMORY_DIR``, else the checkout's or the installed
            package's ``memory/modeling-memory``). Created if missing.

    Returns:
        A :class:`BootstrapResult` describing what was created vs.
        skipped. Existing files are never overwritten — the
        idempotent contract is the whole point of the command.
    """
    # Live finding F-132 (2026-09-04, pip install probed from /tmp): the default
    # was ./memory/modeling-memory relative to the CURRENT DIRECTORY, so the
    # skeleton landed wherever the user happened to be and doctor, which reads
    # resolve_memory_dir(), kept saying "run aiswmm bootstrap memory".
    base = target_dir.expanduser() if target_dir is not None else resolve_memory_dir()
    base.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    skipped: list[Path] = []
    for filename in _SKELETON_FILES:
        path = base / filename
        if path.exists():
            skipped.append(path)
            continue
        path.write_text(_content_for(filename), encoding="utf-8")
        created.append(path)
    return BootstrapResult(target_dir=base, created=created, skipped=skipped)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``aiswmm bootstrap memory`` subcommand.

    The outer ``bootstrap`` namespace exists in case we add more
    bootstrap targets later (e.g. ``aiswmm bootstrap docs``); the
    current sole sub-target is ``memory``.
    """
    parser = subparsers.add_parser(
        "bootstrap",
        help="Scaffold project-local memory and other onboarding files.",
    )
    # Also expose ``--example`` on the ``bootstrap`` verb itself so
    # ``aiswmm bootstrap --example`` works without naming a sub-target.
    register_example_flag(parser, example_text=_BOOTSTRAP_EXAMPLE)
    inner = parser.add_subparsers(dest="bootstrap_target", required=True)
    memory_parser = inner.add_parser(
        "memory",
        help=(
            "Create memory/modeling-memory/ with empty stores so the audit "
            "hook has somewhere to append to."
        ),
    )
    memory_parser.add_argument(
        "--dir",
        dest="target_dir",
        type=Path,
        default=None,
        help=(
            "Directory to scaffold. Default: ./memory/modeling-memory/ "
            "relative to the current working directory."
        ),
    )
    # PRD-08 A.2 (audit #20): the description above intentionally does
    # NOT promise that citations.yaml or reference_benchmarks.yaml will
    # be seeded — those files are project-shipped artifacts the user
    # is expected to maintain by hand.
    register_json_flag(
        memory_parser,
        help_text=(
            "Emit the BootstrapResult as JSON so CI can compare "
            "created/skipped lists without parsing prose."
        ),
    )
    register_quiet_flag(memory_parser)
    register_example_flag(memory_parser, example_text=_BOOTSTRAP_EXAMPLE)
    memory_parser.set_defaults(func=memory_main)


def memory_main(args: argparse.Namespace) -> int:
    """Drive ``aiswmm bootstrap memory`` from argparse to stdout.

    Always returns 0 — the command is idempotent, so "everything was
    already in place" is a success, not a failure. Returning a non-
    zero code would break the CI "ensure-present" use case.
    """
    result = bootstrap_memory_dir(getattr(args, "target_dir", None))
    quiet = bool(getattr(args, "quiet", False))
    if getattr(args, "json", False):
        payload = {
            "target_dir": str(result.target_dir),
            "created": [str(p) for p in result.created],
            "skipped": [str(p) for p in result.skipped],
            # PRD-08 A.2 (audit #20): be explicit that the bootstrap
            # command does NOT seed citations.yaml or
            # reference_benchmarks.yaml; those are user-maintained.
            "not_seeded": [
                "citations.yaml",
                "reference_benchmarks.yaml",
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if quiet:
        # ``--quiet`` collapses to a single-line summary so callers
        # in CI can grep for "ensure-present" success without
        # parsing the multi-line block.
        print(
            f"bootstrap memory: target_dir={result.target_dir} "
            f"created={len(result.created)} skipped={len(result.skipped)}"
        )
        return 0
    print(f"target_dir: {result.target_dir}")
    if result.created:
        print(f"created ({len(result.created)}):")
        for path in result.created:
            print(f"  + {path.name}")
    else:
        print("created: (none)")
    if result.skipped:
        print(f"skipped ({len(result.skipped)}):")
        for path in result.skipped:
            print(f"  = {path.name}")
    else:
        print("skipped: (none)")
    # PRD-08 A.2 (audit #20): clarify scope so the user does not
    # expect bootstrap to seed the project-shipped citation library.
    print(
        "note: citations.yaml and reference_benchmarks.yaml are "
        "separately maintained and not seeded by this command."
    )
    # PRD-08 Phase B (audit #22): point the user at follow-up commands
    # so they know what to do after the skeleton lands. Doctor confirms
    # the stores are visible, ``cite --help`` is where the citation
    # library workflow begins.
    print("")
    print("Next:")
    print(
        "  - aiswmm doctor                 "
        "- confirm the new memory stores are visible"
    )
    print(
        "  - aiswmm cite --help            "
        "- populate citations.yaml when you're ready"
    )
    return 0


__all__ = [
    "BootstrapResult",
    "bootstrap_memory_dir",
    "memory_main",
    "register",
]
