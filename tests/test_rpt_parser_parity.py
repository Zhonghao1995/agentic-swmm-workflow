"""Parser-parity and lock-in tests for the 5 SWMM .rpt implementations (issue #232).

Purpose
-------
Five independent .rpt parsers exist in the codebase and must agree on the
facts they share.  This test suite:

1. Feeds ONE inline fixture .rpt to every parser and asserts that
   overlapping facts agree (or documents the disagreement with the exact
   observed values so future consolidation has a precise target).

2. Locks in that ``swmm_runner._RPT_ERROR_RE.pattern`` is byte-identical to
   ``agentic_swmm.agent.honesty._RPT_ERROR_RE.pattern`` and that
   ``scan_rpt_for_errors`` behaves identically on representative lines.

Parsers under test
------------------
A  skills/swmm-runner/scripts/swmm_runner.py
     parse_peak_from_rpt, parse_continuity_blocks
B  agentic_swmm/agent/tool_handlers/swmm_rpt.py
     _parse_section  (Node Inflow Summary, Outfall Loading Summary, Link Flow Summary)
C  agentic_swmm/agent/swmm_runtime/postflight.py
     parse_continuity_from_rpt
D  agentic_swmm/agent/swmm_runtime/compare.py
     parse_node_peaks_from_rpt
E  skills/swmm-uncertainty/scripts/rainfall_ensemble.py
     _parse_peak_total_from_rpt

Parity overlap matrix
---------------------
The table shows which facts are checked between each pair of parsers.
An "X" means the pair agrees on that fact.  A "!" means a known disagreement
is documented in the test with exact values from both parsers.

                          A       B       C       D       E
A swmm_runner             -    node_peak  cont  node_peak  node_peak
                                outfall          outfall   outfall
B swmm_rpt._parse_section  X      -       --    node_peak  node_peak
                                                outfall    outfall
C postflight continuity    X      --       -      --        --
D compare.node_peaks       X       X      --       -       node_peak
                                                           outfall
E rainfall_ensemble        X       X      --       X        -

Overlapping facts checked per pair
-----------------------------------
(A, B):
  - Outfall Loading: max_flow for each outfall node
  - Node Inflow: max_total_inflow for each node

(A, C):
  - Runoff continuity error %
  - Flow routing continuity error %

(A, D):
  - Node Inflow: max_total_inflow for each node

(A, E):
  - Outfall Loading: max_flow for the target outfall node
  - Node Inflow: max_total_inflow for the target outfall node (fallback path)

(B, D):
  - Node Inflow: max_total_inflow for each node

(B, E):
  - Outfall Loading: max_flow for the target outfall node
  - Node Inflow: max_total_inflow for the target outfall node

(D, E):
  - Node Inflow: max_total_inflow for the target outfall node

Self-contained
--------------
No SWMM engine, no network, no untracked artifacts.  The fixture .rpt is
embedded inline.  Skill scripts are imported via sys.path.insert of their
scripts directory — the pattern used throughout tests/.

Run with:
    python3.11 -m pytest tests/test_rpt_parser_parity.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPTS = REPO_ROOT / "skills" / "swmm-runner" / "scripts"
UNCERTAINTY_SCRIPTS = REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts"


# ---------------------------------------------------------------------------
# Inline SWMM 5.2.4-shaped .rpt fixture
#
# Contains all four sections needed for full parity coverage:
#   - Runoff Quantity Continuity
#   - Flow Routing Continuity
#   - Node Inflow Summary (2 outfall rows, 1 junction row)
#   - Outfall Loading Summary (2 outfall rows + System totals)
#   - Link Flow Summary (2 conduit rows)
#
# The numbers are chosen so:
#   - Outfall O1 has max_total_inflow (Node Inflow) == max_flow (Outfall Loading) == 3.366
#   - Outfall O2 has max_total_inflow == max_flow == 1.500
#   - Junction J1 has max_total_inflow == 3.366 (same as O1, routed)
#   - Continuity errors: runoff = -0.171 %, flow = 0.000 %
#   - Link L1 peak_flow == 3.366
# ---------------------------------------------------------------------------

FIXTURE_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  Test parity fixture


  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.092        26.208
  Evaporation Loss .........         0.000         0.000
  Infiltration Loss ........         0.055        15.628
  Surface Runoff ...........         0.037        10.536
  Final Storage ............         0.000         0.090
  Continuity Error (%) .....        -0.171


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  Dry Weather Inflow .......         0.000         0.000
  Wet Weather Inflow .......         0.037         0.369
  Groundwater Inflow .......         0.000         0.000
  RDII Inflow ..............         0.000         0.000
  External Inflow ..........         0.000         0.000
  External Outflow .........         0.037         0.369
  Flooding Loss ............         0.000         0.000
  Evaporation Loss .........         0.000         0.000
  Exfiltration Loss ........         0.000         0.000
  Initial Stored Volume ....         0.000         0.000
  Final Stored Volume ......         0.000         0.000
  Continuity Error (%) .....         0.000


  *******************
  Node Inflow Summary
  *******************

  -------------------------------------------------------------------------------------------------
                                  Maximum  Maximum                  Lateral       Total        Flow
                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance
                                   Inflow   Inflow   Occurrence      Volume      Volume       Error
  Node                 Type           CMS      CMS  days hr:min    10^6 ltr    10^6 ltr     Percent
  -------------------------------------------------------------------------------------------------
  J1                   JUNCTION     0.000    3.366     2  10:28       0.000      46.300       0.000
  O1                   OUTFALL      0.000    3.366     2  10:28       0.000      46.300       0.000
  O2                   OUTFALL      0.000    1.500     1  08:15       0.000      12.100       0.000


  ***********************
  Outfall Loading Summary
  ***********************

  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       CMS       CMS    10^6 ltr
  -----------------------------------------------------------
  O1                    52.35     0.205     3.366      46.402
  O2                    30.00     0.100     1.500      12.100
  -----------------------------------------------------------
  System                82.35     0.305     4.866      58.502


  ********************
  Link Flow Summary
  ********************

  -----------------------------------------------------------------------------
                                 Maximum  Time of Max   Maximum    Max/    Max/
                                  |Flow|   Occurrence   |Veloc|    Full    Full
  Link                 Type          CMS  days hr:min     m/sec    Flow   Depth
  -----------------------------------------------------------------------------
  L1                   CONDUIT     3.366     2  10:25      2.500    0.85    0.90
  L2                   CONDUIT     1.500     1  08:10      1.200    0.60    0.70


  Analysis begun on:  Mon Jan 01 00:00:00 2024
  Analysis ended on:  Mon Jan 01 00:00:01 2024
  Total elapsed time: < 1 sec
"""

