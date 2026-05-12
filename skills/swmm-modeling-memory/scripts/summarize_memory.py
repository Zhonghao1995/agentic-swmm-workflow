#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_FILES = ("experiment_provenance.json", "comparison.json", "experiment_note.md")
MARKDOWN_OUTPUTS = (
    "modeling_memory_index.md",
    "lessons_learned.md",
    "skill_update_proposals.md",
    "benchmark_verification_plan.md",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def safe_name(value: Any) -> str:
    text = str(value or "run").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "run"


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def stringify_items(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            label = item.get("id") or item.get("name") or item.get("status") or item.get("detail")
            detail = item.get("detail") or item.get("message") or item.get("interpretation")
            if label and detail and label != detail:
                out.append(f"{label}: {detail}")
            elif label:
                out.append(str(label))
            else:
                out.append(json.dumps(item, sort_keys=True))
        else:
            out.append(str(item))
    return out


def discover_run_dirs(runs_dir: Path) -> list[Path]:
    if not runs_dir.exists():
        return []
    candidates: set[Path] = set()
    for name in AUDIT_FILES:
        for path in runs_dir.rglob(name):
            candidates.add(path.parent)
    return sorted(candidates)


def artifact_exists(record: Any) -> bool | None:
    if not isinstance(record, dict):
        return None
    exists = record.get("exists")
    if isinstance(exists, bool):
        return exists
    rel = record.get("relative_path")
    abs_path = record.get("absolute_path")
    if rel or abs_path:
        return True
    return None


def artifact_status(provenance: dict[str, Any], artifact_id: str) -> str:
    artifacts = provenance.get("artifacts")
    if not isinstance(artifacts, dict):
        return "unknown"
    exists = artifact_exists(artifacts.get(artifact_id))
    if exists is True:
        return "found"
    if exists is False:
        return "missing"
    return "unknown"


def collect_artifact_ids(provenance: dict[str, Any]) -> tuple[list[str], list[str]]:
    artifacts = provenance.get("artifacts")
    found: list[str] = []
    missing: list[str] = []
    if not isinstance(artifacts, dict):
        return found, missing
    for artifact_id, record in artifacts.items():
        exists = artifact_exists(record)
        if exists is True:
            found.append(str(artifact_id))
        elif exists is False:
            missing.append(str(artifact_id))
    return sorted(found), sorted(missing)


def infer_qa_status(provenance: dict[str, Any]) -> str:
    qa = provenance.get("qa")
    if isinstance(qa, dict):
        status = qa.get("status")
        if isinstance(status, str) and status:
            return status
        fail_count = qa.get("fail_count")
        if fail_count == 0:
            return "pass"
        if isinstance(fail_count, int) and fail_count > 0:
            return "fail"
    status = provenance.get("status")
    return str(status) if status else "unknown"


def extract_limitations(provenance: dict[str, Any], note_text: str) -> list[str]:
    limitations = stringify_items(as_list(provenance.get("limitations")))
    if limitations:
        return limitations

    lines = note_text.splitlines()
    extracted: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        heading = stripped.lower().lstrip("# ").strip()
        if heading in {"limitations", "evidence boundary", "evidence boundaries"}:
            capture = True
            continue
        if capture and stripped.startswith("#"):
            break
        if capture and stripped.startswith("-"):
            extracted.append(stripped.lstrip("-").strip())
    return extracted


def extract_assumptions(provenance: dict[str, Any], note_text: str) -> list[str]:
    assumptions = stringify_items(as_list(provenance.get("assumptions")))
    if assumptions:
        return assumptions

    extracted: list[str] = []
    capture = False
    for line in note_text.splitlines():
        stripped = line.strip()
        heading = stripped.lower().lstrip("# ").strip()
        if heading in {"assumptions", "modeling assumptions", "modelling assumptions"}:
            capture = True
            continue
        if capture and stripped.startswith("#"):
            break
        if capture and stripped.startswith("-"):
            extracted.append(stripped.lstrip("-").strip())
    return extracted


def evidence_boundary_notes(provenance: dict[str, Any], note_text: str) -> list[str]:
    notes = stringify_items(as_list(provenance.get("evidence_boundary_notes")))
    notes.extend(stringify_items(as_list(provenance.get("warnings"))))
    notes.extend(extract_limitations(provenance, note_text))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in notes:
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def comparison_status(comparison: dict[str, Any]) -> str:
    if not comparison:
        return "missing"
    if comparison.get("comparison_available") is False:
        return "not_requested"
    checks = comparison.get("checks")
    if isinstance(checks, list) and checks:
        mismatches = [c for c in checks if isinstance(c, dict) and c.get("same") is False]
        if mismatches:
            return "mismatch"
        return "match"
    return "available"


def detect_failure_patterns(
    *,
    run_dir: Path,
    provenance: dict[str, Any],
    comparison: dict[str, Any],
    artifacts_missing: list[str],
    audit_files_found: list[str],
) -> list[str]:
    patterns: set[str] = set()

    if "experiment_provenance.json" not in audit_files_found:
        patterns.add("missing_provenance")
    if "experiment_note.md" not in audit_files_found:
        patterns.add("missing_evidence_boundary")
    if not (run_dir / "manifest.json").exists() and artifact_status(provenance, "top_manifest") != "found":
        patterns.add("missing_manifest")

    if "model_inp" in artifacts_missing or not provenance and not list(run_dir.rglob("*.inp")):
        patterns.add("missing_inp")
    if "runner_rpt" in artifacts_missing:
        patterns.add("missing_rpt")
    if "runner_out" in artifacts_missing:
        patterns.add("missing_out")

    qa_status = infer_qa_status(provenance)
    if qa_status == "unknown":
        patterns.add("qa_missing")
    elif qa_status.lower() in {"fail", "failed"}:
        patterns.add("qa_failed")

    metrics = provenance.get("metrics") if isinstance(provenance.get("metrics"), dict) else {}
    swmm_return_code = metrics.get("swmm_return_code") if isinstance(metrics, dict) else None
    if swmm_return_code not in (None, 0):
        patterns.add("swmm_execution_failed")

    if isinstance(metrics, dict):
        peak = metrics.get("peak_flow")
        continuity = metrics.get("continuity_error")
        if not peak:
            patterns.add("peak_flow_parse_missing")
        if not continuity:
            patterns.add("continuity_parse_missing")

    if comparison_status(comparison) == "mismatch":
        patterns.add("comparison_mismatch")

    if patterns & {
        "missing_provenance",
        "missing_manifest",
        "missing_inp",
        "missing_rpt",
        "missing_out",
        "qa_missing",
        "peak_flow_parse_missing",
        "continuity_parse_missing",
    }:
        patterns.add("partial_run")

    if not patterns:
        return ["no_detected_failure"]
    return sorted(patterns)


def build_record(run_dir: Path, runs_dir: Path) -> dict[str, Any]:
    provenance_path = run_dir / "experiment_provenance.json"
    comparison_path = run_dir / "comparison.json"
    note_path = run_dir / "experiment_note.md"
    provenance = read_json(provenance_path)
    comparison = read_json(comparison_path)
    note_text = read_text(note_path)

    audit_files_found = [name for name in AUDIT_FILES if (run_dir / name).exists()]
    audit_files_missing = [name for name in AUDIT_FILES if name not in audit_files_found]
    artifacts_found, artifacts_missing = collect_artifact_ids(provenance)
    qa_status = infer_qa_status(provenance)
    metrics = provenance.get("metrics") if isinstance(provenance.get("metrics"), dict) else {}

    record = {
        "run_id": provenance.get("run_id") or run_dir.name,
        "run_dir": relpath(run_dir, runs_dir.parent),
        "case_name": provenance.get("case_name") or run_dir.name,
        "workflow_mode": provenance.get("workflow_mode") or "unknown",
        "objective": provenance.get("objective") or "",
        "audit_status": provenance.get("status") or ("partial" if audit_files_missing else "unknown"),
        "qa_status": qa_status,
        "swmm_return_code": metrics.get("swmm_return_code") if isinstance(metrics, dict) else None,
        "artifacts_found": artifacts_found,
        "artifacts_missing": sorted(set(artifacts_missing + audit_files_missing)),
        "warnings": stringify_items(as_list(provenance.get("warnings")) + as_list(comparison.get("warnings"))),
        "limitations": extract_limitations(provenance, note_text),
        "metrics": metrics,
        "comparison_status": comparison_status(comparison),
        "failure_patterns": [],
        "assumptions": extract_assumptions(provenance, note_text),
        "evidence_boundary_notes": evidence_boundary_notes(provenance, note_text),
    }
    record["failure_patterns"] = detect_failure_patterns(
        run_dir=run_dir,
        provenance=provenance,
        comparison=comparison,
        artifacts_missing=record["artifacts_missing"],
        audit_files_found=audit_files_found,
    )
    return record


def has_detected_failure(record: dict[str, Any]) -> bool:
    return record.get("failure_patterns") != ["no_detected_failure"]


def md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_index_md(records: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Modeling Memory Index",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
        "Source contract: this index is derived from audited run artifacts, not raw chat history or unsupported external claims.",
        "",
        "| Run | Case | Workflow | QA | SWMM RC | Comparison | Missing evidence | Assumptions | Warnings | Failure patterns | Evidence boundary |",
        "|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(r["run_id"]),
                    md_escape(r["case_name"]),
                    md_escape(r["workflow_mode"]),
                    md_escape(r["qa_status"]),
                    md_escape(r["swmm_return_code"]),
                    md_escape(r["comparison_status"]),
                    md_escape("; ".join(r["artifacts_missing"][:3])),
                    md_escape("; ".join(r["assumptions"][:3])),
                    md_escape("; ".join(r["warnings"][:3])),
                    md_escape(", ".join(r["failure_patterns"])),
                    md_escape("; ".join(r["evidence_boundary_notes"][:3])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_run_memory_note(record: dict[str, Any], generated_at: str) -> str:
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    peak = metrics.get("peak_flow") if isinstance(metrics, dict) else None
    continuity = metrics.get("continuity_error") if isinstance(metrics, dict) else None
    lines = [
        f"# Run Memory: {record.get('run_id')}",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
        "## Run",
        "",
        f"- Run ID: `{record.get('run_id')}`",
        f"- Case: `{record.get('case_name')}`",
        f"- Workflow: `{record.get('workflow_mode')}`",
        f"- Run directory: `{record.get('run_dir')}`",
        f"- Audit status: `{record.get('audit_status')}`",
        f"- QA status: `{record.get('qa_status')}`",
        f"- SWMM return code: `{record.get('swmm_return_code')}`",
        "",
        "## Metrics",
        "",
    ]
    if isinstance(peak, dict):
        lines.extend(
            [
                f"- Peak flow node: `{peak.get('node')}`",
                f"- Peak flow value: `{peak.get('value')}` `{peak.get('unit')}`",
                f"- Peak flow time: `{peak.get('time_hhmm')}`",
                f"- Peak source: `{peak.get('source_section')}`",
            ]
        )
    else:
        lines.append("- Peak flow: missing")
    if isinstance(continuity, dict):
        lines.append(f"- Continuity error: `{json.dumps(continuity.get('values'), sort_keys=True)}`")
    else:
        lines.append("- Continuity error: missing")
    lines.extend(["", "## Evidence Boundary", ""])
    notes = record.get("evidence_boundary_notes") or []
    if notes:
        lines.extend(f"- {note}" for note in notes)
    else:
        lines.append("- No additional evidence-boundary note was recorded.")
    lines.extend(["", "## Failure Patterns", ""])
    for pattern in record.get("failure_patterns") or ["unknown"]:
        lines.append(f"- `{pattern}`")
    lines.extend(["", "## Improvement Signal", ""])
    if has_detected_failure(record):
        lines.append("- This run should be reviewed for possible skill, MCP, CLI, documentation, or benchmark improvements.")
    else:
        lines.append("- No failure-driven improvement signal was detected for this run.")
    lines.append("")
    return "\n".join(lines)


def write_memory_snapshots(out_dir: Path, records: list[dict[str, Any]], index: dict[str, Any], generated_at: str) -> Path:
    stamp = snapshot_stamp(generated_at)
    snapshot_dir = out_dir / "snapshots" / stamp
    write_json(snapshot_dir / "modeling_memory_index.json", index)
    write_text(snapshot_dir / "modeling_memory_index.md", render_index_md(records, generated_at))
    write_text(snapshot_dir / "lessons_learned.md", render_lessons(records, generated_at))
    write_text(snapshot_dir / "skill_update_proposals.md", render_proposals(records, generated_at))
    write_text(snapshot_dir / "benchmark_verification_plan.md", render_benchmark_plan(generated_at))
    return snapshot_dir


def write_by_run_memory(out_dir: Path, records: list[dict[str, Any]], generated_at: str) -> None:
    for record in records:
        run_dir = out_dir / "by-run" / safe_name(record.get("run_id"))
        write_json(run_dir / "memory_record.json", record)
        write_text(run_dir / "memory_note.md", render_run_memory_note(record, generated_at))


def snapshot_stamp(generated_at: str) -> str:
    return generated_at.replace("+00:00", "Z").replace("-", "").replace(":", "")


def repeated_items(records: list[dict[str, Any]], key: str) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for record in records:
        counter.update(str(item) for item in record.get(key, []) if item)
    return [(item, count) for item, count in counter.most_common() if count >= 2]


def render_lessons(records: list[dict[str, Any]], generated_at: str) -> str:
    failure_counts = Counter()
    qa_counts = Counter()
    comparison_counts = Counter()
    for record in records:
        failure_counts.update(record["failure_patterns"])
        qa_counts.update([record["qa_status"]])
        comparison_counts.update([record["comparison_status"]])

    successful = [r for r in records if r["failure_patterns"] == ["no_detected_failure"]]
    lines = [
        "# Lessons Learned",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
        "This synthesis is derived from historical experiment audit artifacts. It is project memory, not proof that a model is calibrated or validated.",
        "",
        "## Source Contract",
        "",
        "- Evidence comes from audited run artifacts such as `experiment_provenance.json`, `comparison.json`, `experiment_note.md`, manifests, QA summaries, SWMM reports, and plots.",
        "- Assumptions, missing evidence, repeated failure patterns, and proposed improvements are kept separate from completed modeling claims.",
        "- External case-study evidence is not included unless it is explicitly referenced by audited run artifacts.",
        "",
        "## Repeated Failure Patterns",
    ]
    if failure_counts:
        for name, count in failure_counts.most_common():
            lines.append(f"- `{name}`: {count} run(s)")
    else:
        lines.append("- No audited runs were found.")

    lines.extend(["", "## Repeated Assumptions"])
    assumptions = repeated_items(records, "assumptions")
    if assumptions:
        for item, count in assumptions:
            lines.append(f"- {item} ({count} run(s))")
    else:
        lines.append("- No repeated assumptions were detected in the audited records.")

    lines.extend(["", "## Repeated Missing Evidence"])
    missing_counter: Counter[str] = Counter()
    for record in records:
        missing_counter.update(record.get("artifacts_missing", []))
    if missing_counter:
        for name, count in missing_counter.most_common():
            lines.append(f"- `{name}` missing in {count} run(s)")
    else:
        lines.append("- No repeated missing artifacts were detected.")

    lines.extend(["", "## Repeated QA Issues"])
    for status, count in qa_counts.most_common():
        lines.append(f"- QA status `{status}`: {count} run(s)")

    lines.extend(["", "## Run-to-Run Difference Signals"])
    for status, count in comparison_counts.most_common():
        lines.append(f"- Comparison status `{status}`: {count} run(s)")

    lines.extend(["", "## Successful Practices"])
    if successful:
        for record in successful:
            lines.append(
                f"- `{record['run_id']}` preserved audit evidence with QA `{record['qa_status']}` and comparison `{record['comparison_status']}`."
            )
    else:
        lines.append("- No run was classified as `no_detected_failure`.")
    lines.extend(
        [
            "",
            "## Validation Boundary",
            "",
            "- A repeated successful practice is operational evidence, not calibration or validation evidence.",
            "- Calibration requires observed data and recorded parameter-selection evidence.",
            "- Validation requires independent evidence beyond a successful SWMM execution and audit.",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def proposal_for_pattern(pattern: str) -> tuple[str, str, list[str]]:
    mapping = {
        "missing_inp": (
            "SWMM build/input handoff",
            "Ensure the workflow records where the runnable INP should be produced before SWMM execution.",
            ["swmm-builder", "swmm-end-to-end"],
        ),
        "missing_rpt": (
            "SWMM runner artifact contract",
            "Ensure failed and successful runs both record expected RPT paths and missing-artifact evidence.",
            ["swmm-runner", "swmm-experiment-audit"],
        ),
        "missing_out": (
            "SWMM runner artifact contract",
            "Ensure failed and successful runs both record expected OUT paths and missing-artifact evidence.",
            ["swmm-runner", "swmm-experiment-audit"],
        ),
        "swmm_execution_failed": (
            "SWMM execution diagnostics",
            "Improve command logging and failure explanation around non-zero SWMM return codes.",
            ["swmm-runner", "swmm-end-to-end"],
        ),
        "qa_missing": (
            "QA gate",
            "Make QA generation or QA-missing reporting explicit before evidence is treated as checked.",
            ["swmm-runner", "swmm-experiment-audit", "swmm-end-to-end"],
        ),
        "qa_failed": (
            "QA gate",
            "Clarify how failed QA is reported and preserved for audit rather than hidden.",
            ["swmm-runner", "swmm-experiment-audit", "swmm-end-to-end"],
        ),
        "peak_flow_parse_missing": (
            "Peak-flow parsing",
            "Check whether the correct SWMM report section is available and whether the parser should report a clearer boundary.",
            ["swmm-runner", "swmm-experiment-audit"],
        ),
        "continuity_parse_missing": (
            "Continuity parsing",
            "Check whether continuity tables are absent, malformed, or not referenced in the run manifest.",
            ["swmm-runner", "swmm-experiment-audit"],
        ),
        "comparison_mismatch": (
            "Run comparison",
            "Review whether mismatches are expected scenario differences or regressions that need acceptance criteria.",
            ["swmm-experiment-audit", "swmm-end-to-end"],
        ),
        "missing_manifest": (
            "Manifest generation",
            "Ensure each stage writes a manifest or that missing manifests are explicitly documented.",
            ["swmm-builder", "swmm-runner", "swmm-experiment-audit", "swmm-end-to-end"],
        ),
        "missing_provenance": (
            "Experiment audit",
            "Run the audit layer for partial and failed runs so downstream memory can use stable provenance.",
            ["swmm-experiment-audit", "swmm-end-to-end"],
        ),
        "missing_evidence_boundary": (
            "Experiment note",
            "Ensure human-readable audit notes state what is executed, inferred, missing, and outside scope.",
            ["swmm-experiment-audit"],
        ),
        "partial_run": (
            "Workflow stop handling",
            "Make partial-run handoff to audit explicit so incomplete evidence is still reusable.",
            ["swmm-end-to-end", "swmm-experiment-audit"],
        ),
        "unknown_failure": (
            "Failure classification",
            "Inspect the run manually and add a deterministic rule only after human review.",
            ["swmm-modeling-memory"],
        ),
    }
    return mapping.get(
        pattern,
        (
            "Workflow review",
            "Inspect recurring evidence and decide whether a skill refinement is warranted.",
            ["swmm-end-to-end"],
        ),
    )


def render_proposals(records: list[dict[str, Any]], generated_at: str) -> str:
    pattern_to_runs: dict[str, list[str]] = defaultdict(list)
    for record in records:
        for pattern in record["failure_patterns"]:
            if pattern != "no_detected_failure":
                pattern_to_runs[pattern].append(str(record["run_id"]))

    lines = [
        "# Skill Update Proposals",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
        "Agentic SWMM is not only an automation workflow; it is a memory-informed, verification-first modeling system that can learn from audited modeling history through controlled skill refinement.",
        "",
        "This document is only a proposal. It is not an automatic skill update and it is not evidence of correctness.",
        "",
        "The modeling-memory skill analyzes historical audit records and generates proposed refinements for relevant workflow skills, such as end-to-end orchestration, audit reporting, QA verification, model building, or result parsing.",
        "",
        "Accepted skill changes require human review and benchmark verification before any existing `SKILL.md` is modified.",
        "",
        "Proposal boundaries:",
        "",
        "- A proposal may identify a repeated workflow weakness.",
        "- A proposal may name candidate skills or scripts to review.",
        "- A proposal must not claim the fix is correct until benchmark runs and audit outputs verify it.",
        "- A proposal must not hide missing evidence by weakening QA or validation boundaries.",
        "",
    ]
    if not pattern_to_runs:
        lines.extend(["No recurring failure-driven skill update proposal was generated.", ""])
        return "\n".join(lines)

    for pattern in sorted(pattern_to_runs):
        step, reason, skills = proposal_for_pattern(pattern)
        runs = sorted(pattern_to_runs[pattern])
        lines.extend(
            [
                f"## `{pattern}`",
                "",
                f"- Potential skill or workflow step: {step}",
                f"- Relevant workflow skill(s): {', '.join(f'`{skill}`' for skill in skills)}",
                f"- Why it may need improvement: {reason}",
                f"- Evidence runs: {', '.join(f'`{run}`' for run in runs)}",
                "- Required control: human review plus benchmark verification before accepting any skill refinement.",
                "",
            ]
        )
    return "\n".join(lines)


def render_benchmark_plan(generated_at: str) -> str:
    return "\n".join(
        [
            "# Benchmark Verification Plan",
            "",
            f"Generated at UTC: `{generated_at}`",
            "",
            "Use this checklist before accepting any skill refinement proposed by modeling memory.",
            "",
            "- Identify the exact proposed skill or workflow change and the runs that motivated it.",
            "- Review the source audit artifacts manually, including `experiment_provenance.json`, `comparison.json`, and `experiment_note.md`.",
            "- Confirm the proposal does not change scientific modeling rules without human approval.",
            "- Run the existing acceptance check when available:",
            "",
            "```bash",
            "python3 scripts/acceptance/run_acceptance.py --run-id latest",
            "```",
            "",
            "- Run relevant benchmark commands when the proposed change touches benchmark behavior:",
            "",
            "```bash",
            "python3 scripts/benchmarks/run_tuflow_swmm_module03_raw_path.py",
            "python3 scripts/benchmarks/run_tecnopolo_199401.py",
            "```",
            "",
            "- Re-run experiment audit on affected runs before treating the change as evidence-backed.",
            "- Re-run modeling-memory summarization and check whether the repeated failure pattern is reduced without hiding missing evidence.",
            "- Accept the skill refinement only after human review confirms the benchmark and audit outputs remain interpretable.",
            "",
        ]
    )


def export_obsidian(out_dir: Path, obsidian_dir: Path) -> None:
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    for name in MARKDOWN_OUTPUTS:
        shutil.copy2(out_dir / name, obsidian_dir / name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize Agentic SWMM experiment audit artifacts into modeling memory and controlled skill-update proposals."
    )
    parser.add_argument("--runs-dir", required=True, type=Path, help="Directory containing audited run folders.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for generated modeling-memory outputs.")
    parser.add_argument("--obsidian-dir", type=Path, help="Optional Obsidian folder for Markdown exports.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runs_dir = args.runs_dir
    out_dir = args.out_dir
    generated_at = now_utc()

    run_dirs = discover_run_dirs(runs_dir)
    records = [build_record(run_dir, runs_dir) for run_dir in run_dirs]
    failure_count = sum(1 for record in records if has_detected_failure(record))

    index = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "source_runs_dir": str(runs_dir),
        "record_count": len(records),
        "run_folder_count": len(run_dirs),
        "failure_record_count": failure_count,
        "failure_pattern_counts": dict(Counter(p for r in records for p in r["failure_patterns"])),
        "qa_status_counts": dict(Counter(r["qa_status"] for r in records)),
        "comparison_status_counts": dict(Counter(r["comparison_status"] for r in records)),
        "records": records,
    }

    write_json(out_dir / "modeling_memory_index.json", index)
    write_text(out_dir / "modeling_memory_index.md", render_index_md(records, generated_at))
    write_text(out_dir / "lessons_learned.md", render_lessons(records, generated_at))
    write_text(out_dir / "skill_update_proposals.md", render_proposals(records, generated_at))
    write_text(out_dir / "benchmark_verification_plan.md", render_benchmark_plan(generated_at))
    snapshot_dir = write_memory_snapshots(out_dir, records, index, generated_at)
    write_by_run_memory(out_dir, records, generated_at)

    obsidian_used = False
    if args.obsidian_dir:
        export_obsidian(out_dir, args.obsidian_dir)
        obsidian_used = True

    print(f"run folders scanned: {len(run_dirs)}")
    print(f"audit records found: {len(records)}")
    print(f"runs with detected failures: {failure_count}")
    print(f"output directory: {out_dir}")
    print(f"snapshot directory: {snapshot_dir}")
    print(f"by-run memory directory: {out_dir / 'by-run'}")
    print(f"obsidian export used: {'yes' if obsidian_used else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
