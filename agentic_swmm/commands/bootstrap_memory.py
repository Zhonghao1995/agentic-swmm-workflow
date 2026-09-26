"""``aiswmm bootstrap memory`` — scaffold the memory store (PRD-06 Phase D.4).

A fresh workspace has no ``memory/store/`` directory; the ledgers
(``parametric_memory.jsonl``, ``calibration_memory.jsonl``,
``negative_lessons.jsonl``) are created lazily by the audit hook the
first time it appends a row. For human onboarding that lazy-create flow
is opaque, so this command creates the skeleton ahead of time so the
user can:

    * grep for the empty JSONL files and confirm where memory lives;
    * paste-edit ``project_overrides.yaml`` before the first run;
    * read the bundled ``README.md`` and follow the link to
      ``docs/memory_runtime.md`` for the substrate's contract.

The reference tables (``reference_benchmarks.yaml``, ``storm_library.yaml``,
``citations.yaml``) are not part of the skeleton: they ship with the
package under ``memory/initial/`` and a project overrides one by copying
it into the store (see ``utils.paths.reference_table_path``).

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


_BOOTSTRAP_EXAMPLE = "aiswmm bootstrap memory --dir memory/store"


# Default target directory. Matches the layout the rest of the package
# uses (``memory/store/``) so the bootstrap output lands where the audit
# hook will later append to it.
_DEFAULT_DIR = Path("memory") / "store"


# Filenames the skeleton creates. Kept as a module-level tuple so the
# CLI help text and the test suite can both reference the same list
# without drifting.
_SKELETON_FILES: tuple[str, ...] = (
    "parametric_memory.jsonl",
    "calibration_memory.jsonl",
    "negative_lessons.jsonl",
    "project_overrides.yaml",
    # The reference tables are no longer skeletons here (2026-09-06): they
    # ship under memory/initial/ and reference_table_path() reads a copy in
    # the store first, so a curated copy still survives upgrades.
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
    "# Memory store\n"
    "\n"
    "This directory is written by aiswmm in normal use and is gitignored.\n"
    "It holds the project's memory ledgers and the session database:\n"
    "\n"
    "* `parametric_memory.jsonl` — append-only log of run-level\n"
    "  parameters and QA metrics.\n"
    "* `calibration_memory.jsonl` — append-only log of accepted\n"
    "  calibrations and goodness-of-fit metrics.\n"
    "* `negative_lessons.jsonl` — append-only log of known-bad\n"
    "  parameter regions and failure codes.\n"
    "* `project_overrides.yaml` — per-project overlay on the shipped\n"
    "  reference benchmarks (memory/initial/reference_benchmarks.yaml).\n"
    "* `memory.sqlite` — the session database, rebuilt from runs/ on\n"
    "  demand (`aiswmm memory repair-sessions`).\n"
    "\n"
    "Hand-written memory ships with the package under memory/initial/;\n"
    "copy a reference table here to override it for this project.\n"
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




def _content_for(filename: str) -> str:
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
            ``AISWMM_MEMORY_DIR``, else ``memory/store`` under the
            workspace). Created if missing.

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
            "Create the memory store (memory/store/) with empty ledgers so "
            "the audit hook has somewhere to append to."
        ),
    )
    memory_parser.add_argument(
        "--dir",
        dest="target_dir",
        type=Path,
        default=None,
        help=(
            "Directory to scaffold. Default: the memory store the runtime "
            "and doctor read (memory/store/ under the workspace)."
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
            "shipped_reference_tables": "memory/initial/",
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
        "note: the reference tables (reference_benchmarks.yaml, storm_library.yaml, "
        "citations.yaml) ship with the package under memory/initial/; copy one "
        "into the store to override it for this project."
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
