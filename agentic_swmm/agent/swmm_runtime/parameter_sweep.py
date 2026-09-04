"""Reference-free propagation of parameter ranges through a SWMM model.

User decision 2026-09-02 (live finding F-55): "how uncertain is the peak
if Manning's n and imperviousness vary" had no typed tool. The three
sensitivity tools need an observed series, and the planner ended up
writing its own runner script. This module is the typed answer: a global
sweep of named parameters over user-given ranges, one SWMM run per
sample, and an honest spread of the peak at the report node.

Scope and honesty:

* Parameters are applied GLOBALLY, the same value to every subcatchment
  (or conduit). That is what a first-pass "vary n and imperviousness"
  question means; per-object spaces belong to the fuzzy/Monte Carlo
  scripts of the swmm-uncertainty skill.
* Sampling is a full factorial when the grid is small, otherwise a seeded
  Latin hypercube; every sample is recorded with its run status, and a
  failed run is carried, never dropped.
* The result is prior sensitivity, not calibrated uncertainty; the summary
  says so.
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.swmm_runtime.climate_scenarios import (
    RunnerFn,
    ScenarioSpec,
    _default_runner,
    write_scenario_inp,
)

#: Parameter name -> (INP section, field index, kind). Field indices follow
#: the SWMM 5 column order after the object name is field 0.
GLOBAL_PARAMETERS: dict[str, tuple[str, int, str]] = {
    "pct_imperv": ("[SUBCATCHMENTS]", 4, "subcatchment percent impervious"),
    "width": ("[SUBCATCHMENTS]", 5, "subcatchment width"),
    "slope": ("[SUBCATCHMENTS]", 6, "subcatchment slope percent"),
    "n_imperv": ("[SUBAREAS]", 1, "Manning's n of impervious area"),
    "n_perv": ("[SUBAREAS]", 2, "Manning's n of pervious area"),
    "s_imperv": ("[SUBAREAS]", 3, "depression storage of impervious area"),
    "s_perv": ("[SUBAREAS]", 4, "depression storage of pervious area"),
    "conduit_n": ("[CONDUITS]", 4, "Manning's n of conduits"),
}

ALIASES: dict[str, str] = {
    "manning_n": "n_imperv",
    "manning": "n_imperv",
    "nimperv": "n_imperv",
    "n-imperv": "n_imperv",
    "imperviousness": "pct_imperv",
    "imperv": "pct_imperv",
    "percent_imperv": "pct_imperv",
    "%imperv": "pct_imperv",
    "roughness": "conduit_n",
    "pipe_n": "conduit_n",
    "conduit_roughness": "conduit_n",
    "dstore_imperv": "s_imperv",
    "dstore_perv": "s_perv",
    "depression_storage_imperv": "s_imperv",
    "depression_storage_perv": "s_perv",
}

MAX_FACTORIAL = 36


def canonical_parameter(name: str) -> str:
    key = str(name).strip().lower().replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in GLOBAL_PARAMETERS:
        known = ", ".join(sorted(GLOBAL_PARAMETERS))
        raise ValueError(f"unknown parameter {name!r}; known: {known}")
    return key


def parse_ranges(raw: Any) -> dict[str, tuple[float, float]]:
    """Accept ``{"n_imperv": [0.01, 0.02]}`` or ``"n_imperv=0.01,0.02;pct_imperv=60,80"``."""
    ranges: dict[str, tuple[float, float]] = {}
    if isinstance(raw, str):
        items: list[tuple[str, Any]] = []
        for chunk in raw.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"range needs name=lo,hi: {chunk!r}")
            name, values = chunk.split("=", 1)
            items.append((name, values.split(",")))
    elif isinstance(raw, dict):
        items = list(raw.items())
    else:
        raise ValueError("ranges must be a mapping or a 'name=lo,hi;...' string")
    for name, values in items:
        if isinstance(values, (int, float)):
            values = [values, values]
        try:
            lo, hi = (float(values[0]), float(values[1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"range for {name!r} must be two numbers") from exc
        if hi < lo:
            lo, hi = hi, lo
        ranges[canonical_parameter(name)] = (lo, hi)
    if not ranges:
        raise ValueError("at least one parameter range is required")
    return ranges


def sample_space(
    ranges: dict[str, tuple[float, float]], n_samples: int | None = None, *, seed: int = 42
) -> list[dict[str, float]]:
    """Full factorial on a small grid, otherwise a seeded Latin hypercube."""
    names = list(ranges)
    if n_samples is None:
        levels = 5 if len(names) <= 2 else 3
        if levels ** len(names) <= MAX_FACTORIAL:
            return _factorial(ranges, levels)
        n_samples = MAX_FACTORIAL
    n_samples = max(2, int(n_samples))
    rng = random.Random(seed)
    columns: dict[str, list[float]] = {}
    for name in names:
        lo, hi = ranges[name]
        slots = list(range(n_samples))
        rng.shuffle(slots)
        columns[name] = [lo + (hi - lo) * ((slot + rng.random()) / n_samples) for slot in slots]
    return [{name: columns[name][i] for name in names} for i in range(n_samples)]


def _factorial(ranges: dict[str, tuple[float, float]], levels: int) -> list[dict[str, float]]:
    grids = {
        name: [lo + (hi - lo) * k / (levels - 1) for k in range(levels)] if hi > lo else [lo]
        for name, (lo, hi) in ranges.items()
    }
    samples: list[dict[str, float]] = [{}]
    for name, values in grids.items():
        samples = [dict(sample, **{name: value}) for sample in samples for value in values]
    return samples


def rewrite_inp(text: str, values: dict[str, float]) -> str:
    """Set each named parameter on EVERY object of its section."""
    targets: dict[str, list[tuple[int, float]]] = {}
    for name, value in values.items():
        section, index, _ = GLOBAL_PARAMETERS[canonical_parameter(name)]
        targets.setdefault(section, []).append((index, float(value)))
    out: list[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.upper()
            out.append(line)
            continue
        if section in targets and stripped and not stripped.startswith(";"):
            body, _, comment = line.partition(";")
            fields = body.split()
            for index, value in targets[section]:
                if len(fields) > index:
                    fields[index] = _format(value)
            line = " ".join(fields) + ((" ;" + comment) if comment else "")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _format(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


@dataclass(frozen=True)
class SweepSample:
    name: str
    values: dict[str, float]
    run_ok: bool
    sample_dir: str
    peak: float | None = None
    flow_units: str | None = None
    error: str = ""
    manifest: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


OAT_DEFAULT_LEVELS = 5
OAT_MAX_LEVELS = 9

AUTO_NODE = "auto"


def resolved_report_node(base_inp: Path, manifest: dict[str, Any]) -> str:
    """The node the runner actually reported on, else the INP's first outfall."""
    from agentic_swmm.agent.swmm_runtime.inp_parsing import default_report_node

    selection = manifest.get("node_selection") if isinstance(manifest, dict) else None
    resolved = selection.get("resolved") if isinstance(selection, dict) else None
    if isinstance(resolved, str) and resolved.strip() and resolved.strip().lower() != AUTO_NODE:
        return resolved.strip()
    peak = (manifest.get("metrics") or {}).get("peak") if isinstance(manifest, dict) else None
    node = peak.get("node") if isinstance(peak, dict) else None
    if isinstance(node, str) and node.strip() and node.strip().lower() != AUTO_NODE:
        return node.strip()
    return default_report_node(base_inp) or "O1"