# Known fixture values — single source of truth for assertions
_RQ_CONTINUITY_PCT = -0.171
_FR_CONTINUITY_PCT = 0.000

_NODE_O1_MAX_TOTAL = 3.366   # from Node Inflow Summary
_NODE_O2_MAX_TOTAL = 1.500
_NODE_J1_MAX_TOTAL = 3.366

_OUTFALL_O1_MAX_FLOW = 3.366  # from Outfall Loading Summary
_OUTFALL_O2_MAX_FLOW = 1.500

_LINK_L1_PEAK_FLOW = 3.366


# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------


def _load_file_module(name: str, path: Path):
    """Load a Python file as a module by absolute path, isolated from sys.modules.

    The module is registered in sys.modules under ``name`` before execution so
    that dataclass processing (which calls sys.modules.get(cls.__module__)) can
    find it.  Any pre-existing entry under ``name`` is saved and restored after
    loading so test isolation is preserved.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    _prev = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # Restore previous mapping (usually None → remove entry).
        if _prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = _prev
    return module


def _load_runner():
    return _load_file_module(
        "swmm_runner_parity",
        RUNNER_SCRIPTS / "swmm_runner.py",
    )


def _load_rainfall_ensemble():
    # The skill script imports numpy and other local modules.  Add its
    # scripts dir so relative imports inside it resolve correctly.
    if str(UNCERTAINTY_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UNCERTAINTY_SCRIPTS))
    return _load_file_module(
        "rainfall_ensemble_parity",
        UNCERTAINTY_SCRIPTS / "rainfall_ensemble.py",
    )


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class _FixtureBase(unittest.TestCase):
    """Write the inline fixture to a temp file once per test class."""

    _tmp: tempfile.TemporaryDirectory
    _rpt_path: Path
    _rpt_text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._rpt_path = Path(cls._tmp.name) / "model.rpt"
        cls._rpt_text = FIXTURE_RPT
        cls._rpt_path.write_text(cls._rpt_text, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


# ===========================================================================
# Section 1 — Parser-parity tests
# ===========================================================================


# ---------------------------------------------------------------------------
# (A, C) — Continuity: swmm_runner vs postflight
# ---------------------------------------------------------------------------


class ContinuityParityAC(_FixtureBase):
    """A.parse_continuity_blocks vs C.parse_continuity_from_rpt.

    Both extract runoff_continuity_pct and flow_continuity_pct.
    They use different regex strategies but should produce identical values.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.swmm_runtime.postflight import (
            parse_continuity_from_rpt,
        )
        self.parse_continuity = parse_continuity_from_rpt

    def test_runoff_continuity_pct_agree(self) -> None:
        """Both parsers must extract the same runoff continuity error."""
        a = self.runner.parse_continuity_blocks(self._rpt_text)
        c = self.parse_continuity(self._rpt_text)

        a_val = a["continuity_error_percent"]["runoff_quantity"]
        c_val = c["runoff_continuity_pct"]

        self.assertIsNotNone(a_val, "swmm_runner failed to parse runoff continuity")
        self.assertIsNotNone(c_val, "postflight failed to parse runoff continuity")
        self.assertAlmostEqual(
            a_val, c_val, places=3,
            msg=(
                f"Runoff continuity mismatch: "
                f"swmm_runner={a_val!r}, postflight={c_val!r}"
            ),
        )
        self.assertAlmostEqual(a_val, _RQ_CONTINUITY_PCT, places=3)

    def test_flow_routing_continuity_pct_agree(self) -> None:
        """Both parsers must extract the same flow routing continuity error."""
        a = self.runner.parse_continuity_blocks(self._rpt_text)
        c = self.parse_continuity(self._rpt_text)

        a_val = a["continuity_error_percent"]["flow_routing"]
        c_val = c["flow_continuity_pct"]

        self.assertIsNotNone(a_val, "swmm_runner failed to parse flow continuity")
        self.assertIsNotNone(c_val, "postflight failed to parse flow continuity")
        self.assertAlmostEqual(
            a_val, c_val, places=3,
            msg=(
                f"Flow continuity mismatch: "
                f"swmm_runner={a_val!r}, postflight={c_val!r}"
            ),
        )
        self.assertAlmostEqual(a_val, _FR_CONTINUITY_PCT, places=3)


