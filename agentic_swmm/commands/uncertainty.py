"""``aiswmm uncertainty <subcommand>`` (issue #55).

Currently exposes a single subcommand::

    aiswmm uncertainty source <run_dir>

which is the paper-reviewer-facing integration layer over the prior
uncertainty slices (Sobol' / Morris / DREAM-ZS / SCE-UA / rainfall
ensemble / MC propagation). It is a thin wrapper over the pure module
``skills/swmm-uncertainty/scripts/source_decomposition.py`` so callers
can regenerate ``uncertainty_source_summary.md`` and
``uncertainty_source_decomposition.json`` on demand.

Exit policy (per the issue acceptance criteria):

* exit 0 on a complete run (every method ran)
* exit 0 with a stderr warning on a partial run (at least one method
  ran, at least one did not)
* exit 1 if no uncertainty raw outputs are present at all, or the
  ``run_dir`` does not exist.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DECOMP_PY = (
    REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts" / "source_decomposition.py"
)


def _load_source_decomposition():
    """Load ``source_decomposition.py`` from the skill path by file.

    Skills are user-facing scaffolds that live outside the
    ``agentic_swmm`` package, so we import the module by file path.
    The result is cached in ``sys.modules`` so repeated invocations in
    a single process pay only one import cost.
    """
    cache_key = "_uncertainty_source_decomposition_module"
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    spec = importlib.util.spec_from_file_location(cache_key, SOURCE_DECOMP_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {SOURCE_DECOMP_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = module
    spec.loader.exec_module(module)
    return module


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "uncertainty",
        help=(
            "Integrated uncertainty source decomposition (issue #55). "
            "Subcommand 'source' regenerates uncertainty_source_summary.md "
            "+ uncertainty_source_decomposition.json from the raw outputs "
            "in <run_dir>/09_audit/."
        ),
    )
    inner = parser.add_subparsers(dest="uncertainty_command", required=True)
    source_parser = inner.add_parser(
        "source",
        help=(
            "Rebuild uncertainty_source_summary.md + "
            "uncertainty_source_decomposition.json for <run_dir>."
        ),
    )
    source_parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to the run directory.",
    )
    source_parser.set_defaults(func=source_main)


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _print_warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def source_main(args: argparse.Namespace) -> int:
    """Run ``decompose`` against ``args.run_dir`` with the issue exit policy.

    The exit policy is:
    - run_dir missing               -> 1
    - 0 of 6 evidence slots present -> 1
    - at least one slot missing     -> 0 + stderr warning
    - all 6 slots present           -> 0
    """
    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        _print_error(f"run_dir is not a directory: {run_dir}")
        return 1

    module = _load_source_decomposition()
    result = module.decompose(run_dir=run_dir)
    payload = result.payload
    evidence = payload.get("evidence_boundary") or {}
    methods_present = result.methods_present
    methods_absent = result.methods_absent

    if not methods_present:
        # Zero raw outputs of any kind -> the report would have nothing
        # to say. Still emit the empty report on disk (so downstream
        # tooling does not crash on a missing file), but signal the
        # incomplete state via a non-zero exit.
        _print_error(
            f"no uncertainty raw outputs found in {run_dir / '09_audit'}; "
            "expected at least one of sensitivity_indices.json, "
            "posterior_samples.csv, rainfall_ensemble_summary.json, "
            "candidate_calibration.json, or uncertainty_summary.json."
        )
        return 1

    summary: dict[str, Any] = {
        "ok": True,
        "schema_version": payload.get("schema_version"),
        "run_id": payload.get("run_id"),
        "markdown_path": str(result.markdown_path),
        "json_path": str(result.json_path),
        "methods_present": methods_present,
        "methods_absent": methods_absent,
    }
    print(json.dumps(summary, indent=2))

    if methods_absent:
        _print_warning(
            "partial uncertainty run; the following methods were not run: "
            + ", ".join(methods_absent)
        )

    return 0
