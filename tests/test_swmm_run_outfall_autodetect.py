"""F6: swmm_run should auto-detect outfall from .inp [OUTFALLS] when node is omitted."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "skills/swmm-end-to-end/scripts/mcp_stdio_call.py"


MINIMAL_INP = """[TITLE]
F6 outfall autodetect smoke

[OPTIONS]
FLOW_UNITS           CMS
INFILTRATION         GREEN_AMPT
FLOW_ROUTING         KINWAVE
START_DATE           01/01/2024
START_TIME           00:00:00
END_DATE             01/01/2024
END_TIME             01:00:00
REPORT_STEP          00:15:00
WET_STEP             00:15:00
DRY_STEP             24:00:00
ROUTING_STEP         00:05:00

[JUNCTIONS]
;;Name           Elevation      MaxDepth       InitDepth      SurDepth       Aponded
J1               0              1.0            0              0              0

[OUTFALLS]
;;Name           Elevation      Type     Stage Data    Gated  Route To
OF_FROM_INP      0              FREE                   NO

[CONDUITS]
;;Name           From Node      To Node        Length     Roughness  InOffset   OutOffset  InitFlow   MaxFlow
C1               J1             OF_FROM_INP    10         0.013      0          0          0          0

[XSECTIONS]
;;Link           Shape          Geom1      Geom2      Geom3      Geom4      Barrels
C1               CIRCULAR       0.3        0          0          0          1

[COORDINATES]
;;Node           X-Coord        Y-Coord
J1               0              0
OF_FROM_INP      10             0

[VERTICES]
;;Link           X-Coord        Y-Coord

[REPORT]
INPUT      NO
CONTROLS   NO
SUBCATCHMENTS ALL
NODES ALL
LINKS ALL
"""


def test_swmm_run_autodetects_outfall_when_node_omitted(tmp_path: Path) -> None:
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(MINIMAL_INP, encoding="utf-8")
    run_dir = tmp_path / "runner"
    response = tmp_path / "response.json"

    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-runner",
            "--tool", "swmm_run",
            "--arguments-json", json.dumps({"inp": str(inp_path), "runDir": str(run_dir)}),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )

    manifest_path = run_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # The manifest peak record must target the auto-detected outfall name from [OUTFALLS],
    # not the previous misleading default "O1".
    assert manifest["metrics"]["peak"]["node"] == "OF_FROM_INP"


def test_swmm_run_respects_explicit_node_override(tmp_path: Path) -> None:
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(MINIMAL_INP, encoding="utf-8")
    run_dir = tmp_path / "runner"
    response = tmp_path / "response.json"

    subprocess.run(
        [
            sys.executable, str(HARNESS),
            "--server-dir", "mcp/swmm-runner",
            "--tool", "swmm_run",
            "--arguments-json", json.dumps({"inp": str(inp_path), "runDir": str(run_dir), "node": "J1"}),
            "--out-response", str(response),
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["metrics"]["peak"]["node"] == "J1"