@dataclass(frozen=True)
class SweepResult:
    ok: bool
    run_dir: str
    node: str
    baseline_peak: float | None
    flow_units: str | None
    samples: tuple[SweepSample, ...]
    summary_json: str
    summary_md: str
    stats: dict[str, Any] = field(default_factory=dict)


def sweep_tag(ranges: Any) -> str:
    """A short file-name tag from the swept parameter names (sorted, joined by +)."""
    try:
        names = sorted(str(k) for k in dict(ranges).keys())
    except Exception:  # noqa: BLE001 - any odd shape gets a neutral tag
        names = []
    tag = "+".join(n.replace("/", "_") for n in names) or "all"
    return tag[:60]


def run_parameter_sweep(
    *,
    base_inp: Path,
    run_dir: Path,
    ranges: dict[str, tuple[float, float]],
    node: str | None = None,
    n_samples: int | None = None,
    seed: int = 42,
    runner: RunnerFn | None = None,
    progress: Callable[[str], None] | None = None,
    mode: str = "joint",
) -> SweepResult:
    """Run the baseline plus one SWMM run per sample; write the spread.

    Artifacts (canonical audit stage, ADR-0004)::

        <run_dir>/09_audit/parameter_sweep/<sample>/model.inp (+ rain copies, rpt, out)
        <run_dir>/09_audit/parameter_sweep.json / parameter_sweep.md
    """
    run = runner or _default_runner
    # Live finding F-72 (2026-09-02): with no node given, the sweep reported
    # the spread at the INP's first outfall (a negligible one) instead of
    # the run's dominant outfall. The baseline now runs with the runner's
    # "auto" node and locks the report node from its manifest; every sample
    # then runs on that node.
    report_node = node or AUTO_NODE
    audit_dir = run_layout.stage_dir(Path(run_dir), run_layout.AUDIT, create=True)
    # Live finding F-108 (2026-09-03, S48): five sweeps in one session wrote
    # the same parameter_sweep.{json,md} and the follow-up read the survivor.
    # Each sweep keeps its own files, named after the parameters it varied.
    # Live finding F-109 (2026-09-03, S48 r2): a reference-free ranking of
    # "which parameters matter most" needs each parameter varied alone; the
    # observed-flow sensitivity tools cannot serve a model without data.
    if mode not in ("joint", "one_at_a_time"):
        raise ValueError(f"mode must be 'joint' or 'one_at_a_time', not {mode!r}")
    tag = ("oat_" if mode == "one_at_a_time" else "") + sweep_tag(ranges)
    sweep_dir = audit_dir / f"parameter_sweep_{tag}"
    if mode == "one_at_a_time":
        # n_samples is PER PARAMETER here; the planner asked for 25 and paid
        # 150 SWMM runs for six parameters (live finding F-110, 2026-09-03,
        # S48 r3). Five levels answer the ranking; nine is the ceiling.
        levels = min(n_samples, OAT_MAX_LEVELS) if n_samples else OAT_DEFAULT_LEVELS
        plan: list[tuple[str, dict[str, float]]] = []
        for name in ranges:
            for i, values in enumerate(sample_space({name: ranges[name]}, levels, seed=seed), start=1):
                plan.append((f"{name}_s{i:02d}", values))
    else:
        plan = [(f"s{i:02d}", values) for i, values in enumerate(sample_space(ranges, n_samples, seed=seed), start=1)]
    runs: list[SweepSample] = []

    def _one(name: str, values: dict[str, float]) -> SweepSample:
        sample_dir = sweep_dir / name
        inp = write_scenario_inp(base_inp, ScenarioSpec(name, 1.0), sample_dir)
        if values:
            inp.write_text(rewrite_inp(inp.read_text(encoding="utf-8", errors="ignore"), values), encoding="utf-8")
        if progress:
            progress(f"parameter sweep: {name}")
        manifest = run(inp, sample_dir, report_node)
        if not isinstance(manifest, dict):
            manifest = {"run_ok": False, "error": "runner returned no manifest"}
        metrics = manifest.get("metrics") or {}
        peak = (metrics.get("peak") or {}).get("peak")
        units = (metrics.get("peak") or {}).get("units") or metrics.get("flow_units")
        run_ok = bool(manifest.get("run_ok")) and isinstance(peak, (int, float))
        return SweepSample(
            name=name,
            values=values,
            run_ok=run_ok,
            sample_dir=str(sample_dir),
            peak=float(peak) if isinstance(peak, (int, float)) else None,
            flow_units=str(units) if units else None,
            error=str(manifest.get("error") or "") if not run_ok else "",
            manifest=manifest,
        )

    baseline = _one("baseline", {})
    if report_node == AUTO_NODE:
        report_node = resolved_report_node(base_inp, baseline.manifest)
    for name, values in plan:
        runs.append(_one(name, values))

    stats = _stats(baseline, runs, ranges)
    if mode == "one_at_a_time":
        stats.update(_one_at_a_time_stats(baseline, runs, ranges))
    units = baseline.flow_units or next((s.flow_units for s in runs if s.flow_units), None)
    payload = {
        "schema_version": "1.0",
        "base_inp": str(base_inp),
        "node": report_node,
        "flow_units": units,
        "ranges": {k: list(v) for k, v in ranges.items()},
        "baseline": _sample_dict(baseline),
        "samples": [_sample_dict(s) for s in runs],
        "stats": stats,
        "evidence_boundary": (
            "Prior sensitivity of the peak to globally applied parameter ranges; "
            "not calibrated uncertainty. Values were set the same on every object of the section."
        ),
    }
    summary_json = audit_dir / f"parameter_sweep_{tag}.json"
    summary_md = audit_dir / f"parameter_sweep_{tag}.md"
    summary_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary_md.write_text(_render_md(report_node, units, baseline, runs, stats, ranges), encoding="utf-8")
    ok = baseline.run_ok and any(s.run_ok for s in runs)
    return SweepResult(
        ok=ok,
        run_dir=str(run_dir),
        node=report_node,
        baseline_peak=baseline.peak,
        flow_units=units,
        samples=tuple(runs),
        summary_json=str(summary_json),
        summary_md=str(summary_md),
        stats=stats,
    )


