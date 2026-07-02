from __future__ import annotations

import argparse
import json
import shutil
import sys
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
from agentic_swmm.agent.swmm_runtime.inp_parsing import copy_inp_sidecar_files
from agentic_swmm.agent.swmm_runtime.run_manifests import (
    build_builder_manifest,
    build_qa_summary,
    build_top_manifest,
    parse_runner_manifest,
    source_type_of,
)
from agentic_swmm.utils.paths import require_file, script_path
from agentic_swmm.utils.subprocess_runner import append_trace, python_command, run_command


_RUN_EXAMPLE = (
    "aiswmm run --inp examples/<case>/model.inp "
    "--run-dir runs/<case> --node O1"
)


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

    source_type = source_type_of(inp)
    run_inp = inputs_dir / "model.inp"
    if inp.resolve() != run_inp.resolve():
        shutil.copy2(inp, run_inp)
    sidecar_inputs = copy_inp_sidecar_files(inp, inputs_dir)
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
    runner_manifest = parse_runner_manifest(result.stdout)
    runner_files = (
        runner_manifest.get("files")
        if isinstance(runner_manifest.get("files"), dict)
        else {}
    )
    peak, continuity, qa_summary = build_qa_summary(runner_manifest, qa_dir=qa_dir)
    (qa_dir / "runner_peak.json").write_text(json.dumps(peak, indent=2), encoding="utf-8")
    (qa_dir / "runner_continuity.json").write_text(json.dumps(continuity, indent=2), encoding="utf-8")
    (qa_dir / "qa_summary.json").write_text(json.dumps(qa_summary, indent=2), encoding="utf-8")

    builder_manifest = build_builder_manifest(
        source_inp=inp,
        run_inp=run_inp,
        builder_inp=builder_inp,
        sidecar_inputs=sidecar_inputs,
        source_type=source_type,
    )
    (builder_dir / "manifest.json").write_text(json.dumps(builder_manifest, indent=2), encoding="utf-8")

    top_manifest = build_top_manifest(
        source_inp=inp,
        run_inp=run_inp,
        builder_inp=builder_inp,
        sidecar_inputs=sidecar_inputs,
        source_type=source_type,
        runner_manifest=runner_manifest,
        runner_files=runner_files,
        runner_dir=runner_dir,
        qa_dir=qa_dir,
        run_dir=run_dir,
        command_trace=result.as_trace(),
    )
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
    # ``aiswmm run > result.json`` yields a clean, parseable document. The one
    # human-readable chrome line (the run directory) goes to ``stderr`` and is
    # suppressed by ``--quiet``. The old "standard layout: ..." boilerplate was
    # dropped — it never changed and the manifest already lists the real paths.
    print(result.stdout.strip())
    if not getattr(args, "quiet", False):
        print(f"run directory: {run_dir}", file=sys.stderr)

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