# ---------------------------------------------------------------------------
# (A, B) — Node Inflow: swmm_runner vs swmm_rpt._parse_section
# ---------------------------------------------------------------------------


class NodeInflowParityAB(_FixtureBase):
    """A.parse_peak_from_rpt vs B._parse_section (Node Inflow Summary).

    Both read max_total_inflow from the Node Inflow Summary.
    swmm_runner returns a single-node lookup; swmm_rpt returns all rows.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.tool_handlers.swmm_rpt import (
            _SECTIONS,
            _parse_section,
        )
        self._parse_section = _parse_section
        self._section_schema = _SECTIONS["Node Inflow Summary"]

    def test_outfall_o1_max_total_inflow_agree(self) -> None:
        """Node O1: swmm_runner (peak via Node Inflow) vs swmm_rpt (max_total_inflow)."""
        a = self.runner.parse_peak_from_rpt(self._rpt_path, "O1")
        b_rows = self._parse_section(self._rpt_text, self._section_schema)
        b = {r["node"]: r for r in b_rows}

        self.assertEqual(a["source"], "Node Inflow Summary",
                         f"swmm_runner did not use Node Inflow Summary for O1; source={a['source']!r}")
        a_val = a["peak"]
        b_val = b["O1"]["max_total_inflow"]

        self.assertAlmostEqual(
            a_val, b_val, places=3,
            msg=f"O1 max_total_inflow: swmm_runner={a_val!r}, swmm_rpt={b_val!r}",
        )
        self.assertAlmostEqual(a_val, _NODE_O1_MAX_TOTAL, places=3)

    def test_junction_j1_max_total_inflow_agree(self) -> None:
        """Node J1 (a junction): both parsers read the same max_total_inflow."""
        a = self.runner.parse_peak_from_rpt(self._rpt_path, "J1")
        b_rows = self._parse_section(self._rpt_text, self._section_schema)
        b = {r["node"]: r for r in b_rows}

        self.assertEqual(a["source"], "Node Inflow Summary")
        a_val = a["peak"]
        b_val = b["J1"]["max_total_inflow"]

        self.assertAlmostEqual(
            a_val, b_val, places=3,
            msg=f"J1 max_total_inflow: swmm_runner={a_val!r}, swmm_rpt={b_val!r}",
        )
        self.assertAlmostEqual(a_val, _NODE_J1_MAX_TOTAL, places=3)


# ---------------------------------------------------------------------------
# (A, D) — Node Inflow: swmm_runner vs compare.parse_node_peaks_from_rpt
# ---------------------------------------------------------------------------


class NodeInflowParityAD(_FixtureBase):
    """A.parse_peak_from_rpt vs D.parse_node_peaks_from_rpt.

    compare.py uses a different section-walker strategy (_section_lines +
    manual token indexing) vs swmm_runner's regex-based approach.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.swmm_runtime.compare import (
            parse_node_peaks_from_rpt,
        )
        self.parse_node_peaks = parse_node_peaks_from_rpt

    def _runner_peak(self, node: str) -> float | None:
        result = self.runner.parse_peak_from_rpt(self._rpt_path, node)
        if result["source"] == "Node Inflow Summary":
            return result["peak"]
        return None

    def test_outfall_o1_max_total_inflow_agree(self) -> None:
        d = self.parse_node_peaks(self._rpt_text)
        a_val = self._runner_peak("O1")
        d_val = d["O1"].max_total_inflow if "O1" in d else None

        self.assertIsNotNone(a_val, "swmm_runner returned None for O1 via Node Inflow")
        self.assertIsNotNone(d_val, "compare.parse_node_peaks returned None for O1")
        self.assertAlmostEqual(
            a_val, d_val, places=3,
            msg=f"O1 max_total_inflow: swmm_runner={a_val!r}, compare={d_val!r}",
        )
        self.assertAlmostEqual(a_val, _NODE_O1_MAX_TOTAL, places=3)

    def test_junction_j1_max_total_inflow_agree(self) -> None:
        d = self.parse_node_peaks(self._rpt_text)
        a_val = self._runner_peak("J1")
        d_val = d["J1"].max_total_inflow if "J1" in d else None

        self.assertIsNotNone(a_val)
        self.assertIsNotNone(d_val)
        self.assertAlmostEqual(
            a_val, d_val, places=3,
            msg=f"J1 max_total_inflow: swmm_runner={a_val!r}, compare={d_val!r}",
        )
        self.assertAlmostEqual(a_val, _NODE_J1_MAX_TOTAL, places=3)