def _sample_dict(sample: SweepSample) -> dict[str, Any]:
    return {
        "name": sample.name,
        "values": sample.values,
        "run_ok": sample.run_ok,
        "peak": sample.peak,
        "flow_units": sample.flow_units,
        "sample_dir": sample.sample_dir,
        "error": sample.error,
    }


def _stats(baseline: SweepSample, runs: list[SweepSample], ranges: dict[str, tuple[float, float]]) -> dict[str, Any]:
    peaks = [s.peak for s in runs if s.run_ok and s.peak is not None]
    stats: dict[str, Any] = {
        "samples_total": len(runs),
        "samples_ok": len(peaks),
        "samples_failed": len(runs) - len(peaks),
    }
    if not peaks:
        return stats
    lo, hi = min(peaks), max(peaks)
    stats.update({"peak_min": lo, "peak_median": statistics.median(peaks), "peak_max": hi, "peak_spread": hi - lo})
    if baseline.peak:
        stats["spread_percent_of_baseline"] = round(100.0 * (hi - lo) / baseline.peak, 2)
    # Marginal effect per parameter: mean peak at the top third of its range
    # minus the mean at the bottom third; the largest wins.
    effects: dict[str, float] = {}
    for name, (r_lo, r_hi) in ranges.items():
        if r_hi <= r_lo:
            continue
        low = [s.peak for s in runs if s.run_ok and s.peak is not None and name in s.values and (s.values[name] - r_lo) / (r_hi - r_lo) <= 1 / 3]
        high = [s.peak for s in runs if s.run_ok and s.peak is not None and name in s.values and (s.values[name] - r_lo) / (r_hi - r_lo) >= 2 / 3]
        if low and high:
            effects[name] = round(statistics.mean(high) - statistics.mean(low), 6)
    if effects:
        stats["marginal_effect_on_peak"] = effects
        stats["dominant_parameter"] = max(effects, key=lambda k: abs(effects[k]))
    return stats


