#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from swmmtoolbox import swmmtoolbox

from candidate_writer import write_candidate_artefacts
from inp_patch import patch_inp_text
from metrics import align_series, compute_metrics, score_from_metrics
from obs_reader import read_series


# PRD-GF-CORE: gap-fill emission helpers.
#
# The agent runtime intercepts ``{"ok": False, "gap_signal": ...}``
# results and routes them through the proposer/UI/recorder. When a
# Python caller (e.g. a test or an MCP wrapper) invokes
# :func:`prepare_calibration_inputs` and the observed-flow file or
# the calibration target field is missing, we emit a structured gap
# signal instead of raising. The legacy CLI still raises
# ``SystemExit`` / ``FileNotFoundError`` for backwards compatibility
# with the long-form ``aiswmm`` recipes that drive this script via
# subprocess; only the new in-process entry point speaks gap-fill.

_VALID_CALIBRATION_TARGETS = {"flow", "depth", "head", "volume"}


def _new_gap_id() -> str:
    """Return a short ``gap-<hex>`` identifier (mirrors gap_fill.protocol)."""
    return f"gap-{uuid.uuid4().hex[:12]}"


def emit_observed_flow_gap_signal(observed_path: str | None) -> dict[str, Any]:
    """Build the L1 ``gap_signal`` result for a missing observed-flow file.

    Used when ``observed_path`` is ``None`` or points at a path that
    does not exist. The shape matches the runtime's interception
    contract.
    """
    return {
        "tool": "swmm_calibrate",
        "args": {"observed": observed_path},
        "ok": False,
        "summary": "missing observed flow file",
        "gap_signal": {
            "gap_id": _new_gap_id(),
            "severity": "L1",
            "kind": "file_path",
            "field": "observed",
            "context": {
                "tool": "swmm_calibrate",
                "step": "load_observed_series",
                "provided_path": observed_path,
            },
        },
    }


def emit_calibration_target_gap_signal(provided: str | None) -> dict[str, Any]:
    """Build the L3 ``gap_signal`` result for a missing calibration target.

    Used when the caller did not specify which series the calibration
    objective should target (flow / depth / head / volume). The
    proposer's registry layer is unlikely to know — most calibration
    tasks default to ``flow`` — so the agent UI prompts the user to
    pick.
    """
    return {
        "tool": "swmm_calibrate",
        "args": {"calibration_target": provided},
        "ok": False,
        "summary": "missing calibration target field",
        "gap_signal": {
            "gap_id": _new_gap_id(),
            "severity": "L3",
            "kind": "param_value",
            "field": "calibration_target",
            "context": {
                "tool": "swmm_calibrate",
                "step": "select_target_series",
                "allowed_values": sorted(_VALID_CALIBRATION_TARGETS),
            },
            "suggestion": {"default": "flow"},
        },
    }


def prepare_calibration_inputs(
    *,
    observed: str | Path | None,
    calibration_target: str | None,
) -> dict[str, Any]:
    """Validate the two PRD-GF-CORE-tracked calibration inputs.

    Returns one of:

    - ``{"ok": True, ...}`` when both inputs are present and the file
      exists on disk.
    - An L1 ``gap_signal`` result when the observed file is missing.
    - An L3 ``gap_signal`` result when the calibration target is
      missing or invalid.

    The function does **not** load the file or run a calibration — it
    is a thin validation gate. The agent runtime calls this before
    invoking the full CLI; on a gap signal the runtime routes through
    the gap-fill state machine and re-invokes with merged args.

    Two emit points (per PRD-GF-CORE):

    1. **L1** — ``observed`` is ``None``, empty string, or points at
       a path that does not exist.
    2. **L3** — ``calibration_target`` is ``None``, empty string, or
       not in ``{"flow", "depth", "head", "volume"}``.

    Order of checks: L1 first, then L3. The runtime's batching
    contract accepts both at once if a single call carries both gaps.
    """
    obs_str = str(observed) if observed is not None else None
    if not obs_str or not obs_str.strip():
        return emit_observed_flow_gap_signal(None)
    if not Path(obs_str).is_file():
        return emit_observed_flow_gap_signal(obs_str)

    if not calibration_target or not str(calibration_target).strip():
        return emit_calibration_target_gap_signal(None)
    if str(calibration_target) not in _VALID_CALIBRATION_TARGETS:
        return emit_calibration_target_gap_signal(str(calibration_target))

    return {
        "tool": "swmm_calibrate",
        "args": {
            "observed": obs_str,
            "calibration_target": str(calibration_target),
        },
        "ok": True,
        "summary": "calibration inputs valid",
    }