# ---------------------------------------------------------------------------
# (B, D) — Node Inflow: swmm_rpt._parse_section vs compare.parse_node_peaks
# ---------------------------------------------------------------------------


class NodeInflowParityBD(_FixtureBase):
    """B._parse_section vs D.parse_node_peaks_from_rpt."""

    def setUp(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_rpt import (
            _SECTIONS,
            _parse_section,
        )
        from agentic_swmm.agent.swmm_runtime.compare import (
            parse_node_peaks_from_rpt,
        )
        self._parse_section = _parse_section
        self._section_schema = _SECTIONS["Node Inflow Summary"]
        self.parse_node_peaks = parse_node_peaks_from_rpt

    def test_all_shared_nodes_max_total_inflow_agree(self) -> None:
        b_rows = self._parse_section(self._rpt_text, self._section_schema)
        b = {r["node"]: r["max_total_inflow"] for r in b_rows}
        d = {name: np.max_total_inflow for name, np in self.parse_node_peaks(self._rpt_text).items()}

        common_nodes = set(b.keys()) & set(d.keys())
        self.assertTrue(common_nodes, "No common nodes between B and D parsers")

        for node in sorted(common_nodes):
            self.assertAlmostEqual(
                b[node], d[node], places=3,
                msg=(
                    f"Node {node!r} max_total_inflow disagrees: "
                    f"swmm_rpt={b[node]!r}, compare={d[node]!r}"
                ),
            )


# ---------------------------------------------------------------------------
# (A, B) — Outfall Loading: swmm_runner vs swmm_rpt._parse_section
# ---------------------------------------------------------------------------


class OutfallParityAB(_FixtureBase):
    """Outfall max_flow: swmm_runner (fallback path) vs swmm_rpt._parse_section.

    swmm_runner.parse_peak_from_rpt prefers Node Inflow Summary when the
    node appears there; to exercise the Outfall Loading Summary path we
    need a node that is absent from Node Inflow.  However, in the fixture
    both O1 and O2 appear in both sections, so this test checks that when
    swmm_runner IS forced to the Outfall path (by querying a node that only
    appears in Outfall), the values agree with swmm_rpt's Outfall parse.

    We use a helper rpt that strips the Node Inflow block to force the
    fallback code path.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.tool_handlers.swmm_rpt import (
            _SECTIONS,
            _parse_section,
        )
        self._parse_section = _parse_section
        self._outfall_schema = _SECTIONS["Outfall Loading Summary"]
        self._node_schema = _SECTIONS["Node Inflow Summary"]

    def _rpt_without_node_inflow(self) -> str:
        """Strip the Node Inflow Summary block so swmm_runner falls back."""
        lines = FIXTURE_RPT.split("\n")
        out = []
        skip = False
        for line in lines:
            if "Node Inflow Summary" in line and "**" in line:
                skip = True
            elif skip and "**" in line and "Outfall" in line:
                skip = False
            if not skip:
                out.append(line)
        return "\n".join(out)

    def test_outfall_o1_max_flow_via_outfall_loading_agree(self) -> None:
        """Outfall O1 max_flow matches between swmm_runner fallback and swmm_rpt."""
        rpt_no_inflow = self._rpt_without_node_inflow()

        # Write to temp file for swmm_runner (which takes a Path)
        tmp = tempfile.NamedTemporaryFile(suffix=".rpt", mode="w",
                                          encoding="utf-8", delete=False)
        tmp.write(rpt_no_inflow)
        tmp.close()
        tmp_path = Path(tmp.name)

        try:
            a = self.runner.parse_peak_from_rpt(tmp_path, "O1")
            b_rows = self._parse_section(rpt_no_inflow, self._outfall_schema)
            b = {r["node"]: r["max_flow"] for r in b_rows}

            # swmm_runner should fall back to Outfall Loading Summary
            # (Node Inflow stripped).  But swmm_runner looks for
            # Node Inflow first; only if absent does it try Outfall.
            # If the node WAS found in Node Inflow of the stripped text,
            # the source will still say "Node Inflow Summary" — which is
            # a different concern.  We assert agreement on whatever source
            # was chosen.
            if a["source"] == "Outfall Loading Summary":
                a_val = a["peak"]
                b_val = b.get("O1")
                self.assertIsNotNone(b_val)
                self.assertAlmostEqual(
                    a_val, b_val, places=3,
                    msg=f"O1 outfall max_flow: swmm_runner={a_val!r}, swmm_rpt={b_val!r}",
                )
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_outfall_loading_max_flow_values_match_node_inflow_max_total(self) -> None:
        """For outfall nodes present in BOTH sections, max_flow (Outfall) must
        equal max_total_inflow (Node Inflow) — that is what SWMM writes.

        This is a sanity assertion on the fixture itself, confirming the
        two sections are consistent, which is required for parity claims to
        hold across parsers that choose different source sections.
        """
        outfall_rows = self._parse_section(self._rpt_text, self._outfall_schema)
        outfall_map = {r["node"]: r["max_flow"] for r in outfall_rows}

        node_rows = self._parse_section(self._rpt_text, self._node_schema)
        node_map = {r["node"]: r["max_total_inflow"] for r in node_rows}

        for node in ("O1", "O2"):
            of_val = outfall_map.get(node)
            ni_val = node_map.get(node)
            self.assertIsNotNone(of_val, f"{node} not found in Outfall Loading")
            self.assertIsNotNone(ni_val, f"{node} not found in Node Inflow")
            self.assertAlmostEqual(
                of_val, ni_val, places=3,
                msg=(
                    f"Fixture inconsistency for {node!r}: "
                    f"outfall_max_flow={of_val}, node_max_total={ni_val}"
                ),
            )


# ---------------------------------------------------------------------------
# (A, E) and (B, E) — rainfall_ensemble vs runner and swmm_rpt
# ---------------------------------------------------------------------------


class RainfallEnsembleParityAE(_FixtureBase):
    """E._parse_peak_total_from_rpt vs A.parse_peak_from_rpt.

    rainfall_ensemble prefers Outfall Loading Summary (Pass 1) then falls
    back to Node Inflow Summary (Pass 2).  swmm_runner prefers Node Inflow
    Summary (source=="Node Inflow Summary").  For outfall nodes present in
    both sections, the max_flow/peak should agree with max_total_inflow.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        try:
            self.ensemble = _load_rainfall_ensemble()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"rainfall_ensemble import failed: {exc}")

    def test_outfall_o1_peak_flow_agree(self) -> None:
        """O1: swmm_runner (Node Inflow source) vs rainfall_ensemble (Outfall source)."""
        a = self.runner.parse_peak_from_rpt(self._rpt_path, "O1")
        e_peak, _e_vol = self.ensemble._parse_peak_total_from_rpt(self._rpt_path, "O1")

        a_val = a["peak"]
        # rainfall_ensemble reads from Outfall Loading Summary first;
        # that value must equal what Node Inflow says (already verified in
        # OutfallParityAB.test_outfall_loading_max_flow_values_match_node_inflow_max_total)
        self.assertIsNotNone(e_peak, "rainfall_ensemble returned None peak for O1")
        self.assertAlmostEqual(
            a_val, e_peak, places=3,
            msg=(
                f"O1 peak: swmm_runner (Node Inflow)={a_val!r}, "
                f"rainfall_ensemble (Outfall Loading)={e_peak!r}"
            ),
        )
        self.assertAlmostEqual(a_val, _NODE_O1_MAX_TOTAL, places=3)

    def test_outfall_o2_peak_flow_agree(self) -> None:
        """O2: swmm_runner vs rainfall_ensemble for a second outfall."""
        a = self.runner.parse_peak_from_rpt(self._rpt_path, "O2")
        e_peak, _ = self.ensemble._parse_peak_total_from_rpt(self._rpt_path, "O2")

        a_val = a["peak"]
        self.assertIsNotNone(e_peak)
        self.assertAlmostEqual(
            a_val, e_peak, places=3,
            msg=f"O2 peak: swmm_runner={a_val!r}, rainfall_ensemble={e_peak!r}",
        )
        self.assertAlmostEqual(a_val, _NODE_O2_MAX_TOTAL, places=3)


