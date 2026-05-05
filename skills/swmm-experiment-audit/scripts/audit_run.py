#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


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


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(repo_root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_swmm_version(repo_root: Path) -> str | None:
    proc = subprocess.run(["swmm5", "--version"], cwd=repo_root, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def relpath(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def resolve_recorded_path(value: str | None, repo_root: Path) -> Path | None:
    if not value:
        return None
    p = Path(value)
    if p.is_absolute():
        return p
    return repo_root / p


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def find_stage_manifest(run_dir: Path, names: list[str]) -> Path | None:
    direct = [run_dir / name / "manifest.json" for name in names]
    found = first_existing(direct)
    if found:
        return found
    candidates: list[Path] = []
    for pattern in names:
        candidates.extend(sorted(run_dir.glob(f"**/*{pattern.strip('0123456789_')}*/manifest.json")))
    return candidates[0] if candidates else None


def artifact_record(
    *,
    artifact_id: str,
    role: str,
    path: Path | None,
    repo_root: Path,
    produced_by: str | None = None,
    used_for: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exists = bool(path and path.exists())
    record: dict[str, Any] = {
        "id": artifact_id,
        "role": role,
        "relative_path": relpath(path, repo_root) if path else None,
        "absolute_path": str(path.resolve()) if path and path.exists() else (str(path) if path else None),
        "exists": exists,
        "sha256": sha256_file(path) if path and exists else None,
        "produced_by": produced_by,
        "used_for": used_for or [],
    }
    if metadata:
        record["metadata"] = metadata
    return record


def normalize_artifacts(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["id"] not in out:
            out[record["id"]] = record
    return out


def parse_node_inflow_peak(rpt_path: Path | None, node: str | None) -> dict[str, Any] | None:
    if not rpt_path or not node or not rpt_path.exists():
        return None
    text = rpt_path.read_text(errors="ignore")
    lines = text.splitlines()

    def extract_section(title: str) -> str:
        start_idx = None
        for i, line in enumerate(lines):
            if title.lower() in line.lower():
                start_idx = i + 1
                break
        if start_idx is None:
            return ""
        block: list[str] = []
        for line in lines[start_idx:]:
            if line.strip().startswith("*****") and block:
                break
            block.append(line)
        return "\n".join(block)

    inflow_block = extract_section("Node Inflow Summary")
    match = re.search(
        rf"^\s*{re.escape(node)}\s+\S+\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+\d+\s+(\d\d):(\d\d)",
        inflow_block,
        re.M,
    )
    if match:
        return {
            "node": node,
            "value": float(match.group(2)),
            "unit": "CMS",
            "time_hhmm": f"{match.group(3)}:{match.group(4)}",
            "source_section": "Node Inflow Summary",
            "source_field": "Maximum Total Inflow",
        }

    outfall_block = extract_section("Outfall Loading Summary")
    fallback = re.search(
        rf"^\s*{re.escape(node)}\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s*$",
        outfall_block,
        re.M,
    )
    if fallback:
        return {
            "node": node,
            "value": float(fallback.group(3)),
            "unit": "CMS",
            "time_hhmm": None,
            "source_section": "Outfall Loading Summary",
            "source_field": "Max Flow",
        }
    return None


def metric_matches_report(recorded: dict[str, Any], parsed: dict[str, Any] | None) -> bool | None:
    if parsed is None:
        return None
    value = recorded.get("value")
    if value is None:
        value = recorded.get("peak")
    if value is None:
        return None
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    return abs(value_f - float(parsed["value"])) <= 1e-9


def current_repo_state(repo_root: Path) -> dict[str, Any]:
    return {
        "root": str(repo_root),
        "git_head": run_git(repo_root, "rev-parse", "HEAD"),
        "git_branch": run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "git_status_porcelain": run_git(repo_root, "status", "--short"),
    }


def derive_status(qa: dict[str, Any], runner_manifest: dict[str, Any], files_exist: bool) -> str:
    if qa:
        fail_count = qa.get("fail_count")
        if fail_count == 0:
            return "pass"
        if isinstance(fail_count, int) and fail_count > 0:
            return "fail"
    if runner_manifest:
        return "pass" if runner_manifest.get("return_code") == 0 else "fail"
    if files_exist:
        return "pass"
    return "unknown"


def build_qa_checks(
    *,
    acceptance_report: dict[str, Any],
    builder_manifest: dict[str, Any],
    runner_manifest: dict[str, Any],
    peak_metric: dict[str, Any] | None,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if acceptance_report.get("qa"):
        qa = dict(acceptance_report["qa"])
        qa["status"] = "pass" if qa.get("fail_count") == 0 else "fail"
        return qa

    checks: list[dict[str, Any]] = []
    validation = builder_manifest.get("validation")
    if isinstance(validation, dict):
        validation_ok = not any(bool(v) for v in validation.values())
        checks.append(
            {
                "id": "builder_input_validation",
                "ok": validation_ok,
                "detail": json.dumps(validation, sort_keys=True),
            }
        )
    if runner_manifest:
        checks.append(
            {
                "id": "runner_return_code_zero",
                "ok": runner_manifest.get("return_code") == 0,
                "detail": f"return_code={runner_manifest.get('return_code')}",
            }
        )
    rpt = artifacts.get("runner_rpt", {})
    out = artifacts.get("runner_out", {})
    if rpt or out:
        checks.append(
            {
                "id": "runner_outputs_exist",
                "ok": bool(rpt.get("exists")) and bool(out.get("exists")),
                "detail": f"rpt_exists={rpt.get('exists')} out_exists={out.get('exists')}",
            }
        )
    if peak_metric:
        checks.append(
            {
                "id": "peak_metric_present",
                "ok": peak_metric.get("value") is not None,
                "detail": f"source_section={peak_metric.get('source_section')}",
            }
        )

    failed = [c for c in checks if not c.get("ok")]
    return {
        "status": "pass" if checks and not failed else ("fail" if failed else "unknown"),
        "pass_count": len(checks) - len(failed),
        "fail_count": len(failed),
        "checks": checks,
    }


def normalize_peak_metric(
    *,
    top_manifest: dict[str, Any],
    acceptance_report: dict[str, Any],
    runner_manifest: dict[str, Any],
    peak_json: dict[str, Any],
    minimal_manifest: dict[str, Any],
    runner_rpt_path: Path | None,
) -> dict[str, Any] | None:
    peak = {}
    for candidate in (
        peak_json,
        (runner_manifest.get("metrics") or {}).get("peak") or {},
        (acceptance_report.get("key_outputs") or {}).get("peak") or {},
    ):
        if candidate:
            peak = candidate
            break

    if not peak and minimal_manifest.get("qoi"):
        qoi = minimal_manifest["qoi"]
        for key, value in qoi.items():
            if key.startswith("peak_flow_cms_at_"):
                peak = {
                    "node": key.removeprefix("peak_flow_cms_at_"),
                    "peak": value,
                    "time_hhmm": qoi.get("time_of_peak_hhmm"),
                    "source": "manifest qoi",
                }
                break

    if not peak:
        return None

    node = peak.get("node")
    value = peak.get("value", peak.get("peak"))
    parsed = parse_node_inflow_peak(runner_rpt_path, node)
    source_section = peak.get("source")
    source_field = None
    if source_section == "Node Inflow Summary":
        source_field = "Maximum Total Inflow"
    elif source_section == "Outfall Loading Summary":
        source_field = "Max Flow"

    normalized = {
        "name": "peak_flow",
        "node": node,
        "value": value,
        "unit": "CMS",
        "time_hhmm": peak.get("time_hhmm"),
        "source_artifact": "runner_rpt" if runner_rpt_path else None,
        "source_section": source_section,
        "source_field": source_field,
        "source_validation": {
            "parsed_from_report": parsed,
            "matches_report": metric_matches_report({"value": value}, parsed),
        },
    }

    if parsed and normalized["source_section"] in (None, "manifest qoi"):
        normalized["source_section"] = parsed["source_section"]
        normalized["source_field"] = parsed["source_field"]
    return normalized


def normalize_continuity(
    *,
    acceptance_report: dict[str, Any],
    runner_manifest: dict[str, Any],
    continuity_json: dict[str, Any],
) -> dict[str, Any] | None:
    continuity = {}
    for candidate in (
        continuity_json,
        ((runner_manifest.get("metrics") or {}).get("continuity") or {}),
    ):
        if candidate:
            continuity = candidate
            break
    errors = continuity.get("continuity_error_percent")
    if not errors:
        errors = (acceptance_report.get("key_outputs") or {}).get("continuity_error_percent")
    if not errors:
        return None
    return {
        "name": "continuity_error",
        "unit": "percent",
        "values": errors,
        "source_artifact": "runner_rpt",
        "source_sections": ["Runoff Quantity Continuity", "Flow Routing Continuity"],
    }


def collect_run(
    run_dir: Path,
    *,
    repo_root: Path,
    case_name: str | None = None,
    workflow_mode: str | None = None,
    objective: str | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    top_manifest_path = run_dir / "manifest.json"
    acceptance_report_path = run_dir / "acceptance_report.json"
    acceptance_report_md_path = run_dir / "acceptance_report.md"
    builder_manifest_path = find_stage_manifest(run_dir, ["04_builder", "05_builder", "builder"])
    runner_manifest_path = find_stage_manifest(run_dir, ["05_runner", "06_runner", "runner"])
    network_qa_path = first_existing([run_dir / "06_qa/network_qa.json", run_dir / "07_qa/network_qa.json"])
    continuity_qa_path = first_existing([run_dir / "06_qa/runner_continuity.json", run_dir / "07_qa/runner_continuity.json"])
    peak_qa_path = first_existing([run_dir / "06_qa/runner_peak.json", run_dir / "07_qa/runner_peak.json"])

    top_manifest = read_json(top_manifest_path)
    acceptance_report = read_json(acceptance_report_path)
    builder_manifest = read_json(builder_manifest_path) if builder_manifest_path else {}
    runner_manifest = read_json(runner_manifest_path) if runner_manifest_path else {}
    network_qa = read_json(network_qa_path) if network_qa_path else {}
    continuity_qa = read_json(continuity_qa_path) if continuity_qa_path else {}
    peak_qa = read_json(peak_qa_path) if peak_qa_path else {}

    minimal_manifest = top_manifest if "qoi" in top_manifest or "files" in top_manifest else {}

    runner_files = runner_manifest.get("files") or {}
    minimal_files = minimal_manifest.get("files") or {}
    top_outputs = top_manifest.get("outputs") or {}

    inp_path = resolve_recorded_path((top_outputs.get("built_inp") or {}).get("path") if isinstance(top_outputs.get("built_inp"), dict) else None, repo_root)
    if inp_path is None:
        inp_path = resolve_recorded_path((builder_manifest.get("outputs") or {}).get("inp"), repo_root)
    if inp_path is None:
        inp_path = resolve_recorded_path(minimal_files.get("inp"), repo_root)

    rpt_path = resolve_recorded_path((top_outputs.get("runner_rpt") or {}).get("path") if isinstance(top_outputs.get("runner_rpt"), dict) else None, repo_root)
    if rpt_path is None:
        rpt_path = resolve_recorded_path(runner_files.get("rpt"), repo_root)
    if rpt_path is None:
        rpt_path = resolve_recorded_path(minimal_files.get("rpt"), repo_root)

    out_path = resolve_recorded_path((top_outputs.get("runner_out") or {}).get("path") if isinstance(top_outputs.get("runner_out"), dict) else None, repo_root)
    if out_path is None:
        out_path = resolve_recorded_path(runner_files.get("out"), repo_root)
    if out_path is None:
        out_path = resolve_recorded_path(minimal_files.get("out"), repo_root)

    stdout_path = resolve_recorded_path(runner_files.get("stdout"), repo_root)
    stderr_path = resolve_recorded_path(runner_files.get("stderr"), repo_root)
    if stdout_path is None:
        stdout_path = run_dir / "stdout.txt"
    if stderr_path is None:
        stderr_path = run_dir / "stderr.txt"

    artifact_records = [
        artifact_record(
            artifact_id="top_manifest",
            role="Run-level provenance",
            path=top_manifest_path,
            repo_root=repo_root,
            produced_by="workflow",
            used_for=["run identity", "repo state", "command trace", "input/output hashes"],
        ),
        artifact_record(
            artifact_id="acceptance_report_json",
            role="QA summary",
            path=acceptance_report_path,
            repo_root=repo_root,
            produced_by="acceptance runner",
            used_for=["QA status", "key outputs", "artifact map"],
        ),
        artifact_record(
            artifact_id="acceptance_report_md",
            role="Human-readable QA report",
            path=acceptance_report_md_path,
            repo_root=repo_root,
            produced_by="acceptance runner",
            used_for=["quick QA inspection"],
        ),
        artifact_record(
            artifact_id="builder_manifest",
            role="Build provenance",
            path=builder_manifest_path,
            repo_root=repo_root,
            produced_by="swmm-builder",
            used_for=["build inputs", "validation diagnostics", "INP hash"],
        ),
        artifact_record(
            artifact_id="model_inp",
            role="SWMM input model",
            path=inp_path,
            repo_root=repo_root,
            produced_by="swmm-builder",
            used_for=["SWMM execution input"],
        ),
        artifact_record(
            artifact_id="runner_manifest",
            role="SWMM execution provenance",
            path=runner_manifest_path,
            repo_root=repo_root,
            produced_by="swmm-runner",
            used_for=["return code", "SWMM version", "runner metrics", "report/output paths"],
        ),
        artifact_record(
            artifact_id="runner_rpt",
            role="SWMM text report",
            path=rpt_path,
            repo_root=repo_root,
            produced_by="swmm5 via swmm-runner",
            used_for=["peak extraction", "continuity extraction", "report review"],
        ),
        artifact_record(
            artifact_id="runner_out",
            role="SWMM binary output",
            path=out_path,
            repo_root=repo_root,
            produced_by="swmm5 via swmm-runner",
            used_for=["hydrograph extraction", "plotting"],
        ),
        artifact_record(
            artifact_id="runner_stdout",
            role="SWMM stdout log",
            path=stdout_path,
            repo_root=repo_root,
            produced_by="swmm-runner",
            used_for=["execution debugging"],
        ),
        artifact_record(
            artifact_id="runner_stderr",
            role="SWMM stderr log",
            path=stderr_path,
            repo_root=repo_root,
            produced_by="swmm-runner",
            used_for=["execution debugging"],
        ),
        artifact_record(
            artifact_id="network_qa",
            role="Network QA result",
            path=network_qa_path,
            repo_root=repo_root,
            produced_by="network QA stage",
            used_for=["network validity check"],
        ),
        artifact_record(
            artifact_id="continuity_qa",
            role="Continuity QA result",
            path=continuity_qa_path,
            repo_root=repo_root,
            produced_by="runner QA stage",
            used_for=["continuity pass/fail", "continuity metric capture"],
        ),
        artifact_record(
            artifact_id="peak_qa",
            role="Peak metric QA result",
            path=peak_qa_path,
            repo_root=repo_root,
            produced_by="runner QA stage",
            used_for=["peak flow", "peak time", "metric source"],
        ),
    ]
    artifacts = normalize_artifacts(artifact_records)

    peak_metric = normalize_peak_metric(
        top_manifest=top_manifest,
        acceptance_report=acceptance_report,
        runner_manifest=runner_manifest,
        peak_json=peak_qa,
        minimal_manifest=minimal_manifest,
        runner_rpt_path=rpt_path,
    )
    continuity_metric = normalize_continuity(
        acceptance_report=acceptance_report,
        runner_manifest=runner_manifest,
        continuity_json=continuity_qa,
    )

    qa = build_qa_checks(
        acceptance_report=acceptance_report,
        builder_manifest=builder_manifest,
        runner_manifest=runner_manifest,
        peak_metric=peak_metric,
        artifacts=artifacts,
    )
    status = derive_status(qa, runner_manifest, bool(inp_path and rpt_path and out_path))

    warnings: list[str] = []
    for warning in top_manifest.get("qa_warnings") or []:
        if isinstance(warning, dict):
            kind = warning.get("kind") or "qa_warning"
            boundary = warning.get("boundary")
            value = warning.get("value_percent", warning.get("value"))
            detail = f"{kind}: {boundary}" if boundary else str(kind)
            if value is not None:
                detail = f"{detail} value={value}"
            warnings.append(detail)
        else:
            warnings.append(str(warning))
    if (top_manifest.get("repo") or {}).get("git_status_porcelain"):
        warnings.append("The recorded Git working tree was not clean at run time.")
    if peak_metric and (peak_metric.get("source_validation") or {}).get("matches_report") is False:
        warnings.append("Recorded peak metric does not match the value parsed from the reported source section.")
    if status == "unknown":
        warnings.append("Run status is unknown because no complete QA or runner manifest was found.")

    provenance = {
        "schema_version": "1.0",
        "generated_by": "swmm-experiment-audit",
        "generated_at_utc": now_utc(),
        "run_id": top_manifest.get("run_id") or acceptance_report.get("run_id") or run_dir.name,
        "case_name": case_name or top_manifest.get("case_name") or run_dir.name,
        "objective": objective,
        "workflow_mode": workflow_mode or top_manifest.get("pipeline") or acceptance_report.get("pipeline"),
        "status": status,
        "run_dir": {
            "relative_path": relpath(run_dir, repo_root),
            "absolute_path": str(run_dir),
        },
        "repo": top_manifest.get("repo") or current_repo_state(repo_root),
        "tools": top_manifest.get("tools")
        or {
            "python_executable": sys.executable,
            "python_version": sys.version.replace("\n", " "),
            "swmm5_version": get_swmm_version(repo_root),
        },
        "commands": top_manifest.get("commands") or [],
        "inputs": top_manifest.get("inputs") or {},
        "artifacts": artifacts,
        "metrics": {
            "peak_flow": peak_metric,
            "continuity_error": continuity_metric,
            "swmm_return_code": runner_manifest.get("return_code"),
            "builder_counts": builder_manifest.get("counts"),
        },
        "qa": qa,
        "warnings": warnings,
        "raw_sources": {
            "top_manifest": relpath(top_manifest_path, repo_root) if top_manifest_path.exists() else None,
            "acceptance_report": relpath(acceptance_report_path, repo_root) if acceptance_report_path.exists() else None,
            "builder_manifest": relpath(builder_manifest_path, repo_root) if builder_manifest_path else None,
            "runner_manifest": relpath(runner_manifest_path, repo_root) if runner_manifest_path else None,
        },
    }
    return provenance


def artifact_sha(provenance: dict[str, Any], artifact_id: str) -> str | None:
    artifact = (provenance.get("artifacts") or {}).get(artifact_id) or {}
    return artifact.get("sha256")


def peak_value(provenance: dict[str, Any]) -> Any:
    peak = ((provenance.get("metrics") or {}).get("peak_flow") or {})
    return peak.get("value")


def peak_matches_report(provenance: dict[str, Any]) -> Any:
    peak = ((provenance.get("metrics") or {}).get("peak_flow") or {})
    return (peak.get("source_validation") or {}).get("matches_report")


def build_comparison(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    if baseline is None:
        return {
            "schema_version": "1.0",
            "generated_by": "swmm-experiment-audit",
            "generated_at_utc": now_utc(),
            "comparison_available": False,
            "reason": "No --compare-to run directory was provided.",
            "current_run_id": current.get("run_id"),
        }

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, baseline_value: Any, current_value: Any, interpretation: str) -> None:
        checks.append(
            {
                "id": check_id,
                "baseline": baseline_value,
                "current": current_value,
                "same": baseline_value == current_value,
                "interpretation": interpretation,
            }
        )

    add_check("status", baseline.get("status"), current.get("status"), "Overall audit status.")
    add_check(
        "git_head",
        (baseline.get("repo") or {}).get("git_head"),
        (current.get("repo") or {}).get("git_head"),
        "Code version captured by the run manifest.",
    )
    add_check("model_inp_sha256", artifact_sha(baseline, "model_inp"), artifact_sha(current, "model_inp"), "SWMM input model identity.")
    add_check("runner_out_sha256", artifact_sha(baseline, "runner_out"), artifact_sha(current, "runner_out"), "SWMM binary output identity.")
    add_check("runner_rpt_sha256", artifact_sha(baseline, "runner_rpt"), artifact_sha(current, "runner_rpt"), "SWMM text report identity; may differ because reports include timestamps.")
    add_check("peak_flow", peak_value(baseline), peak_value(current), "Recorded peak-flow metric.")
    add_check(
        "peak_source_section",
        (((baseline.get("metrics") or {}).get("peak_flow") or {}).get("source_section")),
        (((current.get("metrics") or {}).get("peak_flow") or {}).get("source_section")),
        "Report section used for peak-flow extraction.",
    )
    add_check(
        "peak_matches_report",
        peak_matches_report(baseline),
        peak_matches_report(current),
        "Whether the recorded peak value matches the value re-parsed from the source report section.",
    )
    add_check(
        "continuity_error",
        (((baseline.get("metrics") or {}).get("continuity_error") or {}).get("values")),
        (((current.get("metrics") or {}).get("continuity_error") or {}).get("values")),
        "Parsed continuity errors.",
    )

    warnings: list[str] = []
    for label, provenance in (("baseline", baseline), ("current", current)):
        peak = ((provenance.get("metrics") or {}).get("peak_flow") or {})
        validation = peak.get("source_validation") or {}
        if validation.get("matches_report") is False:
            warnings.append(f"{label} peak-flow record does not match the value parsed from its source report section.")

    if artifact_sha(baseline, "model_inp") == artifact_sha(current, "model_inp") and peak_value(baseline) != peak_value(current):
        warnings.append("Peak flow changed while the SWMM input hash is unchanged; check parser version, metric source, or report records.")

    return {
        "schema_version": "1.0",
        "generated_by": "swmm-experiment-audit",
        "generated_at_utc": now_utc(),
        "comparison_available": True,
        "baseline_run_id": baseline.get("run_id"),
        "current_run_id": current.get("run_id"),
        "baseline_run_dir": (baseline.get("run_dir") or {}).get("relative_path"),
        "current_run_dir": (current.get("run_dir") or {}).get("relative_path"),
        "checks": checks,
        "warnings": warnings,
    }


def short(value: Any, n: int = 12) -> str:
    if value is None:
        return "n/a"
    text = str(value)
    return text[:n] + "..." if len(text) > n else text


def safe_note_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(value or "experiment")).strip(" .-")
    return text or "experiment"


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def render_note(provenance: dict[str, Any], comparison: dict[str, Any], repo_root: Path) -> str:
    run_id = provenance.get("run_id")
    status = provenance.get("status")
    peak = ((provenance.get("metrics") or {}).get("peak_flow") or {})
    continuity = ((provenance.get("metrics") or {}).get("continuity_error") or {})
    artifacts = provenance.get("artifacts") or {}
    qa = provenance.get("qa") or {}
    repo = provenance.get("repo") or {}
    tools = provenance.get("tools") or {}

    frontmatter = [
        "---",
        "type: experiment-audit",
        "project: Agentic SWMM",
        "generated_by: swmm-experiment-audit",
        f"run_id: {run_id}",
        f"status: {status}",
        f"created_at_utc: {provenance.get('generated_at_utc')}",
        "tags:",
        "  - agentic-swmm",
        "  - swmm",
        "  - experiment-audit",
        "---",
        "",
    ]

    summary = [
        f"# Experiment Audit - {run_id}",
        "",
        "## Executive Summary",
        "",
        f"This audit consolidates the available provenance, artifacts, metrics, and QA checks for `{run_id}`.",
        "",
    ]
    if peak:
        summary.append(
            f"The recorded peak flow is `{peak.get('value')}` {peak.get('unit') or ''} at `{peak.get('node')}`, "
            f"with source `{peak.get('source_section')}` / `{peak.get('source_field')}`."
        )
        summary.append("")
    if comparison.get("comparison_available"):
        summary.append(f"A comparison against `{comparison.get('baseline_run_id')}` is included below.")
        summary.append("")

    identity_rows = [
        ["Run ID", f"`{run_id}`"],
        ["Case name", provenance.get("case_name") or "n/a"],
        ["Workflow mode", provenance.get("workflow_mode") or "n/a"],
        ["Status", str(status).upper()],
        ["Run directory", f"`{(provenance.get('run_dir') or {}).get('relative_path')}`"],
        ["Git branch", f"`{repo.get('git_branch')}`"],
        ["Git HEAD", f"`{repo.get('git_head')}`"],
        ["SWMM version", f"`{tools.get('swmm5_version')}`"],
        ["Commands recorded", len(provenance.get("commands") or [])],
        ["Inputs hashed", len(provenance.get("inputs") or {})],
    ]

    sections = [
        "## Run Identity",
        "",
        md_table(["Field", "Value"], identity_rows),
        "",
    ]

    command_rows = []
    for command in provenance.get("commands") or []:
        stdout_file = resolve_recorded_path(command.get("stdout_file"), repo_root)
        stderr_file = resolve_recorded_path(command.get("stderr_file"), repo_root)
        command_rows.append(
            [
                f"`{command.get('id')}`",
                command.get("return_code"),
                command.get("duration_seconds"),
                f"`{relpath(stdout_file, repo_root) if stdout_file else command.get('stdout_file')}`",
                f"`{relpath(stderr_file, repo_root) if stderr_file else command.get('stderr_file')}`",
            ]
        )
    if command_rows:
        sections.extend(
            [
                "## Workflow Trace",
                "",
                md_table(["Stage", "Return code", "Duration sec", "stdout", "stderr"], command_rows),
                "",
            ]
        )

    qa_rows = []
    for check in qa.get("checks") or []:
        qa_rows.append([f"`{check.get('id')}`", "PASS" if check.get("ok") else "FAIL", check.get("detail")])
    if qa_rows:
        sections.extend(
            [
                "## QA Gates",
                "",
                md_table(["QA gate", "Status", "Evidence"], qa_rows),
                "",
                f"Overall QA: **{qa.get('pass_count', 0)} pass, {qa.get('fail_count', 0)} fail**.",
                "",
            ]
        )

    metric_rows = []
    if peak:
        metric_rows.append(
            [
                f"Peak flow at `{peak.get('node')}`",
                peak.get("value"),
                peak.get("unit"),
                f"`{peak.get('source_artifact')}`",
                f"`{peak.get('source_section')}` / `{peak.get('source_field')}`",
            ]
        )
    if continuity:
        for key, value in (continuity.get("values") or {}).items():
            metric_rows.append([key, value, continuity.get("unit"), "`runner_rpt`", ", ".join(continuity.get("source_sections") or [])])
    return_code = (provenance.get("metrics") or {}).get("swmm_return_code")
    if return_code is not None:
        metric_rows.append(["SWMM return code", return_code, "code", "`runner_manifest`", "`return_code`"])
    if metric_rows:
        sections.extend(
            [
                "## Key Metrics",
                "",
                md_table(["Metric", "Value", "Unit", "Source artifact", "Source table / field"], metric_rows),
                "",
            ]
        )

    artifact_rows = []
    for artifact_id, artifact in artifacts.items():
        if artifact.get("exists"):
            artifact_rows.append(
                [
                    f"`{artifact_id}`",
                    artifact.get("role"),
                    f"`{artifact.get('relative_path')}`",
                    f"`{short(artifact.get('sha256'))}`",
                    artifact.get("produced_by") or "",
                    ", ".join(artifact.get("used_for") or []),
                ]
            )
    if artifact_rows:
        sections.extend(
            [
                "## Artifact Index",
                "",
                md_table(["Artifact ID", "Role", "Relative path", "SHA256", "Produced by", "Used for"], artifact_rows),
                "",
            ]
        )

    sections.extend(
        [
            "## Metric Source Contract",
            "",
            md_table(
                ["Metric", "Required source", "Reason"],
                [
                    ["Peak flow at a node/outfall", "`Node Inflow Summary` / `Maximum Total Inflow`", "Includes time of maximum total inflow and avoids confusing depth/HGL values with flow."],
                    ["Peak flow fallback for outfalls", "`Outfall Loading Summary` / `Max Flow`", "Used only if no timed node inflow entry is available."],
                    ["Continuity error", "SWMM continuity tables", "Uses SWMM's own report rather than re-implementing hydrologic accounting."],
                ],
            ),
            "",
            "Do not extract peak flow from `Node Depth Summary`; that table reports depth and HGL, not flow.",
            "",
        ]
    )

    if comparison.get("comparison_available"):
        comparison_rows = []
        for check in comparison.get("checks") or []:
            comparison_rows.append(
                [
                    f"`{check.get('id')}`",
                    f"`{short(check.get('baseline'))}`",
                    f"`{short(check.get('current'))}`",
                    "same" if check.get("same") else "changed",
                    check.get("interpretation"),
                ]
            )
        sections.extend(
            [
                f"## Comparison Against `{comparison.get('baseline_run_id')}`",
                "",
                md_table(["Check", "Baseline", "Current", "Result", "Interpretation"], comparison_rows),
                "",
            ]
        )
        if comparison.get("warnings"):
            sections.extend(["Comparison warnings:", ""])
            for warning in comparison["warnings"]:
                sections.append(f"- {warning}")
            sections.append("")
    else:
        sections.extend(["## Comparison", "", "No comparison target was provided.", ""])

    if provenance.get("warnings"):
        sections.extend(["## Warnings", ""])
        for warning in provenance["warnings"]:
            sections.append(f"- {warning}")
        sections.append("")

    sections.extend(
        [
            "## Evidence Notes",
            "",
            "- This note is generated from machine-readable run artifacts.",
            "- The primary machine-readable record is `experiment_provenance.json`.",
            "- Use `comparison.json` for baseline/scenario or before/after parser comparisons.",
            "",
        ]
    )

    return "\n".join(frontmatter + summary + sections)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate Agentic SWMM experiment audit outputs.")
    ap.add_argument("--run-dir", type=Path, required=True, help="Run directory to audit.")
    ap.add_argument("--compare-to", type=Path, help="Optional baseline run directory for comparison.")
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root.")
    ap.add_argument("--case-name", help="Optional human-readable case name.")
    ap.add_argument("--workflow-mode", help="Optional workflow mode label.")
    ap.add_argument("--objective", help="Optional run objective.")
    ap.add_argument("--out-provenance", type=Path, help="Output path for experiment_provenance.json.")
    ap.add_argument("--out-comparison", type=Path, help="Output path for comparison.json.")
    ap.add_argument("--out-note", type=Path, help="Output path for experiment_note.md.")
    ap.add_argument(
        "--obsidian-dir",
        type=Path,
        help="Optional Obsidian vault folder where a copy of experiment_note.md should be written.",
    )
    ap.add_argument(
        "--obsidian-note-name",
        help="Optional file name for the Obsidian note. Defaults to '<run_id> experiment audit.md'.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    run_dir = args.run_dir.resolve()

    provenance = collect_run(
        run_dir,
        repo_root=repo_root,
        case_name=args.case_name,
        workflow_mode=args.workflow_mode,
        objective=args.objective,
    )
    baseline = (
        collect_run(args.compare_to.resolve(), repo_root=repo_root)
        if args.compare_to
        else None
    )
    comparison = build_comparison(provenance, baseline)

    out_provenance = args.out_provenance or (run_dir / "experiment_provenance.json")
    out_comparison = args.out_comparison or (run_dir / "comparison.json")
    out_note = args.out_note or (run_dir / "experiment_note.md")
    obsidian_note = None
    if args.obsidian_dir:
        note_name = args.obsidian_note_name or f"{safe_note_name(provenance.get('run_id'))} experiment audit.md"
        obsidian_note = args.obsidian_dir / note_name

    write_json(out_provenance, provenance)
    write_json(out_comparison, comparison)
    note_text = render_note(provenance, comparison, repo_root)
    write_text(out_note, note_text)
    if obsidian_note:
        write_text(obsidian_note, note_text)

    print(
        json.dumps(
            {
                "ok": True,
                "run_id": provenance.get("run_id"),
                "status": provenance.get("status"),
                "experiment_provenance": str(out_provenance),
                "comparison": str(out_comparison),
                "experiment_note": str(out_note),
                "obsidian_note": str(obsidian_note) if obsidian_note else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