@dataclass(frozen=True)
class ParamBound:
    name: str
    min_value: float
    max_value: float
    value_type: str = "float"
    precision: int | None = None

    def from_unit(self, unit_value: float) -> float | int:
        clamped = min(1.0, max(0.0, unit_value))
        value = self.min_value + (self.max_value - self.min_value) * clamped
        if self.value_type == "int":
            return int(round(value))
        if self.precision is not None:
            return round(float(value), self.precision)
        return float(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min": self.min_value,
            "max": self.max_value,
            "type": self.value_type,
            "precision": self.precision,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def ensure_param_sets(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "parameter_sets" in obj:
        return obj["parameter_sets"]
    raise ValueError("Parameter sets JSON must be a list or contain 'parameter_sets'")


def ensure_named_trial(item: dict, idx: int) -> dict:
    out = dict(item)
    out.setdefault("name", f"trial_{idx:03d}")
    if "params" not in out or not isinstance(out["params"], dict):
        raise ValueError(f"Trial {out['name']} missing params object")
    return out


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def parse_search_space(obj: Any) -> dict[str, ParamBound]:
    if not isinstance(obj, dict) or not obj:
        raise ValueError("Search-space JSON must be a non-empty object")

    out: dict[str, ParamBound] = {}
    for name, spec in obj.items():
        if isinstance(spec, dict):
            if "min" not in spec or "max" not in spec:
                raise ValueError(f"Search-space parameter '{name}' requires 'min' and 'max'")
            min_value = float(spec["min"])
            max_value = float(spec["max"])
            raw_type = str(spec.get("type", "float")).lower().strip()
            if raw_type in {"int", "integer"}:
                value_type = "int"
            elif raw_type in {"float", "number"}:
                value_type = "float"
            else:
                raise ValueError(f"Unsupported search-space type for '{name}': {raw_type}")
            precision = spec.get("precision")
            if precision is not None:
                precision = int(precision)
                if precision < 0:
                    raise ValueError(f"Precision for '{name}' must be >= 0")
            if value_type == "int":
                precision = None
        elif isinstance(spec, (list, tuple)) and len(spec) == 2:
            min_value = float(spec[0])
            max_value = float(spec[1])
            value_type = "float"
            precision = None
        else:
            raise ValueError(
                f"Search-space parameter '{name}' must be either [min, max] or an object with min/max/type"
            )

        if not math.isfinite(min_value) or not math.isfinite(max_value):
            raise ValueError(f"Search-space parameter '{name}' has non-finite bounds")
        if min_value > max_value:
            raise ValueError(f"Search-space parameter '{name}' has min > max")

        out[name] = ParamBound(
            name=name,
            min_value=min_value,
            max_value=max_value,
            value_type=value_type,
            precision=precision,
        )
    return out


def serialize_bounds(bounds: dict[str, ParamBound]) -> dict[str, dict[str, Any]]:
    return {name: bound.to_dict() for name, bound in bounds.items()}


def sample_random_params(bounds: dict[str, ParamBound], count: int, rng: random.Random) -> list[dict[str, float | int]]:
    out: list[dict[str, float | int]] = []
    for _ in range(count):
        params: dict[str, float | int] = {}
        for name, bound in bounds.items():
            params[name] = bound.from_unit(rng.random())
        out.append(params)
    return out


def sample_lhs_params(bounds: dict[str, ParamBound], count: int, rng: random.Random) -> list[dict[str, float | int]]:
    if count <= 0:
        raise ValueError("LHS sample count must be >= 1")

    unit_vectors: dict[str, list[float]] = {}
    for name in bounds:
        vals = [(i + rng.random()) / count for i in range(count)]
        rng.shuffle(vals)
        unit_vectors[name] = vals

    out: list[dict[str, float | int]] = []
    for idx in range(count):
        params: dict[str, float | int] = {}
        for name, bound in bounds.items():
            params[name] = bound.from_unit(unit_vectors[name][idx])
        out.append(params)
    return out


def refine_bounds_from_elite(
    current_bounds: dict[str, ParamBound],
    global_bounds: dict[str, ParamBound],
    elite_results: list[dict],
    margin_fraction: float,
    min_span_fraction: float,
) -> dict[str, ParamBound]:
    refined: dict[str, ParamBound] = {}
    for name, current in current_bounds.items():
        global_bound = global_bounds[name]
        values = [
            float(rec["params"][name])
            for rec in elite_results
            if isinstance(rec.get("params"), dict) and name in rec["params"]
        ]
        if not values:
            refined[name] = current
            continue

        lo = min(values)
        hi = max(values)
        spread = hi - lo

        global_span = global_bound.max_value - global_bound.min_value
        min_span = max(0.0, global_span * min_span_fraction)

        if spread <= 0:
            center = sum(values) / len(values)
            current_span = current.max_value - current.min_value
            span = max(min_span, current_span * 0.25)
            lo = center - (span / 2.0)
            hi = center + (span / 2.0)
        else:
            lo = lo - (spread * margin_fraction)
            hi = hi + (spread * margin_fraction)
            if (hi - lo) < min_span:
                center = (hi + lo) / 2.0
                lo = center - (min_span / 2.0)
                hi = center + (min_span / 2.0)

        lo = max(global_bound.min_value, lo)
        hi = min(global_bound.max_value, hi)
        if lo >= hi:
            lo = global_bound.min_value
            hi = global_bound.max_value

        refined[name] = ParamBound(
            name=name,
            min_value=float(lo),
            max_value=float(hi),
            value_type=global_bound.value_type,
            precision=global_bound.precision,
        )
    return refined


def build_search_trials(
    samples: list[dict[str, float | int]],
    trial_prefix: str,
    start_index: int,
    strategy: str,
    round_index: int,
) -> list[dict]:
    trials: list[dict] = []
    for local_idx, params in enumerate(samples, start=1):
        global_idx = start_index + local_idx - 1
        trials.append(
            {
                "name": f"{trial_prefix}_{global_idx:04d}",
                "params": params,
                "metadata": {
                    "search_strategy": strategy,
                    "search_round": round_index,
                    "search_sample_index": local_idx,
                },
            }
        )
    return trials


def extract_simulated_series(out_path: Path, swmm_node: str, swmm_attr: str, aggregate: str) -> pd.DataFrame:
    label = f"node,{swmm_node},{swmm_attr}"
    series = swmmtoolbox.extract(str(out_path), label)
    if isinstance(series, pd.Series):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
    elif isinstance(series, pd.DataFrame):
        df = series.reset_index()
        df.columns = ["timestamp", "flow"]
        df = df[["timestamp", "flow"]]
    else:
        raise TypeError(f"Unexpected series type from swmmtoolbox: {type(series)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if aggregate == "daily_mean":
        df = df.set_index("timestamp").resample("D").mean(numeric_only=True).reset_index()
    return df


def run_swmm(inp: Path, run_dir: Path, rpt_name: str = "model.rpt", out_name: str = "model.out") -> tuple[int, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rpt = run_dir / rpt_name
    out = run_dir / out_name
    proc = subprocess.run(["swmm5", str(inp), str(rpt), str(out)], capture_output=True, text=True)
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8", errors="ignore")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8", errors="ignore")
    return proc.returncode, rpt, out


def describe_series(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"count": 0, "start": None, "end": None}
    ts = pd.to_datetime(df["timestamp"])
    return {
        "count": int(len(df)),
        "start": ts.min().isoformat(),
        "end": ts.max().isoformat(),
    }


def filter_series_window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    if start:
        out = out[out["timestamp"] >= pd.Timestamp(start)]
    if end:
        out = out[out["timestamp"] <= pd.Timestamp(end)]
    return out.reset_index(drop=True)


def load_observed_series(
    observed_path: Path,
    timestamp_col: str | None,
    flow_col: str | None,
    time_format: str | None,
    obs_start: str | None,
    obs_end: str | None,
) -> pd.DataFrame:
    observed = read_series(
        observed_path,
        timestamp_col=timestamp_col,
        flow_col=flow_col,
        time_format=time_format,
    )
    return filter_series_window(observed, obs_start, obs_end)


def finalize_trial(
    result: dict,
    status: str,
    reason_code: str,
    reason_detail: str | None,
    started_perf: float,
) -> dict:
    diagnostics = result.setdefault("diagnostics", {})
    diagnostics["finished_at_utc"] = utc_now_iso()
    diagnostics["elapsed_seconds"] = round(time.perf_counter() - started_perf, 6)
    result["status"] = status
    result["reason_code"] = reason_code
    result["reason_detail"] = reason_detail
    if status in {"failed", "invalid"}:
        result["error"] = reason_detail or reason_code
    return result


def evaluate_trial(
    base_inp: Path,
    patch_map: dict,
    trial: dict,
    observed: pd.DataFrame,
    run_root: Path,
    swmm_node: str,
    swmm_attr: str,
    objective: str,
    aggregate: str,
    obs_start: str | None,
    obs_end: str | None,
    dry_run: bool = False,
) -> dict:
    started_perf = time.perf_counter()
    trial_name = trial["name"]
    trial_dir = run_root / trial_name
    trial_dir.mkdir(parents=True, exist_ok=True)
    patched_inp = trial_dir / "model.inp"

    result: dict[str, Any] = {
        "trial": trial_name,
        "params": trial["params"],
        "run_dir": str(trial_dir),
        "dry_run": dry_run,
        "metrics": None,
        "objective": None,
        "status": "pending",
        "reason_code": None,
        "reason_detail": None,
        "observed_series": describe_series(observed),
        "diagnostics": {
            "started_at_utc": utc_now_iso(),
            "swmm_node": swmm_node,
            "swmm_attr": swmm_attr,
            "aggregate": aggregate,
            "observed_window": {"start": obs_start, "end": obs_end},
            "observed_points": int(len(observed)),
        },
    }
    if "metadata" in trial:
        result["metadata"] = trial["metadata"]

    try:
        patched_text = patch_inp_text(base_inp.read_text(errors="ignore"), patch_map, trial["params"])
        patched_inp.write_text(patched_text, encoding="utf-8")
        result["files"] = {"inp": str(patched_inp)}
    except Exception as exc:  # noqa: BLE001
        return finalize_trial(
            result,
            status="invalid",
            reason_code="patch_failed",
            reason_detail=f"Failed to apply parameters to INP: {exc}",
            started_perf=started_perf,
        )

    if dry_run:
        return finalize_trial(
            result,
            status="dry_run",
            reason_code="dry_run_enabled",
            reason_detail="Trial not executed because --dry-run was set",
            started_perf=started_perf,
        )

    try:
        rc, rpt, out = run_swmm(patched_inp, trial_dir)
    except FileNotFoundError as exc:
        return finalize_trial(
            result,
            status="failed",
            reason_code="swmm_binary_missing",
            reason_detail=f"swmm5 executable not found: {exc}",
            started_perf=started_perf,
        )
    except Exception as exc:  # noqa: BLE001
        return finalize_trial(
            result,
            status="failed",
            reason_code="swmm_execution_error",
            reason_detail=f"Failed to execute swmm5: {exc}",
            started_perf=started_perf,
        )

    result["return_code"] = rc
    result["files"].update({"rpt": str(rpt), "out": str(out)})
    if rc != 0:
        return finalize_trial(
            result,
            status="failed",
            reason_code="swmm_execution_failed",
            reason_detail=f"swmm5 returned non-zero exit code {rc}",
            started_perf=started_perf,
        )

    try:
        simulated = extract_simulated_series(out, swmm_node=swmm_node, swmm_attr=swmm_attr, aggregate=aggregate)
    except Exception as exc:  # noqa: BLE001
        return finalize_trial(
            result,
            status="invalid",
            reason_code="simulation_extract_failed",
            reason_detail=f"Failed to extract simulated series from model.out: {exc}",
            started_perf=started_perf,
        )

    try:
        aligned = align_series(observed, simulated)
        metrics = compute_metrics(observed, simulated)
    except Exception as exc:  # noqa: BLE001
        return finalize_trial(
            result,
            status="invalid",
            reason_code="metric_computation_failed",
            reason_detail=f"Failed while computing metrics: {exc}",
            started_perf=started_perf,
        )

    aligned_view = aligned.rename(columns={"flow_obs": "flow"})
    aligned_view = aligned_view[["timestamp", "flow"]] if not aligned_view.empty else pd.DataFrame(columns=["timestamp", "flow"])

    result["simulated_series"] = describe_series(simulated)
    result["aligned_series"] = describe_series(aligned_view)
    result["metrics"] = metrics.to_dict()

    observed_count = int(len(observed))
    overlap_fraction = None if observed_count == 0 else float(metrics.count / observed_count)
    result["diagnostics"]["simulated_points"] = int(len(simulated))
    result["diagnostics"]["aligned_points"] = int(metrics.count)
    result["diagnostics"]["overlap_fraction_of_observed"] = overlap_fraction

    if metrics.count == 0:
        return finalize_trial(
            result,
            status="invalid",
            reason_code="no_overlap",
            reason_detail="Observed and simulated series do not overlap on timestamps",
            started_perf=started_perf,
        )

    try:
        objective_value = score_from_metrics(metrics, objective)
    except Exception as exc:  # noqa: BLE001
        return finalize_trial(
            result,
            status="invalid",
            reason_code="objective_scoring_failed",
            reason_detail=f"Objective scoring failed: {exc}",
            started_perf=started_perf,
        )

    if not is_finite_number(objective_value):
        return finalize_trial(
            result,
            status="invalid",
            reason_code="objective_unavailable",
            reason_detail=f"Objective '{objective}' was not available for this trial",
            started_perf=started_perf,
        )

    result["objective"] = float(objective_value)
    if overlap_fraction is not None and overlap_fraction < 0.75:
        result["warning"] = (
            "Low overlap between observed and simulated timestamps. "
            "Check simulation window, observed window, and aggregation choices."
        )

    return finalize_trial(
        result,
        status="ok",
        reason_code="ok",
        reason_detail=None,
        started_perf=started_perf,
    )


def evaluate_trials(
    base_inp: Path,
    patch_map: dict,
    trials: list[dict],
    observed: pd.DataFrame,
    run_root: Path,
    swmm_node: str,
    swmm_attr: str,
    objective: str,
    aggregate: str,
    obs_start: str | None,
    obs_end: str | None,
    dry_run: bool,
) -> list[dict]:
    return [
        evaluate_trial(
            base_inp,
            patch_map,
            trial,
            observed,
            run_root,
            swmm_node,
            swmm_attr,
            objective,
            aggregate,
            obs_start,
            obs_end,
            dry_run=dry_run,
        )
        for trial in trials
    ]


def rank_results(results: list[dict]) -> list[dict]:
    def key(rec: dict) -> tuple[int, float, str]:
        objective = rec.get("objective")
        has_objective = is_finite_number(objective)
        status_rank = 0 if rec.get("status") == "ok" else 1
        score = float(objective) if has_objective else float("-inf")
        return (status_rank, -score, rec.get("trial", ""))

    return sorted(results, key=key)


def pick_best_result(ranked_results: list[dict]) -> dict | None:
    for rec in ranked_results:
        if rec.get("status") == "ok" and is_finite_number(rec.get("objective")):
            return rec
    return ranked_results[0] if ranked_results else None


def summarize_status_counts(results: list[dict]) -> dict[str, int]:
    counts = {
        "total": len(results),
        "ok": 0,
        "failed": 0,
        "invalid": 0,
        "dry_run": 0,
        "other": 0,
    }
    for rec in results:
        status = str(rec.get("status", "other"))
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    return counts


def build_ranking_table(ranked_results: list[dict]) -> list[dict]:
    table: list[dict[str, Any]] = []
    for idx, rec in enumerate(ranked_results, start=1):
        metrics = rec.get("metrics") or {}
        metadata = rec.get("metadata") or {}
        table.append(
            {
                "rank": idx,
                "trial": rec.get("trial"),
                "status": rec.get("status"),
                "reason_code": rec.get("reason_code"),
                "objective": rec.get("objective"),
                "metrics_count": metrics.get("count"),
                "nse": metrics.get("nse"),
                "rmse": metrics.get("rmse"),
                "bias": metrics.get("bias"),
                "peak_flow_error": metrics.get("peak_flow_error"),
                "peak_timing_error_minutes": metrics.get("peak_timing_error_minutes"),
                "overlap_fraction_of_observed": (rec.get("diagnostics") or {}).get("overlap_fraction_of_observed"),
                "search_round": metadata.get("search_round"),
                "run_dir": rec.get("run_dir"),
            }
        )
    return table


def _fmt_num(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"{float(value):.{digits}f}"
    return str(value)


def format_ranking_text(ranking_table: list[dict], objective: str, top_n: int) -> str:
    if not ranking_table:
        return "No ranking rows available."
    shown = ranking_table[: max(1, top_n)]
    lines = [
        f"Ranking summary ({objective}, showing {len(shown)} of {len(ranking_table)})",
        "rank | trial | status | reason | objective | nse | rmse | count",
        "-----+-------+--------+--------+-----------+-----+------+------",
    ]
    for row in shown:
        lines.append(
            " | ".join(
                [
                    str(row.get("rank", "-")),
                    str(row.get("trial", "-")),
                    str(row.get("status", "-")),
                    str(row.get("reason_code", "-")),
                    _fmt_num(row.get("objective")),
                    _fmt_num(row.get("nse")),
                    _fmt_num(row.get("rmse")),
                    str(row.get("metrics_count", "-")),
                ]
            )
        )
    return "\n".join(lines)


def _pbias_pct_from_bundle_dict(metrics_dict: dict[str, Any], observed: pd.DataFrame) -> float | None:
    """Return PBIAS% in the calibration_summary shape, or ``None``.

    The legacy random/lhs/adaptive trial pipeline emits a
    :class:`MetricBundle`-as-dict (``metrics_dict``) that has ``bias``
    (mean diff) and ``count`` (overlap length); the candidate writer
    expects PBIAS%, defined as ``100 * sum(sim - obs) / sum(obs)``.
    Reconstructing it from ``bias * count`` keeps the conversion small
    and consistent with the SCE-UA path in :mod:`sceua`.
    """
    bias = metrics_dict.get("bias")
    count = metrics_dict.get("count")
    if bias is None or count is None or count == 0:
        return None
    try:
        obs_sum = float(observed["flow"].astype(float).sum())
    except Exception:
        return None
    if obs_sum == 0.0:
        return None
    return float(100.0 * float(bias) * int(count) / obs_sum)


def build_candidate_summary_from_best(
    best: dict[str, Any] | None,
    *,
    strategy: str,
    iterations: int,
    observed: pd.DataFrame,
    convergence_trace_ref: str | None = None,
) -> dict[str, Any] | None:
    """Project a best-result dict into the candidate writer's summary shape.

    The candidate writer is strategy-agnostic and expects the
    ``calibration_summary.json`` shape (KGE primary + decomposition +
    secondary metrics + strategy + iterations + convergence_trace_ref).
    The legacy random/lhs/adaptive payload exposes the same metric
    fields via the best trial's :class:`MetricBundle`-as-dict, so we
    can construct an equivalent summary without re-running SWMM.

    Returns ``None`` when there is no usable best (e.g. all trials
    failed or KGE is undefined) — the candidate writer is then skipped
    by the caller. We choose to skip rather than emit a partial
    candidate so the on-disk evidence boundary stays sharp.
    """
    if not best:
        return None
    metrics = best.get("metrics") or {}
    kge_value = metrics.get("kge")
    decomposition = metrics.get("kge_decomposition")
    if kge_value is None or not isinstance(decomposition, dict):
        return None
    secondary = {
        "nse": metrics.get("nse"),
        "pbias_pct": _pbias_pct_from_bundle_dict(metrics, observed),
        "rmse": metrics.get("rmse"),
        "peak_error_rel": metrics.get("peak_flow_error"),
        "peak_timing_min": metrics.get("peak_timing_error_minutes"),
    }
    return {
        "primary_objective": "kge",
        "primary_value": float(kge_value),
        "kge_decomposition": {
            "r": float(decomposition.get("r", 0.0)),
            "alpha": float(decomposition.get("alpha", 0.0)),
            "beta": float(decomposition.get("beta", 0.0)),
        },
        "secondary_metrics": secondary,
        "strategy": strategy,
        "iterations": int(iterations),
        "convergence_trace_ref": convergence_trace_ref,
    }


def emit_candidate_artefacts(
    args: argparse.Namespace,
    *,
    summary: dict[str, Any] | None,
    best_params: dict[str, Any] | None,
    extra_refs: dict[str, str] | None = None,
) -> None:
    """Write 3-artefact candidate handover when ``--candidate-run-dir`` is set.

    A no-op when the caller did not request a candidate (back-compat
    with existing flows that do not yet route through ``09_audit/``).
    Likewise a no-op when there is no usable best result — the
    canonical INP stays untouched and there is nothing to hand over.
    """
    run_dir = getattr(args, "candidate_run_dir", None)
    if run_dir is None:
        return
    if summary is None or not best_params:
        return
    write_candidate_artefacts(
        run_dir=Path(run_dir),
        canonical_inp=Path(args.base_inp),
        patch_map=load_json(args.patch_map),
        best_params=best_params,
        summary=summary,
        extra_refs=extra_refs or {},
    )


def build_common_controls(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "base_inp": str(args.base_inp),
        "patch_map": str(args.patch_map),
        "observed": str(args.observed),
        "run_root": str(args.run_root),
        "swmm_node": args.swmm_node,
        "swmm_attr": args.swmm_attr,
        "objective": args.objective,
        "aggregate": args.aggregate,
        "obs_start": args.obs_start,
        "obs_end": args.obs_end,
        "dry_run": bool(args.dry_run),
    }


def emit_payload(args: argparse.Namespace, payload: dict) -> None:
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    ranking_table = payload.get("ranking_table")
    if args.ranking_json and isinstance(ranking_table, list):
        args.ranking_json.parent.mkdir(parents=True, exist_ok=True)
        args.ranking_json.write_text(json.dumps(ranking_table, indent=2), encoding="utf-8")

    if args.print_ranking and isinstance(ranking_table, list):
        print(
            format_ranking_text(ranking_table, objective=args.objective, top_n=args.ranking_top),
            file=sys.stderr,
        )

    print(json.dumps(payload, indent=2))


def cmd_sensitivity(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    observed = load_observed_series(
        args.observed,
        args.timestamp_col,
        args.flow_col,
        args.time_format,
        args.obs_start,
        args.obs_end,
    )

    trials = [ensure_named_trial(t, i + 1) for i, t in enumerate(ensure_param_sets(load_json(args.parameter_sets)))]
    results = evaluate_trials(
        args.base_inp,
        patch_map,
        trials,
        observed,
        args.run_root,
        args.swmm_node,
        args.swmm_attr,
        args.objective,
        args.aggregate,
        args.obs_start,
        args.obs_end,
        args.dry_run,
    )
    ranked = rank_results(results)
    ranking_table = build_ranking_table(ranked)

    payload = {
        "mode": "sensitivity",
        "objective": args.objective,
        "controls": build_common_controls(args),
        "status_counts": summarize_status_counts(ranked),
        "ranking_table": ranking_table,
        "results": ranked,
    }
    emit_payload(args, payload)


def cmd_calibrate(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    observed = load_observed_series(
        args.observed,
        args.timestamp_col,
        args.flow_col,
        args.time_format,
        args.obs_start,
        args.obs_end,
    )

    trials = [ensure_named_trial(t, i + 1) for i, t in enumerate(ensure_param_sets(load_json(args.parameter_sets)))]
    results = evaluate_trials(
        args.base_inp,
        patch_map,
        trials,
        observed,
        args.run_root,
        args.swmm_node,
        args.swmm_attr,
        args.objective,
        args.aggregate,
        args.obs_start,
        args.obs_end,
        args.dry_run,
    )
    ranked = rank_results(results)
    ranking_table = build_ranking_table(ranked)
    best = pick_best_result(ranked)

    payload = {
        "mode": "calibrate",
        "objective": args.objective,
        "controls": build_common_controls(args),
        "status_counts": summarize_status_counts(ranked),
        "ranking_table": ranking_table,
        "best": best,
        "results": ranked,
    }

    if args.best_params_out and best:
        args.best_params_out.parent.mkdir(parents=True, exist_ok=True)
        args.best_params_out.write_text(json.dumps(best["params"], indent=2), encoding="utf-8")

    summary_for_candidate = build_candidate_summary_from_best(
        best,
        strategy="calibrate",
        iterations=len(trials),
        observed=observed,
    )
    emit_candidate_artefacts(
        args,
        summary=summary_for_candidate,
        best_params=(best["params"] if best else None),
    )
    emit_payload(args, payload)


def cmd_validate(args: argparse.Namespace) -> None:
    patch_map = load_json(args.patch_map)
    params_obj = load_json(args.best_params)
    observed = load_observed_series(
        args.observed,
        args.timestamp_col,
        args.flow_col,
        args.time_format,
        args.obs_start,
        args.obs_end,
    )

    trial = {"name": args.trial_name, "params": params_obj}
    result = evaluate_trial(
        args.base_inp,
        patch_map,
        trial,
        observed,
        args.run_root,
        args.swmm_node,
        args.swmm_attr,
        args.objective,
        args.aggregate,
        args.obs_start,
        args.obs_end,
        dry_run=args.dry_run,
    )

    ranking_table = build_ranking_table([result])
    payload = {
        "mode": "validate",
        "objective": args.objective,
        "controls": build_common_controls(args),
        "status_counts": summarize_status_counts([result]),
        "ranking_table": ranking_table,
        "result": result,
    }
    emit_payload(args, payload)


def _cmd_search_dream_zs(
    args: argparse.Namespace,
    patch_map: dict,
    bounds: dict[str, ParamBound],
    observed: pd.DataFrame,
) -> None:
    """DREAM-ZS branch of search; depends on the optional `spotpy` package."""

    try:
        from dream_zs import DreamZsConfig, run_dream_zs  # local import: spotpy only needed here
    except ImportError as exc:  # pragma: no cover - defensive
        raise SystemExit(
            "DREAM-ZS strategy requires the optional 'spotpy' dependency. "
            "Install it with `pip install spotpy`.\n"
            f"Underlying error: {exc}"
        ) from exc

    if args.objective != "kge":
        raise SystemExit(
            "--strategy dream-zs currently requires --objective kge "
            f"(got --objective {args.objective}). "
            "The DREAM-ZS likelihood is defined on (1 - KGE)."
        )
    if args.dream_chains < 2:
        raise SystemExit("--dream-chains must be >= 2 for a Gelman-Rubin Rhat check.")

    run_root = Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    output_dir = (
        Path(args.dream_output_dir)
        if args.dream_output_dir is not None
        else summary_path.parent
    )

    def _runner(inp: Path, trial_dir: Path):
        return run_swmm(inp, trial_dir)

    def _extract(out_path: Path) -> pd.DataFrame:
        return extract_simulated_series(
            out_path,
            swmm_node=args.swmm_node,
            swmm_attr=args.swmm_attr,
            aggregate=args.aggregate,
        )

    config = DreamZsConfig(
        base_inp=Path(args.base_inp),
        patch_map=patch_map,
        observed=observed,
        run_root=run_root,
        swmm_node=args.swmm_node,
        swmm_attr=args.swmm_attr,
        aggregate=args.aggregate,
        obs_start=args.obs_start,
        obs_end=args.obs_end,
        bounds=bounds,
        iterations=int(args.iterations),
        seed=int(args.seed),
        n_chains=int(args.dream_chains),
        sigma=float(args.dream_sigma),
        rhat_threshold=float(args.dream_rhat_threshold),
        output_dir=output_dir,
        swmm_runner=_runner,
        extract_series=_extract,
        runs_after_convergence=int(args.dream_runs_after_convergence),
    )

    result = run_dream_zs(config)
    summary = result["summary"]
    summary["controls"] = {
        **build_common_controls(args),
        "search_space": str(args.search_space),
        "search_strategy": args.strategy,
        "seed": args.seed,
        "iterations": args.iterations,
        "dream_chains": args.dream_chains,
        "dream_sigma": args.dream_sigma,
        "dream_rhat_threshold": args.dream_rhat_threshold,
        "dream_output_dir": str(output_dir),
        "parsed_search_space": serialize_bounds(bounds),
    }

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.best_params_out:
        Path(args.best_params_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.best_params_out).write_text(
            json.dumps(result["best_params"], indent=2),
            encoding="utf-8",
        )
    else:
        # The acceptance criteria call for best_params.json under the audit
        # directory regardless of --best-params-out; mirror it there when no
        # explicit path was supplied.
        (output_dir / "best_params.json").write_text(
            json.dumps(result["best_params"], indent=2),
            encoding="utf-8",
        )
    dream_extra_refs = {
        "convergence_csv": Path(summary["convergence_trace_ref"]).name,
        "posterior_samples_csv": Path(result["posterior_samples_csv"]).name,
        "posterior_correlation_png": Path(result["correlation_png"]).name,
    }
    emit_candidate_artefacts(
        args,
        summary=summary,
        best_params=result.get("best_params"),
        extra_refs=dream_extra_refs,
    )
    print(json.dumps(summary, indent=2))


def _cmd_search_sceua(
    args: argparse.Namespace,
    patch_map: dict,
    bounds: dict[str, ParamBound],
    observed: pd.DataFrame,
) -> None:
    """SCE-UA branch of search; depends on the optional `spotpy` package."""

    try:
        from sceua import SceuaConfig, run_sceua  # local import: spotpy only needed here
    except ImportError as exc:  # pragma: no cover - defensive
        raise SystemExit(
            "SCE-UA strategy requires the optional 'spotpy' dependency. "
            "Install it with `pip install spotpy`.\n"
            f"Underlying error: {exc}"
        ) from exc

    if args.objective != "kge":
        # SCE-UA is wired to minimise (1 - KGE); make this explicit at the CLI to
        # avoid silent objective drift. Users who want NSE / RMSE optimisation
        # should keep using the random / lhs / adaptive strategies for now.
        raise SystemExit(
            "--strategy sceua currently requires --objective kge "
            f"(got --objective {args.objective}). "
            "Other objectives are tracked in issue #53 (DREAM-ZS) and follow-ups."
        )

    run_root = Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    convergence_csv = (
        Path(args.convergence_csv)
        if args.convergence_csv is not None
        else summary_path.parent / "convergence.csv"
    )

    def _runner(inp: Path, trial_dir: Path):
        return run_swmm(inp, trial_dir)

    def _extract(out_path: Path) -> pd.DataFrame:
        return extract_simulated_series(
            out_path,
            swmm_node=args.swmm_node,
            swmm_attr=args.swmm_attr,
            aggregate=args.aggregate,
        )

    config = SceuaConfig(
        base_inp=Path(args.base_inp),
        patch_map=patch_map,
        observed=observed,
        run_root=run_root,
        swmm_node=args.swmm_node,
        swmm_attr=args.swmm_attr,
        aggregate=args.aggregate,
        obs_start=args.obs_start,
        obs_end=args.obs_end,
        bounds=bounds,
        iterations=int(args.iterations),
        seed=int(args.seed),
        ngs=int(args.sceua_ngs),
        convergence_csv=convergence_csv,
        swmm_runner=_runner,
        extract_series=_extract,
    )

    result = run_sceua(config)
    summary = result["summary"]
    # Add `controls` block so the SCE-UA summary remains comparable to the other
    # strategies' top-level CLI payloads.
    summary["controls"] = {
        **build_common_controls(args),
        "search_space": str(args.search_space),
        "search_strategy": args.strategy,
        "seed": args.seed,
        "iterations": args.iterations,
        "sceua_ngs": args.sceua_ngs,
        "parsed_search_space": serialize_bounds(bounds),
    }

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.best_params_out:
        Path(args.best_params_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.best_params_out).write_text(
            json.dumps(result["best_params"], indent=2),
            encoding="utf-8",
        )
    emit_candidate_artefacts(
        args,
        summary=summary,
        best_params=result.get("best_params"),
        extra_refs={"convergence_csv": Path(summary["convergence_trace_ref"]).name},
    )
    print(json.dumps(summary, indent=2))


def cmd_search(args: argparse.Namespace) -> None:
    if args.iterations < 1:
        raise ValueError("--iterations must be >= 1")
    if args.strategy == "adaptive" and args.rounds < 2:
        raise ValueError("--rounds must be >= 2 for adaptive strategy")
    if args.strategy not in {"adaptive"} and args.rounds != 1:
        raise ValueError("--rounds can only be >1 when --strategy adaptive")
    if not (0 < args.elite_fraction <= 1):
        raise ValueError("--elite-fraction must be in (0, 1]")
    if not (0 <= args.refine_margin <= 1):
        raise ValueError("--refine-margin must be in [0, 1]")
    if not (0 < args.min_span_fraction <= 1):
        raise ValueError("--min-span-fraction must be in (0, 1]")

    patch_map = load_json(args.patch_map)
    bounds = parse_search_space(load_json(args.search_space))
    observed = load_observed_series(
        args.observed,
        args.timestamp_col,
        args.flow_col,
        args.time_format,
        args.obs_start,
        args.obs_end,
    )

    if args.strategy == "sceua":
        _cmd_search_sceua(args, patch_map, bounds, observed)
        return
    if args.strategy == "dream-zs":
        _cmd_search_dream_zs(args, patch_map, bounds, observed)
        return

    rng = random.Random(args.seed)
    trial_counter = 1
    all_results: list[dict] = []
    round_summaries: list[dict] = []

    def sample_once(active_bounds: dict[str, ParamBound], strategy: str) -> list[dict[str, float | int]]:
        if strategy == "random":
            return sample_random_params(active_bounds, args.iterations, rng)
        return sample_lhs_params(active_bounds, args.iterations, rng)

    if args.strategy in {"random", "lhs"}:
        samples = sample_once(bounds, args.strategy)
        trials = build_search_trials(
            samples=samples,
            trial_prefix="search",
            start_index=trial_counter,
            strategy=args.strategy,
            round_index=1,
        )
        trial_counter += len(trials)

        round_results = evaluate_trials(
            args.base_inp,
            patch_map,
            trials,
            observed,
            args.run_root,
            args.swmm_node,
            args.swmm_attr,
            args.objective,
            args.aggregate,
            args.obs_start,
            args.obs_end,
            args.dry_run,
        )
        all_results.extend(round_results)

        round_ranked = rank_results(round_results)
        round_best = pick_best_result(round_ranked)
        round_summaries.append(
            {
                "round": 1,
                "sampling_strategy": args.strategy,
                "trial_count": len(round_results),
                "status_counts": summarize_status_counts(round_results),
                "best_trial": round_best["trial"] if round_best else None,
                "best_objective": round_best["objective"] if round_best else None,
                "bounds_before": serialize_bounds(bounds),
                "bounds_after": serialize_bounds(bounds),
            }
        )

    else:
        active_bounds = dict(bounds)
        for round_idx in range(1, args.rounds + 1):
            bounds_before = serialize_bounds(active_bounds)
            samples = sample_once(active_bounds, "lhs")
            trials = build_search_trials(
                samples=samples,
                trial_prefix="search",
                start_index=trial_counter,
                strategy="adaptive_lhs",
                round_index=round_idx,
            )
            trial_counter += len(trials)

            round_results = evaluate_trials(
                args.base_inp,
                patch_map,
                trials,
                observed,
                args.run_root,
                args.swmm_node,
                args.swmm_attr,
                args.objective,
                args.aggregate,
                args.obs_start,
                args.obs_end,
                args.dry_run,
            )
            all_results.extend(round_results)

            round_ranked = rank_results(round_results)
            valid_round = [
                rec
                for rec in round_ranked
                if rec.get("status") == "ok" and is_finite_number(rec.get("objective"))
            ]
            elite_count = int(math.ceil(len(valid_round) * args.elite_fraction)) if valid_round else 0
            elite_count = max(1, elite_count) if valid_round else 0
            elite = valid_round[:elite_count]

            if elite and round_idx < args.rounds:
                active_bounds = refine_bounds_from_elite(
                    current_bounds=active_bounds,
                    global_bounds=bounds,
                    elite_results=elite,
                    margin_fraction=args.refine_margin,
                    min_span_fraction=args.min_span_fraction,
                )

            round_best = pick_best_result(round_ranked)
            round_summaries.append(
                {
                    "round": round_idx,
                    "sampling_strategy": "lhs",
                    "trial_count": len(round_results),
                    "status_counts": summarize_status_counts(round_results),
                    "elite_count": elite_count,
                    "best_trial": round_best["trial"] if round_best else None,
                    "best_objective": round_best["objective"] if round_best else None,
                    "bounds_before": bounds_before,
                    "bounds_after": serialize_bounds(active_bounds),
                }
            )

    ranked = rank_results(all_results)
    best = pick_best_result(ranked)
    ranking_table = build_ranking_table(ranked)

    payload = {
        "mode": "search",
        "objective": args.objective,
        "controls": {
            **build_common_controls(args),
            "search_space": str(args.search_space),
            "search_strategy": args.strategy,
            "seed": args.seed,
            "iterations": args.iterations,
            "rounds": args.rounds,
            "elite_fraction": args.elite_fraction,
            "refine_margin": args.refine_margin,
            "min_span_fraction": args.min_span_fraction,
            "parsed_search_space": serialize_bounds(bounds),
        },
        "status_counts": summarize_status_counts(ranked),
        "rounds": round_summaries,
        "ranking_table": ranking_table,
        "best": best,
        "results": ranked,
    }

    if args.best_params_out and best:
        args.best_params_out.parent.mkdir(parents=True, exist_ok=True)
        args.best_params_out.write_text(json.dumps(best["params"], indent=2), encoding="utf-8")

    summary_for_candidate = build_candidate_summary_from_best(
        best,
        strategy=args.strategy,
        iterations=int(args.iterations) * max(1, int(args.rounds)),
        observed=observed,
    )
    emit_candidate_artefacts(
        args,
        summary=summary_for_candidate,
        best_params=(best["params"] if best else None),
    )
    emit_payload(args, payload)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser, include_param_sets: bool = True) -> None:
        sp.add_argument("--base-inp", required=True, type=Path)
        sp.add_argument("--patch-map", required=True, type=Path)
        if include_param_sets:
            sp.add_argument("--parameter-sets", required=True, type=Path)
        sp.add_argument("--observed", required=True, type=Path)
        sp.add_argument("--run-root", required=True, type=Path)
        sp.add_argument("--swmm-node", default="O1")
        sp.add_argument("--swmm-attr", default="Total_inflow")
        sp.add_argument(
            "--objective",
            default="nse",
            choices=["nse", "kge", "rmse", "bias", "peak_flow_error", "peak_timing_error"],
        )
        sp.add_argument("--aggregate", choices=["none", "daily_mean"], default="none")
        sp.add_argument("--timestamp-col", default=None)
        sp.add_argument("--flow-col", default=None)
        sp.add_argument("--time-format", default=None)
        sp.add_argument("--obs-start", default=None, help="Inclusive observed-series window start, e.g. 1984-05-23")
        sp.add_argument("--obs-end", default=None, help="Inclusive observed-series window end, e.g. 1984-05-28")
        sp.add_argument("--summary-json", required=True, type=Path)
        sp.add_argument("--ranking-json", default=None, type=Path)
        sp.add_argument("--print-ranking", action="store_true")
        sp.add_argument("--ranking-top", default=10, type=int)
        sp.add_argument("--dry-run", action="store_true")

    sp_s = sub.add_parser("sensitivity")
    add_common(sp_s, include_param_sets=True)
    sp_s.set_defaults(func=cmd_sensitivity)

    sp_c = sub.add_parser("calibrate")
    add_common(sp_c, include_param_sets=True)
    sp_c.add_argument("--best-params-out", default=None, type=Path)
    sp_c.add_argument(
        "--candidate-run-dir",
        type=Path,
        default=None,
        help=(
            "Run directory to receive the candidate-handover artefacts "
            "(candidate_calibration.json, candidate_inp_patch.json, "
            "calibration_report.md) in <run>/09_audit/. Required by "
            "`aiswmm calibration accept` (PRD-Z, issue #54)."
        ),
    )
    sp_c.set_defaults(func=cmd_calibrate)

    sp_v = sub.add_parser("validate")
    add_common(sp_v, include_param_sets=False)
    sp_v.add_argument("--best-params", required=True, type=Path)
    sp_v.add_argument("--trial-name", default="validation")
    sp_v.set_defaults(func=cmd_validate)

    sp_search = sub.add_parser("search")
    add_common(sp_search, include_param_sets=False)
    sp_search.add_argument("--search-space", required=True, type=Path)
    sp_search.add_argument(
        "--strategy",
        choices=["random", "lhs", "adaptive", "sceua", "dream-zs"],
        default="lhs",
    )
    sp_search.add_argument("--iterations", type=int, default=12, help="Trial count per round")
    sp_search.add_argument("--rounds", type=int, default=1, help="Number of rounds (adaptive requires >=2)")
    sp_search.add_argument("--seed", type=int, default=42)
    sp_search.add_argument("--elite-fraction", type=float, default=0.3)
    sp_search.add_argument("--refine-margin", type=float, default=0.1)
    sp_search.add_argument("--min-span-fraction", type=float, default=0.1)
    sp_search.add_argument("--best-params-out", default=None, type=Path)
    sp_search.add_argument(
        "--convergence-csv",
        default=None,
        type=Path,
        help="Where SCE-UA writes the per-iteration KGE trace (default: alongside summary).",
    )
    sp_search.add_argument(
        "--sceua-ngs",
        type=int,
        default=4,
        help="Number of complexes for SCE-UA (default 4). Spotpy recommends 2*p+1 minimum.",
    )
    sp_search.add_argument(
        "--dream-chains",
        type=int,
        default=4,
        help="Number of MCMC chains for DREAM-ZS (default 4). >=2 required for Rhat.",
    )
    sp_search.add_argument(
        "--dream-sigma",
        type=float,
        default=0.1,
        help="Likelihood width sigma on (1-KGE) for DREAM-ZS (default 0.1).",
    )
    sp_search.add_argument(
        "--dream-rhat-threshold",
        type=float,
        default=1.2,
        help="Gelman-Rubin Rhat convergence threshold for DREAM-ZS (default 1.2).",
    )
    sp_search.add_argument(
        "--dream-output-dir",
        type=Path,
        default=None,
        help=(
            "Audit directory for DREAM-ZS artefacts (posterior_samples.csv, "
            "chain_convergence.json, posterior_<param>.png, posterior_correlation.png). "
            "Defaults to the parent of --summary-json."
        ),
    )
    sp_search.add_argument(
        "--dream-runs-after-convergence",
        type=int,
        default=50,
        help="Extra DREAM-ZS samples after Gelman-Rubin convergence (default 50).",
    )
    sp_search.add_argument(
        "--candidate-run-dir",
        type=Path,
        default=None,
        help=(
            "Run directory to receive the candidate-handover artefacts "
            "(candidate_calibration.json, candidate_inp_patch.json, "
            "calibration_report.md) in <run>/09_audit/. Required by "
            "`aiswmm calibration accept` (PRD-Z, issue #54)."
        ),
    )
    sp_search.set_defaults(func=cmd_search)

    return ap


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
