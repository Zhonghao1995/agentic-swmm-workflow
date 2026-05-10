from __future__ import annotations

import argparse
import os
import time

from agentic_swmm.utils.paths import script_path
from agentic_swmm.utils.subprocess_runner import python_command, run_command


DEMO_SCRIPTS = {
    "acceptance": ("scripts", "acceptance", "run_acceptance.py"),
    "tecnopolo": ("scripts", "benchmarks", "run_tecnopolo_199401.py"),
    "tuflow-raw": ("scripts", "benchmarks", "run_tuflow_swmm_module03_raw_path.py"),
}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("demo", help="Run a prepared demo workflow.")
    parser.add_argument("name", choices=sorted(DEMO_SCRIPTS), help="Demo workflow to run.")
    parser.add_argument("--run-id", help="Run id for demos that support it. Defaults to a timestamped id.")
    parser.add_argument("--keep-existing", action="store_true", help="Keep an existing acceptance run directory.")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    script = script_path(*DEMO_SCRIPTS[args.name])
    command = python_command(script)
    if args.name == "acceptance":
        run_id = args.run_id or os.environ.get("AGENTIC_SWMM_RUN_ID") or f"demo-{int(time.time())}"
        command.extend(["--run-id", run_id])
        if args.keep_existing:
            command.append("--keep-existing")
    result = run_command(command)
    print(result.stdout.strip())
    return result.return_code
