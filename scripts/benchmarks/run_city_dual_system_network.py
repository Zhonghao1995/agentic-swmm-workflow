#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "skills/swmm-network/examples/city-dual-system"
RUN_DIR = REPO_ROOT / "runs/benchmarks/city-dual-system-network"


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPO_ROOT, check=True, capture_output=True, text=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> None:
    python = sys.executable
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    paths = {
        "network": RUN_DIR / "04_network/network.json",
        "network_qa": RUN_DIR / "04_network/network_qa.json",
        "landuse": RUN_DIR / "02_params/landuse.json",
        "soil": RUN_DIR / "02_params/soil.json",
        "params": RUN_DIR / "02_params/merged_params.json",
        "rainfall_json": RUN_DIR / "03_climate/rainfall.json",
        "timeseries": RUN_DIR / "03_climate/timeseries.txt",
        "raingage_json": RUN_DIR / "03_climate/raingage.json",
        "raingage_txt": RUN_DIR / "03_climate/raingage.txt",
        "inp": RUN_DIR / "05_builder/model.inp",
        "builder_manifest": RUN_DIR / "05_builder/manifest.json",
        "runner_dir": RUN_DIR / "06_runner",
    }

    stages = []
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-network/scripts/city_network_adapter.py",
                "--pipes-csv",
                str(EXAMPLE_DIR / "pipes.csv"),
                "--outfalls-csv",
                str(EXAMPLE_DIR / "outfalls.csv"),
                "--mapping-json",
                str(EXAMPLE_DIR / "mapping.json"),
                "--out",
                str(paths["network"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-network/scripts/network_qa.py",
                str(paths["network"]),
                "--report-json",
                str(paths["network_qa"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-params/scripts/landuse_to_swmm_params.py",
                "--input",
                str(EXAMPLE_DIR / "landuse.csv"),
                "--output",
                str(paths["landuse"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-params/scripts/soil_to_greenampt.py",
                "--input",
                str(EXAMPLE_DIR / "soil.csv"),
                "--output",
                str(paths["soil"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-params/scripts/merge_swmm_params.py",
                "--landuse-json",
                str(paths["landuse"]),
                "--soil-json",
                str(paths["soil"]),
                "--output",
                str(paths["params"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-climate/scripts/format_rainfall.py",
                "--input",
                "skills/swmm-climate/examples/rainfall_event.csv",
                "--out-json",
                str(paths["rainfall_json"]),
                "--out-timeseries",
                str(paths["timeseries"]),
                "--series-name",
                "TS_CITY_DEMO",
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-climate/scripts/build_raingage_section.py",
                "--rainfall-json",
                str(paths["rainfall_json"]),
                "--gage-id",
                "RG1",
                "--interval-min",
                "5",
                "--out-text",
                str(paths["raingage_txt"]),
                "--out-json",
                str(paths["raingage_json"]),
            ]
        )
    )
    stages.append(
        run_cmd(
            [
                python,
                "skills/swmm-builder/scripts/build_swmm_inp.py",
                "--subcatchments-csv",
                str(EXAMPLE_DIR / "subcatchments.csv"),
                "--params-json",
                str(paths["params"]),
                "--network-json",
                str(paths["network"]),
                "--rainfall-json",
                str(paths["rainfall_json"]),
                "--raingage-json",
                str(paths["raingage_json"]),
                "--config-json",
                "skills/swmm-builder/examples/options_config.json",
                "--out-inp",
                str(paths["inp"]),
                "--out-manifest",
                str(paths["builder_manifest"]),
            ]
        )
    )

    runner = {"attempted": False, "ok": None, "reason": None}
    if shutil.which("swmm5"):
        run_cmd(
            [
                python,
                "skills/swmm-runner/scripts/swmm_runner.py",
                "run",
                "--inp",
                str(paths["inp"]),
                "--run-dir",
                str(paths["runner_dir"]),
                "--node",
                "OF_MAIN",
                "--rpt-name",
                "model.rpt",
                "--out-name",
                "model.out",
            ]
        )
        runner = {"attempted": True, "ok": True, "reason": None}
    else:
        runner = {"attempted": False, "ok": None, "reason": "swmm5 not found; build and QA completed"}

    network = json.loads(paths["network"].read_text(encoding="utf-8"))
    network_qa = json.loads(paths["network_qa"].read_text(encoding="utf-8"))
    builder_manifest = json.loads(paths["builder_manifest"].read_text(encoding="utf-8"))
    summary = {
        "ok": bool(network_qa.get("ok")) and bool(builder_manifest.get("ok")),
        "benchmark": "city-dual-system-network",
        "run_dir": str(RUN_DIR.relative_to(REPO_ROOT)),
        "network_counts": network["meta"]["counts"],
        "system_layers": network["meta"]["system_layers"],
        "network_qa": {
            "ok": network_qa.get("ok"),
            "issue_count": network_qa.get("issue_count"),
            "summary": network_qa.get("summary"),
        },
        "builder_counts": builder_manifest.get("counts"),
        "runner": runner,
        "evidence_boundary": "Structured city asset CSV adapter benchmark; not CAD drawing recognition or fully coupled dual drainage hydraulics.",
    }
    write_json(RUN_DIR / "manifest.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
