#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class StageResult:
    id: str
    command: list[str]
    return_code: int
    started_at_utc: str
    finished_at_utc: str
    duration_seconds: float
    stdout_file: str
    stderr_file: str
    stdout_json: dict[str, Any] | None


class StageError(RuntimeError):
    def __init__(self, stage: StageResult):
        self.stage = stage
        super().__init__(f"Stage failed: {stage.id} (rc={stage.return_code})")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def maybe_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def run_stage(*, stage_id: str, cmd: list[str], logs_dir: Path) -> StageResult:
    start = datetime.now(timezone.utc)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    end = datetime.now(timezone.utc)

    stdout_file = logs_dir / f"{stage_id}.stdout.txt"
    stderr_file = logs_dir / f"{stage_id}.stderr.txt"
    write_text(stdout_file, proc.stdout)
    write_text(stderr_file, proc.stderr)

    result = StageResult(
        id=stage_id,
        command=cmd,
        return_code=proc.returncode,
        started_at_utc=start.isoformat(timespec="seconds"),
        finished_at_utc=end.isoformat(timespec="seconds"),
        duration_seconds=round((end - start).total_seconds(), 3),
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
        stdout_json=maybe_json(proc.stdout),
    )
    if proc.returncode != 0:
        raise StageError(result)
    return result


