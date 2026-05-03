#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(out):
        raise ValueError(f"{field} must be a finite number")
    return out


def normalize_type(spec: dict[str, Any]) -> str:
    raw = str(spec.get("type", spec.get("membership", ""))).strip().lower()
    aliases = {
        "fixed": "crisp",
        "constant": "crisp",
        "tri": "triangular",
        "triangle": "triangular",
        "trap": "trapezoidal",
        "trapezoid": "trapezoidal",
    }
    return aliases.get(raw, raw)


def read_baseline_values(inp_path: Path, patch_map: dict[str, Any]) -> dict[str, float]:
    lines = inp_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    baselines: dict[str, float] = {}
    current_section: str | None = None

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped.upper()
            continue
        if stripped.startswith(";"):
            continue

        code = raw.split(";", 1)[0]
        tokens = code.split()
        if not tokens:
            continue

        for name, spec in patch_map.items():
            if name in baselines:
                continue
            if current_section != str(spec["section"]).upper():
                continue
            if tokens[0] != str(spec["object"]):
                continue
            idx = int(spec["field_index"])
            if idx >= len(tokens):
                raise IndexError(f"Field index {idx} out of range for {name} on line: {raw}")
            baselines[name] = finite_float(tokens[idx], field=f"baseline for {name}")

    missing = sorted(set(patch_map) - set(baselines))
    if missing:
        raise KeyError(f"Could not resolve baseline value(s) from INP: {missing}")
    return baselines


@dataclass(frozen=True)
class ResolvedFuzzyParameter:
    name: str
    kind: str
    lower: float
    upper: float
    baseline: float | None = None
    core_lower: float | None = None
    core_upper: float | None = None
    precision: int | None = None
    value_type: str = "float"
    source: str | None = None

    def alpha_interval(self, alpha: float) -> tuple[float, float]:
        if alpha < 0.0 or alpha > 1.0:
            raise ValueError(f"alpha must be within [0, 1], got {alpha}")

        if self.kind == "crisp":
            value = self.baseline if self.baseline is not None else self.lower
            return self._format(value), self._format(value)
        if self.kind == "interval":
            return self._format(self.lower), self._format(self.upper)
        if self.kind == "triangular":
            if self.baseline is None:
                raise ValueError(f"Triangular parameter '{self.name}' is missing baseline")
            lo = self.lower + alpha * (self.baseline - self.lower)
            hi = self.upper - alpha * (self.upper - self.baseline)
            return self._format(lo), self._format(hi)
        if self.kind == "trapezoidal":
            if self.core_lower is None or self.core_upper is None:
                raise ValueError(f"Trapezoidal parameter '{self.name}' is missing core bounds")
            lo = self.lower + alpha * (self.core_lower - self.lower)
            hi = self.upper - alpha * (self.upper - self.core_upper)
            return self._format(lo), self._format(hi)

        raise ValueError(f"Unsupported fuzzy parameter kind: {self.kind}")

    def _format(self, value: float) -> float | int:
        if self.value_type == "int":
            return int(round(value))
        if self.precision is not None:
            return round(float(value), self.precision)
        return float(value)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.kind,
            "lower": self.lower,
            "upper": self.upper,
            "baseline": self.baseline,
            "core_lower": self.core_lower,
            "core_upper": self.core_upper,
            "value_type": self.value_type,
            "precision": self.precision,
        }
        if self.source:
            out["source"] = self.source
        return out


def resolve_baseline(name: str, spec: dict[str, Any], baseline_values: dict[str, float]) -> float | None:
    raw = spec.get("baseline", "from_model")
    if raw in {None, "none"}:
        return None
    if raw == "from_model":
        if name not in baseline_values:
            raise KeyError(f"Parameter '{name}' was not found in the patch map / base INP baseline values")
        return baseline_values[name]
    return finite_float(raw, field=f"{name}.baseline")


def parse_value_type(name: str, spec: dict[str, Any]) -> tuple[str, int | None]:
    raw_type = str(spec.get("value_type", spec.get("type_hint", "float"))).strip().lower()
    if raw_type in {"int", "integer"}:
        return "int", None
    if raw_type not in {"float", "number"}:
        raise ValueError(f"{name}.value_type must be 'float' or 'int'")
    precision = spec.get("precision")
    if precision is None:
        return "float", None
    precision_int = int(precision)
    if precision_int < 0:
        raise ValueError(f"{name}.precision must be >= 0")
    return "float", precision_int


def _require_order(name: str, values: list[tuple[str, float]]) -> None:
    for (left_name, left), (right_name, right) in zip(values, values[1:]):
        if left > right:
            raise ValueError(f"{name} requires {left_name} <= {right_name}; got {left} > {right}")


