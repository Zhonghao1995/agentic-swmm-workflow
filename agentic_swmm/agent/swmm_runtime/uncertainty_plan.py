"""Plan-only uncertainty sampler (PRD-06 Phase B.4 + B §8).

A modeler asks: "scan how sensitive NSE is to Manning's *n* and the
runoff coefficient." ``plan_uncertainty_run`` returns *what samples
to run* — not any SWMM output. A separate executor (later phase)
consumes the ``samples`` list.

Why split the verb
------------------
- A planning verb is fast (no SWMM binary, no file IO besides the
  INP hash). The agent can call it interactively.
- The executor will be cluster-aware and likely SWMM-version-aware.
  Keeping the plan pure means the same plan replays across machines.

Backends
--------
- ``method="morris"`` — SALib elementary effects sampler.
- ``method="sobol"`` — SALib Saltelli sampler.
- SALib missing? Return an empty :class:`UncertaintyPlan` with
  ``provenance["error"]`` set. The modeler still gets a deterministic
  failure mode, never an ``ImportError`` cascade.

Resource estimation (PRD-06 Phase B §8)
---------------------------------------
After the plan is built, callers can ask :func:`estimate_resources` to
project wall-clock, disk, and (optional) LLM-token costs. The estimate
is conservative by design — the user aborts before launch when the
projection looks unreasonable.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_METHODS = ("morris", "sobol")

# Conservative single-run default when memory has nothing to say. 30 s
# fits a small INP run on commodity hardware; we err on the optimistic
# side so the prompt looks reasonable for first-time users, and rely on
# the parametric_memory median to tighten it once the user has history.
_DEFAULT_BASE_RUN_SECONDS = 30.0
# .rpt + .out scaling. Real numbers from a few hundred small Saanich
# runs landed at ~250 KB .rpt and ~500 KB .out — round up to a
# kilobyte-aligned conservative estimate.
_DEFAULT_RPT_BYTES = 250 * 1024
_DEFAULT_OUT_BYTES = 500 * 1024
_DEFAULT_EXTRA_BYTES = 50 * 1024


@dataclass
class UncertaintyPlan:
    """Result of :func:`plan_uncertainty_run`.

    ``samples`` is a list of ``{parameter_name: float}`` dicts — one
    per simulation the executor must run. ``n_samples_actual`` may
    exceed ``n_samples_requested`` because SALib rounds up to honour
    its budget formulas (Morris uses ``r * (k + 1)`` trajectories;
    Saltelli uses ``N * (2k + 2)`` with second-order interactions).

    ``provenance`` records what the modeler typed in plus an INP
    fingerprint so reruns can detect when the base model changed.
    """

    samples: list[dict[str, float]]
    method: str
    n_samples_requested: int
    n_samples_actual: int
    parameter_names: list[str]
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": [dict(s) for s in self.samples],
            "method": self.method,
            "n_samples_requested": self.n_samples_requested,
            "n_samples_actual": self.n_samples_actual,
            "parameter_names": list(self.parameter_names),
            "provenance": dict(self.provenance),
        }


def plan_uncertainty_run(
    base_inp: Path,
    parameters: dict[str, tuple[float, float]],
    *,
    method: str = "morris",
    n_samples: int = 50,
    seed: int = 0,
) -> UncertaintyPlan:
    """Generate a sample plan for an uncertainty scan over ``base_inp``.

    ``parameters`` maps parameter names to ``(low, high)`` bounds.
    ``method`` selects the SALib sampler; ``n_samples`` is the user-
    facing budget (SALib may round it up). ``seed`` is forwarded so
    plans are reproducible.

    The verb does not execute SWMM; it only builds the sample plan.
    A separate executor will consume :attr:`UncertaintyPlan.samples`.
    """
    base_inp = Path(base_inp)
    if method not in SUPPORTED_METHODS:
        raise ValueError(
            f"method must be one of {SUPPORTED_METHODS}, got {method!r}"
        )
    if not parameters:
        raise ValueError("parameters must be a non-empty mapping")
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")

    parameter_names = list(parameters.keys())
    bounds = [list(parameters[name]) for name in parameter_names]

    # Hash the base INP so a downstream rerun can verify the model
    # behind the plan has not drifted. Tolerant of missing file —
    # planning before the INP is in place is a legitimate workflow.
    base_inp_hash = _hash_inp(base_inp)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

    try:
        salib_version, samples_array = _draw_samples(
            method=method,
            parameter_names=parameter_names,
            bounds=bounds,
            n_samples=n_samples,
            seed=seed,
        )
    except _SalibMissingError as exc:
        return UncertaintyPlan(
            samples=[],
            method=method,
            n_samples_requested=n_samples,
            n_samples_actual=0,
            parameter_names=parameter_names,
            provenance={
                "method": method,
                "n_samples_requested": n_samples,
                "seed": seed,
                "base_inp_hash": base_inp_hash,
                "salib_version": None,
                "timestamp": timestamp,
                "error": str(exc),
            },
        )

    samples: list[dict[str, float]] = [
        {name: float(row[idx]) for idx, name in enumerate(parameter_names)}
        for row in samples_array
    ]

    return UncertaintyPlan(
        samples=samples,
        method=method,
        n_samples_requested=n_samples,
        n_samples_actual=len(samples),
        parameter_names=parameter_names,
        provenance={
            "method": method,
            "n_samples_requested": n_samples,
            "seed": seed,
            "base_inp_hash": base_inp_hash,
            "salib_version": salib_version,
            "timestamp": timestamp,
        },
    )


def _hash_inp(base_inp: Path) -> str:
    """Return ``sha256:<hex>`` for ``base_inp``, or ``"missing"`` on absence.

    Files larger than the typical INP (a few MB) still fit in memory
    on any developer laptop, so we hash in one shot rather than
    streaming for now.
    """
    if not base_inp.is_file():
        return "missing"
    try:
        content = base_inp.read_bytes()
    except OSError:
        return "unreadable"
    return "sha256:" + hashlib.sha256(content).hexdigest()


class _SalibMissingError(RuntimeError):
    """Raised when SALib (or one of its sub-modules) cannot be imported.

    Private to this module — callers get a populated
    :class:`UncertaintyPlan` with ``provenance["error"]`` instead.
    """


def _draw_samples(
    *,
    method: str,
    parameter_names: list[str],
    bounds: list[list[float]],
    n_samples: int,
    seed: int,
):
    """Call SALib's sampler. Returns ``(version, ndarray-like)``.

    Raises :class:`_SalibMissingError` if SALib (or a required
    sub-module) is not importable.
    """
    try:
        import SALib  # noqa: F401
    except ImportError as exc:
        raise _SalibMissingError(f"SALib not importable: {exc}") from exc

    problem = {
        "num_vars": len(parameter_names),
        "names": list(parameter_names),
        "bounds": [list(b) for b in bounds],
    }
    salib_version = getattr(__import__("SALib"), "__version__", "unknown")

    if method == "morris":
        try:
            from SALib.sample import morris as morris_sampler
        except ImportError as exc:
            raise _SalibMissingError(
                f"SALib.sample.morris not importable: {exc}"
            ) from exc
        # ``N`` in SALib Morris is the trajectory count; total
        # evaluations = N * (k + 1). We treat the user-facing
        # ``n_samples`` as the trajectory budget so the request
        # corresponds to k+1 sims per unit.
        samples = morris_sampler.sample(problem, N=n_samples, seed=seed)
        return salib_version, samples

    if method == "sobol":
        try:
            from SALib.sample import sobol as sobol_sampler
        except ImportError as exc:
            raise _SalibMissingError(
                f"SALib.sample.sobol not importable: {exc}"
            ) from exc
        samples = sobol_sampler.sample(
            problem, N=n_samples, calc_second_order=True, seed=seed
        )
        return salib_version, samples

    # Unreachable — method is validated by the caller.
    raise _SalibMissingError(f"unsupported method: {method}")


# ---------------------------------------------------------------------------
# Resource estimation (PRD-06 Phase B §8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceEstimate:
    """Projected cost of executing an :class:`UncertaintyPlan`.

    All numbers are *projections*; the actual executor may diverge if a
    SWMM run hangs or the cluster is shared. The estimate exists so the
    user can abort before paying for an expensive scan.

    ``base_run_seconds_source`` carries the provenance of the per-run
    duration we projected from:

    - ``"user_supplied"`` — caller passed ``base_run_seconds=...``
    - ``"parametric_memory_median"`` — median ``wall_time_s`` from
      ``parametric_memory.jsonl`` rows matching ``case_name``
    - ``"conservative_default"`` — no history available; we used the
      conservative built-in default
    """

    n_runs_estimated: int
    base_run_seconds_estimated: float
    base_run_seconds_source: str
    wall_clock_seconds_estimated: float
    disk_bytes_estimated: int
    llm_tokens_estimated: int
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_runs_estimated": int(self.n_runs_estimated),
            "base_run_seconds_estimated": float(self.base_run_seconds_estimated),
            "base_run_seconds_source": self.base_run_seconds_source,
            "wall_clock_seconds_estimated": float(self.wall_clock_seconds_estimated),
            "disk_bytes_estimated": int(self.disk_bytes_estimated),
            "llm_tokens_estimated": int(self.llm_tokens_estimated),
            "assumptions": list(self.assumptions),
        }


def _read_wall_time_seconds(parametric_store: Path, case_name: str) -> list[float]:
    """Return ``wall_time_s`` values for ``case_name`` from the JSONL store.

    Missing file, missing key, or non-numeric values are silently
    skipped — this is best-effort historical lookup, not a hard
    validation. Empty list means "no history; fall back to the
    conservative default".
    """
    if not parametric_store.is_file():
        return []
    out: list[float] = []
    try:
        for raw in parametric_store.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("case_name") != case_name:
                continue
            # parametric_memory carries wall_time_s under qa_metrics or
            # performance_metrics depending on writer; tolerate both.
            for container_name in ("qa_metrics", "performance_metrics"):
                container = row.get(container_name) or {}
                value = container.get("wall_time_s")
                if value is not None:
                    try:
                        out.append(float(value))
                    except (TypeError, ValueError):
                        pass
            # Some writers stamp it at the top level. Tolerate that too.
            top = row.get("wall_time_s")
            if top is not None:
                try:
                    out.append(float(top))
                except (TypeError, ValueError):
                    pass
    except OSError:
        return []
    return out


def estimate_resources(
    plan: UncertaintyPlan,
    *,
    base_run_seconds: float | None = None,
    parametric_store: Path | None = None,
    case_name: str | None = None,
    llm_in_loop: bool = False,
    avg_llm_tokens_per_run: int = 0,
) -> ResourceEstimate:
    """Project the wall-clock, disk, and token cost of executing ``plan``.

    ``base_run_seconds`` precedence:

    1. Caller-supplied ``base_run_seconds`` (highest trust).
    2. Median ``wall_time_s`` from ``parametric_memory.jsonl`` rows
       matching ``case_name`` (when both are passed and at least one
       row carries the field).
    3. Conservative built-in default (lowest trust).

    The returned :class:`ResourceEstimate` always carries a non-empty
    ``assumptions`` list so the user can read what we assumed.
    """
    assumptions: list[str] = []

    # ----- per-run duration ------------------------------------------------
    if base_run_seconds is not None:
        if base_run_seconds <= 0:
            raise ValueError("base_run_seconds must be positive")
        per_run = float(base_run_seconds)
        per_run_source = "user_supplied"
        assumptions.append(
            f"per-run wall time {per_run:.1f}s from caller --base-run-seconds"
        )
    elif parametric_store is not None and case_name:
        history = _read_wall_time_seconds(Path(parametric_store), case_name)
        if history:
            per_run = float(statistics.median(history))
            per_run_source = "parametric_memory_median"
            assumptions.append(
                f"per-run wall time {per_run:.1f}s from parametric_memory "
                f"median over {len(history)} rows for case={case_name!r}"
            )
        else:
            per_run = _DEFAULT_BASE_RUN_SECONDS
            per_run_source = "conservative_default"
            assumptions.append(
                f"per-run wall time {per_run:.1f}s from conservative default "
                f"(no parametric history for case={case_name!r})"
            )
    else:
        per_run = _DEFAULT_BASE_RUN_SECONDS
        per_run_source = "conservative_default"
        assumptions.append(
            f"per-run wall time {per_run:.1f}s from conservative default "
            "(no case_name / parametric store supplied)"
        )

    # ----- run count -------------------------------------------------------
    n_runs = int(plan.n_samples_actual or 0)
    assumptions.append(
        f"executor performs {n_runs} runs (one per sample in the plan)"
    )

    # ----- wall clock + disk + tokens -------------------------------------
    wall_clock = float(n_runs * per_run)
    assumptions.append(
        f"single-threaded execution: {n_runs} x {per_run:.1f}s = {wall_clock:.0f}s "
        f"({wall_clock / 60.0:.1f} min)"
    )

    per_run_bytes = _DEFAULT_RPT_BYTES + _DEFAULT_OUT_BYTES + _DEFAULT_EXTRA_BYTES
    disk_bytes = int(n_runs * per_run_bytes)
    assumptions.append(
        f"per-run on-disk footprint ~{per_run_bytes // 1024} KB "
        "(.rpt + .out + extras); total ~"
        f"{disk_bytes / (1024 * 1024):.1f} MB"
    )

    if llm_in_loop:
        if avg_llm_tokens_per_run < 0:
            raise ValueError("avg_llm_tokens_per_run must be >= 0")
        llm_tokens = int(n_runs * avg_llm_tokens_per_run)
        assumptions.append(
            f"LLM-in-loop enabled: {n_runs} x {avg_llm_tokens_per_run} "
            f"= {llm_tokens} tokens"
        )
    else:
        llm_tokens = 0
        assumptions.append("LLM-in-loop disabled (llm_tokens_estimated = 0)")

    return ResourceEstimate(
        n_runs_estimated=n_runs,
        base_run_seconds_estimated=per_run,
        base_run_seconds_source=per_run_source,
        wall_clock_seconds_estimated=wall_clock,
        disk_bytes_estimated=disk_bytes,
        llm_tokens_estimated=llm_tokens,
        assumptions=assumptions,
    )


def format_estimate_block(estimate: ResourceEstimate) -> str:
    """Render a one-block human-readable summary of ``estimate``.

    The block is multi-line and includes every assumption — printed
    above the y/N prompt so the user has the context to decide.
    """
    lines = [
        "Resource estimate",
        "-----------------",
        f"  n_runs                   : {estimate.n_runs_estimated}",
        f"  base_run_seconds         : {estimate.base_run_seconds_estimated:.1f} "
        f"({estimate.base_run_seconds_source})",
        f"  wall_clock_seconds       : "
        f"{estimate.wall_clock_seconds_estimated:.0f} "
        f"({estimate.wall_clock_seconds_estimated / 60.0:.1f} min)",
        f"  disk_bytes               : "
        f"{estimate.disk_bytes_estimated} "
        f"(~{estimate.disk_bytes_estimated / (1024 * 1024):.1f} MB)",
        f"  llm_tokens               : {estimate.llm_tokens_estimated}",
        "Assumptions:",
    ]
    for a in estimate.assumptions:
        lines.append(f"  - {a}")
    return "\n".join(lines)
