"""Guard tests for the INP -> network_qa bridge (swmm-network improvement).

`skills/swmm-network/scripts/inp_to_network.py` converts a SWMM `.inp` into
the `network.json` shape so `network_qa.run_qa` can run its structural checks
on any model that emitted an INP — notably a SWMManywhere-synthesized network,
which produces an INP but no network.json. This unifies structural QA across
the real-data paths and the synth path.

Covers:
1. The converter produces the right shape (junctions/outfalls/conduits/xsection).
2. run_qa on a converted INP flags an isolated node and a no-outfall-path node
   (the two "orphan node" senses the user cares about).
3. A real SI model (saanich-b7) converts + QAs without crashing, with counts
   that match the INP.

Import pattern mirrors tests/test_design_review_engine.py: the swmm-network
scripts are standalone (stdlib only), loaded by adding their dir to sys.path.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = (
    Path(__file__).parent.parent / "skills" / "swmm-network" / "scripts"
)
_SAANICH_INP = (
    Path(__file__).parent.parent
    / "docs"
    / "framework-validation"
    / "saanich-b7-network-routed-20260513"
    / "model.inp"
)

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import inp_to_network as i2n  # noqa: E402
import network_qa as nq  # noqa: E402


# A tiny INP with three deliberate structural facts:
#   J1 -> J2 -> O1   : healthy path to the outfall
#   J3 -> J3B        : a stub that never reaches an outfall (no_outfall_path)
#   ORPHAN           : a junction with no conduit at all (isolated_node)
_TINY_INP = """\
[JUNCTIONS]
J1   100
J2   99
J3   98
J3B  97
ORPHAN 96

[OUTFALLS]
O1   90   FREE

[COORDINATES]
J1   0  0
J2   1  0
J3   2  0
J3B  3  0
ORPHAN 4 0
O1   5  0

[CONDUITS]
C1   J1   J2   50  0.013  0  0  0  0
C2   J2   O1   50  0.013  0  0  0  0
C3   J3   J3B  50  0.013  0  0  0  0

[XSECTIONS]
C1   CIRCULAR  0.3  0  0  0
C2   CIRCULAR  0.3  0  0  0
C3   CIRCULAR  0.3  0  0  0
"""


def test_converter_shape(tmp_path: Path):
    inp = tmp_path / "tiny.inp"
    inp.write_text(_TINY_INP)
    net = i2n.inp_to_network(inp)

    assert {j["id"] for j in net["junctions"]} == {"J1", "J2", "J3", "J3B", "ORPHAN"}
    assert [o["id"] for o in net["outfalls"]] == ["O1"]
    assert {c["id"] for c in net["conduits"]} == {"C1", "C2", "C3"}

    c1 = next(c for c in net["conduits"] if c["id"] == "C1")
    assert c1["from_node"] == "J1" and c1["to_node"] == "J2"
    assert c1["length"] == 50.0 and c1["roughness"] == 0.013
    assert c1["xsection"]["geom1"] == 0.3
    # Coordinates carried through so network_qa does not spuriously flag them.
    assert net["junctions"][0]["coordinates"] == {"x": 0.0, "y": 0.0}


def test_run_qa_flags_isolated_and_no_outfall_path(tmp_path: Path):
    inp = tmp_path / "tiny.inp"
    inp.write_text(_TINY_INP)
    report = nq.run_qa(i2n.inp_to_network(inp))

    codes = {(i["code"], i.get("id")) for i in report["issues"]}
    assert ("isolated_node", "ORPHAN") in codes
    assert ("no_outfall_path", "J3") in codes
    # No error-severity issues (coords present, xsections valid) → ok stays True.
    assert report["ok"] is True
    assert report["summary"]["conduit_count"] == 3
    assert report["summary"]["outfall_count"] == 1
    assert report["summary"]["junction_count"] == 5


def test_saanich_real_inp_converts_and_qas():
    net = i2n.inp_to_network(_SAANICH_INP)
    report = nq.run_qa(net)
    s = report["summary"]
    assert s["conduit_count"] > 0
    assert s["junction_count"] > 0
    # Converter counts must match what run_qa summarised (no silent drops).
    assert s["conduit_count"] == len(net["conduits"])
    assert s["junction_count"] == len(net["junctions"])
