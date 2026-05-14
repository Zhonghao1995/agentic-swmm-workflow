#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_FILES = ("experiment_provenance.json", "comparison.json", "experiment_note.md", "model_diagnostics.json")
# Schema 1.1 stores audit artefacts in <run-dir>/09_audit/. Old runs that
# still have files at run-dir root are tolerated as a read-only fallback.
AUDIT_SUBDIR = "09_audit"


def audit_dir_for(run_dir: Path) -> Path:
    """Return the canonical audit subdir under ``run_dir``.

    Falls back to ``run_dir`` itself when the legacy root-level layout is
    in use (so summarisation of un-migrated runs still works).
    """
    new = run_dir / AUDIT_SUBDIR
    if new.is_dir() and any((new / name).exists() for name in AUDIT_FILES):
        return new
    return run_dir
MARKDOWN_OUTPUTS = (
    "modeling_memory_index.md",
    "project_memory_index.md",
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


def safe_slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.lower()).strip("-._")
    return text or "unknown-project"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


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
    """Discover run directories that carry audit artefacts.

    Recognises both the 1.1 layout (``<run-dir>/09_audit/<file>``) and the
    legacy root-level layout (``<run-dir>/<file>``). The returned paths
    are always run dirs, never the ``09_audit`` subdir itself.
    """
    if not runs_dir.exists():
        return []
    candidates: set[Path] = set()
    for name in AUDIT_FILES:
        for path in runs_dir.rglob(name):
            parent = path.parent
            # If the file lives inside a 09_audit/ subdir, the actual
            # run dir is the grandparent. Otherwise, the parent is the
            # run dir (legacy root layout).
            if parent.name == AUDIT_SUBDIR:
                parent = parent.parent
            # Skip anything that ended up under .archive/ during cleanup.
            try:
                rel = parent.relative_to(runs_dir)
            except ValueError:
                continue
            if any(part == ".archive" for part in rel.parts):
                continue
            candidates.add(parent)
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


def project_key(record: dict[str, Any]) -> str:
    case = str(record.get("case_name") or record.get("run_id") or "").lower()
    workflow = str(record.get("workflow_mode") or "").lower()
    run_dir = str(record.get("run_dir") or "").lower()
    text = " ".join([case, workflow, run_dir])
    if "tod" in text or "todcreek" in text:
        return "tod-creek"
    if "tecnopolo" in text:
        return "tecnopolo"
    if "tuflow" in text:
        return "tuflow"
    if "generate_swmm_inp" in text or "generate-swmm-inp" in text:
        return "generate-swmm-inp"
    if "acceptance" in text:
        return "acceptance"
    return safe_slug(str(record.get("case_name") or record.get("workflow_mode") or "unknown-project"))