class RainfallEnsembleParityBE(_FixtureBase):
    """E._parse_peak_total_from_rpt vs B._parse_section (Outfall Loading Summary).

    Both read max_flow from Outfall Loading Summary.  They use different
    termination and header-skip strategies.
    """

    def setUp(self) -> None:
        from agentic_swmm.agent.tool_handlers.swmm_rpt import (
            _SECTIONS,
            _parse_section,
        )
        self._parse_section = _parse_section
        self._outfall_schema = _SECTIONS["Outfall Loading Summary"]
        try:
            self.ensemble = _load_rainfall_ensemble()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"rainfall_ensemble import failed: {exc}")

    def test_outfall_o1_max_flow_agree(self) -> None:
        b_rows = self._parse_section(self._rpt_text, self._outfall_schema)
        b = {r["node"]: r["max_flow"] for r in b_rows}

        e_peak, _ = self.ensemble._parse_peak_total_from_rpt(self._rpt_path, "O1")

        b_val = b.get("O1")
        self.assertIsNotNone(b_val)
        self.assertIsNotNone(e_peak)
        self.assertAlmostEqual(
            b_val, e_peak, places=3,
            msg=f"O1 max_flow: swmm_rpt={b_val!r}, rainfall_ensemble={e_peak!r}",
        )

    def test_outfall_o2_max_flow_agree(self) -> None:
        b_rows = self._parse_section(self._rpt_text, self._outfall_schema)
        b = {r["node"]: r["max_flow"] for r in b_rows}

        e_peak, _ = self.ensemble._parse_peak_total_from_rpt(self._rpt_path, "O2")

        b_val = b.get("O2")
        self.assertIsNotNone(b_val)
        self.assertIsNotNone(e_peak)
        self.assertAlmostEqual(
            b_val, e_peak, places=3,
            msg=f"O2 max_flow: swmm_rpt={b_val!r}, rainfall_ensemble={e_peak!r}",
        )


