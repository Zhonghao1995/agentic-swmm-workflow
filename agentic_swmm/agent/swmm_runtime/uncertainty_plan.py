"""Plan-only uncertainty sampler (PRD-06 Phase B.4).

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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_METHODS = ("morris", "sobol")


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
