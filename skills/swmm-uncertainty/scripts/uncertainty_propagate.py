#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
CALIBRATION_SCRIPTS = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
RUNNER_SCRIPT = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"
if str(CALIBRATION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_SCRIPTS))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fuzzy_membership import build_alpha_intervals, read_baseline_values, resolve_fuzzy_space, write_json  # noqa: E402
from inp_patch import patch_inp_text  # noqa: E402
from sampling import generate_parameter_sets  # noqa: E402


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(results), "ok": 0, "failed": 0, "invalid": 0, "dry_run": 0, "other": 0}
    for rec in results:
        status = str(rec.get("status", "other"))
        counts[status if status in counts else "other"] += 1
    return counts


def numeric_envelope(values: list[Any]) -> dict[str, Any]:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(nums),
        "min": min(nums),
        "max": max(nums),
        "mean": sum(nums) / len(nums),
    }


def run_runner(inp: Path, run_dir: Path, node: str) -> tuple[int, dict[str, Any], str, str]:
    cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "run",
        "--inp",
        str(inp),
        "--run-dir",
        str(run_dir),
        "--node",
        node,
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    parsed: dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            obj = json.loads(proc.stdout)
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            parsed = {}
    return proc.returncode, parsed, proc.stdout, proc.stderr


def evaluate_trial(
    *,
    base_inp_text: str,
    patch_map: dict[str, Any],
    trial: dict[str, Any],
    trials_dir: Path,
    swmm_node: str,
    dry_run: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    trial_dir = trials_dir / trial["name"]
    trial_dir.mkdir(parents=True, exist_ok=True)
    inp_path = trial_dir / "model.inp"
    result: dict[str, Any] = {
        "trial": trial["name"],
        "params": trial["params"],
        "metadata": trial.get("metadata", {}),
        "run_dir": str(trial_dir),
        "status": "pending",
        "reason_code": None,
        "reason_detail": None,
        "metrics": {},
        "files": {"inp": str(inp_path)},
        "started_at_utc": utc_now(),
        "dry_run": dry_run,
    }

    try:
        inp_path.write_text(patch_inp_text(base_inp_text, patch_map, trial["params"]), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.update(
            {
                "status": "invalid",
                "reason_code": "patch_failed",
                "reason_detail": str(exc),
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        )
        return result

    if dry_run:
        result.update(
            {
                "status": "dry_run",
                "reason_code": "dry_run_enabled",
                "reason_detail": "Trial INP was generated but SWMM was not executed.",
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        )
        return result

    if not shutil.which("swmm5"):
        result.update(
            {
                "status": "failed",
                "reason_code": "swmm_binary_missing",
                "reason_detail": "swmm5 executable was not found on PATH.",
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        )
        return result

    rc, manifest, stdout, stderr = run_runner(inp_path, trial_dir, swmm_node)
    result["return_code"] = rc
    result["files"].update(
        {
            "rpt": str(trial_dir / "model.rpt"),
            "out": str(trial_dir / "model.out"),
            "manifest": str(trial_dir / "manifest.json"),
            "stdout": str(trial_dir / "stdout.txt"),
            "stderr": str(trial_dir / "stderr.txt"),
        }
    )
    if not (trial_dir / "stdout.txt").exists():
        (trial_dir / "uncertainty_runner_stdout.txt").write_text(stdout, encoding="utf-8", errors="ignore")
    if not (trial_dir / "stderr.txt").exists():
        (trial_dir / "uncertainty_runner_stderr.txt").write_text(stderr, encoding="utf-8", errors="ignore")

    if rc != 0:
        result.update(
            {
                "status": "failed",
                "reason_code": "swmm_execution_failed",
                "reason_detail": f"SWMM runner returned non-zero exit code {rc}.",
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        )
        return result

    metrics = manifest.get("metrics", {}) if isinstance(manifest, dict) else {}
    result["metrics"] = {
        "peak": metrics.get("peak"),
        "continuity": metrics.get("continuity"),
    }
    result.update(
        {
            "status": "ok",
            "reason_code": "ok",
            "reason_detail": None,
            "elapsed_seconds": round(time.perf_counter() - started, 6),
        }
    )
    return result


def summarize_by_alpha(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in results:
        alpha = (rec.get("metadata") or {}).get("alpha")
        grouped[f"{float(alpha):.2f}" if alpha is not None else "unknown"].append(rec)

    out: dict[str, Any] = {}
    for alpha, records in sorted(grouped.items(), key=lambda item: item[0]):
        ok_records = [rec for rec in records if rec.get("status") == "ok"]
        peak_values = [
            ((rec.get("metrics") or {}).get("peak") or {}).get("peak")
            for rec in ok_records
        ]
        runoff_continuity = [
            (((rec.get("metrics") or {}).get("continuity") or {}).get("continuity_error_percent") or {}).get(
                "runoff_quantity"
            )
            for rec in ok_records
        ]
        routing_continuity = [
            (((rec.get("metrics") or {}).get("continuity") or {}).get("continuity_error_percent") or {}).get(
                "flow_routing"
            )
            for rec in ok_records
        ]
        out[alpha] = {
            "status_counts": status_counts(records),
            "peak_flow": numeric_envelope(peak_values),
            "runoff_continuity_error_percent": numeric_envelope(runoff_continuity),
            "flow_routing_continuity_error_percent": numeric_envelope(routing_continuity),
        }
    return out


def parse_config(config_path: Path) -> dict[str, Any]:
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("Uncertainty config must be a JSON object")
    config.setdefault("alpha_levels", [0.0, 0.25, 0.5, 0.75, 1.0])
    config.setdefault("sampling", {})
    config["sampling"].setdefault("method", "lhs")
    config["sampling"].setdefault("samples_per_alpha", 20)
    config["sampling"].setdefault("seed", 42)
    config.setdefault("outputs", {})
    config["outputs"].setdefault("swmm_node", "O1")
    return config


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Propagate fuzzy SWMM parameter uncertainty through alpha-cut samples.")
    ap.add_argument("--base-inp", required=True, type=Path)
    ap.add_argument("--patch-map", required=True, type=Path)
    ap.add_argument("--fuzzy-space", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--summary-json", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    config = parse_config(args.config)
    patch_map = load_json(args.patch_map)
    fuzzy_space = load_json(args.fuzzy_space)
    base_inp_text = args.base_inp.read_text(encoding="utf-8", errors="ignore")

    run_root = args.run_root
    run_root.mkdir(parents=True, exist_ok=True)
    trials_dir = run_root / "trials"

    baseline_values = read_baseline_values(args.base_inp, patch_map)
    resolved = resolve_fuzzy_space(fuzzy_space, baseline_values)
    alpha_levels = [float(alpha) for alpha in config["alpha_levels"]]
    alpha_intervals = build_alpha_intervals(resolved, alpha_levels)

    sampling_cfg = config["sampling"]
    trials = generate_parameter_sets(
        alpha_intervals,
        method=str(sampling_cfg["method"]),
        samples_per_alpha=int(sampling_cfg["samples_per_alpha"]),
        seed=int(sampling_cfg["seed"]),
    )

    resolved_json = {"parameters": {name: param.to_dict() for name, param in resolved.items()}}
    parameter_sets_json = {"parameter_sets": trials}

    write_json(run_root / "fuzzy_space.resolved.json", resolved_json)
    write_json(run_root / "alpha_intervals.json", alpha_intervals)
    write_json(run_root / "parameter_sets.json", parameter_sets_json)

    swmm_node = str(config["outputs"].get("swmm_node", "O1"))
    results = [
        evaluate_trial(
            base_inp_text=base_inp_text,
            patch_map=patch_map,
            trial=trial,
            trials_dir=trials_dir,
            swmm_node=swmm_node,
            dry_run=bool(args.dry_run),
        )
        for trial in trials
    ]

    payload = {
        "mode": "fuzzy_uncertainty_propagation",
        "created_at_utc": utc_now(),
        "controls": {
            "base_inp": str(args.base_inp),
            "patch_map": str(args.patch_map),
            "fuzzy_space": str(args.fuzzy_space),
            "config": str(args.config),
            "run_root": str(run_root),
            "dry_run": bool(args.dry_run),
            "swmm_node": swmm_node,
        },
        "sampling": {
            "method": sampling_cfg["method"],
            "samples_per_alpha": sampling_cfg["samples_per_alpha"],
            "seed": sampling_cfg["seed"],
            "trial_count": len(trials),
        },
        "baseline_values": {name: baseline_values[name] for name in resolved},
        "resolved_fuzzy_space": resolved_json,
        "alpha_intervals": alpha_intervals,
        "status_counts": status_counts(results),
        "alpha_summary": summarize_by_alpha(results),
        "results": results,
    }

    write_json(args.summary_json, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
