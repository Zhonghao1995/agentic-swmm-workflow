from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_quiet_flag,
)
from agentic_swmm.agent.honesty import (
    SwmmRunError,
    assert_swmm_run_ok,
    is_honesty_layer_disabled,
)
from agentic_swmm.utils.paths import repo_root, require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


_RUN_EXAMPLE = (
    "aiswmm run --inp examples/<case>/model.inp "
    "--run-dir runs/<case> --node O1"
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root().resolve()))
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_type(path: Path) -> str:
    try:
        path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return "external_inp_import"
    return "repository_inp"


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
        # PRD-08 A.3 (audit #4): the previous code treated a section
        # header token like ``[OPTIONS]`` as a filename and surfaced
        # "FILE not found: /…/[OPTIONS]" — confusing for users whose
        # INP merely has sections in the wrong order. Detect a
        # section-header-shaped token and raise an INP-parser error
        # instead so the message points at the real problem.
        if raw.startswith("[") and raw.endswith("]"):
            # Find the offending line number for the error so the user
            # can locate it in the editor.
            line_no = 0
            for i, line in enumerate(text.splitlines(), start=1):
                if raw in line and "FILE" in line.upper():
                    line_no = i
                    break
            raise FileNotFoundError(
                f"INP parser error at line {line_no or '?'}: encountered "
                f"section header {raw} where a filename was expected. "
                "The INP file likely has sections in the wrong order; see "
                "the SWMM 5 manual for the canonical section order."
            )
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
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_RUN_EXAMPLE)
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

    source_type = _source_type(inp)
    run_inp = inputs_dir / "model.inp"
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
        "source_type": source_type,
        "outputs": {"inp": _rel(builder_inp)},
        "inputs": {
            "source_inp": {"path": _rel(inp), "sha256": _sha256(inp), "source_type": source_type},
            "copied_inp": {"path": _rel(run_inp), "sha256": _sha256(run_inp)},
            "sidecar_files": [{"path": _rel(path), "sha256": _sha256(path)} for path in sidecar_inputs],
        },
        "validation": {
            "status": "pass",
            "notes": [
                "Prepared-input workflow: INP was supplied by the user/example and copied into 04_builder as the execution handoff.",
                "External INP imports are copied into the run directory before execution; SWMM runs against the run-local copy.",
            ]
            if source_type == "external_inp_import"
            else ["Prepared-input workflow: INP was supplied by the user/example and copied into 04_builder as the execution handoff."],
        },
    }
    (builder_dir / "manifest.json").write_text(json.dumps(builder_manifest, indent=2), encoding="utf-8")

    top_manifest = {
        "schema_version": "1.0",
        "generated_by": "agentic-swmm",
        "generated_at_utc": _now_utc(),
        "run_id": run_dir.name,
        "pipeline": source_type if source_type == "external_inp_import" else "prepared-input-cli",
        "inputs": {
            "source_inp": {"path": _rel(inp), "sha256": _sha256(inp), "source_type": source_type},
            "run_inp": {"path": _rel(run_inp), "sha256": _sha256(run_inp), "imported_copy": True},
            "builder_inp": {"path": _rel(builder_inp), "sha256": _sha256(builder_inp)},
            "sidecar_files": [{"path": _rel(path), "sha256": _sha256(path)} for path in sidecar_inputs],
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
    # Stamp the case identity so this run groups with other runs of the same
    # watershed in parametric memory (audit reads case_id -> case_name; the
    # memory-informed read side resolves the same slug). Previously --case-id
    # was accepted by argparse but dropped here, so runs never grouped.
    case_id_raw = getattr(args, "case_id", None)
    if case_id_raw:
        from agentic_swmm.case.case_id import is_valid_case_id

        case_id = str(case_id_raw).strip()
        if case_id and is_valid_case_id(case_id):
            top_manifest["case_id"] = case_id
        elif case_id:
            print(
                f"warning: --case-id {case_id!r} is not a valid slug; not recorded",
                file=sys.stderr,
            )
    (run_dir / "manifest.json").write_text(json.dumps(top_manifest, indent=2), encoding="utf-8")
    # Audit #1 residual: ``stdout`` must carry *only* the JSON manifest so
    # ``aiswmm run > result.json`` yields a clean, parseable document.
    # The human-readable chrome (run directory + standard layout) goes to
    # ``stderr`` alongside any SWMM-error text surfaced below.
    print(result.stdout.strip())
    print(f"run directory: {run_dir}", file=sys.stderr)
    print(
        "standard layout: 00_inputs/, 04_builder/, 05_runner/, 06_qa/, "
        "manifest.json, command_trace.json",
        file=sys.stderr,
    )

    # PRD-08 A.1 (audit #1): the runner historically returned 0 even
    # when SWMM wrote ``ERROR \d+:`` lines into the .rpt. The manifest
    # has already been written (so downstream auditors can still see
    # what happened); now we surface the verbatim error line to stderr
    # and exit non-zero so an ``&&``-chained pipeline aborts cleanly.
    # The ``AISWMM_DISABLE_HONESTY_LAYER`` env var preserves the legacy
    # path for callers that intentionally accept partial runs.
    if not is_honesty_layer_disabled() and runner_files.get("rpt"):
        rpt_path = Path(runner_files["rpt"])
        try:
            assert_swmm_run_ok(rpt_path)
        except SwmmRunError as exc:
            for line in exc.error_lines:
                print(line, file=sys.stderr)
            print(
                f"error: SWMM reported {len(exc.error_lines)} error(s); "
                f"see {rpt_path}",
                file=sys.stderr,
            )
            return 1
    return result.return_code
