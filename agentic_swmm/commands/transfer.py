"""``aiswmm transfer`` — cross-watershed transfer-learning surface (PRD-07 Phase 5).

Thin CLI on top of
:func:`agentic_swmm.memory.cross_watershed_transfer.recommend_parameters_for_new_case`.
Default output is a human-readable table; ``--json`` swaps to a
machine-readable JSON payload the next agent (Phase D HITL surface)
can consume directly.

No mutation. The recommender is advisory: it surfaces candidates and
their best parameter sets, but the user is the only path by which a
recommendation lands in an INP. This mirrors the rest of the PRD-07
contract — agent reads memory, agent proposes, human commits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_inp_flag,
    register_path_flag,
    register_quiet_flag,
)
from agentic_swmm.memory.cross_watershed_transfer import (
    TransferRecommendation,
    recommend_parameters_for_new_case,
)


_TRANSFER_EXAMPLE = "aiswmm transfer --inp examples/saanich/saanich.inp --top-k 3"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "transfer",
        help=(
            "Recommend warm-start parameters for a new INP by ranking "
            "calibrated prior cases by watershed similarity (PRD-07 Phase 5)."
        ),
    )
    register_inp_flag(
        parser,
        required=True,
        help_text="Path to the new case's INP file (read-only).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help=(
            "Maximum number of recommendations to surface (default 3). "
            "Lower values keep the table short; higher values surface "
            "all alternatives the agent considered."
        ),
    )
    # PRD-08 A.2: ``--calibration-memory-path`` is the canonical name;
    # ``--calibration-store`` continues to work as a deprecated alias.
    register_path_flag(
        parser,
        noun="calibration-memory",
        help_text=(
            "Path to calibration_memory.jsonl. Defaults to the project's "
            "canonical store under memory/modeling-memory/."
        ),
        default=Path("memory/modeling-memory/calibration_memory.jsonl"),
        legacy_aliases=("--calibration-store",),
        dest="calibration_store",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Repo root used by the conventional-location INP lookup. "
            "Defaults to the calibration store's grandparent directory."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON list instead of a table.",
    )
    register_path_flag(
        parser,
        noun="storm-library",
        help_text=(
            "Path to storm_library.yaml. Defaults to "
            "memory/modeling-memory/storm_library.yaml under the "
            "repo root."
        ),
        default=None,
        legacy_aliases=("--storm-library",),
        dest="storm_library",
    )
    register_path_flag(
        parser,
        noun="negative-lessons",
        help_text=(
            "Path to negative_lessons.jsonl. Defaults to "
            "memory/modeling-memory/negative_lessons.jsonl under the "
            "repo root."
        ),
        default=None,
        legacy_aliases=("--negative-lessons",),
        dest="negative_lessons",
    )
    parser.add_argument(
        "--benchmarks-path",
        type=Path,
        default=None,
        help=(
            "Path to reference_benchmarks.yaml. Defaults to "
            "memory/modeling-memory/reference_benchmarks.yaml under "
            "the repo root."
        ),
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_TRANSFER_EXAMPLE)
    parser.set_defaults(func=main)


def _format_table(recs: list[TransferRecommendation]) -> str:
    """Render recommendations as a fixed-width table.

    Designed for a terminal of ≥80 columns. Each row carries enough
    information for a user to decide whether to accept the warm
    start: source case, similarity, primary objective, and how many
    other candidates were considered.
    """
    if not recs:
        return (
            "no cross-watershed transfer candidates found "
            "(calibration store empty or no similar prior cases)\n"
        )

    header = (
        f"{'rank':>4}  "
        f"{'source_case':<24}  "
        f"{'similarity':>10}  "
        f"{'objective':<24}  "
        f"{'params':<28}\n"
    )
    sep = "-" * (len(header) - 1) + "\n"
    lines = [header, sep]
    for rank, rec in enumerate(recs, start=1):
        obj = "—"
        record = rec.source_calibration_record
        if record.objective_name and record.objective_value is not None:
            try:
                obj = f"{record.objective_name}={float(record.objective_value):.3f}"
            except (TypeError, ValueError):
                obj = str(record.objective_name)
        # Compact param signature: ``key1=v1, key2=v2`` truncated to
        # the column width so very large parameter sets do not
        # explode the row.
        params = ", ".join(
            f"{k}={v}" for k, v in sorted(rec.proposed_parameters.items())
        ) or "—"
        if len(params) > 27:
            params = params[:24] + "..."
        lines.append(
            f"{rank:>4}  "
            f"{rec.source_case[:24]:<24}  "
            f"{rec.similarity:>10.4f}  "
            f"{obj[:24]:<24}  "
            f"{params:<28}\n"
        )
    if recs[0].n_alternatives:
        lines.append(
            f"\n({recs[0].n_alternatives} additional candidate(s) ranked lower)\n"
        )
    return "".join(lines)


def _format_enrichment_sections(
    recs: list[TransferRecommendation], *, max_lessons: int = 3
) -> str:
    """Render Round-3 enrichment blocks under the main table.

    Each section prints only when at least one recommendation carries
    a non-empty value for that field. Recommendations are processed in
    rank order so the highest-similarity source's data leads.

    ``max_lessons`` caps the number of recent failure patterns shown
    per source so the default human-readable output stays terminal-
    friendly. The full lessons list is still available via ``--json``.
    """
    lines: list[str] = []
    for rec in recs:
        if rec.recommended_design_storm is not None:
            key = rec.recommended_design_storm.get("key") or "(unnamed)"
            lines.append(
                f"\nRecommended design storm: {key} from storm_library "
                f"(source: {rec.source_case})"
            )
        if rec.recommended_manning_n:
            joined = ", ".join(
                f"{k}={v}" for k, v in sorted(rec.recommended_manning_n.items())
            )
            lines.append(
                f"\nRecommended Manning's n starters from {rec.source_case}: "
                f"{joined}"
            )
        if rec.known_failure_patterns:
            shown = rec.known_failure_patterns[:max_lessons]
            lines.append(
                f"\nKnown failure patterns from {rec.source_case}: "
                f"{len(rec.known_failure_patterns)} lesson(s) — "
                f"{len(shown)} most-recent shown"
            )
            for lesson in shown:
                params = lesson.get("parameters_tried") or {}
                params_str = ", ".join(
                    f"{k}={v}" for k, v in sorted(params.items())
                ) or "—"
                lines.append(
                    f"  - {lesson.get('lesson_type', '?')}: "
                    f"params=[{params_str}] note={lesson.get('note') or ''}"
                )
    if lines:
        lines.append("")
    return "\n".join(lines)


def main(args: argparse.Namespace) -> int:
    target = args.inp
    if not target.is_file():
        print(f"error: --inp not found: {target}", file=sys.stderr)
        return 1
    try:
        recs = recommend_parameters_for_new_case(
            target,
            calibration_store=args.calibration_store,
            top_k=args.top_k,
            repo_root=args.repo_root,
            storm_library_path=args.storm_library,
            negative_lessons_store=args.negative_lessons,
            benchmarks_path=args.benchmarks_path,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "target_inp": str(target),
            "calibration_store": str(args.calibration_store),
            "top_k": int(args.top_k),
            "recommendations": [r.to_dict() for r in recs],
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_format_table(recs))
        enrichment = _format_enrichment_sections(recs)
        if enrichment:
            sys.stdout.write(enrichment)
    return 0
