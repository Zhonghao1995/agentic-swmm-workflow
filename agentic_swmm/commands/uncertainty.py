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
            "Uncertainty verbs. 'source' rebuilds the source decomposition "
            "for an existing run (issue #55); 'plan' produces a sample plan "
            "for a parameter scan without running SWMM (PRD-06 B.4)."
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

    plan_parser = inner.add_parser(
        "plan",
        help=(
            "Plan an uncertainty scan over a parameter set. Returns a "
            "sample list; does NOT execute SWMM."
        ),
    )
    plan_parser.add_argument(
        "--base-inp",
        type=Path,
        required=True,
        help="Base INP whose hash gets stamped into the plan provenance.",
    )
    plan_parser.add_argument(
        "--param",
        action="append",
        required=True,
        metavar="NAME=LOW,HIGH",
        help=(
            "Parameter to perturb with its [low,high] bounds. Repeatable. "
            "Example: --param manning_n=0.01,0.03"
        ),
    )
    plan_parser.add_argument(
        "--method",
        choices=("morris", "sobol"),
        default="morris",
        help="SALib sampler to use (default morris).",
    )
    plan_parser.add_argument(
        "--n-samples",
        type=int,
        default=50,
        help="Sample budget (SALib may round up) (default 50).",
    )
    plan_parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed forwarded to the sampler for reproducibility (default 0).",
    )
    plan_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Write the plan JSON here. When omitted, the plan is printed "
            "to stdout so a shell redirect still works."
        ),
    )
    # PRD-06 Phase B §8 — ResourceEstimate gating.
    plan_parser.add_argument(
        "--no-estimate",
        action="store_true",
        help="Skip the resource estimate (legacy behavior).",
    )
    plan_parser.add_argument(
        "--abort-on-estimate",
        action="store_true",
        help=(
            "Print the resource estimate and exit 0 without launching. "
            "Useful for budget review and dry-run workflows."
        ),
    )
    plan_parser.add_argument(
        "--llm-in-loop",
        action="store_true",
        help="Include LLM token cost in the resource estimate.",
    )
    plan_parser.add_argument(
        "--avg-llm-tokens-per-run",
        type=int,
        default=0,
        help=(
            "Avg LLM tokens per run, used when --llm-in-loop is set "
            "(default 0)."
        ),
    )
    plan_parser.add_argument(
        "--base-run-seconds",
        type=float,
        default=None,
        help=(
            "Manual per-run wall-time override (highest precedence; "
            "skips parametric_memory lookup)."
        ),
    )
    plan_parser.add_argument(
        "--case-name",
        default=None,
        help=(
            "Case name used to look up parametric_memory median for "
            "per-run estimate."
        ),
    )
    plan_parser.add_argument(
        "--parametric-store",
        type=Path,
        default=None,
        help="Optional path to parametric_memory.jsonl (case_name lookup).",
    )
    plan_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the launch prompt (non-interactive callers / CI).",
    )
    plan_parser.set_defaults(func=plan_main)


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


def _parse_param_specs(specs: list[str]) -> dict[str, tuple[float, float]]:
    """Parse ``NAME=LOW,HIGH`` strings into ``{name: (low, high)}``.

    Raises :class:`ValueError` with a human-readable message on a
    malformed spec so the CLI shows a clean error rather than a stack
    trace.
    """
    parameters: dict[str, tuple[float, float]] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(
                f"--param spec must be NAME=LOW,HIGH; got {spec!r}"
            )
        name, bounds_text = spec.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"--param spec has empty NAME: {spec!r}")
        if "," not in bounds_text:
            raise ValueError(
                f"--param bounds must be LOW,HIGH; got {bounds_text!r}"
            )
        low_text, high_text = bounds_text.split(",", 1)
        try:
            low = float(low_text)
            high = float(high_text)
        except ValueError as exc:
            raise ValueError(
                f"--param {name}: bounds must be numeric ({exc})"
            ) from exc
        if high < low:
            raise ValueError(
                f"--param {name}: LOW ({low}) must be <= HIGH ({high})"
            )
        parameters[name] = (low, high)
    return parameters


def plan_main(args: argparse.Namespace) -> int:
    """Produce a sample plan and write it as JSON.

    Exit codes:
    - 0 — plan produced (samples may be empty when SALib is missing,
      in which case ``provenance.error`` explains it). Also 0 when the
      user aborts via the prompt — that is a valid no-launch outcome,
      not an error.
    - 1 — malformed ``--param`` spec or other input error.
    """
    from agentic_swmm.agent.swmm_runtime.uncertainty_plan import (
        estimate_resources,
        format_estimate_block,
        plan_uncertainty_run,
    )

    try:
        parameters = _parse_param_specs(args.param)
    except ValueError as exc:
        _print_error(str(exc))
        return 1

    try:
        plan = plan_uncertainty_run(
            base_inp=args.base_inp,
            parameters=parameters,
            method=args.method,
            n_samples=args.n_samples,
            seed=args.seed,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return 1

    payload = plan.to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)

    # ----- Resource estimate (PRD-06 Phase B §8) -----------------------------
    # The estimate runs by default. ``--no-estimate`` restores legacy
    # behavior (print the plan, exit). ``--abort-on-estimate`` prints
    # the estimate and exits before any launch action. Otherwise:
    # - TTY -> prompt y/N (default N).
    # - non-TTY -> print estimate to stdout, exit 0 (machine consumers
    #   parse the JSON and decide).
    if not args.no_estimate:
        try:
            estimate = estimate_resources(
                plan,
                base_run_seconds=args.base_run_seconds,
                parametric_store=args.parametric_store,
                case_name=args.case_name,
                llm_in_loop=args.llm_in_loop,
                avg_llm_tokens_per_run=args.avg_llm_tokens_per_run,
            )
        except ValueError as exc:
            _print_error(str(exc))
            return 1

        print(format_estimate_block(estimate))
        payload["resource_estimate"] = estimate.to_dict()
        text = json.dumps(payload, indent=2, sort_keys=True)

        if args.abort_on_estimate:
            return 0

        if not args.yes and _stdin_is_tty():
            answer = _prompt_proceed()
            if not answer:
                print("aborted by user; plan not written.")
                return 0
        elif not args.yes:
            # Non-TTY (CI, pipe) — print estimate already done.
            # The machine consumer parses the JSON below; we keep
            # writing the plan to --out if asked.
            pass

    if args.out is None:
        print(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            f"wrote {plan.n_samples_actual}-sample plan to {args.out}"
        )
    return 0


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _prompt_proceed() -> bool:
    """Render the y/N prompt with safe default N (no launch)."""
    try:
        reply = input("Proceed with launch? [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}
