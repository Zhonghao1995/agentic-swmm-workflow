#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def _format_value(value: float, resolved: dict[str, Any] | None, template: Any) -> float | int:
    resolved = resolved or {}
    value_type = str(resolved.get("value_type", "")).lower()
    if value_type in {"int", "integer"} or (not value_type and isinstance(template, int) and not isinstance(template, bool)):
        return int(round(value))
    precision = resolved.get("precision")
    if precision is not None:
        return round(float(value), int(precision))
    return float(value)


def sample_lhs(
    intervals: dict[str, tuple[Any, Any]],
    resolved: dict[str, dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[dict[str, float | int]]:
    if count <= 0:
        raise ValueError("samples_per_alpha must be >= 1")

    unit_vectors: dict[str, list[float]] = {}
    for name in intervals:
        vals = [(i + rng.random()) / count for i in range(count)]
        rng.shuffle(vals)
        unit_vectors[name] = vals

    samples: list[dict[str, float | int]] = []
    for sample_idx in range(count):
        params: dict[str, float | int] = {}
        for name, (lo, hi) in intervals.items():
            value = float(lo) + (float(hi) - float(lo)) * unit_vectors[name][sample_idx]
            params[name] = _format_value(value, resolved.get(name), lo)
        samples.append(params)
    return samples


def sample_random(
    intervals: dict[str, tuple[Any, Any]],
    resolved: dict[str, dict[str, Any]],
    count: int,
    rng: random.Random,
) -> list[dict[str, float | int]]:
    if count <= 0:
        raise ValueError("samples_per_alpha must be >= 1")
    samples: list[dict[str, float | int]] = []
    for _ in range(count):
        params: dict[str, float | int] = {}
        for name, (lo, hi) in intervals.items():
            value = rng.uniform(float(lo), float(hi))
            params[name] = _format_value(value, resolved.get(name), lo)
        samples.append(params)
    return samples


def sample_boundary(intervals: dict[str, tuple[Any, Any]]) -> list[dict[str, float | int]]:
    names = list(intervals)
    choices = [[intervals[name][0], intervals[name][1]] for name in names]
    samples: list[dict[str, float | int]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()
    for values in itertools.product(*choices):
        sample = {name: value for name, value in zip(names, values)}
        key = tuple(sorted(sample.items()))
        if key in seen:
            continue
        seen.add(key)
        samples.append(sample)
    return samples


def intervals_for_alpha(alpha_intervals: dict[str, Any], alpha: float) -> dict[str, tuple[Any, Any]]:
    intervals: dict[str, tuple[Any, Any]] = {}
    for name, record in alpha_intervals["parameters"].items():
        cuts = record.get("alpha_cuts") or []
        matching = [cut for cut in cuts if abs(float(cut["alpha"]) - alpha) < 1e-12]
        if not matching:
            raise KeyError(f"No alpha-cut for parameter '{name}' at alpha={alpha}")
        cut = matching[0]
        intervals[name] = (cut["lower"], cut["upper"])
    return intervals


def resolved_specs(alpha_intervals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: record.get("resolved") or {}
        for name, record in alpha_intervals["parameters"].items()
    }


def generate_parameter_sets(
    alpha_intervals: dict[str, Any],
    *,
    method: str,
    samples_per_alpha: int,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    method = method.lower().strip()
    alpha_levels = [float(alpha) for alpha in alpha_intervals["alpha_levels"]]
    resolved = resolved_specs(alpha_intervals)
    trials: list[dict[str, Any]] = []
    trial_idx = 1

    for alpha in alpha_levels:
        intervals = intervals_for_alpha(alpha_intervals, alpha)
        if all(float(lo) == float(hi) for lo, hi in intervals.values()):
            samples = [{name: lo for name, (lo, _hi) in intervals.items()}]
        elif method == "lhs":
            samples = sample_lhs(intervals, resolved, samples_per_alpha, rng)
        elif method == "random":
            samples = sample_random(intervals, resolved, samples_per_alpha, rng)
        elif method == "boundary":
            samples = sample_boundary(intervals)
        else:
            raise ValueError(f"Unsupported sampling method: {method}")

        for local_idx, params in enumerate(samples, start=1):
            trials.append(
                {
                    "name": f"alpha_{alpha:.2f}_trial_{local_idx:03d}",
                    "params": params,
                    "metadata": {
                        "alpha": alpha,
                        "sample_index": local_idx,
                        "global_sample_index": trial_idx,
                        "sampling_method": method,
                    },
                }
            )
            trial_idx += 1

    return trials


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate parameter sets from fuzzy alpha-cut intervals.")
    ap.add_argument("--alpha-intervals", required=True, type=Path)
    ap.add_argument("--method", default="lhs", choices=["lhs", "random", "boundary"])
    ap.add_argument("--samples-per-alpha", default=20, type=int)
    ap.add_argument("--seed", default=42, type=int)
    ap.add_argument("--out", required=True, type=Path)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    alpha_intervals = load_json(args.alpha_intervals)
    trials = generate_parameter_sets(
        alpha_intervals,
        method=args.method,
        samples_per_alpha=args.samples_per_alpha,
        seed=args.seed,
    )
    write_json(args.out, {"parameter_sets": trials})


if __name__ == "__main__":
    main()
