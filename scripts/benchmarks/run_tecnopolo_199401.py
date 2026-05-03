#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from swmmtoolbox import extract


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "examples" / "tecnopolo"
DEFAULT_RUN_DIR = REPO_ROOT / "runs" / "benchmarks" / "tecnopolo-199401-prepared"
NODE_TARGET = "J22"
OUTFALL_TARGET = "OUT_0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT.resolve()))


def run_cmd(cmd: list[str], *, cwd: Path = REPO_ROOT, stdout: Path | None = None, stderr: Path | None = None) -> subprocess.CompletedProcess[str]:
    stdout_handle = stdout.open("w", encoding="utf-8") if stdout else subprocess.PIPE
    stderr_handle = stderr.open("w", encoding="utf-8") if stderr else subprocess.PIPE
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            check=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
    finally:
        if stdout:
            stdout_handle.close()
        if stderr:
            stderr_handle.close()


def run_json(cmd: list[str]) -> dict:
    proc = run_cmd(cmd)
    return json.loads(proc.stdout)


def copy_inputs(run_dir: Path) -> Path:
    builder_dir = run_dir / "05_builder"
    inputs_dir = run_dir / "00_inputs"
    builder_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "tecnopolo_r1_199401.inp": "model.inp",
        "EXT_RAIN_199401_MACAO_CUM_NEW_5MIN.DAT": "EXT_RAIN_199401_MACAO_CUM_NEW_5MIN.DAT",
        "EXT_TEM_199401_ROME_NASA_mm_day.dat": "EXT_TEM_199401_ROME_NASA_mm_day.dat",
        "EXT_EVP_199401_ROME_NASA_mm_day.dat": "EXT_EVP_199401_ROME_NASA_mm_day.dat",
    }
    for src_name, dst_name in files.items():
        src = EXAMPLE_DIR / src_name
        dst = builder_dir / dst_name
        shutil.copy2(src, dst)
        shutil.copy2(dst, inputs_dir / dst_name)
    return builder_dir / "model.inp"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compare_direct_and_runner(run_dir: Path) -> dict:
    direct_out = run_dir / "10_direct" / "model.out"
    runner_out = run_dir / "06_runner" / "model.out"
    direct_rpt = run_dir / "10_direct" / "model.rpt"
    runner_rpt = run_dir / "06_runner" / "model.rpt"

    def strip_analysis_timestamps(path: Path) -> str:
        lines = []
        for line in path.read_text(errors="ignore").splitlines():
            if "Analysis begun on:" in line or "Analysis ended on:" in line:
                continue
            lines.append(line)
        return "\n".join(lines)

    result = {
        "direct_out_sha256": sha256_file(direct_out),
        "runner_out_sha256": sha256_file(runner_out),
        "out_binary_identical": sha256_file(direct_out) == sha256_file(runner_out),
        "direct_rpt_sha256": sha256_file(direct_rpt),
        "runner_rpt_sha256": sha256_file(runner_rpt),
        "rpt_identical_except_analysis_timestamps": strip_analysis_timestamps(direct_rpt) == strip_analysis_timestamps(runner_rpt),
        "known_rpt_difference": "SWMM Analysis begun/ended timestamp lines only",
        "direct_rpt": rel(direct_rpt),
        "runner_rpt": rel(runner_rpt),
        "direct_out": rel(direct_out),
        "runner_out": rel(runner_out),
    }
    write_json(run_dir / "07_qa" / "direct_runner_consistency.json", result)
    return result


def validate_internal_node(run_dir: Path) -> dict:
    runner_series = extract(str(run_dir / "06_runner" / "model.out"), f"node,{NODE_TARGET},Total_inflow").iloc[:, 0]
    direct_series = extract(str(run_dir / "10_direct" / "model.out"), f"node,{NODE_TARGET},Total_inflow").iloc[:, 0]
    result = {
        "node": NODE_TARGET,
        "selection": "Internal junction with nonzero inflow in the January 1994 event.",
        "no_rerun_required": True,
        "reason": "The completed SWMM report and binary output include all nodes.",
        "runner_out_peak_cms": float(runner_series.max()),
        "direct_out_peak_cms": float(direct_series.max()),
        "runner_out_peak_time": str(runner_series.idxmax()),
        "direct_out_peak_time": str(direct_series.idxmax()),
        "out_series_identical": bool(runner_series.equals(direct_series)),
        "point_count": int(len(runner_series)),
        "source": f"node,{NODE_TARGET},Total_inflow from model.out",
    }
    write_json(run_dir / "07_qa" / f"node_{NODE_TARGET}_validation.json", result)
    return result