def diagnostic_ids(model_diagnostics: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in model_diagnostics.get("diagnostics") or []:
        if isinstance(item, dict) and item.get("id"):
            item_id = str(item["id"])
            if item_id not in seen:
                out.append(item_id)
                seen.add(item_id)
    return out


def suspect_parameters(model_diagnostics: dict[str, Any]) -> list[str]:
    suspects: set[str] = set()
    mapping = {
        "continuity_error_high": "routing_step / storage / inflow-outflow accounting",
        "node_flooding_detected": "node surcharge/flooding settings",
        "conduit_slope_suspicious": "node invert elevation / conduit length / conduit direction",
        "subcatchment_area_nonpositive": "subcatchment area",
        "subcatchment_width_nonpositive": "subcatchment width",
        "imperviousness_out_of_range": "subcatchment imperviousness",
        "missing_rain_gage": "rain gage assignment",
        "subcatchment_outlet_missing": "subcatchment outlet",
        "outfall_disconnected": "outfall connectivity",
        "routing_step_large": "routing step",
    }
    for item in model_diagnostics.get("diagnostics") or []:
        if not isinstance(item, dict):
            continue
        label = mapping.get(str(item.get("id") or ""))
        if label:
            suspects.add(label)
    return sorted(suspects)


def next_run_cautions(record: dict[str, Any]) -> list[str]:
    cautions: list[str] = []
    for pattern in record.get("failure_patterns", []):
        if pattern == "comparison_mismatch":
            cautions.append("Review whether run differences are expected scenario changes or regressions.")
        elif pattern == "continuity_parse_missing":
            cautions.append("Ensure continuity tables are available and referenced in run artifacts.")
        elif pattern == "missing_inp":
            cautions.append("Record the runnable SWMM INP handoff before execution.")
        elif pattern == "peak_flow_parse_missing":
            cautions.append("Confirm peak flow is parsed from Node Inflow Summary or documented fallback.")
        elif pattern == "partial_run":
            cautions.append("Keep partial-run evidence explicit so downstream memory can reuse it safely.")
    for item in record.get("model_diagnostic_ids", []):
        if item == "continuity_error_high":
            cautions.append("Inspect continuity error before treating the run as hydrologic evidence.")
        elif item == "node_flooding_detected":
            cautions.append("Review node flooding before accepting the model behavior.")
        elif item == "routing_step_large":
            cautions.append("Consider reducing routing step for the next diagnostic run.")
        elif item in {"subcatchment_area_nonpositive", "subcatchment_width_nonpositive", "imperviousness_out_of_range"}:
            cautions.append("Check subcatchment physical parameters before rerunning.")
        elif item in {"missing_rain_gage", "subcatchment_outlet_missing", "outfall_disconnected"}:
            cautions.append("Check model connectivity and rainfall assignments before rerunning.")
    deduped: list[str] = []
    seen: set[str] = set()
    for item in cautions:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def detect_failure_patterns(
    *,
    run_dir: Path,
    provenance: dict[str, Any],
    comparison: dict[str, Any],
    model_diagnostics: dict[str, Any],
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

    if model_diagnostics.get("status") == "fail":
        patterns.add("swmm_model_diagnostic_error")

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
    audit_dir = audit_dir_for(run_dir)
    provenance_path = audit_dir / "experiment_provenance.json"
    comparison_path = audit_dir / "comparison.json"
    note_path = audit_dir / "experiment_note.md"
    diagnostics_path = audit_dir / "model_diagnostics.json"
    provenance = read_json(provenance_path)
    comparison = read_json(comparison_path)
    model_diagnostics = read_json(diagnostics_path)
    note_text = read_text(note_path)

    audit_files_found = [name for name in AUDIT_FILES if (audit_dir / name).exists()]
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
        "model_diagnostics": model_diagnostics or provenance.get("model_diagnostics") or {},
        "comparison_status": comparison_status(comparison),
        "failure_patterns": [],
        "assumptions": extract_assumptions(provenance, note_text),
        "evidence_boundary_notes": evidence_boundary_notes(provenance, note_text),
    }
    record["failure_patterns"] = detect_failure_patterns(
        run_dir=run_dir,
        provenance=provenance,
        comparison=comparison,
        model_diagnostics=record["model_diagnostics"],
        artifacts_missing=record["artifacts_missing"],
        audit_files_found=audit_files_found,
    )
    record["project_key"] = project_key(record)
    record["model_diagnostic_ids"] = diagnostic_ids(record["model_diagnostics"])
    record["suspect_parameters"] = suspect_parameters(record["model_diagnostics"])
    record["next_run_cautions"] = next_run_cautions(record)
    return record


def build_run_memory_summary(record: dict[str, Any], generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_by": "swmm-modeling-memory",
        "generated_at_utc": generated_at,
        "run_id": record.get("run_id"),
        "run_dir": record.get("run_dir"),
        "project_key": record.get("project_key"),
        "case_name": record.get("case_name"),
        "workflow_mode": record.get("workflow_mode"),
        "success": record.get("failure_patterns") == ["no_detected_failure"] and record.get("qa_status") == "pass",
        "audit_status": record.get("audit_status"),
        "qa_status": record.get("qa_status"),
        "swmm_return_code": record.get("swmm_return_code"),
        "comparison_status": record.get("comparison_status"),
        "qa_issues": [] if record.get("qa_status") == "pass" else [record.get("qa_status")],
        "failure_patterns": record.get("failure_patterns", []),
        "missing_evidence": record.get("artifacts_missing", []),
        "warnings": record.get("warnings", []),
        "assumptions": record.get("assumptions", []),
        "evidence_boundary_notes": record.get("evidence_boundary_notes", []),
        "model_diagnostics_status": (record.get("model_diagnostics") or {}).get("status"),
        "model_diagnostic_ids": record.get("model_diagnostic_ids", []),
        "suspect_parameters": record.get("suspect_parameters", []),
        "next_run_cautions": record.get("next_run_cautions", []),
    }


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
        "| Run | Project | Case | Workflow | QA | SWMM RC | Comparison | Warnings | Failure patterns | Model diagnostics | Evidence boundary |",
        "|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(r["run_id"]),
                    md_escape(r["project_key"]),
                    md_escape(r["case_name"]),
                    md_escape(r["workflow_mode"]),
                    md_escape(r["qa_status"]),
                    md_escape(r["swmm_return_code"]),
                    md_escape(r["comparison_status"]),
                    md_escape("; ".join(r["warnings"][:3])),
                    md_escape(", ".join(r["failure_patterns"])),
                    md_escape(", ".join(r.get("model_diagnostic_ids", [])[:5])),
                    md_escape("; ".join(r["evidence_boundary_notes"][:3])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def repeated_items(records: list[dict[str, Any]], key: str) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for record in records:
        counter.update(str(item) for item in record.get(key, []) if item)
    return [(item, count) for item, count in counter.most_common() if count >= 2]


def records_by_project(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("project_key") or "unknown-project")].append(record)
    return dict(sorted(grouped.items()))


def project_summary(project: str, records: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    failure_counts: Counter[str] = Counter()
    diagnostic_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    qa_counts: Counter[str] = Counter()
    for record in records:
        failure_counts.update(record.get("failure_patterns", []))
        diagnostic_counts.update(record.get("model_diagnostic_ids", []))
        missing_counts.update(record.get("artifacts_missing", []))
        qa_counts.update([str(record.get("qa_status") or "unknown")])
    return {
        "schema_version": "1.0",
        "generated_by": "swmm-modeling-memory",
        "generated_at_utc": generated_at,
        "project_key": project,
        "record_count": len(records),
        "run_ids": [record.get("run_id") for record in records],
        "qa_status_counts": dict(qa_counts),
        "failure_pattern_counts": dict(failure_counts),
        "model_diagnostic_counts": dict(diagnostic_counts),
        "missing_evidence_counts": dict(missing_counts),
        "next_run_cautions": sorted({item for record in records for item in record.get("next_run_cautions", [])}),
    }


def render_project_memory_md(project: str, summary: dict[str, Any]) -> str:
    lines = [
        f"# Project Modeling Memory - {project}",
        "",
        f"Generated at UTC: `{summary['generated_at_utc']}`",
        "",
        f"- Runs: {summary['record_count']}",
        f"- Run IDs: {', '.join(f'`{run}`' for run in summary['run_ids'])}",
        "",
        "## QA States",
    ]
    for key, count in sorted(summary["qa_status_counts"].items()):
        lines.append(f"- `{key}`: {count} run(s)")
    lines.extend(["", "## Failure Patterns"])
    if summary["failure_pattern_counts"]:
        for key, count in sorted(summary["failure_pattern_counts"].items()):
            lines.append(f"- `{key}`: {count} run(s)")
    else:
        lines.append("- No failure patterns were detected.")
    lines.extend(["", "## SWMM Model Diagnostics"])
    if summary["model_diagnostic_counts"]:
        for key, count in sorted(summary["model_diagnostic_counts"].items()):
            lines.append(f"- `{key}`: {count} run(s)")
    else:
        lines.append("- No deterministic SWMM model diagnostics were recorded.")
    lines.extend(["", "## Missing Evidence"])
    if summary["missing_evidence_counts"]:
        for key, count in sorted(summary["missing_evidence_counts"].items()):
            lines.append(f"- `{key}` missing in {count} run(s)")
    else:
        lines.append("- No missing evidence was detected.")
    lines.extend(["", "## Next-Run Cautions"])
    if summary["next_run_cautions"]:
        for item in summary["next_run_cautions"]:
            lines.append(f"- {item}")
    else:
        lines.append("- No project-level cautions were generated.")
    lines.append("")
    return "\n".join(lines)


def render_project_index_md(project_summaries: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Project Memory Index",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
        "| Project | Runs | Failure patterns | Model diagnostics | Missing evidence |",
        "|---|---:|---|---|---|",
    ]
    for summary in project_summaries:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(summary["project_key"]),
                    md_escape(summary["record_count"]),
                    md_escape(", ".join(sorted(summary["failure_pattern_counts"].keys())) or "none"),
                    md_escape(", ".join(sorted(summary["model_diagnostic_counts"].keys())) or "none"),
                    md_escape(", ".join(sorted(summary["missing_evidence_counts"].keys())) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


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

    lines.extend(["", "## Repeated SWMM Model Diagnostics"])
    diagnostics = repeated_items(records, "model_diagnostic_ids")
    if diagnostics:
        for item, count in diagnostics:
            lines.append(f"- `{item}`: {count} run(s)")
    else:
        lines.append("- No repeated deterministic SWMM model diagnostics were detected.")

    lines.extend(["", "## Successful Practices"])
    if successful:
        for record in successful:
            lines.append(
                f"- `{record['run_id']}` preserved audit evidence with QA `{record['qa_status']}` and comparison `{record['comparison_status']}`."
            )
    else:
        lines.append("- No run was classified as `no_detected_failure`.")
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
        "swmm_model_diagnostic_error": (
            "SWMM model diagnostics",
            "Review deterministic model diagnostics before treating a run as valid modeling evidence.",
            ["swmm-experiment-audit", "swmm-runner", "swmm-builder"],
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
    parser.add_argument(
        "--no-run-summaries",
        action="store_true",
        help="Do not write per-run memory_summary.json cards next to audited run artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runs_dir = args.runs_dir
    out_dir = args.out_dir
    generated_at = now_utc()

    run_dirs = discover_run_dirs(runs_dir)
    records = [build_record(run_dir, runs_dir) for run_dir in run_dirs]
    failure_count = sum(1 for record in records if has_detected_failure(record))
    run_summaries = [build_run_memory_summary(record, generated_at) for record in records]
    project_summaries = [
        project_summary(project, project_records, generated_at)
        for project, project_records in records_by_project(records).items()
    ]

    index = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "source_runs_dir": str(runs_dir),
        "record_count": len(records),
        "run_folder_count": len(run_dirs),
        "failure_record_count": failure_count,
        "failure_pattern_counts": dict(Counter(p for r in records for p in r["failure_patterns"])),
        "model_diagnostic_counts": dict(Counter(item for r in records for item in r.get("model_diagnostic_ids", []))),
        "qa_status_counts": dict(Counter(r["qa_status"] for r in records)),
        "comparison_status_counts": dict(Counter(r["comparison_status"] for r in records)),
        "project_counts": dict(Counter(r["project_key"] for r in records)),
        "records": records,
    }

    write_json(out_dir / "modeling_memory_index.json", index)
    write_json(out_dir / "run_memory_summaries.json", {"generated_at_utc": generated_at, "records": run_summaries})
    if not args.no_run_summaries:
        for run_dir, summary in zip(run_dirs, run_summaries):
            write_json(run_dir / "memory_summary.json", summary)

    projects_dir = out_dir / "projects"
    for summary in project_summaries:
        project_dir = projects_dir / summary["project_key"]
        write_json(project_dir / "project_memory.json", summary)
        write_text(project_dir / "project_memory.md", render_project_memory_md(summary["project_key"], summary))

    write_text(out_dir / "modeling_memory_index.md", render_index_md(records, generated_at))
    write_text(out_dir / "project_memory_index.md", render_project_index_md(project_summaries, generated_at))
    write_text(out_dir / "lessons_learned.md", render_lessons(records, generated_at))
    write_text(out_dir / "skill_update_proposals.md", render_proposals(records, generated_at))
    write_text(out_dir / "benchmark_verification_plan.md", render_benchmark_plan(generated_at))

    obsidian_used = False
    if args.obsidian_dir:
        export_obsidian(out_dir, args.obsidian_dir)
        obsidian_used = True

    print(f"run folders scanned: {len(run_dirs)}")
    print(f"audit records found: {len(records)}")
    print(f"runs with detected failures: {failure_count}")
    print(f"run memory summaries written: {'no' if args.no_run_summaries else len(run_summaries)}")
    print(f"project memory groups written: {len(project_summaries)}")
    print(f"output directory: {out_dir}")
    print(f"obsidian export used: {'yes' if obsidian_used else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