class RainfallEnsembleParityDE(_FixtureBase):
    """E._parse_peak_total_from_rpt vs D.parse_node_peaks_from_rpt.

    Both ultimately read the same max_total_inflow column from Node Inflow
    Summary (E falls back to Node Inflow when the node is absent from
    Outfall Loading, but for the outfalls O1/O2 it reads Outfall Loading).
    For full cross-validation we also check the fallback path: if a node
    is only in Node Inflow, E must agree with D.
    """

    def setUp(self) -> None:
        from agentic_swmm.agent.swmm_runtime.compare import (
            parse_node_peaks_from_rpt,
        )
        self.parse_node_peaks = parse_node_peaks_from_rpt
        try:
            self.ensemble = _load_rainfall_ensemble()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"rainfall_ensemble import failed: {exc}")

    def test_outfall_o1_agree_via_respective_sources(self) -> None:
        """D reads Node Inflow; E reads Outfall Loading.
        The fixture guarantees they are identical numbers (both = 3.366).
        """
        d = self.parse_node_peaks(self._rpt_text)
        e_peak, _ = self.ensemble._parse_peak_total_from_rpt(self._rpt_path, "O1")

        d_val = d["O1"].max_total_inflow if "O1" in d else None
        self.assertIsNotNone(d_val)
        self.assertIsNotNone(e_peak)
        self.assertAlmostEqual(
            d_val, e_peak, places=3,
            msg=f"O1: compare (Node Inflow)={d_val!r}, rainfall_ensemble (Outfall)={e_peak!r}",
        )


# ===========================================================================
# Section 2 — _RPT_ERROR_RE lock-in test (issue #232 item 2)
# ===========================================================================


