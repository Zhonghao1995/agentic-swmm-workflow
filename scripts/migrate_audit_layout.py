#!/usr/bin/env python3
"""Converge legacy audit-artefact patterns into ``<run-dir>/09_audit/``.

The audit-cleanup PRD enumerates five legacy patterns
(``.claude/prds/PRD_audit.md`` D6 table):

  P1 - audit files at run-dir root, e.g. ``runs/<case>/experiment_note.md``.
  P2 - audit files at bucket/case root, e.g.
       ``runs/benchmarks/<case>/experiment_note.md``.
  P3 - audit files at a deeply nested run dir, e.g.
       ``runs/external-case-candidates/<bucket>/<month>/<runner>/experiment_note.md``.
  P4 - unnumbered ``audit/`` dir with the GIS schema.
  P5 - empty ``06_audit/`` dir.

After ``--apply``:

  - P1/P2/P3: ``experiment_note.md``, ``experiment_provenance.json``,
    ``comparison.json`` (if present) are ``git mv``'d into a sibling
    ``09_audit/`` at the same depth.
  - P4: ``audit/`` is renamed to ``09_audit/`` (contents preserved). If
    no ``experiment_note.md`` exists after the rename, a stub is
    synthesized from the GIS manifests with frontmatter
    ``source: migrated-from-gis-audit``.
  - P5: empty ``06_audit/`` is removed; the next ``aiswmm audit`` run
    on that case will create ``09_audit/`` from scratch.

Usage::

    python3 scripts/migrate_audit_layout.py            # dry-run (default)
    python3 scripts/migrate_audit_layout.py --apply
    python3 scripts/migrate_audit_layout.py --apply --only P4

The script is idempotent: re-running on an already-migrated tree is a
no-op. ``git mv`` is used whenever the tree is tracked, so the move can
be reversed via ``git revert`` + ``git mv`` back per the PRD's Rollback
section.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


VALID_PATTERNS = ("P1", "P2", "P3", "P4", "P5")
LEGACY_ROOT_FILES = ("experiment_note.md", "experiment_provenance.json", "comparison.json")


@dataclass
class MigrationStep:
    """One concrete migration action to perform (or describe in dry-run)."""

    pattern: str
    run_dir: Path
    description: str
    # If the action is a set of file moves, list them here. ``rmdir`` and
    # ``synthesize`` actions express themselves via description only.
    moves: list[tuple[Path, Path]]
    # Optional callable to run after moves (e.g. stub synthesis).
    post_apply: object | None = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# git mv helpers (shared with archive_zombies)
# ---------------------------------------------------------------------------


def _git_available(cwd: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_mv(src: Path, dst: Path, repo_root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return proc.returncode == 0


def _move(src: Path, dst: Path, *, use_git: bool, repo_root: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if use_git and _git_mv(src, dst, repo_root):
        return
    shutil.move(str(src), str(dst))


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


def _looks_like_run_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    # Has any P1-style audit file or any of the SWMM stage hints.
    for name in LEGACY_ROOT_FILES:
        if (path / name).exists():
            return True
    for name in (
        "manifest.json",
        "acceptance_report.json",
        "05_builder",
        "06_runner",
        "08_plot",
        "07_qa",
        "06_qa",
        "audit",
        "06_audit",
        "09_audit",
    ):
        if (path / name).exists():
            return True
    return False


def _bucket_depth(run_dir: Path, runs_root: Path) -> int:
    try:
        return len(run_dir.relative_to(runs_root).parts)
    except ValueError:
        return 0


def _classify_root_pattern(run_dir: Path, runs_root: Path) -> str | None:
    """Return P1/P2/P3 when the dir has legacy root-level audit files."""
    if not any((run_dir / name).exists() for name in LEGACY_ROOT_FILES):
        return None
    depth = _bucket_depth(run_dir, runs_root)
    if depth <= 1:
        return "P1"
    if depth == 2:
        return "P2"
    return "P3"


def iter_candidate_dirs(runs_root: Path) -> Iterable[Path]:
    """Yield every dir under runs_root that could host audit artefacts.

    The migration must look inside bucket dirs (``benchmarks/``,
    ``end-to-end/`` etc.) and arbitrarily deep nesting
    (``external-case-candidates/.../runner-fixed/``).
    """
    if not runs_root.exists():
        return
    for path in runs_root.rglob("*"):
        if not path.is_dir():
            continue
        if any(part == ".archive" or part.startswith(".") for part in path.relative_to(runs_root).parts):
            continue
        yield path


# ---------------------------------------------------------------------------
# Pattern-specific planners
# ---------------------------------------------------------------------------


def _plan_p1_p2_p3(run_dir: Path, runs_root: Path) -> MigrationStep | None:
    pattern = _classify_root_pattern(run_dir, runs_root)
    if pattern is None:
        return None
    audit_dir = run_dir / "09_audit"
    moves: list[tuple[Path, Path]] = []
    for name in LEGACY_ROOT_FILES:
        src = run_dir / name
        if src.exists() and not (audit_dir / name).exists():
            moves.append((src, audit_dir / name))
    if not moves:
        return None
    return MigrationStep(
        pattern=pattern,
        run_dir=run_dir,
        description=f"git mv {len(moves)} file(s) into {audit_dir.relative_to(runs_root)}",
        moves=moves,
    )


def _plan_p4(run_dir: Path, runs_root: Path) -> MigrationStep | None:
    audit_dir = run_dir / "audit"
    target = run_dir / "09_audit"
    if not audit_dir.is_dir():
        return None
    if target.exists():
        return None  # already converged
    # Single dir-level move; the script handles the rename specially.

    def _post(repo_root: Path, *, use_git: bool, dry_run: bool) -> str:
        # After audit/ -> 09_audit/ rename, ensure experiment_note.md and
        # experiment_provenance.json exist with the migrated-from-gis stub.
        if dry_run:
            return f"would synthesize stub experiment_note.md + experiment_provenance.json in {target.relative_to(runs_root)}"
        return _synthesize_p4_stub(target)

    return MigrationStep(
        pattern="P4",
        run_dir=run_dir,
        description=f"rename audit/ -> 09_audit/ and (re-)synthesize note stub at {target.relative_to(runs_root)}",
        moves=[(audit_dir, target)],
        post_apply=_post,
    )


def _plan_p5(run_dir: Path, runs_root: Path) -> MigrationStep | None:
    legacy = run_dir / "06_audit"
    if not legacy.is_dir():
        return None
    try:
        children = list(legacy.iterdir())
    except OSError:
        return None
    if children:
        return None  # not empty; skip with warning per PRD
    return MigrationStep(
        pattern="P5",
        run_dir=run_dir,
        description=f"rmdir empty {legacy.relative_to(runs_root)}",
        moves=[],
        post_apply=lambda repo_root, *, use_git, dry_run: (
            f"would rmdir {legacy.relative_to(runs_root)}"
            if dry_run
            else _rmdir_safe(legacy)
        ),
    )


def _rmdir_safe(path: Path) -> str:
    try:
        path.rmdir()
        return f"rmdir {path}"
    except OSError as exc:
        return f"skip rmdir {path}: {exc}"


def _synthesize_p4_stub(audit_dir: Path) -> str:
    """Write a minimal experiment_note.md and experiment_provenance.json
    inside a freshly-renamed P4 ``09_audit/``.
    """
    note_path = audit_dir / "experiment_note.md"
    prov_path = audit_dir / "experiment_provenance.json"
    case_name = audit_dir.parent.name
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    actions: list[str] = []

    # Pull a one-line summary from any pre-existing method_summary.md.
    summary_line = ""
    msum = audit_dir / "method_summary.md"
    if msum.exists():
        for line in msum.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                summary_line = line
                break

    if not note_path.exists():
        note_text = "\n".join(
            [
                "---",
                "type: experiment-audit",
                "source: migrated-from-gis-audit",
                f"case: {case_name}",
                f"date: {now[:10]}",
                "status: migrated",
                "tags:",
                "  - agentic-swmm",
                "  - experiment-audit",
                "  - migrated-from-gis-audit",
                "---",
                "",
                f"# Experiment Audit (migrated) - {case_name}",
                "",
                "This note was synthesized from a legacy GIS-style `audit/` directory ",
                "during the audit-layer cleanup migration. The original artefacts ",
                "(method_summary.md, input_checksums.json, processing_commands.json, ",
                "qgis_entropy_run_manifest.json) are preserved in this folder.",
                "",
            ]
        )
        if summary_line:
            note_text += "\n## Method summary (carried forward)\n\n" + summary_line + "\n"
        note_path.write_text(note_text, encoding="utf-8")
        actions.append(f"wrote stub {note_path}")

    if not prov_path.exists():
        prov_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "generated_by": "scripts/migrate_audit_layout.py",
                    "generated_at_utc": now,
                    "source": "migrated-from-gis-audit",
                    "case_name": case_name,
                    "status": "migrated",
                    "run_dir": {"relative_path": str(audit_dir.parent), "absolute_path": str(audit_dir.parent)},
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        actions.append(f"wrote stub {prov_path}")
    return "; ".join(actions) if actions else "stub already present"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def plan(runs_root: Path, *, only: str | None = None) -> list[MigrationStep]:
    """Compute the migration steps for the given runs/ tree."""
    seen_run_dirs: set[Path] = set()
    steps: list[MigrationStep] = []
    for candidate in iter_candidate_dirs(runs_root):
        if not _looks_like_run_dir(candidate):
            continue
        if candidate in seen_run_dirs:
            continue
        # If candidate is inside an already-classified run dir, skip; otherwise
        # multiple stage subdirs would each get classified.
        if any(parent in seen_run_dirs for parent in candidate.parents):
            continue
        seen_run_dirs.add(candidate)
        for planner in (_plan_p1_p2_p3, _plan_p4, _plan_p5):
            step = planner(candidate, runs_root)
            if step is None:
                continue
            if only and step.pattern != only:
                continue
            steps.append(step)
    return steps


def execute_step(step: MigrationStep, *, repo_root: Path, use_git: bool, dry_run: bool) -> list[str]:
    msgs: list[str] = []
    for src, dst in step.moves:
        if dry_run:
            msgs.append(f"would move: {src} -> {dst}")
            continue
        _move(src, dst, use_git=use_git, repo_root=repo_root)
        msgs.append(f"mv: {src} -> {dst}")
    if step.post_apply is not None:
        msg = step.post_apply(repo_root, use_git=use_git, dry_run=dry_run)  # type: ignore[misc]
        if msg:
            msgs.append(msg)
    return msgs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--runs-root", type=Path, default=Path("runs"), help="Path to runs/ to migrate.")
    p.add_argument("--apply", action="store_true", help="Apply moves; default is dry-run.")
    p.add_argument(
        "--only",
        choices=VALID_PATTERNS,
        help="Restrict to one pattern (P1/P2/P3/P4/P5).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runs_root: Path = args.runs_root.resolve()
    if not runs_root.exists():
        print(f"runs root does not exist: {runs_root}", file=sys.stderr)
        return 0
    repo_root = runs_root.parent
    use_git = _git_available(runs_root)
    steps = plan(runs_root, only=args.only)
    if not steps:
        print("nothing to migrate; tree is already converged on 09_audit/")
        return 0
    header = "DRY RUN" if not args.apply else "APPLY"
    print(f"{header}: {len(steps)} migration step(s)")
    for step in steps:
        print(f"  [{step.pattern}] {step.run_dir}: {step.description}")
        for msg in execute_step(step, repo_root=repo_root, use_git=use_git, dry_run=not args.apply):
            print(f"    {msg}")
    if not args.apply:
        print("Re-run with --apply to perform the migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
