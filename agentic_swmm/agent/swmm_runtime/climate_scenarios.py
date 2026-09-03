"""Climate-forcing scenario batches over a (calibrated) SWMM model.

The post-processing pipeline this module completes (ADR-0010): an
upstream model (SWMMCanada or any INP) is calibrated against observed
data first (`aiswmm calibrate`, ADR-0005), then batch-simulated under
precipitation-scaled climate scenarios to compare system response. The
scaling approach is the standard first-order climate-uplift screen:
multiply every rainfall input by a scenario factor (e.g. 1.20 for a
+20% uplift), leaving the network untouched, and diff the responses.

Scope and honesty:

* Precipitation scaling covers inline ``[TIMESERIES]`` rows referenced
  by ``[RAINGAGES]`` and external ``FILE``-referenced ``.dat`` series
  (scaled copies are written next to each scenario INP). Design-storm
  regeneration and IDF-based uplifts stay in :mod:`design_storm`.
* Each scenario runs through the same audited runner script the
  ``aiswmm run`` CLI drives, one flat scenario directory per scenario
  under the canonical ``03_climate/scenarios/`` stage (ADR-0004).
* The summary reports what the runner manifests actually parsed
  (continuity totals, peak at the report node); a scenario whose run
  failed is carried with ``run_ok=false``, never silently dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.swmm_runtime.run_manifests import parse_runner_manifest
from agentic_swmm.utils.paths import script_path
from agentic_swmm.utils.subprocess_runner import python_command, run_command


@dataclass(frozen=True)
class ScenarioSpec:
    """One climate scenario: a name and a precipitation multiplier."""

    name: str
    precip_factor: float


DEFAULT_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("baseline", 1.0),
    ScenarioSpec("plus10", 1.10),
    ScenarioSpec("plus20", 1.20),
    ScenarioSpec("plus35", 1.35),
)


def parse_factors(spec: str) -> tuple[ScenarioSpec, ...]:
    """Parse ``"1.0,1.1,1.35"`` into named scenarios.

    ``1.0`` becomes ``baseline``; other factors become ``plusNN`` /
    ``minusNN`` from the percent delta (``1.35`` -> ``plus35``).
    Raises ``ValueError`` on an empty or non-numeric spec.
    """
    scenarios: list[ScenarioSpec] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        factor = float(token)  # ValueError propagates with the bad token
        if factor <= 0:
            raise ValueError(f"precipitation factor must be positive: {factor}")
        delta = round((factor - 1.0) * 100)
        if delta == 0:
            name = "baseline"
        elif delta > 0:
            name = f"plus{delta}"
        else:
            name = f"minus{-delta}"
        scenarios.append(ScenarioSpec(name, factor))
    if not scenarios:
        raise ValueError("no precipitation factors given")
    return tuple(scenarios)


# ----------------------------------------------------------------------
# INP rainfall scaling
# ----------------------------------------------------------------------

_NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


@dataclass(frozen=True)
class RainSources:
    """External-input inventory relevant to precipitation scaling.

    ``series_names``: TIMESERIES names raingages reference (their inline
    rows scale; their FILE-backed data scales).
    ``file_paths``: FILE paths raingages reference directly (scale).
    ``series_files``: every ``Name FILE path`` declaration inside
    ``[TIMESERIES]`` (temperature, evaporation, and FILE-backed rain
    alike) — each must be copied beside a scenario INP for the model to
    run at all, and scaled only when its name is a rain series.
    """

    series_names: frozenset[str]
    file_paths: tuple[str, ...]
    series_files: tuple[tuple[str, str], ...] = ()


def rain_sources(inp_text: str) -> RainSources:
    """Collect raingage sources plus every FILE-backed timeseries."""
    series: set[str] = set()
    files: list[str] = []
    series_files: list[tuple[str, str]] = []
    section = None
    for raw in inp_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.upper()
            continue
        tokens = stripped.split(";", 1)[0].split()
        if section == "[RAINGAGES]":
            for index, token in enumerate(tokens):
                upper = token.upper()
                if upper == "TIMESERIES" and index + 1 < len(tokens):
                    series.add(tokens[index + 1])
                elif upper == "FILE" and index + 1 < len(tokens):
                    files.append(tokens[index + 1].strip('"'))
        elif section == "[TIMESERIES]":
            for index, token in enumerate(tokens):
                if token.upper() == "FILE" and index + 1 < len(tokens):
                    series_files.append((tokens[0], tokens[index + 1].strip('"')))
                    break
    return RainSources(
        series_names=frozenset(series),
        file_paths=tuple(files),
        series_files=tuple(series_files),
    )


def scale_timeseries(inp_text: str, factor: float, series_names: frozenset[str]) -> str:
    """Scale the value column of the named inline [TIMESERIES] rows.

    Rows for other series, FILE rows, comments, and section structure
    pass through untouched. The value is the LAST numeric token of each
    data row (SWMM accepts ``Name Date Time Value`` and ``Name Time
    Value`` shapes; both end in the value).
    """
    out: list[str] = []
    section = None
    for raw in inp_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.upper()
            out.append(raw)
            continue
        if section != "[TIMESERIES]" or not stripped or stripped.startswith(";"):
            out.append(raw)
            continue
        code, *comment = raw.split(";", 1)
        tokens = code.split()
        if not tokens or tokens[0] not in series_names or "FILE" in (t.upper() for t in tokens):
            out.append(raw)
            continue
        if tokens and _NUMBER.match(tokens[-1]):
            tokens[-1] = _format_scaled(tokens[-1], factor)
            line = "  ".join(tokens)
            if comment:
                line += " ;" + comment[0]
            out.append(line)
        else:
            out.append(raw)
    return "\n".join(out) + ("\n" if inp_text.endswith("\n") else "")


def scale_dat_text(dat_text: str, factor: float) -> str:
    """Scale the last numeric column of a SWMM rainfall ``.dat`` file."""
    out: list[str] = []
    for raw in dat_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            out.append(raw)
            continue
        tokens = raw.split()
        if tokens and _NUMBER.match(tokens[-1]):
            tokens[-1] = _format_scaled(tokens[-1], factor)
            out.append("  ".join(tokens))
        else:
            out.append(raw)
    return "\n".join(out) + ("\n" if dat_text.endswith("\n") else "")


def _format_scaled(token: str, factor: float) -> str:
    return f"{float(token) * factor:.6g}"


def write_scenario_inp(base_inp: Path, scenario: ScenarioSpec, out_dir: Path) -> Path:
    """Write the scenario's scaled model into ``out_dir``.

    Inline rain series are scaled in the INP text; FILE-referenced
    ``.dat`` series get scaled copies beside the scenario INP with the
    FILE token rewritten to the local basename, so the scenario folder
    is self-contained.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    text = base_inp.read_text(encoding="utf-8", errors="ignore")
    sources = rain_sources(text)
    scaled = scale_timeseries(text, scenario.precip_factor, sources.series_names)

    def _localise(file_ref: str, *, scale: bool) -> None:
        nonlocal scaled
        source = Path(file_ref)
        if not source.is_absolute():
            source = base_inp.parent / source
        local = out_dir / source.name
        if source.is_file():
            body = source.read_text(encoding="utf-8", errors="ignore")
            local.write_text(
                scale_dat_text(body, scenario.precip_factor) if scale else body,
                encoding="utf-8",
            )
        # Rewrite the reference (basename) whether or not the copy
        # succeeded, so a missing source surfaces as the runner's own
        # missing-file error instead of silently using unscaled data.
        scaled = scaled.replace(file_ref, local.name)

    # Raingage FILE sources are rainfall by definition: scale them.
    for file_ref in sources.file_paths:
        _localise(file_ref, scale=True)
    # FILE-backed timeseries: every one must exist beside the scenario
    # INP for SWMM to run (temperature/evaporation included); only the
    # rain series' data is scaled.
    for series_name, file_ref in sources.series_files:
        _localise(file_ref, scale=series_name in sources.series_names)

    out_inp = out_dir / "model.inp"
    out_inp.write_text(scaled, encoding="utf-8")
    return out_inp