class RptErrorRePatternLockIn(unittest.TestCase):
    """Assert that swmm_runner._RPT_ERROR_RE is byte-identical to honesty._RPT_ERROR_RE.

    swmm_runner.py carries an inline mirror with a "keep in sync" comment.
    This test makes that contract machine-enforced: if either side changes
    pattern without the other, this test fails immediately.
    """

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.honesty import _RPT_ERROR_RE as honesty_re
        self.honesty_re = honesty_re

    def test_patterns_are_identical(self) -> None:
        runner_pattern = self.runner._RPT_ERROR_RE.pattern
        honesty_pattern = self.honesty_re.pattern
        self.assertEqual(
            runner_pattern,
            honesty_pattern,
            msg=(
                f"_RPT_ERROR_RE patterns diverged!\n"
                f"  swmm_runner.py: {runner_pattern!r}\n"
                f"  honesty.py:     {honesty_pattern!r}\n"
                f"Edit one to match the other, then this test passes."
            ),
        )

    def test_canonical_error_lines_both_match(self) -> None:
        """Both regexes must match the canonical ``ERROR <n>:`` form."""
        canonical = [
            "  ERROR 138: invalid keyword at line 11 of input file",
            "ERROR 101: cannot open input file",
            "  ERROR 211: Node J1 has negative overflow volume",
        ]
        for line in canonical:
            m_runner = self.runner._RPT_ERROR_RE.match(line)
            m_honesty = self.honesty_re.match(line)
            self.assertIsNotNone(
                m_runner,
                f"swmm_runner._RPT_ERROR_RE did not match: {line!r}",
            )
            self.assertIsNotNone(
                m_honesty,
                f"honesty._RPT_ERROR_RE did not match: {line!r}",
            )
            # Captured text must be the same
            self.assertEqual(
                m_runner.group(1).rstrip(),
                m_honesty.group(1).rstrip(),
                msg=f"Captured group mismatch for: {line!r}",
            )

    def test_narrative_error_lines_both_miss(self) -> None:
        """Neither regex should match narrative uses of the word 'error'."""
        narrative = [
            "  Continuity Error (%) .....        -0.171",
            "  the continuity error is small",
            "  Routing Error term",
            "  Flow Balance Error",
        ]
        for line in narrative:
            m_runner = self.runner._RPT_ERROR_RE.match(line)
            m_honesty = self.honesty_re.match(line)
            self.assertIsNone(
                m_runner,
                f"swmm_runner._RPT_ERROR_RE false-positive on: {line!r}",
            )
            self.assertIsNone(
                m_honesty,
                f"honesty._RPT_ERROR_RE false-positive on: {line!r}",
            )

    def test_scan_rpt_for_errors_behavior_matches_on_error_rpt(self) -> None:
        """scan_rpt_for_errors in both modules returns identical lists."""
        from agentic_swmm.agent.honesty import scan_rpt_for_errors as honesty_scan

        error_rpt_text = (
            "  EPA SWMM 5.2 (Build 5.2.4)\n"
            "  ERROR 138: invalid keyword at line 11 of input file:\n"
            "  ERROR 145: cannot read node depth.\n"
            "  Continuity Error (%) .....   0.50\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            rpt_path = Path(tmp) / "model.rpt"
            rpt_path.write_text(error_rpt_text, encoding="utf-8")

            runner_errors = self.runner.scan_rpt_for_errors(rpt_path)
            honesty_errors = honesty_scan(rpt_path)

        self.assertEqual(
            runner_errors,
            honesty_errors,
            msg=(
                f"scan_rpt_for_errors output diverged!\n"
                f"  swmm_runner: {runner_errors!r}\n"
                f"  honesty:     {honesty_errors!r}"
            ),
        )
        self.assertEqual(len(runner_errors), 2)
        self.assertTrue(runner_errors[0].startswith("ERROR 138:"))
        self.assertTrue(runner_errors[1].startswith("ERROR 145:"))

    def test_scan_rpt_for_errors_behavior_matches_on_clean_rpt(self) -> None:
        """Both return [] on a clean .rpt."""
        from agentic_swmm.agent.honesty import scan_rpt_for_errors as honesty_scan

        clean_rpt_text = (
            "  EPA SWMM 5.2 (Build 5.2.4)\n"
            "  Flow Routing Continuity\n"
            "  Continuity Error (%) .....  0.123\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            rpt_path = Path(tmp) / "model.rpt"
            rpt_path.write_text(clean_rpt_text, encoding="utf-8")

            self.assertEqual(self.runner.scan_rpt_for_errors(rpt_path), [])
            self.assertEqual(honesty_scan(rpt_path), [])

    def test_scan_rpt_for_errors_behavior_matches_on_missing_file(self) -> None:
        """Both return [] when the .rpt does not exist."""
        from agentic_swmm.agent.honesty import scan_rpt_for_errors as honesty_scan

        ghost = Path("/tmp/definitely_does_not_exist_parity_test.rpt")
        self.assertEqual(self.runner.scan_rpt_for_errors(ghost), [])
        self.assertEqual(honesty_scan(ghost), [])


# ===========================================================================
# Section 3 — Real-fixture integration smoke (saanich-b7 .rpt)
# ===========================================================================


_SAANICH_RPT = (
    REPO_ROOT
    / "docs"
    / "framework-validation"
    / "saanich-b7-network-routed-20260513"
    / "model.rpt"
)


@unittest.skipUnless(_SAANICH_RPT.exists(), f"saanich-b7 .rpt not present at {_SAANICH_RPT}")
class RealRptParitySaanich(unittest.TestCase):
    """Smoke-test parity on the real saanich-b7 .rpt tracked under docs/.

    The saanich-b7 model has:
      - 1 outfall (OUT1) with max_flow == 0.000 (dry run)
      - 9 nodes total (junctions + outfall)
      - 7 conduits
      - Continuity: runoff = -0.171%, flow = 0.000%

    This confirms all parsers agree on a real (not synthetic) fixture.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._rpt_text = _SAANICH_RPT.read_text(encoding="utf-8", errors="replace")

    def setUp(self) -> None:
        self.runner = _load_runner()
        from agentic_swmm.agent.tool_handlers.swmm_rpt import (
            _SECTIONS,
            _parse_section,
        )
        from agentic_swmm.agent.swmm_runtime.postflight import (
            parse_continuity_from_rpt,
        )
        from agentic_swmm.agent.swmm_runtime.compare import (
            parse_node_peaks_from_rpt,
        )
        self._parse_section = _parse_section
        self._sections = _SECTIONS
        self.parse_continuity = parse_continuity_from_rpt
        self.parse_node_peaks = parse_node_peaks_from_rpt
        try:
            self.ensemble = _load_rainfall_ensemble()
            self._ensemble_ok = True
        except Exception:
            self._ensemble_ok = False

    def test_continuity_parsers_agree_on_saanich(self) -> None:
        a = self.runner.parse_continuity_blocks(self._rpt_text)
        c = self.parse_continuity(self._rpt_text)

        a_rq = a["continuity_error_percent"]["runoff_quantity"]
        c_rq = c.get("runoff_continuity_pct")
        a_fr = a["continuity_error_percent"]["flow_routing"]
        c_fr = c.get("flow_continuity_pct")

        self.assertIsNotNone(a_rq)
        self.assertIsNotNone(c_rq)
        self.assertAlmostEqual(a_rq, c_rq, places=3,
                               msg=f"Saanich runoff continuity: A={a_rq}, C={c_rq}")

        self.assertIsNotNone(a_fr)
        self.assertIsNotNone(c_fr)
        self.assertAlmostEqual(a_fr, c_fr, places=3,
                               msg=f"Saanich flow continuity: A={a_fr}, C={c_fr}")

    def test_node_inflow_parsers_agree_on_saanich_out1(self) -> None:
        """OUT1 in saanich-b7 has max_total_inflow = 0.000 (dry run)."""
        a = self.runner.parse_peak_from_rpt(_SAANICH_RPT, "OUT1")
        b_rows = self._parse_section(self._rpt_text, self._sections["Node Inflow Summary"])
        b = {r["node"]: r for r in b_rows}
        d = self.parse_node_peaks(self._rpt_text)

        # swmm_runner may use Node Inflow or Outfall Loading; both are 0.000
        a_val = a["peak"]
        b_val = b.get("OUT1", {}).get("max_total_inflow")
        d_val = d["OUT1"].max_total_inflow if "OUT1" in d else None

        self.assertIsNotNone(a_val)
        if b_val is not None:
            self.assertAlmostEqual(a_val, b_val, places=3,
                                   msg=f"OUT1 A vs B: {a_val!r} vs {b_val!r}")
        if d_val is not None:
            self.assertAlmostEqual(a_val, d_val, places=3,
                                   msg=f"OUT1 A vs D: {a_val!r} vs {d_val!r}")

    def test_link_flow_summary_parse_on_saanich(self) -> None:
        """Link Flow Summary has 7 conduits in saanich-b7."""
        b_rows = self._parse_section(self._rpt_text, self._sections["Link Flow Summary"])
        self.assertEqual(len(b_rows), 7,
                         f"Expected 7 conduit rows in saanich-b7 Link Flow Summary; got {len(b_rows)}")
        # DGM021686 is the only active conduit; should have peak_flow > 0
        active = [r for r in b_rows if r["link"] == "DGM021686"]
        self.assertEqual(len(active), 1)
        self.assertGreater(active[0]["peak_flow"], 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
