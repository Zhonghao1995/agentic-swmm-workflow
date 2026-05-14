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
DEFAULT_OBSIDIAN_VAULT = Path.home() / "Documents" / "Agentic-SWMM-Obsidian-Vault"
DEFAULT_OBSIDIAN_AUDIT_DIR = DEFAULT_OBSIDIAN_VAULT / "20_Audit_Layer" / "Experiment_Audits"
DEFAULT_OBSIDIAN_AUDIT_INDEX = DEFAULT_OBSIDIAN_VAULT / "20_Audit_Layer" / "Experiment Audit Index.md"


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


def obsidian_wikilink(note_path: Path) -> str:
    return f"[[{note_path.stem}]]"


def update_obsidian_audit_index(
    index_path: Path,
    provenance: dict[str, Any],
    out_provenance: Path,
    out_comparison: Path,
    obsidian_note: Path,
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
    else:
        text = "\n".join(
            [
                "---",
                "project: Agentic SWMM",
                "type: experiment-audit-index",
                "status: active",
                "---",
                "",
                "# Experiment Audit Index",
                "",
                "## Audited Runs",
                "",
                "| Run ID | Status | Audit Note | Provenance JSON | Comparison JSON | Last Updated |",
                "|---|---|---|---|---|---|",
                "",
            ]
        )

    marker = "|---|---|---|---|---|---|"
    if marker not in text:
        text = text.rstrip() + "\n\n## Audited Runs\n\n| Run ID | Status | Audit Note | Provenance JSON | Comparison JSON | Last Updated |\n|---|---|---|---|---|---|\n"

    run_id = str(provenance.get("run_id") or obsidian_note.stem)
    status = str(provenance.get("status") or "unknown")
    generated_at = str(provenance.get("generated_at_utc") or now_utc())
    row = (
        f"| `{run_id}` | {status} | {obsidian_wikilink(obsidian_note)} | "
        f"`{out_provenance}` | `{out_comparison}` | {generated_at} |"
    )

    lines = text.splitlines()
    filtered: list[str] = []
    for line in lines:
        if line.startswith(f"| `{run_id}` |"):
            continue
        filtered.append(line)

    insert_at = None
    for i, line in enumerate(filtered):
        if line.strip() == marker:
            insert_at = i + 1
            break

    if insert_at is None:
        filtered.extend(["", "## Audited Runs", "", "| Run ID | Status | Audit Note | Provenance JSON | Comparison JSON | Last Updated |", marker])
        insert_at = len(filtered)

    filtered.insert(insert_at, row)
    write_text(index_path, "\n".join(filtered).rstrip() + "\n")


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
    try:
        proc = subprocess.run(["swmm5", "--version"], cwd=repo_root, capture_output=True, text=True)
    except FileNotFoundError:
        return None
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


def parse_inp_sections(inp_path: Path | None) -> dict[str, list[list[str]]]:
    if not inp_path or not inp_path.exists():
        return {}
    sections: dict[str, list[list[str]]] = {}
    current: str | None = None
    for raw in inp_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].upper()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections.setdefault(current, []).append(line.split())
    return sections