# ----------------------------------------------------------------------
# Batch execution + summary
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioRun:
    name: str
    precip_factor: float
    run_ok: bool
    scenario_dir: str
    inp: str
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class ClimateBatchResult:
    ok: bool
    run_dir: str
    node: str
    scenarios: tuple[ScenarioRun, ...]
    summary_json: str
    summary_md: str


RunnerFn = Callable[[Path, Path, str], dict[str, Any]]


def _default_runner(inp: Path, scenario_dir: Path, node: str) -> dict[str, Any]:
    """Run one scenario through the audited swmm-runner skill script."""
    script = script_path("skills", "swmm-runner", "scripts", "swmm_runner.py")
    command = python_command(
        script, "run", "--inp", str(inp), "--run-dir", str(scenario_dir), "--node", node
    )
    result = run_command(command, check=False)
    manifest = parse_runner_manifest(result.stdout)
    if not manifest:
        manifest = {"run_ok": False, "error": (result.stderr or "runner produced no manifest")[-2000:]}
    return manifest


def run_climate_batch(
    *,
    base_inp: Path,
    run_dir: Path,
    scenarios: tuple[ScenarioSpec, ...] = DEFAULT_SCENARIOS,
    node: str | None = None,
    runner: RunnerFn | None = None,
    progress: Callable[[str], None] | None = None,
) -> ClimateBatchResult:
    """Batch-run precipitation-scaled scenarios; write the comparison.

    Artifacts land in the canonical climate stage (ADR-0004)::

        <run_dir>/03_climate/scenarios/<name>/model.inp (+ scaled .dat)
        <run_dir>/03_climate/scenarios/<name>/model.rpt / model.out ...
        <run_dir>/03_climate/climate_summary.json / climate_summary.md
    """
    from agentic_swmm.agent.swmm_runtime.inp_parsing import default_report_node

    runner = runner or _default_runner
    report_node = node or default_report_node(base_inp) or "O1"
    climate_root = run_layout.stage_dir(run_dir, run_layout.CLIMATE, create=True)
    scenarios_root = climate_root / "scenarios"

    runs: list[ScenarioRun] = []
    for scenario in scenarios:
        if progress is not None:
            progress(f"scenario {scenario.name} (x{scenario.precip_factor:g})")
        scenario_dir = scenarios_root / scenario.name
        inp = write_scenario_inp(base_inp, scenario, scenario_dir)
        try:
            manifest = runner(inp, scenario_dir, report_node)
        except Exception as exc:  # noqa: BLE001 - one bad scenario must not sink the batch
            runs.append(
                ScenarioRun(
                    name=scenario.name,
                    precip_factor=scenario.precip_factor,
                    run_ok=False,
                    scenario_dir=str(scenario_dir),
                    inp=str(inp),
                    error=str(exc),
                )
            )
            continue
        runs.append(
            ScenarioRun(
                name=scenario.name,
                precip_factor=scenario.precip_factor,
                run_ok=bool(manifest.get("run_ok")),
                scenario_dir=str(scenario_dir),
                inp=str(inp),
                metrics=_summary_metrics(manifest),
                error=str(manifest.get("error", "")) if not manifest.get("run_ok") else "",
            )
        )

    summary_json = climate_root / "climate_summary.json"
    summary_md = climate_root / "climate_summary.md"
    payload = {
        "schema_version": "1.0",
        "base_inp": str(base_inp),
        "node": report_node,
        "scenarios": [run.__dict__ for run in runs],
    }
    summary_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary_md.write_text(_render_summary_md(report_node, runs), encoding="utf-8")

    return ClimateBatchResult(
        ok=all(run.run_ok for run in runs),
        run_dir=str(run_dir),
        node=report_node,
        scenarios=tuple(runs),
        summary_json=str(summary_json),
        summary_md=str(summary_md),
    )