def run_git(*args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_swmm_version() -> str | None:
    proc = subprocess.run(["swmm5", "--version"], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def build_md_report(report: dict[str, Any], manifest_path: Path) -> str:
    qa_checks = report["qa"]["checks"]
    lines = [
        "# Acceptance Report (Step 1)",
        "",
        f"- status: {'PASS' if report['ok'] else 'FAIL'}",
        f"- run_id: {report['run_id']}",
        f"- run_dir: {report['run_dir']}",
        f"- manifest: {manifest_path}",
        "",
        "## QA Checks",
        "",
        "| check | status | detail |",
        "|---|---|---|",
    ]
    for check in qa_checks:
        lines.append(f"| {check['id']} | {'PASS' if check['ok'] else 'FAIL'} | {check['detail']} |")

    lines.extend(
        [
            "",
            "## Key Artifacts",
            "",
            f"- built_inp: {report['artifacts']['built_inp']}",
            f"- runner_rpt: {report['artifacts']['runner_rpt']}",
            f"- runner_out: {report['artifacts']['runner_out']}",
            f"- acceptance_report_json: {report['artifacts']['acceptance_report_json']}",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Step-1 acceptance runner for the publish repo.")
    ap.add_argument("--run-id", default="latest", help="Run directory name under runs/acceptance/")
    ap.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not delete an existing runs/acceptance/<run-id> directory before running.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    python = sys.executable

    run_dir = REPO_ROOT / "runs" / "acceptance" / args.run_id
    logs_dir = run_dir / "_logs"

    if run_dir.exists() and not args.keep_existing:
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    input_paths = {
        "subcatchments_geojson": REPO_ROOT / "skills/swmm-gis/examples/subcatchments_demo.geojson",
        "network_json": REPO_ROOT / "skills/swmm-network/examples/basic-network.json",
        "landuse_csv": REPO_ROOT / "skills/swmm-params/examples/landuse_input.csv",
        "soil_csv": REPO_ROOT / "skills/swmm-params/examples/soil_input.csv",
        "rainfall_csv": REPO_ROOT / "skills/swmm-climate/examples/rainfall_event.csv",
        "builder_config_json": REPO_ROOT / "skills/swmm-builder/examples/options_config.json",
        "gis_script": REPO_ROOT / "skills/swmm-gis/scripts/preprocess_subcatchments.py",
        "landuse_script": REPO_ROOT / "skills/swmm-params/scripts/landuse_to_swmm_params.py",
        "soil_script": REPO_ROOT / "skills/swmm-params/scripts/soil_to_greenampt.py",
        "merge_script": REPO_ROOT / "skills/swmm-params/scripts/merge_swmm_params.py",
        "climate_script": REPO_ROOT / "skills/swmm-climate/scripts/format_rainfall.py",
        "raingage_script": REPO_ROOT / "skills/swmm-climate/scripts/build_raingage_section.py",
        "builder_script": REPO_ROOT / "skills/swmm-builder/scripts/build_swmm_inp.py",
        "runner_script": REPO_ROOT / "skills/swmm-runner/scripts/swmm_runner.py",
        "network_qa_script": REPO_ROOT / "skills/swmm-network/scripts/network_qa.py",
    }
    for p in input_paths.values():
        if not p.exists():
            raise FileNotFoundError(f"Required input/script not found: {p}")

    stage_paths = {
        "gis_csv": run_dir / "01_gis/subcatchments_preprocessed.csv",
        "gis_json": run_dir / "01_gis/subcatchments_preprocessed.json",
        "landuse_json": run_dir / "02_params/landuse_mapped.json",
        "soil_json": run_dir / "02_params/soil_mapped.json",
        "merged_params_json": run_dir / "02_params/merged_params.json",
        "rainfall_json": run_dir / "03_climate/rainfall_formatted.json",
        "timeseries_txt": run_dir / "03_climate/rainfall_timeseries.txt",
        "raingage_txt": run_dir / "03_climate/raingage.txt",
        "raingage_json": run_dir / "03_climate/raingage.json",
        "inp": run_dir / "04_builder/model.inp",
        "builder_manifest": run_dir / "04_builder/manifest.json",
        "runner_dir": run_dir / "05_runner",
        "runner_rpt": run_dir / "05_runner/acceptance.rpt",
        "runner_out": run_dir / "05_runner/acceptance.out",
        "network_qa_json": run_dir / "06_qa/network_qa.json",
        "continuity_json": run_dir / "06_qa/runner_continuity.json",
        "peak_json": run_dir / "06_qa/runner_peak.json",
    }

    stage_results: list[StageResult] = []
    created_at = now_utc_iso()
    failed_stage: StageResult | None = None
    try:
        stage_results.append(
            run_stage(
                stage_id="01_swmm_gis_preprocess",
                cmd=[
                    python,
                    str(input_paths["gis_script"]),
                    "--subcatchments-geojson",
                    str(input_paths["subcatchments_geojson"]),
                    "--network-json",
                    str(input_paths["network_json"]),
                    "--default-rain-gage",
                    "RG1",
                    "--out-csv",
                    str(stage_paths["gis_csv"]),
                    "--out-json",
                    str(stage_paths["gis_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="02_swmm_params_landuse",
                cmd=[
                    python,
                    str(input_paths["landuse_script"]),
                    "--input",
                    str(input_paths["landuse_csv"]),
                    "--output",
                    str(stage_paths["landuse_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="03_swmm_params_soil",
                cmd=[
                    python,
                    str(input_paths["soil_script"]),
                    "--input",
                    str(input_paths["soil_csv"]),
                    "--output",
                    str(stage_paths["soil_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="04_swmm_params_merge",
                cmd=[
                    python,
                    str(input_paths["merge_script"]),
                    "--landuse-json",
                    str(stage_paths["landuse_json"]),
                    "--soil-json",
                    str(stage_paths["soil_json"]),
                    "--output",
                    str(stage_paths["merged_params_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="05_swmm_climate_format",
                cmd=[
                    python,
                    str(input_paths["climate_script"]),
                    "--input",
                    str(input_paths["rainfall_csv"]),
                    "--out-json",
                    str(stage_paths["rainfall_json"]),
                    "--out-timeseries",
                    str(stage_paths["timeseries_txt"]),
                    "--series-name",
                    "TS_EVENT",
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="06_swmm_climate_raingage",
                cmd=[
                    python,
                    str(input_paths["raingage_script"]),
                    "--rainfall-json",
                    str(stage_paths["rainfall_json"]),
                    "--gage-id",
                    "RG1",
                    "--interval-min",
                    "5",
                    "--out-text",
                    str(stage_paths["raingage_txt"]),
                    "--out-json",
                    str(stage_paths["raingage_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="07_swmm_builder_build",
                cmd=[
                    python,
                    str(input_paths["builder_script"]),
                    "--subcatchments-csv",
                    str(stage_paths["gis_csv"]),
                    "--params-json",
                    str(stage_paths["merged_params_json"]),
                    "--network-json",
                    str(input_paths["network_json"]),
                    "--rainfall-json",
                    str(stage_paths["rainfall_json"]),
                    "--raingage-json",
                    str(stage_paths["raingage_json"]),
                    "--config-json",
                    str(input_paths["builder_config_json"]),
                    "--out-inp",
                    str(stage_paths["inp"]),
                    "--out-manifest",
                    str(stage_paths["builder_manifest"]),
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="08_swmm_runner_run",
                cmd=[
                    python,
                    str(input_paths["runner_script"]),
                    "run",
                    "--inp",
                    str(stage_paths["inp"]),
                    "--run-dir",
                    str(stage_paths["runner_dir"]),
                    "--node",
                    "OF1",
                    "--rpt-name",
                    stage_paths["runner_rpt"].name,
                    "--out-name",
                    stage_paths["runner_out"].name,
                ],
                logs_dir=logs_dir,
            )
        )
        stage_results.append(
            run_stage(
                stage_id="09_qa_network",
                cmd=[
                    python,
                    str(input_paths["network_qa_script"]),
                    str(input_paths["network_json"]),
                    "--report-json",
                    str(stage_paths["network_qa_json"]),
                ],
                logs_dir=logs_dir,
            )
        )
        continuity_stage = run_stage(
            stage_id="10_qa_runner_continuity",
            cmd=[
                python,
                str(input_paths["runner_script"]),
                "continuity",
                "--rpt",
                str(stage_paths["runner_rpt"]),
            ],
            logs_dir=logs_dir,
        )
        stage_results.append(continuity_stage)
        if continuity_stage.stdout_json is not None:
            write_json(stage_paths["continuity_json"], continuity_stage.stdout_json)

        peak_stage = run_stage(
            stage_id="11_qa_runner_peak",
            cmd=[
                python,
                str(input_paths["runner_script"]),
                "peak",
                "--rpt",
                str(stage_paths["runner_rpt"]),
                "--node",
                "OF1",
            ],
            logs_dir=logs_dir,
        )
        stage_results.append(peak_stage)
        if peak_stage.stdout_json is not None:
            write_json(stage_paths["peak_json"], peak_stage.stdout_json)
    except StageError as exc:
        failed_stage = exc.stage

    manifest_path = run_dir / "manifest.json"
    report_json_path = run_dir / "acceptance_report.json"
    report_md_path = run_dir / "acceptance_report.md"

    network_qa = (
        json.loads(stage_paths["network_qa_json"].read_text(encoding="utf-8"))
        if stage_paths["network_qa_json"].exists()
        else {}
    )
    builder_manifest = (
        json.loads(stage_paths["builder_manifest"].read_text(encoding="utf-8"))
        if stage_paths["builder_manifest"].exists()
        else {}
    )
    runner_manifest = (
        json.loads((stage_paths["runner_dir"] / "manifest.json").read_text(encoding="utf-8"))
        if (stage_paths["runner_dir"] / "manifest.json").exists()
        else {}
    )
    continuity = (
        json.loads(stage_paths["continuity_json"].read_text(encoding="utf-8"))
        if stage_paths["continuity_json"].exists()
        else {}
    )
    peak = (
        json.loads(stage_paths["peak_json"].read_text(encoding="utf-8"))
        if stage_paths["peak_json"].exists()
        else {}
    )

    validation = builder_manifest.get("validation") or {}
    validation_ok = not any(bool(v) for v in validation.values())
    continuity_err = (continuity.get("continuity_error_percent") or {})
    continuity_ok = continuity_err.get("runoff_quantity") is not None and continuity_err.get("flow_routing") is not None

    checks = [
        {
            "id": "builder_input_validation",
            "ok": validation_ok,
            "detail": json.dumps(validation, sort_keys=True),
        },
        {
            "id": "runner_return_code_zero",
            "ok": runner_manifest.get("return_code") == 0,
            "detail": f"return_code={runner_manifest.get('return_code')}",
        },
        {
            "id": "runner_outputs_exist",
            "ok": stage_paths["runner_rpt"].exists() and stage_paths["runner_out"].exists(),
            "detail": (
                f"rpt_exists={stage_paths['runner_rpt'].exists()} "
                f"out_exists={stage_paths['runner_out'].exists()}"
            ),
        },
        {
            "id": "network_qa_ok",
            "ok": bool(network_qa.get("ok")),
            "detail": f"issue_count={network_qa.get('issue_count')}",
        },
        {
            "id": "continuity_parsed",
            "ok": continuity_ok,
            "detail": json.dumps(continuity_err, sort_keys=True),
        },
    ]

    failed = [c for c in checks if not c["ok"]]
    report = {
        "pipeline": "acceptance_step1",
        "ok": failed_stage is None and not failed,
        "run_id": args.run_id,
        "run_dir": str(run_dir),
        "created_at_utc": created_at,
        "failed_stage": failed_stage.id if failed_stage else None,
        "qa": {
            "pass_count": len(checks) - len(failed),
            "fail_count": len(failed),
            "checks": checks,
        },
        "key_outputs": {
            "builder_subcatchments": (builder_manifest.get("counts") or {}).get("subcatchments"),
            "builder_network_conduits": (builder_manifest.get("counts") or {}).get("network_conduits"),
            "continuity_error_percent": continuity_err,
            "peak": peak,
        },
        "artifacts": {
            "built_inp": str(stage_paths["inp"]),
            "builder_manifest": str(stage_paths["builder_manifest"]),
            "runner_manifest": str(stage_paths["runner_dir"] / "manifest.json"),
            "runner_rpt": str(stage_paths["runner_rpt"]),
            "runner_out": str(stage_paths["runner_out"]),
            "network_qa_json": str(stage_paths["network_qa_json"]),
            "continuity_json": str(stage_paths["continuity_json"]),
            "peak_json": str(stage_paths["peak_json"]),
            "acceptance_report_json": str(report_json_path),
            "acceptance_report_md": str(report_md_path),
        },
    }

    inputs_manifest = {name: path_record(path) for name, path in input_paths.items()}
    commands_manifest = [
        {
            "id": st.id,
            "command": st.command,
            "return_code": st.return_code,
            "started_at_utc": st.started_at_utc,
            "finished_at_utc": st.finished_at_utc,
            "duration_seconds": st.duration_seconds,
            "stdout_file": st.stdout_file,
            "stderr_file": st.stderr_file,
        }
        for st in stage_results
    ]
    manifest = {
        "manifest_version": "1.0",
        "pipeline": "acceptance_step1",
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "repo": {
            "root": str(REPO_ROOT),
            "git_head": run_git("rev-parse", "HEAD"),
            "git_branch": run_git("rev-parse", "--abbrev-ref", "HEAD"),
            "git_status_porcelain": run_git("status", "--short"),
        },
        "tools": {
            "python_executable": python,
            "python_version": sys.version.replace("\n", " "),
            "swmm5_version": get_swmm_version(),
        },
        "inputs": inputs_manifest,
        "commands": commands_manifest,
        "outputs": {
            "run_dir": str(run_dir),
            "built_inp": path_record(stage_paths["inp"]) if stage_paths["inp"].exists() else None,
            "runner_rpt": path_record(stage_paths["runner_rpt"]) if stage_paths["runner_rpt"].exists() else None,
            "runner_out": path_record(stage_paths["runner_out"]) if stage_paths["runner_out"].exists() else None,
            "acceptance_report_json": str(report_json_path),
            "acceptance_report_md": str(report_md_path),
        },
    }

    write_json(report_json_path, report)
    write_text(report_md_path, build_md_report(report, manifest_path))
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "run_dir": str(run_dir),
                "manifest": str(manifest_path),
                "report_json": str(report_json_path),
                "report_md": str(report_md_path),
                "built_inp": str(stage_paths["inp"]),
                "runner_rpt": str(stage_paths["runner_rpt"]),
                "runner_out": str(stage_paths["runner_out"]),
            },
            indent=2,
        )
    )

    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