def _one_at_a_time_stats(baseline: SweepSample, runs: list[SweepSample], ranges: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """Per-parameter spread when each parameter was varied alone, plus a ranking."""
    per: dict[str, dict[str, Any]] = {}
    for name in ranges:
        peaks = [s.peak for s in runs if s.run_ok and s.peak is not None and list(s.values) == [name]]
        if not peaks:
            per[name] = {"samples_ok": 0}
            continue
        lo, hi = min(peaks), max(peaks)
        entry: dict[str, Any] = {"samples_ok": len(peaks), "peak_min": lo, "peak_max": hi, "peak_spread": round(hi - lo, 6)}
        if baseline.peak:
            entry["spread_percent_of_baseline"] = round(100.0 * (hi - lo) / baseline.peak, 2)
        per[name] = entry
    ranking = sorted((n for n in per if per[n].get("samples_ok")), key=lambda n: per[n]["peak_spread"], reverse=True)
    out: dict[str, Any] = {"mode": "one_at_a_time", "per_parameter": per, "ranking": ranking}
    if ranking:
        out["dominant_parameter"] = ranking[0]
    return out


def _render_md(node: str, units: str | None, baseline: SweepSample, runs: list[SweepSample], stats: dict[str, Any], ranges: dict[str, tuple[float, float]]) -> str:
    unit = units or "flow units not recorded"
    lines = [
        "# Parameter range propagation",
        "",
        f"Report node: `{node}`. Peak flow unit: {unit}. Every value was applied to all objects of its section.",
        "",
        "| parameter | low | high |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {name} | {lo:g} | {hi:g} |" for name, (lo, hi) in ranges.items()]
    lines += ["", f"Baseline peak: {baseline.peak if baseline.peak is not None else 'n/a'}", ""]
    if "peak_min" in stats:
        lines += [
            f"Peak over {stats['samples_ok']} successful samples: min {stats['peak_min']:g}, median {stats['peak_median']:g}, max {stats['peak_max']:g}"
            + (f" ({stats['spread_percent_of_baseline']}% of baseline)" if "spread_percent_of_baseline" in stats else ""),
        ]
        if "dominant_parameter" in stats:
            lines.append(f"Dominant parameter: {stats['dominant_parameter']} (marginal effects {stats['marginal_effect_on_peak']})")
    if stats.get("samples_failed"):
        lines.append(f"Failed samples: {stats['samples_failed']}")
    if stats.get("mode") == "one_at_a_time" and stats.get("per_parameter"):
        lines += ["", "One-at-a-time ranking (each parameter varied alone, the others at baseline):", "",
                  "| rank | parameter | low | high | peak min | peak max | spread (% of baseline) |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for rank, name in enumerate(stats.get("ranking") or [], start=1):
            e = stats["per_parameter"][name]; lo, hi = ranges[name]
            lines.append(f"| {rank} | {name} | {lo:g} | {hi:g} | {e.get('peak_min', 'n/a')} | {e.get('peak_max', 'n/a')} | {e.get('spread_percent_of_baseline', 'n/a')} |")
    lines += ["", "| sample | " + " | ".join(ranges) + " | run ok | peak |", "| --- | " + " | ".join("---" for _ in ranges) + " | --- | --- |"]
    for s in runs:
        vals = " | ".join(f"{s.values[name]:g}" if name in s.values else "base" for name in ranges)
        lines.append(f"| {s.name} | {vals} | {'yes' if s.run_ok else 'FAILED'} | {s.peak if s.peak is not None else 'n/a'} |")
    lines += ["", "Evidence boundary: prior sensitivity to globally applied ranges, not calibrated uncertainty.", ""]
    return "\n".join(lines)


__all__ = [
    "ALIASES",
    "AUTO_NODE",
    "resolved_report_node",
    "GLOBAL_PARAMETERS",
    "SweepResult",
    "SweepSample",
    "canonical_parameter",
    "parse_ranges",
    "rewrite_inp",
    "run_parameter_sweep",
    "sample_space",
]