def _summary_metrics(manifest: dict[str, Any]) -> dict[str, Any]:
    """Lift the comparison-relevant numbers out of a runner manifest."""
    metrics = manifest.get("metrics") or {}
    continuity = metrics.get("continuity") or {}
    runoff = continuity.get("runoff_quantity") or {}
    routing = continuity.get("flow_routing") or {}
    peak = metrics.get("peak") or {}

    def _depth(table: dict[str, Any], key: str) -> float | None:
        row = table.get(key)
        if isinstance(row, dict):
            value = row.get("col2", row.get("col1"))
            return float(value) if isinstance(value, (int, float)) else None
        return None

    return {
        "total_precip_depth": _depth(runoff, "Total Precipitation"),
        "surface_runoff_depth": _depth(runoff, "Surface Runoff"),
        "flooding_loss_volume": _depth(routing, "Flooding Loss"),
        "external_outflow_volume": _depth(routing, "External Outflow"),
        "peak_flow": peak.get("peak"),
        # Live finding F-62 (2026-09-02): the unit is the report's (F-52);
        # older manifests carry none and the table says so.
        "flow_units": peak.get("units") or metrics.get("flow_units"),
        "peak_time": peak.get("time_hhmm"),
        "continuity_error_percent": (continuity.get("continuity_error_percent") or {}),
    }


def _peak_units_label(runs: list[ScenarioRun]) -> str:
    units = {str((run.metrics or {}).get("flow_units")) for run in runs if (run.metrics or {}).get("flow_units")}
    if len(units) == 1:
        return units.pop()
    return "flow units not recorded" if not units else "mixed units: " + ", ".join(sorted(units))


def _render_summary_md(node: str, runs: list[ScenarioRun]) -> str:
    lines = [
        "# Climate scenario comparison",
        "",
        f"Report node: `{node}`. Depth/volume columns come straight from each",
        "scenario's SWMM continuity tables; a failed scenario keeps its row.",
        "",
        f"| scenario | precip factor | run ok | total precip | surface runoff | flooding loss | external outflow | peak flow ({_peak_units_label(runs)}) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        metrics = run.metrics or {}

        def _cell(key: str) -> str:
            value = metrics.get(key)
            return f"{value:g}" if isinstance(value, (int, float)) else "n/a"

        lines.append(
            f"| {run.name} | x{run.precip_factor:g} | {'yes' if run.run_ok else 'FAILED'} "
            f"| {_cell('total_precip_depth')} | {_cell('surface_runoff_depth')} "
            f"| {_cell('flooding_loss_volume')} | {_cell('external_outflow_volume')} "
            f"| {_cell('peak_flow')} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SCENARIOS",
    "ClimateBatchResult",
    "RainSources",
    "ScenarioRun",
    "ScenarioSpec",
    "parse_factors",
    "rain_sources",
    "run_climate_batch",
    "scale_dat_text",
    "scale_timeseries",
    "write_scenario_inp",
]