def write_top_manifest(run_dir: Path, inp: Path) -> None:
    git_branch = subprocess.run(["git", "branch", "--show-current"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    git_status = subprocess.run(["git", "status", "--short"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    runner_manifest = json.loads((run_dir / "06_runner" / "manifest.json").read_text(encoding="utf-8"))
    swmm_version = (runner_manifest.get("swmm5") or {}).get("version")
    outputs = {
        "built_inp": run_dir / "05_builder" / "model.inp",
        "runner_rpt": run_dir / "06_runner" / "model.rpt",
        "runner_out": run_dir / "06_runner" / "model.out",
        "runner_manifest": run_dir / "06_runner" / "manifest.json",
        "peak_qa": run_dir / "07_qa" / "runner_peak.json",
        "continuity_qa": run_dir / "07_qa" / "runner_continuity.json",
        "direct_runner_consistency": run_dir / "07_qa" / "direct_runner_consistency.json",
        "internal_node_validation": run_dir / "07_qa" / f"node_{NODE_TARGET}_validation.json",
        "outfall_plot": run_dir / "08_plot" / f"rain_runoff_{OUTFALL_TARGET}.png",
        "internal_node_plot": run_dir / "08_plot" / f"rain_runoff_{NODE_TARGET}.png",
        "direct_baseline_out": run_dir / "10_direct" / "model.out",
    }
    inputs = {
        "prepared_inp": inp,
        "rainfall": run_dir / "05_builder" / "EXT_RAIN_199401_MACAO_CUM_NEW_5MIN.DAT",
        "temperature": run_dir / "05_builder" / "EXT_TEM_199401_ROME_NASA_mm_day.dat",
        "evaporation": run_dir / "05_builder" / "EXT_EVP_199401_ROME_NASA_mm_day.dat",
    }
    manifest = {
        "manifest_version": "1.0",
        "run_id": run_dir.name,
        "case_name": "Tecnopolo January 1994 prepared-input benchmark",
        "pipeline": "external multi-subcatchment prepared-input benchmark",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "swmm-end-to-end Mode B adapted for externally supplied INP",
        "repo": {"root": str(REPO_ROOT), "git_branch": git_branch, "git_head": git_head, "git_status_porcelain": git_status},
        "tools": {"swmm5_version": swmm_version, "runner": "skills/swmm-runner", "plot": "skills/swmm-plot", "audit": "skills/swmm-experiment-audit"},
        "inputs": {key: {"path": rel(path), "sha256": sha256_file(path)} for key, path in inputs.items()},
        "outputs": {key: {"path": rel(path), "sha256": sha256_file(path)} for key, path in outputs.items() if path.exists()},
        "commands": [
            {"stage": "direct_baseline", "tool": "swmm5", "output_dir": rel(run_dir / "10_direct")},
            {"stage": "runner", "tool": "swmm-runner CLI", "output_dir": rel(run_dir / "06_runner")},
            {"stage": "qa", "tool": "swmm-runner peak/continuity", "output_dir": rel(run_dir / "07_qa")},
            {"stage": "plot", "tool": "swmm-plot", "output_dir": rel(run_dir / "08_plot")},
            {"stage": "audit", "tool": "swmm-experiment-audit", "output_dir": rel(run_dir)},
        ],
    }
    write_json(run_dir / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tecnopolo January 1994 prepared-input benchmark.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--keep-existing", action="store_true", help="Do not delete an existing run directory before running.")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    if run_dir.exists() and not args.keep_existing:
        shutil.rmtree(run_dir)
    for subdir in ["06_runner", "07_qa", "08_plot", "10_direct"]:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    inp = copy_inputs(run_dir)
    direct_dir = run_dir / "10_direct"
    run_cmd(["swmm5", str(inp), str(direct_dir / "model.rpt"), str(direct_dir / "model.out")], stdout=direct_dir / "stdout.txt", stderr=direct_dir / "stderr.txt")

    run_cmd([
        "python3",
        "skills/swmm-runner/scripts/swmm_runner.py",
        "run",
        "--inp",
        str(inp),
        "--run-dir",
        str(run_dir / "06_runner"),
        "--node",
        OUTFALL_TARGET,
    ])

    runner_rpt = run_dir / "06_runner" / "model.rpt"
    peak = run_json(["python3", "skills/swmm-runner/scripts/swmm_runner.py", "peak", "--rpt", str(runner_rpt), "--node", OUTFALL_TARGET])
    continuity = run_json(["python3", "skills/swmm-runner/scripts/swmm_runner.py", "continuity", "--rpt", str(runner_rpt)])
    write_json(run_dir / "07_qa" / "runner_peak.json", peak)
    write_json(run_dir / "07_qa" / "runner_continuity.json", continuity)

    node_peak = run_json(["python3", "skills/swmm-runner/scripts/swmm_runner.py", "peak", "--rpt", str(runner_rpt), "--node", NODE_TARGET])
    write_json(run_dir / "07_qa" / f"node_{NODE_TARGET}_peak.json", node_peak)
    consistency = compare_direct_and_runner(run_dir)
    node_validation = validate_internal_node(run_dir)

    for node in [OUTFALL_TARGET, NODE_TARGET]:
        run_cmd([
            "python3",
            "skills/swmm-plot/scripts/plot_rain_runoff_si.py",
            "--inp",
            str(inp),
            "--out",
            str(run_dir / "06_runner" / "model.out"),
            "--rain-ts",
            "MACAO_94_23",
            "--rain-kind",
            "cumulative_depth_mm",
            "--dt-min",
            "5",
            "--node",
            node,
            "--node-attr",
            "Total_inflow",
            "--focus-day",
            "1994-01-11",
            "--window-start",
            "00:00",
            "--window-end",
            "12:00",
            "--out-png",
            str(run_dir / "08_plot" / f"rain_runoff_{node}.png"),
        ])

    write_top_manifest(run_dir, inp)
    run_cmd([
        "python3",
        "skills/swmm-experiment-audit/scripts/audit_run.py",
        "--run-dir",
        str(run_dir),
        "--case-name",
        "Tecnopolo January 1994 prepared-input benchmark",
        "--workflow-mode",
        "external multi-subcatchment prepared-input benchmark",
        "--objective",
        "Verify prepared-input execution, direct SWMM consistency, node-level QA, plotting, and audit.",
    ])

    summary = {
        "run_dir": rel(run_dir),
        "status": "pass",
        "outfall_peak": peak,
        "internal_node_peak": node_peak,
        "continuity_error_percent": continuity.get("continuity_error_percent"),
        "direct_runner_consistency": consistency,
        "internal_node_validation": node_validation,
        "audit_note": rel(run_dir / "experiment_note.md"),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
