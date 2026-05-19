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
from dataclasses import dataclass, field
from pathlib import Path


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
        target_dir: Directory to scaffold. ``None`` defaults to
            ``./memory/modeling-memory/`` relative to the current
            working directory. The path is created if missing.

    Returns:
        A :class:`BootstrapResult` describing what was created vs.
        skipped. Existing files are never overwritten — the
        idempotent contract is the whole point of the command.
    """
    base = (target_dir or _DEFAULT_DIR).expanduser()
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
    memory_parser.set_defaults(func=memory_main)


def memory_main(args: argparse.Namespace) -> int:
    """Drive ``aiswmm bootstrap memory`` from argparse to stdout.

    Always returns 0 — the command is idempotent, so "everything was
    already in place" is a success, not a failure. Returning a non-
    zero code would break the CI "ensure-present" use case.
    """
    result = bootstrap_memory_dir(getattr(args, "target_dir", None))
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
    return 0


__all__ = [
    "BootstrapResult",
    "bootstrap_memory_dir",
    "memory_main",
    "register",
]
