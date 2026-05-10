from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root, require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path.resolve())


def _parse_runner_manifest(stdout: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _copy_inp_sidecar_files(inp: Path, inputs_dir: Path) -> list[Path]:
    copied: list[Path] = []
    text = inp.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r"\bFILE\s+\"?([^\"\s;]+)\"?", text, flags=re.IGNORECASE):
        raw = match.group(1)
        source = Path(raw)
        if not source.is_absolute():
            source = inp.parent / source
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"INP references an external FILE that was not found: {source}")
        target = inputs_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied.append(target)
    return copied


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("run", help="Run a SWMM INP file and write runner artifacts.")
    parser.add_argument("--inp", required=True, type=Path, help="Input SWMM .inp file.")
    parser.add_argument("--run-dir", "--out", dest="run_dir", required=True, type=Path, help="Run output directory.")
    parser.add_argument("--node", default="O1", help="Node/outfall used for peak-flow parsing.")
    parser.add_argument("--rpt-name", help="Report file name. Defaults to model.rpt.")
    parser.add_argument("--out-name", help="Binary output file name. Defaults to model.out.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    inp = require_file(args.inp, "INP file")
    run_dir = args.run_dir.expanduser().resolve()
    inputs_dir = run_dir / "00_inputs"
    builder_dir = run_dir / "04_builder"
    runner_dir = run_dir / "05_runner"
    qa_dir = run_dir / "06_qa"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    builder_dir.mkdir(parents=True, exist_ok=True)
    runner_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    run_inp = inputs_dir / inp.name
    if inp.resolve() != run_inp.resolve():
        shutil.copy2(inp, run_inp)
    sidecar_inputs = _copy_inp_sidecar_files(inp, inputs_dir)
    builder_inp = builder_dir / "model.inp"
    shutil.copy2(run_inp, builder_inp)
    for sidecar in sidecar_inputs:
        target = builder_dir / sidecar.name
        if sidecar.resolve() != target.resolve():
            shutil.copy2(sidecar, target)

    script = script_path("skills", "swmm-runner", "scripts", "swmm_runner.py")
    command = python_command(
        script,
        "run",
        "--inp",
        str(builder_inp),
        "--run-dir",
        str(runner_dir),
        "--node",
        args.node,
    )
    if args.rpt_name:
        command.extend(["--rpt-name", args.rpt_name])
    if args.out_name:
        command.extend(["--out-name", args.out_name])

    result = run_command(command)
    append_trace(run_dir / "command_trace.json", result, stage="run")
    runner_manifest = _parse_runner_manifest(result.stdout)
    runner_files = runner_manifest.get("files") if isinstance(runner_manifest.get("files"), dict) else {}
    peak = (runner_manifest.get("metrics") or {}).get("peak") or {}
    continuity = (runner_manifest.get("metrics") or {}).get("continuity") or {}
    continuity_errors = continuity.get("continuity_error_percent") or {}
    qa_checks = [
        {
            "id": "swmm_return_code_zero",
            "ok": runner_manifest.get("return_code") == 0,
            "detail": f"return_code={runner_manifest.get('return_code')}",
        },
        {
            "id": "runner_rpt_exists",
            "ok": bool(runner_files.get("rpt") and Path(runner_files["rpt"]).exists()),
            "detail": runner_files.get("rpt"),
        },
        {
            "id": "runner_out_exists",
            "ok": bool(runner_files.get("out") and Path(runner_files["out"]).exists()),
            "detail": runner_files.get("out"),
        },
        {
            "id": "peak_parsed",
            "ok": peak.get("peak") is not None,
            "detail": f"node={peak.get('node')} source={peak.get('source')} peak={peak.get('peak')}",
        },
        {
            "id": "continuity_parsed",
            "ok": any(value is not None for value in continuity_errors.values()),
            "detail": continuity_errors,
        },
    ]
    qa_summary = {
        "schema_version": "1.0",
        "status": "pass" if all(check["ok"] for check in qa_checks) else "fail",
        "checks": qa_checks,
        "peak_file": str(qa_dir / "runner_peak.json"),
        "continuity_file": str(qa_dir / "runner_continuity.json"),
    }
    (qa_dir / "runner_peak.json").write_text(json.dumps(peak, indent=2), encoding="utf-8")
    (qa_dir / "runner_continuity.json").write_text(json.dumps(continuity, indent=2), encoding="utf-8")
    (qa_dir / "qa_summary.json").write_text(json.dumps(qa_summary, indent=2), encoding="utf-8")

    builder_manifest = {
        "schema_version": "1.0",
        "stage": "prepared-input-handoff",
        "outputs": {"inp": _rel(builder_inp)},
        "inputs": {
            "source_inp": _rel(inp),
            "copied_inp": _rel(run_inp),
            "sidecar_files": [_rel(path) for path in sidecar_inputs],
        },
        "validation": {
            "status": "pass",
            "notes": ["Prepared-input workflow: INP was supplied by the user/example and copied into 04_builder as the execution handoff."],
        },
    }
    (builder_dir / "manifest.json").write_text(json.dumps(builder_manifest, indent=2), encoding="utf-8")

    top_manifest = {
        "schema_version": "1.0",
        "generated_by": "agentic-swmm",
        "generated_at_utc": _now_utc(),
        "run_id": run_dir.name,
        "pipeline": "prepared-input-cli",
        "inputs": {
            "source_inp": {"path": _rel(inp)},
            "run_inp": {"path": _rel(run_inp)},
            "builder_inp": {"path": _rel(builder_inp)},
            "sidecar_files": [{"path": _rel(path)} for path in sidecar_inputs],
        },
        "outputs": {
            "built_inp": {"path": _rel(builder_inp)},
            "runner_rpt": {"path": _rel(Path(runner_files["rpt"]))} if runner_files.get("rpt") else None,
            "runner_out": {"path": _rel(Path(runner_files["out"]))} if runner_files.get("out") else None,
            "runner_manifest": {"path": _rel(runner_dir / "manifest.json")},
            "qa_summary": {"path": _rel(qa_dir / "qa_summary.json")},
        },
        "commands": [result.as_trace()],
        "tools": {
            "agentic_swmm_command": "run",
            "swmm5_version": (runner_manifest.get("swmm5") or {}).get("version"),
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(top_manifest, indent=2), encoding="utf-8")
    print(result.stdout.strip())
    print(f"run directory: {run_dir}")
    print("standard layout: 00_inputs/, 04_builder/, 05_runner/, 06_qa/, manifest.json, command_trace.json")
    return result.return_code