def resolve_parameter(name: str, spec: dict[str, Any], baseline_values: dict[str, float]) -> ResolvedFuzzyParameter:
    kind = normalize_type(spec)
    value_type, precision = parse_value_type(name, spec)
    source = spec.get("source")

    if kind == "crisp":
        baseline = resolve_baseline(name, spec, baseline_values)
        if baseline is None:
            baseline = finite_float(spec.get("value"), field=f"{name}.value")
        return ResolvedFuzzyParameter(
            name=name,
            kind=kind,
            lower=baseline,
            upper=baseline,
            baseline=baseline,
            precision=precision,
            value_type=value_type,
            source=source,
        )

    lower = finite_float(spec.get("lower", spec.get("a")), field=f"{name}.lower")
    upper = finite_float(spec.get("upper", spec.get("c" if kind == "triangular" else "d")), field=f"{name}.upper")
    if lower > upper:
        raise ValueError(f"{name}.lower must be <= upper")

    if kind == "interval":
        return ResolvedFuzzyParameter(
            name=name,
            kind=kind,
            lower=lower,
            upper=upper,
            precision=precision,
            value_type=value_type,
            source=source,
        )

    if kind == "triangular":
        baseline = resolve_baseline(name, spec, baseline_values)
        if baseline is None:
            baseline = finite_float(spec.get("mode", spec.get("b")), field=f"{name}.mode")
        _require_order(name, [("lower", lower), ("baseline", baseline), ("upper", upper)])
        return ResolvedFuzzyParameter(
            name=name,
            kind=kind,
            lower=lower,
            upper=upper,
            baseline=baseline,
            precision=precision,
            value_type=value_type,
            source=source,
        )

    if kind == "trapezoidal":
        baseline = resolve_baseline(name, spec, baseline_values)
        if "core_lower" in spec or "core_upper" in spec or "b" in spec:
            core_lower = finite_float(spec.get("core_lower", spec.get("b")), field=f"{name}.core_lower")
            core_upper = finite_float(spec.get("core_upper", spec.get("c")), field=f"{name}.core_upper")
        elif "core_width" in spec:
            if baseline is None:
                raise ValueError(f"{name}.core_width requires a baseline")
            width = finite_float(spec["core_width"], field=f"{name}.core_width")
            if width < 0:
                raise ValueError(f"{name}.core_width must be >= 0")
            core_lower = baseline - width / 2.0
            core_upper = baseline + width / 2.0
        else:
            if baseline is None:
                raise ValueError(f"{name} trapezoidal spec requires core bounds or core_width")
            core_lower = baseline
            core_upper = baseline

        _require_order(
            name,
            [("lower", lower), ("core_lower", core_lower), ("core_upper", core_upper), ("upper", upper)],
        )
        return ResolvedFuzzyParameter(
            name=name,
            kind=kind,
            lower=lower,
            upper=upper,
            baseline=baseline,
            core_lower=core_lower,
            core_upper=core_upper,
            precision=precision,
            value_type=value_type,
            source=source,
        )

    raise ValueError(f"Unsupported fuzzy membership type for '{name}': {kind}")


def resolve_fuzzy_space(fuzzy_space: dict[str, Any], baseline_values: dict[str, float]) -> dict[str, ResolvedFuzzyParameter]:
    raw_params = fuzzy_space.get("parameters", fuzzy_space)
    if not isinstance(raw_params, dict) or not raw_params:
        raise ValueError("Fuzzy space must contain a non-empty 'parameters' object")

    out: dict[str, ResolvedFuzzyParameter] = {}
    for name, raw_spec in raw_params.items():
        if not isinstance(raw_spec, dict):
            raise ValueError(f"Fuzzy parameter '{name}' must be an object")
        out[name] = resolve_parameter(name, raw_spec, baseline_values)
    return out


def build_alpha_intervals(
    parameters: dict[str, ResolvedFuzzyParameter],
    alpha_levels: list[float],
) -> dict[str, Any]:
    out: dict[str, Any] = {"alpha_levels": alpha_levels, "parameters": {}}
    for alpha in alpha_levels:
        if alpha < 0.0 or alpha > 1.0:
            raise ValueError(f"alpha level must be within [0, 1], got {alpha}")
    for name, param in parameters.items():
        out["parameters"][name] = {
            "resolved": param.to_dict(),
            "alpha_cuts": [
                {
                    "alpha": alpha,
                    "lower": param.alpha_interval(alpha)[0],
                    "upper": param.alpha_interval(alpha)[1],
                }
                for alpha in alpha_levels
            ],
        }
    return out


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Resolve fuzzy SWMM parameter membership functions into alpha-cut intervals.")
    ap.add_argument("--base-inp", required=True, type=Path)
    ap.add_argument("--patch-map", required=True, type=Path)
    ap.add_argument("--fuzzy-space", required=True, type=Path)
    ap.add_argument("--alpha-levels", default="0,0.25,0.5,0.75,1")
    ap.add_argument("--out-resolved", required=True, type=Path)
    ap.add_argument("--out-alpha-intervals", required=True, type=Path)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    patch_map = load_json(args.patch_map)
    fuzzy_space = load_json(args.fuzzy_space)
    alpha_levels = [float(item.strip()) for item in args.alpha_levels.split(",") if item.strip()]
    baselines = read_baseline_values(args.base_inp, patch_map)
    resolved = resolve_fuzzy_space(fuzzy_space, baselines)
    write_json(args.out_resolved, {"parameters": {name: p.to_dict() for name, p in resolved.items()}})
    write_json(args.out_alpha_intervals, build_alpha_intervals(resolved, alpha_levels))


if __name__ == "__main__":
    main()
