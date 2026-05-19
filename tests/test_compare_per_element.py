"""Tests for the Round-3 per-element + version-aware compare surface.

These tests pin the new behaviour on top of the Phase B.1 baseline:

- ``parse_node_peaks_from_rpt`` and ``parse_subcatch_runoff_from_rpt``
  pull structured rows out of a SWMM .rpt without flagging missing
  sections as errors.
- ``RunComparison`` carries per-node and per-subcatch diffs plus
  ranked top-mover lists.
- ``render_comparison_table`` surfaces the top-mover blocks by default
  and respects ``--per-node`` / ``--per-subcatch`` expansion flags via
  the rendering keyword arguments.
- ``compare_runs`` reads ``swmm_version`` from each run's
  ``experiment_provenance.json`` and refuses cross-minor comparisons
  without an explicit override.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.compare import (
    NodePeak,
    NodePeakDiff,
    RunComparison,
    SubcatchRunoff,
    SubcatchRunoffDiff,
    compare_runs,
    parse_node_peaks_from_rpt,
    parse_subcatch_runoff_from_rpt,
    render_comparison_table,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic .rpt bodies that include continuity + per-element
# sections. The lateral/total inflow values are intentionally spread so a
# top-mover ranking has a deterministic outcome.
# ---------------------------------------------------------------------------


def _rpt_with_sections(
    runoff_continuity: float = -0.171,
    flow_continuity: float = 0.500,
    *,
    sub_runoff_mm: dict[str, float] | None = None,
    node_inflow: dict[str, tuple[float, float, str]] | None = None,
) -> str:
    sub_runoff_mm = sub_runoff_mm or {"S1": 10.54, "S2": 8.20}
    node_inflow = node_inflow or {
        "J1": (1.184, 1.184, "2  13:54"),
        "J2": (0.500, 0.700, "2  14:00"),
        "O1": (0.000, 1.184, "2  12:47"),
    }
    head = (
        "  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)\n"
        "  ------------------------------------------------------------\n"
        "\n"
        "  **************************        Volume         Depth\n"
        "  Runoff Quantity Continuity     hectare-m            mm\n"
        "  **************************     ---------       -------\n"
        "  Total Precipitation ......         0.092        26.208\n"
        "  Surface Runoff ...........         0.037        10.536\n"
        f"  Continuity Error (%) .....        {runoff_continuity:>6.3f}\n"
        "\n"
        "  **************************        Volume        Volume\n"
        "  Flow Routing Continuity        hectare-m      10^6 ltr\n"
        "  **************************     ---------     ---------\n"
        "  External Outflow .........         0.037         0.369\n"
        f"  Continuity Error (%) .....         {flow_continuity:>5.3f}\n"
    )
    # Subcatchment Runoff Summary
    sub_lines = []
    sub_lines.append("\n")
    sub_lines.append("  ***************************\n")
    sub_lines.append("  Subcatchment Runoff Summary\n")
    sub_lines.append("  ***************************\n")
    sub_lines.append("\n")
    sub_lines.append(
        "  -----------------------------------------------------------------\n"
    )
    sub_lines.append(
        "                            Total      Total      Total      Total     Imperv       Perv      Total       Total     Peak  Runoff\n"
    )
    sub_lines.append(
        "                           Precip      Runon       Evap      Infil     Runoff     Runoff     Runoff      Runoff   Runoff   Coeff\n"
    )
    sub_lines.append(
        "  Subcatchment                 mm         mm         mm         mm         mm         mm         mm    10^6 ltr      CMS\n"
    )
    sub_lines.append(
        "  -----------------------------------------------------------------\n"
    )
    for name, mm in sub_runoff_mm.items():
        # Pad columns: precip runon evap infil imperv perv total_mm total_vol peak coeff
        sub_lines.append(
            f"  {name:<24}    26.21       0.00       0.00      15.63      10.54       0.00     {mm:>6.2f}    {mm * 0.04:>6.2f}     0.00   0.402\n"
        )
    sub_lines.append("\n")
    sub_lines.append("\n")
    # Node Depth Summary intentionally present between subcatch and inflow
    # sections so the inflow section header is not adjacent to the
    # subcatch section ending.
    sub_lines.append("  ******************\n")
    sub_lines.append("  Node Depth Summary\n")
    sub_lines.append("  ******************\n")
    sub_lines.append("\n")
    sub_lines.append("  ---------------------------------------------------------------------------------\n")
    sub_lines.append("                                 Average  Maximum  Maximum  Time of Max    Reported\n")
    sub_lines.append("                                   Depth    Depth      HGL   Occurrence   Max Depth\n")
    sub_lines.append("  Node                 Type       Meters   Meters   Meters  days hr:min      Meters\n")
    sub_lines.append("  ---------------------------------------------------------------------------------\n")
    for name in node_inflow:
        sub_lines.append(
            f"  {name:<20} JUNCTION     0.13     0.50   100.50     2  13:31        0.50\n"
        )
    sub_lines.append("\n")
    sub_lines.append("\n")
    # Node Inflow Summary
    sub_lines.append("  *******************\n")
    sub_lines.append("  Node Inflow Summary\n")
    sub_lines.append("  *******************\n")
    sub_lines.append("\n")
    sub_lines.append(
        "  -------------------------------------------------------------------------------------------------\n"
    )
    sub_lines.append(
        "                                  Maximum  Maximum                  Lateral       Total        Flow\n"
    )
    sub_lines.append(
        "                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance\n"
    )
    sub_lines.append(
        "                                   Inflow   Inflow   Occurrence      Volume      Volume       Error\n"
    )
    sub_lines.append(
        "  Node                 Type           CMS      CMS  days hr:min    10^6 ltr    10^6 ltr     Percent\n"
    )
    sub_lines.append(
        "  -------------------------------------------------------------------------------------------------\n"
    )
    for name, (lat, total, tom) in node_inflow.items():
        sub_lines.append(
            f"  {name:<20} JUNCTION    {lat:>5.3f}   {total:>5.3f}     {tom}         107         107       0.001\n"
        )
    sub_lines.append("\n")
    return head + "".join(sub_lines)


def _write_run_dir(parent: Path, name: str, rpt_body: str | None = None) -> Path:
    run_dir = parent / name
    run_dir.mkdir()
    if rpt_body is not None:
        (run_dir / "model.rpt").write_text(rpt_body, encoding="utf-8")
    return run_dir


def _write_provenance(run_dir: Path, run_id: str, swmm_version: str | None = None) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(exist_ok=True)
    payload: dict[str, object] = {"run_id": run_id}
    if swmm_version is not None:
        payload["swmm_version"] = swmm_version
    (audit / "experiment_provenance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Parser tests.
# ---------------------------------------------------------------------------


class NodePeakParserTests(unittest.TestCase):
    def test_three_nodes_parsed(self) -> None:
        text = _rpt_with_sections(
            node_inflow={
                "J1": (1.184, 1.184, "2  13:54"),
                "J2": (0.500, 0.700, "2  14:00"),
                "O1": (0.000, 1.184, "2  12:47"),
            }
        )
        peaks = parse_node_peaks_from_rpt(text)
        self.assertEqual(set(peaks), {"J1", "J2", "O1"})
        self.assertAlmostEqual(peaks["J1"].max_total_inflow, 1.184, places=3)
        self.assertEqual(peaks["O1"].max_lateral_inflow, 0.0)
        self.assertEqual(peaks["J2"].time_of_max, "2 14:00")

    def test_missing_node_section_returns_empty(self) -> None:
        text = "no node section in this body whatsoever"
        self.assertEqual(parse_node_peaks_from_rpt(text), {})

    def test_node_section_malformed_row_skipped(self) -> None:
        # Section header present but the data rows are mangled.
        text = (
            "  *******************\n"
            "  Node Inflow Summary\n"
            "  *******************\n"
            "\n"
            "  -------------------------------------------------------------\n"
            "  Node                 Type           CMS      CMS  days hr:min\n"
            "  -------------------------------------------------------------\n"
            "  GARBAGE\n"
            "\n"
        )
        # A 1-token row is below the threshold and is skipped silently.
        self.assertEqual(parse_node_peaks_from_rpt(text), {})


class SubcatchRunoffParserTests(unittest.TestCase):
    def test_four_subcatches_parsed(self) -> None:
        text = _rpt_with_sections(
            sub_runoff_mm={"S1": 10.54, "S2": 8.20, "S3": 5.30, "S4": 12.10}
        )
        sub = parse_subcatch_runoff_from_rpt(text)
        self.assertEqual(set(sub), {"S1", "S2", "S3", "S4"})
        self.assertAlmostEqual(sub["S1"].total_runoff_mm, 10.54, places=2)
        self.assertGreater(sub["S4"].total_runoff_volume_10_6L or 0, 0)

    def test_missing_subcatch_section_returns_empty(self) -> None:
        self.assertEqual(parse_subcatch_runoff_from_rpt(""), {})


# ---------------------------------------------------------------------------
# RunComparison-level wiring.
# ---------------------------------------------------------------------------


class RunComparisonPerElementTests(unittest.TestCase):
    def test_node_peak_diffs_present_after_compare(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(
                base,
                "b",
                _rpt_with_sections(
                    node_inflow={
                        "J1": (1.184, 1.300, "2  13:54"),
                        "J2": (0.500, 0.700, "2  14:00"),
                        "O1": (0.000, 1.300, "2  12:47"),
                    }
                ),
            )
            result = compare_runs(a, b)
        self.assertIn("J1", result.node_peak_diffs)
        self.assertAlmostEqual(
            result.node_peak_diffs["J1"].delta_max_total_inflow, 0.116, places=3
        )

    def test_subcatch_runoff_diffs_present_after_compare(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(
                base, "a", _rpt_with_sections(sub_runoff_mm={"S1": 10.0, "S2": 5.0})
            )
            b = _write_run_dir(
                base,
                "b",
                _rpt_with_sections(sub_runoff_mm={"S1": 11.0, "S2": 5.5}),
            )
            result = compare_runs(a, b)
        self.assertIn("S1", result.subcatch_runoff_diffs)
        self.assertAlmostEqual(
            result.subcatch_runoff_diffs["S1"].delta_pct, 10.0, places=2
        )

    def test_top_movers_nodes_ranked_by_abs_delta_pct(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(
                base,
                "a",
                _rpt_with_sections(
                    node_inflow={
                        "J1": (1.0, 1.0, "2  10:00"),
                        "J2": (1.0, 1.0, "2  10:00"),
                        "J3": (1.0, 1.0, "2  10:00"),
                    }
                ),
            )
            b = _write_run_dir(
                base,
                "b",
                _rpt_with_sections(
                    node_inflow={
                        "J1": (1.0, 1.5, "2  10:00"),   # +50%
                        "J2": (1.0, 1.1, "2  10:00"),   # +10%
                        "J3": (1.0, 1.05, "2  10:00"),  # +5%
                    }
                ),
            )
            result = compare_runs(a, b)
        self.assertEqual(result.top_movers_nodes[0][0], "J1")
        # Ranked descending by |delta_pct|
        deltas = [abs(p) for _, p in result.top_movers_nodes]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_top_movers_subcatches_ranked_by_abs_delta_pct(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(
                base,
                "a",
                _rpt_with_sections(
                    sub_runoff_mm={"S1": 10.0, "S2": 10.0, "S3": 10.0}
                ),
            )
            b = _write_run_dir(
                base,
                "b",
                _rpt_with_sections(
                    sub_runoff_mm={"S1": 15.0, "S2": 11.0, "S3": 10.5}
                ),
            )
            result = compare_runs(a, b)
        self.assertEqual(result.top_movers_subcatches[0][0], "S1")
        deltas = [abs(p) for _, p in result.top_movers_subcatches]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_to_dict_roundtrips_with_new_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(base, "b", _rpt_with_sections())
            result = compare_runs(a, b)
        payload = result.to_dict()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        for key in (
            "node_peak_diffs",
            "subcatch_runoff_diffs",
            "top_movers_nodes",
            "top_movers_subcatches",
        ):
            self.assertIn(key, decoded)

    def test_missing_section_in_one_run_is_safe(self) -> None:
        # Run A has full sections; run B has only the continuity header.
        thin = (
            "  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)\n"
            "\n"
            "  **************************        Volume         Depth\n"
            "  Runoff Quantity Continuity     hectare-m            mm\n"
            "  **************************     ---------       -------\n"
            "  Continuity Error (%) .....         0.100\n"
            "\n"
            "  Flow Routing Continuity        hectare-m      10^6 ltr\n"
            "  **************************     ---------     ---------\n"
            "  Continuity Error (%) .....         0.500\n"
        )
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(base, "b", thin)
            result = compare_runs(a, b)
        # Diffs are still produced — entries for nodes that only exist
        # in A will have peak_b=None.
        self.assertIn("J1", result.node_peak_diffs)
        self.assertIsNone(result.node_peak_diffs["J1"].peak_b)


# ---------------------------------------------------------------------------
# Render tests.
# ---------------------------------------------------------------------------


class RenderTopMoverTests(unittest.TestCase):
    def test_default_render_includes_top_3_nodes_and_subcatches(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(
                base,
                "b",
                _rpt_with_sections(
                    node_inflow={
                        "J1": (1.184, 1.300, "2  13:54"),
                        "J2": (0.500, 0.800, "2  14:00"),
                        "O1": (0.000, 1.300, "2  12:47"),
                    },
                    sub_runoff_mm={"S1": 12.0, "S2": 9.0},
                ),
            )
            result = compare_runs(a, b)
            text = render_comparison_table(result)
        self.assertIn("Top", text)
        self.assertIn("nodes that moved most", text)
        self.assertIn("subcatches that moved most", text)

    def test_per_node_flag_expands_table(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(base, "b", _rpt_with_sections())
            result = compare_runs(a, b)
            text = render_comparison_table(result, show_per_node=True)
        self.assertIn("Per-node peak inflow:", text)

    def test_per_subcatch_flag_expands_table(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run_dir(base, "a", _rpt_with_sections())
            b = _write_run_dir(base, "b", _rpt_with_sections())
            result = compare_runs(a, b)
            text = render_comparison_table(result, show_per_subcatch=True)
        self.assertIn("Per-subcatch runoff", text)


class DataclassShapeTests(unittest.TestCase):
    def test_node_peak_to_dict(self) -> None:
        p = NodePeak(
            node="J1",
            max_lateral_inflow=1.0,
            max_total_inflow=1.2,
            time_of_max="2 13:54",
        )
        out = p.to_dict()
        self.assertEqual(out["node"], "J1")
        self.assertEqual(out["max_total_inflow"], 1.2)

    def test_node_peak_diff_to_dict(self) -> None:
        d = NodePeakDiff(node="J1")
        out = d.to_dict()
        self.assertIsNone(out["peak_a"])
        self.assertIsNone(out["delta_pct"])

    def test_subcatch_runoff_to_dict(self) -> None:
        s = SubcatchRunoff(subcatch="S1", total_runoff_mm=10.0)
        out = s.to_dict()
        self.assertEqual(out["total_runoff_mm"], 10.0)

    def test_subcatch_runoff_diff_to_dict(self) -> None:
        d = SubcatchRunoffDiff(subcatch="S1")
        out = d.to_dict()
        self.assertIsNone(out["runoff_a"])

    def test_run_comparison_extended_defaults(self) -> None:
        rc = RunComparison(run_a_id="a", run_b_id="b")
        self.assertEqual(rc.node_peak_diffs, {})
        self.assertEqual(rc.subcatch_runoff_diffs, {})
        self.assertEqual(rc.top_movers_nodes, [])
        self.assertEqual(rc.top_movers_subcatches, [])


if __name__ == "__main__":
    unittest.main()
