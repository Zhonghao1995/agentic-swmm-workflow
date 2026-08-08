"""network_qa outfall in-degree rules (maintenance sweep, 2026-08-08).

SWMM allows exactly one inlet link and zero outlet links per outfall
(engine ERROR 141; a system left without an acceptable outlet then dies
with ERROR 145). The structural QA missed this rule, which let the
city-dual-system benchmark's unrunnable reference network sail through
with ok=true while the benchmark harness hardcoded its runner verdict.
These tests pin the new rule via the script's CLI, and pin that the
repaired bundled example passes.
"""
from __future__ import annotations

import json
import subprocess
import sys

from agentic_swmm.utils.paths import repo_root

SCRIPT = repo_root() / "skills" / "swmm-network" / "scripts" / "network_qa.py"


def _qa(tmp_path, network: dict) -> dict:
    network_path = tmp_path / "network.json"
    report_path = tmp_path / "report.json"
    network_path.write_text(json.dumps(network), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(network_path), "--report-json", str(report_path)],
        capture_output=True,
        text=True,
        cwd=repo_root(),
    )
    assert report_path.is_file(), proc.stderr[-1000:]
    return json.loads(report_path.read_text(encoding="utf-8"))


def _network(pipes: list[tuple[str, str, str]]) -> dict:
    return {
        "meta": {"counts": {}, "system_layers": []},
        "junctions": [
            {"id": jid, "invert_elev": 100.0, "x": 0, "y": 0}
            for jid in {p[1] for p in pipes} | {p[2] for p in pipes}
            if not jid.startswith("OF_")
        ],
        "outfalls": [
            {"id": oid, "invert_elev": 98.0, "x": 10, "y": 0, "type": "FREE"}
            for oid in {p[1] for p in pipes} | {p[2] for p in pipes}
            if oid.startswith("OF_")
        ],
        "conduits": [
            {
                "id": cid,
                "from_node": fn,
                "to_node": tn,
                "length": 10.0,
                "roughness": 0.013,
                "xsection": {"shape": "CIRCULAR", "geom1": 0.5},
            }
            for cid, fn, tn in pipes
        ],
        "subcatchments": [],
    }


def test_two_inlets_on_one_outfall_is_an_error(tmp_path):
    report = _qa(
        tmp_path,
        _network([("P1", "J1", "OF_X"), ("P2", "J2", "OF_X")]),
    )
    kinds = {issue["code"] if "code" in issue else issue.get("kind") for issue in report["issues"]}
    flat = json.dumps(report["issues"])
    assert report["ok"] is False
    assert "outfall_multiple_inlets" in flat


def test_outfall_with_outgoing_link_is_an_error(tmp_path):
    report = _qa(
        tmp_path,
        _network([("P1", "J1", "OF_X"), ("P2", "OF_X", "J2")]),
    )
    assert report["ok"] is False
    assert "outfall_has_outlet" in json.dumps(report["issues"])


def test_one_inlet_per_outfall_stays_ok(tmp_path):
    report = _qa(
        tmp_path,
        _network([("P1", "J1", "OF_A"), ("P2", "J2", "OF_B")]),
    )
    assert "outfall_multiple_inlets" not in json.dumps(report["issues"])
    assert "outfall_has_outlet" not in json.dumps(report["issues"])