def parse_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        if ":" not in value:
            return int(round(float(value)))
        parts = [int(float(p)) for p in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def fnum(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diagnostic(
    check_id: str,
    severity: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
    recommendation: str | None = None,
) -> dict[str, Any]:
    out = {"id": check_id, "severity": severity, "message": message}
    if evidence:
        out["evidence"] = evidence
    if recommendation:
        out["recommendation"] = recommendation
    return out


def parse_flooding_diagnostics(rpt_path: Path | None) -> list[dict[str, Any]]:
    if not rpt_path or not rpt_path.exists():
        return []
    text = rpt_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if "Node Flooding Summary" in line:
            start_idx = i + 1
            break
    if start_idx is None:
        return []

    rows: list[dict[str, Any]] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if "No nodes were flooded" in stripped:
            return []
        if not stripped:
            if rows:
                break
            continue
        if stripped.startswith("*****"):
            break
        parts = stripped.split()
        if len(parts) < 2 or parts[0].startswith("-") or parts[0].lower() in {"node", "name"}:
            continue
        numeric = [fnum(part) for part in parts[1:]]
        numeric = [value for value in numeric if value is not None]
        if numeric and any(value > 0 for value in numeric):
            rows.append(
                {
                    "node": parts[0],
                    "values": numeric,
                    "source_section": "Node Flooding Summary",
                }
            )
    return [
        diagnostic(
            "node_flooding_detected",
            "warning",
            "SWMM report contains node flooding values greater than zero.",
            evidence={"flooded_nodes": rows},
            recommendation="Inspect flooded nodes before treating the run as hydrologically acceptable.",
        )
    ] if rows else []


def build_model_diagnostics(
    *,
    inp_path: Path | None,
    rpt_path: Path | None,
    continuity_metric: dict[str, Any] | None,
    repo_root: Path,
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    sections = parse_inp_sections(inp_path)

    nodes: set[str] = set()
    node_inverts: dict[str, float] = {}
    for section in ("JUNCTIONS", "OUTFALLS", "STORAGE"):
        for row in sections.get(section, []):
            if row:
                nodes.add(row[0])
                inv = fnum(row[1]) if len(row) > 1 else None
                if inv is not None:
                    node_inverts[row[0]] = inv

    subcatchments = {row[0] for row in sections.get("SUBCATCHMENTS", []) if row}
    raingages = {row[0] for row in sections.get("RAINGAGES", []) if row}

    for row in sections.get("SUBCATCHMENTS", []):
        if len(row) < 8:
            continue
        name, rain_gage, outlet = row[0], row[1], row[2]
        area = fnum(row[3])
        imperv = fnum(row[4])
        width = fnum(row[5])
        if not rain_gage or rain_gage == "*" or rain_gage not in raingages:
            diagnostics.append(
                diagnostic(
                    "missing_rain_gage",
                    "error",
                    f"Subcatchment {name} references a missing rain gage.",
                    evidence={"subcatchment": name, "rain_gage": rain_gage},
                    recommendation="Define the rain gage or correct the subcatchment rain-gage reference.",
                )
            )
        if not outlet or outlet == "*" or (outlet not in nodes and outlet not in subcatchments):
            diagnostics.append(
                diagnostic(
                    "subcatchment_outlet_missing",
                    "error",
                    f"Subcatchment {name} references a missing outlet.",
                    evidence={"subcatchment": name, "outlet": outlet},
                    recommendation="Route the subcatchment to an existing node or subcatchment.",
                )
            )
        if area is not None and area <= 0:
            diagnostics.append(
                diagnostic("subcatchment_area_nonpositive", "error", f"Subcatchment {name} has non-positive area.", evidence={"subcatchment": name, "area": area})
            )
        if width is not None and width <= 0:
            diagnostics.append(
                diagnostic("subcatchment_width_nonpositive", "error", f"Subcatchment {name} has non-positive width.", evidence={"subcatchment": name, "width": width})
            )
        if imperv is not None and not (0 <= imperv <= 100):
            diagnostics.append(
                diagnostic(
                    "imperviousness_out_of_range",
                    "error",
                    f"Subcatchment {name} has imperviousness outside 0-100 percent.",
                    evidence={"subcatchment": name, "imperviousness": imperv},
                )
            )

    incoming: set[str] = set()
    for row in sections.get("CONDUITS", []):
        if len(row) < 4:
            continue
        link, from_node, to_node = row[0], row[1], row[2]
        incoming.add(to_node)
        length = fnum(row[3])
        if from_node not in nodes or to_node not in nodes:
            diagnostics.append(
                diagnostic(
                    "conduit_node_missing",
                    "error",
                    f"Conduit {link} references a missing node.",
                    evidence={"link": link, "from_node": from_node, "to_node": to_node},
                )
            )
        if length is not None and length <= 0:
            diagnostics.append(diagnostic("conduit_length_nonpositive", "error", f"Conduit {link} has non-positive length.", evidence={"link": link, "length": length}))
        if length and from_node in node_inverts and to_node in node_inverts:
            slope = (node_inverts[from_node] - node_inverts[to_node]) / length
            if slope < -0.001 or slope > 0.2:
                diagnostics.append(
                    diagnostic(
                        "conduit_slope_suspicious",
                        "warning",
                        f"Conduit {link} has a suspicious invert-derived slope.",
                        evidence={"link": link, "from_node": from_node, "to_node": to_node, "slope": slope},
                        recommendation="Check node invert elevations, conduit direction, and conduit length.",
                    )
                )

    for row in sections.get("OUTFALLS", []):
        if row and row[0] not in incoming:
            diagnostics.append(
                diagnostic(
                    "outfall_disconnected",
                    "warning",
                    f"Outfall {row[0]} is not referenced as a conduit downstream node.",
                    evidence={"outfall": row[0]},
                    recommendation="Confirm the outfall is intentionally disconnected or add upstream routing.",
                )
            )

    for row in sections.get("OPTIONS", []):
        if len(row) >= 2 and row[0].upper() == "ROUTING_STEP":
            seconds = parse_duration_seconds(row[1])
            if seconds is not None and seconds > 300:
                diagnostics.append(
                    diagnostic(
                        "routing_step_large",
                        "warning",
                        "Routing step is larger than 5 minutes.",
                        evidence={"routing_step": row[1], "seconds": seconds},
                        recommendation="Use a smaller routing step for hydraulically sensitive or unstable models.",
                    )
                )

    continuity_values = (continuity_metric or {}).get("values") or {}
    for name, value in continuity_values.items():
        value_f = fnum(value)
        if value_f is not None and abs(value_f) > 5.0:
            diagnostics.append(
                diagnostic(
                    "continuity_error_high",
                    "warning",
                    "Continuity error exceeds the 5 percent screening threshold.",
                    evidence={"continuity_type": name, "value_percent": value_f, "threshold_percent": 5.0},
                    recommendation="Inspect model stability, routing step, storage, and external inflow/outflow accounting.",
                )
            )

    diagnostics.extend(parse_flooding_diagnostics(rpt_path))
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")
    return {
        "schema_version": "1.1",
        "generated_by": "swmm-experiment-audit",
        "generated_at_utc": now_utc(),
        "source_inp": relpath(inp_path, repo_root) if inp_path and inp_path.exists() else None,
        "source_rpt": relpath(rpt_path, repo_root) if rpt_path and rpt_path.exists() else None,
        "status": "fail" if errors else ("warning" if warnings else "pass"),
        "error_count": errors,
        "warning_count": warnings,
        "diagnostics": diagnostics,
    }


def normalize_uncertainty_ensemble(top_manifest: dict[str, Any], repo_root: Path) -> dict[str, Any] | None:
    outputs = top_manifest.get("outputs") or {}
    summary_record = outputs.get("summary") if isinstance(outputs, dict) else None
    summary_path = None
    if isinstance(summary_record, dict):
        summary_path = resolve_recorded_path(summary_record.get("path"), repo_root)
    if summary_path is None:
        summary_path = resolve_recorded_path(top_manifest.get("summary"), repo_root)
    summary = read_json(summary_path) if summary_path else {}
    if not summary or "uncertainty" not in str(summary.get("mode", "")).lower():
        return None

    entropy_summary = summary.get("entropy_summary") or {}
    nodes = entropy_summary.get("nodes") or []
    entropy_nodes = []
    for node in nodes:
        if isinstance(node, dict):
            entropy_nodes.append(
                {
                    "node": node.get("node"),
                    "max_entropy": node.get("max_entropy"),
                    "mean_entropy": node.get("mean_entropy"),
                    "sample_count": node.get("sample_count"),
                    "entropy_json": relpath(resolve_recorded_path(node.get("entropy_json"), repo_root), repo_root)
                    if node.get("entropy_json")
                    else None,
                }
            )
    return {
        "mode": summary.get("mode"),
        "sample_count": summary.get("samples"),
        "seed": summary.get("seed"),
        "primary_node": summary.get("node"),
        "selected_plot_node": summary.get("selected_plot_node"),
        "peak_cms_envelope": summary.get("peak_cms_envelope"),
        "peak_percent_change_envelope": summary.get("peak_percent_change_envelope"),
        "node_ranking": summary.get("node_ranking", [])[:5],
        "entropy": {
            "bins": entropy_summary.get("bins"),
            "figure": relpath(resolve_recorded_path(entropy_summary.get("figure"), repo_root), repo_root)
            if entropy_summary.get("figure")
            else None,
            "nodes": entropy_nodes,
        },
        "evidence_boundary": "Uncertainty ensemble summary; no observed-flow calibration metrics are implied.",
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
    model_diagnostics_path = run_dir / "model_diagnostics.json"

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
        artifact_record(
            artifact_id="model_diagnostics",
            role="Deterministic SWMM model diagnostics",
            path=model_diagnostics_path,
            repo_root=repo_root,
            produced_by="swmm-experiment-audit",
            used_for=["SWMM-specific screening diagnostics", "modeling memory"],
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
    model_diagnostics = build_model_diagnostics(
        inp_path=inp_path,
        rpt_path=rpt_path,
        continuity_metric=continuity_metric,
        repo_root=repo_root,
    )
    uncertainty_ensemble = normalize_uncertainty_ensemble(top_manifest, repo_root)

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
        "schema_version": "1.1",
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
        "model_diagnostics": model_diagnostics,
        "uncertainty_ensemble": uncertainty_ensemble,
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
            "schema_version": "1.1",
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
        "schema_version": "1.1",
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


def readable_note_name(provenance: dict[str, Any]) -> str:
    name = provenance.get("case_name") or provenance.get("run_id") or "experiment"
    text = safe_note_name(name).replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else "Experiment"


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
    model_diagnostics = provenance.get("model_diagnostics") or {}
    uncertainty_ensemble = provenance.get("uncertainty_ensemble") or {}

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

    diagnostic_rows = []
    for item in model_diagnostics.get("diagnostics") or []:
        diagnostic_rows.append(
            [
                f"`{item.get('id')}`",
                str(item.get("severity") or "unknown").upper(),
                item.get("message") or "",
                json.dumps(item.get("evidence") or {}, sort_keys=True),
            ]
        )
    if diagnostic_rows:
        sections.extend(
            [
                "## Model Diagnostics",
                "",
                md_table(["Check", "Severity", "Message", "Evidence"], diagnostic_rows),
                "",
            ]
        )

    if uncertainty_ensemble:
        entropy = uncertainty_ensemble.get("entropy") or {}
        entropy_rows = []
        for node in entropy.get("nodes") or []:
            entropy_rows.append(
                [
                    f"`{node.get('node')}`",
                    node.get("sample_count"),
                    node.get("max_entropy"),
                    node.get("mean_entropy"),
                    f"`{node.get('entropy_json')}`",
                ]
            )
        ranked_rows = []
        for row in uncertainty_ensemble.get("node_ranking") or []:
            if not row.get("ok", True):
                continue
            ranked_rows.append(
                [
                    f"`{row.get('node')}`",
                    row.get("baseline_peak_cms"),
                    row.get("relative_peak_spread_percent_of_baseline"),
                    row.get("absolute_peak_spread_cms"),
                ]
            )
        sections.extend(
            [
                "## Uncertainty Ensemble",
                "",
                md_table(
                    ["Field", "Value"],
                    [
                        ["Mode", uncertainty_ensemble.get("mode")],
                        ["Samples", uncertainty_ensemble.get("sample_count")],
                        ["Seed", uncertainty_ensemble.get("seed")],
                        ["Primary node", uncertainty_ensemble.get("primary_node")],
                        ["Selected plot node", uncertainty_ensemble.get("selected_plot_node")],
                        ["Peak envelope", json.dumps(uncertainty_ensemble.get("peak_cms_envelope"), sort_keys=True)],
                        ["Peak percent-change envelope", json.dumps(uncertainty_ensemble.get("peak_percent_change_envelope"), sort_keys=True)],
                        ["Entropy curve figure", f"`{entropy.get('figure')}`"],
                    ],
                ),
                "",
            ]
        )
        if entropy_rows:
            sections.extend(
                [
                    "### Output Entropy",
                    "",
                    md_table(["Node", "Samples", "Max H*", "Mean H*", "Entropy JSON"], entropy_rows),
                    "",
                ]
            )
        if ranked_rows:
            sections.extend(
                [
                    "### Node Spread Ranking",
                    "",
                    md_table(["Node", "Baseline peak CMS", "Relative spread percent", "Absolute spread CMS"], ranked_rows),
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
    ap.add_argument("--out-model-diagnostics", type=Path, help="Output path for model_diagnostics.json.")
    ap.add_argument(
        "--obsidian-dir",
        type=Path,
        default=DEFAULT_OBSIDIAN_AUDIT_DIR,
        help=(
            "Obsidian vault folder where a copy of experiment_note.md should be written. "
            f"Defaults to {DEFAULT_OBSIDIAN_AUDIT_DIR}."
        ),
    )
    ap.add_argument(
        "--obsidian-note-name",
        help="Optional file name for the Obsidian note. Defaults to a readable case/run title.",
    )
    ap.add_argument(
        "--obsidian-index",
        type=Path,
        default=DEFAULT_OBSIDIAN_AUDIT_INDEX,
        help=(
            "Obsidian audit index to update after writing the note. "
            f"Defaults to {DEFAULT_OBSIDIAN_AUDIT_INDEX}."
        ),
    )
    ap.add_argument(
        "--no-obsidian",
        action="store_true",
        help="Disable the default Obsidian note copy and index update.",
    )
    return ap.parse_args()


def validate_run_layout(run_dir: Path) -> str | None:
    """Pre-flight check for the audit-cleanup invariant (PRD M6).

    Returns ``None`` when the run dir is compatible with the 1.1 layout.
    Returns an error string when legacy root-level audit artefacts are
    present (P1/P2/P3); the caller should print the message to stderr and
    exit non-zero so the user runs ``scripts/migrate_audit_layout.py``.
    """
    legacy_files = [
        name
        for name in ("experiment_note.md", "experiment_provenance.json", "comparison.json")
        if (run_dir / name).exists()
    ]
    if legacy_files:
        joined = ", ".join(legacy_files)
        return (
            f"refusing to overwrite legacy root-level audit artefacts in {run_dir}: {joined}. "
            "Run `python3 scripts/migrate_audit_layout.py --apply` first; "
            "schema 1.1 writes into <run-dir>/09_audit/ only."
        )
    return None


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    run_dir = args.run_dir.resolve()

    error = validate_run_layout(run_dir)
    if error is not None:
        print(error, file=sys.stderr)
        raise SystemExit(2)

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

    audit_dir = run_dir / "09_audit"
    out_provenance = args.out_provenance or (audit_dir / "experiment_provenance.json")
    out_comparison = args.out_comparison or (audit_dir / "comparison.json")
    out_note = args.out_note or (audit_dir / "experiment_note.md")
    out_model_diagnostics = args.out_model_diagnostics or (audit_dir / "model_diagnostics.json")
    obsidian_note = None
    if args.obsidian_dir and not args.no_obsidian:
        note_name = args.obsidian_note_name or f"{readable_note_name(provenance)}.md"
        obsidian_note = args.obsidian_dir / note_name

    write_json(out_model_diagnostics, provenance.get("model_diagnostics") or {})
    provenance["artifacts"]["model_diagnostics"] = artifact_record(
        artifact_id="model_diagnostics",
        role="Deterministic SWMM model diagnostics",
        path=out_model_diagnostics,
        repo_root=repo_root,
        produced_by="swmm-experiment-audit",
        used_for=["SWMM-specific screening diagnostics", "modeling memory"],
    )
    write_json(out_provenance, provenance)
    write_json(out_comparison, comparison)
    note_text = render_note(provenance, comparison, repo_root)
    write_text(out_note, note_text)
    if obsidian_note:
        write_text(obsidian_note, note_text)
        if args.obsidian_index:
            update_obsidian_audit_index(
                args.obsidian_index,
                provenance,
                out_provenance,
                out_comparison,
                obsidian_note,
            )

    print(
        json.dumps(
            {
                "ok": True,
                "run_id": provenance.get("run_id"),
                "status": provenance.get("status"),
                "experiment_provenance": str(out_provenance),
                "comparison": str(out_comparison),
                "experiment_note": str(out_note),
                "model_diagnostics": str(out_model_diagnostics),
                "obsidian_note": str(obsidian_note) if obsidian_note else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
