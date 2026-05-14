from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "skills/swmm-network/examples/city-dual-system"


def test_city_network_adapter_infers_dual_system_nodes(tmp_path: Path) -> None:
    out = tmp_path / "network.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "skills/swmm-network/scripts/city_network_adapter.py"),
            "--pipes-csv",
            str(EXAMPLE_DIR / "pipes.csv"),
            "--outfalls-csv",
            str(EXAMPLE_DIR / "outfalls.csv"),
            "--mapping-json",
            str(EXAMPLE_DIR / "mapping.json"),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(proc.stdout)
    network = json.loads(out.read_text(encoding="utf-8"))

    assert summary["ok"] is True
    assert summary["inferred_junctions"] == 4
    assert set(summary["system_layers"]) == {"major_surface", "minor_pipe"}
    assert len(network["conduits"]) == 4
    assert any(j["id"] == "J_AUTO_0p000_0p000" and j["inferred"] for j in network["junctions"])
    assert network["meta"]["dual_system_ready"] is True


def test_city_network_adapter_output_passes_network_qa(tmp_path: Path) -> None:
    network_json = tmp_path / "network.json"
    qa_json = tmp_path / "network_qa.json"
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "skills/swmm-network/scripts/city_network_adapter.py"),
            "--pipes-csv",
            str(EXAMPLE_DIR / "pipes.csv"),
            "--outfalls-csv",
            str(EXAMPLE_DIR / "outfalls.csv"),
            "--mapping-json",
            str(EXAMPLE_DIR / "mapping.json"),
            "--out",
            str(network_json),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "skills/swmm-network/scripts/network_qa.py"),
            str(network_json),
            "--report-json",
            str(qa_json),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    qa = json.loads(qa_json.read_text(encoding="utf-8"))

    assert qa["ok"] is True
    assert qa["issue_count"] == 0
    assert qa["summary"]["dual_system_ready"] is True
    assert qa["summary"]["inferred_junction_count"] == 4
