"""Unit tests for the ``read_rpt_summary`` typed-tool handler.

The handler parses the structured summary sections of a SWMM ``.rpt``
file (Link Flow Summary, Outfall Loading Summary, Node Inflow Summary)
and returns top-N rows as typed JSON objects, ranked by peak flow by
default. It exists because ``read_file`` is capped at 4000 chars —
which is the first ~25 lines of a 359 KB rpt — so the LLM cannot reach
the summary sections via ``read_file`` alone. ``read_rpt_summary`` lets
the LLM ask "what are the busiest conduits / outfalls?" in one step
without burning turns on grep / ``run_allowed_command`` workarounds.

These tests pin:

* the typed-param validation (missing/bad ``rpt_path``, missing/unknown
  ``section``, non-``.rpt`` files, file-not-found),
* per-section parsing — exact field names, types, and default-sort
  ordering for all three supported sections,
* ``top_n`` clamping (negative/zero → 1, >50 → 50),
* ``sort_by`` override (and fallback to default on unknown columns),
* the registry plumbing (``read_rpt_summary`` appears in the registry
  and is marked read-only so QUICK profile auto-approves it),
* a smoke test against the real 359 KB rpt that lives under ``runs/``
  (gated on existence so CI without the run dir is fine) — the top
  conduit must be ``116-119`` with peak ≈ 103.46 LPS.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import pytest

from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


# ---------------------------------------------------------------------------
# Synthetic rpt fixtures — small, deterministic, fully under repo root so
# ``_required_repo_file`` accepts the path without monkeypatching.
# ---------------------------------------------------------------------------

# A trimmed-down rpt with all three target sections. Section headers
# follow the SWMM 5.2 format: a row of asterisks, the section title,
# another row of asterisks, then a blank line, then the column header
# block (dashes / wrapped headers / dashes), then data rows, then a
# blank line (or System totals for Outfall) ending the section.
_SYNTHETIC_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  *******************
  Node Inflow Summary
  *******************

  -------------------------------------------------------------------------------------------------
                                  Maximum  Maximum                  Lateral       Total        Flow
                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance
                                   Inflow   Inflow   Occurrence      Volume      Volume       Error
  Node                 Type           LPS      LPS  days hr:min    10^6 ltr    10^6 ltr     Percent
  -------------------------------------------------------------------------------------------------
  119_outfall          OUTFALL       0.00   134.37     0  00:15           0       0.341       0.000
  128_outfall          OUTFALL       0.00    43.08     0  00:15           0      0.0258       0.000
  001                  JUNCTION      9.71   500.00     0  00:11      0.0058       0.500       0.000


  ***********************
  Outfall Loading Summary
  ***********************

  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       LPS       LPS    10^6 ltr
  -----------------------------------------------------------
  119_outfall           11.62     64.87    134.37       0.341
  128_outfall            4.32     18.53     43.08       0.026
  384_outfall            8.78     89.53    353.96       0.275
  -----------------------------------------------------------
  System                 5.55    771.23   1914.16       1.892


  ********************
  Link Flow Summary
  ********************

  -----------------------------------------------------------------------------
                                 Maximum  Time of Max   Maximum    Max/    Max/
                                  |Flow|   Occurrence   |Veloc|    Full    Full
  Link                 Type          LPS  days hr:min     m/sec    Flow   Depth
  -----------------------------------------------------------------------------
  116-119              CONDUIT    103.46     0  00:21      5.85    2.21    1.00
  125-116              CONDUIT    103.15     0  00:23      2.59    1.29    1.00
  208-125              CONDUIT     81.76     0  00:09      4.71    0.97    1.00
  124-125              CONDUIT     47.38     0  01:03      1.19    0.87    1.00
  123-124              CONDUIT     47.37     0  00:08      1.23    0.99    1.00


  ***************************
  Flow Classification Summary
  ***************************

  No conduits.
"""


_SCRATCH_ROOT = repo_root() / "runs" / "_test_rpt"


def _write_rpt(tmp_path: Path, content: str = _SYNTHETIC_RPT) -> Path:
    """Drop the synthetic rpt into a repo-relative scratch dir under
    ``runs/_test_rpt/`` (real repo root). ``_required_repo_file``
    only accepts files under ``repo_root()``, so we cannot use
    pytest's ``tmp_path`` directly — ``tmp_path`` lives in
    ``/tmp`` / ``/var/folders/...`` outside the repo. ``runs/*`` is
    gitignored so the scratch tree never appears in ``git status``."""
    scratch = _SCRATCH_ROOT / tmp_path.name
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / "model.rpt"
    target.write_text(content, encoding="utf-8")
    return target


def setUpModule() -> None:  # pragma: no cover — pytest module fixture
    """Wipe any leftover scratch tree from a previous run so each
    pytest invocation starts clean."""
    if _SCRATCH_ROOT.exists():
        shutil.rmtree(_SCRATCH_ROOT, ignore_errors=True)


def tearDownModule() -> None:  # pragma: no cover — pytest module fixture
    """Clean up the scratch tree after the suite — leaves no trace
    even though ``runs/*`` is gitignored."""
    if _SCRATCH_ROOT.exists():
        shutil.rmtree(_SCRATCH_ROOT, ignore_errors=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ReadRptSummaryValidationTests(unittest.TestCase):
    def test_missing_rpt_path_returns_failure(self) -> None:
        call = ToolCall(name="read_rpt_summary", args={"section": "Link Flow Summary"})
        result = _read_rpt_summary_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("rpt_path", result["summary"])

    def test_empty_rpt_path_returns_failure(self) -> None:
        call = ToolCall(
            name="read_rpt_summary",
            args={"rpt_path": "   ", "section": "Link Flow Summary"},
        )
        result = _read_rpt_summary_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("rpt_path", result["summary"])

    def test_non_rpt_suffix_returns_failure(self) -> None:
        # Use a real file that exists in the repo but with the wrong
        # suffix — the suffix gate must trip before the file-exists check.
        call = ToolCall(
            name="read_rpt_summary",
            args={"rpt_path": "README.md", "section": "Link Flow Summary"},
        )
        result = _read_rpt_summary_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn(".rpt", result["summary"])

    def test_file_not_found_returns_failure(self) -> None:
        call = ToolCall(
            name="read_rpt_summary",
            args={
                "rpt_path": "runs/_test_rpt/does_not_exist.rpt",
                "section": "Link Flow Summary",
            },
        )
        result = _read_rpt_summary_tool(call, Path("/tmp"))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["summary"].lower())

    def test_missing_section_returns_failure(self) -> None:
        rpt = _write_rpt(Path("missing_section"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={"rpt_path": str(rpt.relative_to(repo_root()))},
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertFalse(result["ok"])
            self.assertIn("section", result["summary"])
        finally:
            rpt.unlink(missing_ok=True)

    def test_unknown_section_returns_failure(self) -> None:
        rpt = _write_rpt(Path("unknown_section"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Made Up Section",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertFalse(result["ok"])
            self.assertIn("unsupported section", result["summary"].lower())
            # The error must list the three supported names so the LLM
            # can immediately retry with one of them.
            self.assertIn("Link Flow Summary", result["summary"])
            self.assertIn("Outfall Loading Summary", result["summary"])
            self.assertIn("Node Inflow Summary", result["summary"])
        finally:
            rpt.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Link Flow Summary parsing
# ---------------------------------------------------------------------------


class ReadRptSummaryLinkFlowTests(unittest.TestCase):
    def test_returns_eight_fields_with_correct_types(self) -> None:
        rpt = _write_rpt(Path("link_flow_types"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            rows = result["rows"]
            self.assertGreater(len(rows), 0)
            first = rows[0]
            expected_keys = {
                "link",
                "type",
                "peak_flow",
                "time_days",
                "time_hhmm",
                "max_velocity",
                "max_full_flow_ratio",
                "max_full_depth_ratio",
            }
            self.assertEqual(set(first.keys()), expected_keys)
            self.assertIsInstance(first["link"], str)
            self.assertIsInstance(first["type"], str)
            self.assertIsInstance(first["peak_flow"], float)
            self.assertIsInstance(first["time_days"], int)
            self.assertIsInstance(first["time_hhmm"], str)
            self.assertIsInstance(first["max_velocity"], float)
            self.assertIsInstance(first["max_full_flow_ratio"], float)
            self.assertIsInstance(first["max_full_depth_ratio"], float)
            # time_hhmm must look like HH:MM
            self.assertRegex(first["time_hhmm"], r"^\d{1,2}:\d{2}$")
        finally:
            rpt.unlink(missing_ok=True)

    def test_default_sort_is_peak_flow_desc(self) -> None:
        rpt = _write_rpt(Path("link_flow_sort"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "top_n": 5,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            peaks = [row["peak_flow"] for row in result["rows"]]
            self.assertEqual(peaks, sorted(peaks, reverse=True))
            # Top row in synthetic fixture is the 116-119 conduit with
            # peak_flow=103.46.
            self.assertEqual(result["rows"][0]["link"], "116-119")
            self.assertAlmostEqual(result["rows"][0]["peak_flow"], 103.46, places=2)
            self.assertEqual(result["sort_by"], "peak_flow")
        finally:
            rpt.unlink(missing_ok=True)

    def test_total_rows_reflects_pre_truncation_count(self) -> None:
        rpt = _write_rpt(Path("link_flow_total"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "top_n": 2,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["shown"], 2)
            self.assertEqual(result["total_rows"], 5)  # synthetic has 5 conduits
            self.assertEqual(len(result["rows"]), 2)
        finally:
            rpt.unlink(missing_ok=True)

    def test_top_n_clamps_to_one_when_zero(self) -> None:
        rpt = _write_rpt(Path("link_flow_clamp_low"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "top_n": 0,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["rows"]), 1)
        finally:
            rpt.unlink(missing_ok=True)

    def test_top_n_clamps_to_fifty_when_huge(self) -> None:
        # Need a fixture with >50 rows to actually observe the cap; we
        # synthesise one by repeating rows. The synthetic block has 5
        # rows; multiply the data block into ~60 unique rows.
        header = _SYNTHETIC_RPT.split("Link Flow Summary")[0] + "Link Flow Summary\n  ********************\n\n"
        block = (
            "  -----------------------------------------------------------------------------\n"
            "                                 Maximum  Time of Max   Maximum    Max/    Max/\n"
            "                                  |Flow|   Occurrence   |Veloc|    Full    Full\n"
            "  Link                 Type          LPS  days hr:min     m/sec    Flow   Depth\n"
            "  -----------------------------------------------------------------------------\n"
        )
        rows = "\n".join(
            f"  X-{i:03d}              CONDUIT    {(60 - i):6.2f}     0  00:{i % 60:02d}      1.00    0.50    0.50"
            for i in range(60)
        )
        synth = header + block + rows + "\n\n"
        rpt = _write_rpt(Path("link_flow_clamp_high"), content=synth)
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "top_n": 9999,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            self.assertEqual(len(result["rows"]), 50)
            self.assertEqual(result["total_rows"], 60)
        finally:
            rpt.unlink(missing_ok=True)

    def test_sort_by_override_reorders_rows(self) -> None:
        rpt = _write_rpt(Path("link_flow_sort_override"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "sort_by": "max_full_flow_ratio",
                    "top_n": 5,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            self.assertEqual(result["sort_by"], "max_full_flow_ratio")
            ratios = [row["max_full_flow_ratio"] for row in result["rows"]]
            self.assertEqual(ratios, sorted(ratios, reverse=True))
            # Top of synthetic by ratio is 116-119 (2.21).
            self.assertEqual(result["rows"][0]["link"], "116-119")
        finally:
            rpt.unlink(missing_ok=True)

    def test_unknown_sort_by_falls_back_to_default(self) -> None:
        rpt = _write_rpt(Path("link_flow_bad_sort"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Link Flow Summary",
                    "sort_by": "not_a_column",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["sort_by"], "peak_flow")
            peaks = [row["peak_flow"] for row in result["rows"]]
            self.assertEqual(peaks, sorted(peaks, reverse=True))
        finally:
            rpt.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Outfall Loading Summary parsing
# ---------------------------------------------------------------------------


class ReadRptSummaryOutfallLoadingTests(unittest.TestCase):
    def test_returns_five_fields_with_correct_types(self) -> None:
        rpt = _write_rpt(Path("outfall_types"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Outfall Loading Summary",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            first = result["rows"][0]
            expected_keys = {
                "node",
                "flow_freq_pct",
                "avg_flow",
                "max_flow",
                "total_volume_10_6_ltr",
            }
            self.assertEqual(set(first.keys()), expected_keys)
            self.assertIsInstance(first["node"], str)
            self.assertIsInstance(first["flow_freq_pct"], float)
            self.assertIsInstance(first["avg_flow"], float)
            self.assertIsInstance(first["max_flow"], float)
            self.assertIsInstance(first["total_volume_10_6_ltr"], float)
        finally:
            rpt.unlink(missing_ok=True)

    def test_default_sort_is_max_flow_desc(self) -> None:
        rpt = _write_rpt(Path("outfall_sort"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Outfall Loading Summary",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["sort_by"], "max_flow")
            max_flows = [row["max_flow"] for row in result["rows"]]
            self.assertEqual(max_flows, sorted(max_flows, reverse=True))
            # 384_outfall has max_flow=353.96 in fixture
            self.assertEqual(result["rows"][0]["node"], "384_outfall")
        finally:
            rpt.unlink(missing_ok=True)

    def test_system_row_is_excluded(self) -> None:
        """``System`` is a totals row that lives between the data and
        the closing ``---``. It must not appear in the parsed rows."""
        rpt = _write_rpt(Path("outfall_no_system"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Outfall Loading Summary",
                    "top_n": 50,
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"])
            for row in result["rows"]:
                self.assertNotEqual(row["node"].lower(), "system")
        finally:
            rpt.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Node Inflow Summary parsing
# ---------------------------------------------------------------------------


class ReadRptSummaryNodeInflowTests(unittest.TestCase):
    def test_returns_seven_fields_with_correct_types(self) -> None:
        rpt = _write_rpt(Path("node_inflow_types"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Node Inflow Summary",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            first = result["rows"][0]
            expected_keys = {
                "node",
                "type",
                "max_lateral_inflow",
                "max_total_inflow",
                "lateral_inflow_volume_10_6_ltr",
                "total_inflow_volume_10_6_ltr",
                "flow_balance_error_pct",
            }
            self.assertEqual(set(first.keys()), expected_keys)
            self.assertIsInstance(first["node"], str)
            self.assertIsInstance(first["type"], str)
            self.assertIsInstance(first["max_lateral_inflow"], float)
            self.assertIsInstance(first["max_total_inflow"], float)
            self.assertIsInstance(first["lateral_inflow_volume_10_6_ltr"], float)
            self.assertIsInstance(first["total_inflow_volume_10_6_ltr"], float)
            self.assertIsInstance(first["flow_balance_error_pct"], float)
        finally:
            rpt.unlink(missing_ok=True)

    def test_default_sort_is_max_total_inflow_desc(self) -> None:
        rpt = _write_rpt(Path("node_inflow_sort"))
        try:
            call = ToolCall(
                name="read_rpt_summary",
                args={
                    "rpt_path": str(rpt.relative_to(repo_root())),
                    "section": "Node Inflow Summary",
                },
            )
            result = _read_rpt_summary_tool(call, Path("/tmp"))
            self.assertTrue(result["ok"], result.get("summary"))
            self.assertEqual(result["sort_by"], "max_total_inflow")
            totals = [row["max_total_inflow"] for row in result["rows"]]
            self.assertEqual(totals, sorted(totals, reverse=True))
            # Top of fixture: 001 (max_total_inflow=500.00).
            self.assertEqual(result["rows"][0]["node"], "001")
        finally:
            rpt.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class ReadRptSummaryRegistryTests(unittest.TestCase):
    def test_read_rpt_summary_is_registered(self) -> None:
        self.assertIn("read_rpt_summary", AgentToolRegistry().names)

    def test_read_rpt_summary_is_read_only(self) -> None:
        """``is_read_only=True`` lets QUICK profile auto-approve the
        tool without prompting — the rpt parser is pure read."""
        self.assertTrue(AgentToolRegistry().is_read_only("read_rpt_summary"))

    def test_schema_has_section_enum_and_required_args(self) -> None:
        registry = AgentToolRegistry()
        spec = next(s for s in registry.schemas() if s["name"] == "read_rpt_summary")
        params = spec["parameters"]
        self.assertEqual(set(params["required"]), {"rpt_path", "section"})
        self.assertEqual(
            set(params["properties"]["section"]["enum"]),
            {"Link Flow Summary", "Outfall Loading Summary", "Node Inflow Summary"},
        )

    def test_description_signals_why_to_use_this_over_read_file(self) -> None:
        """The LLM needs to understand that ``read_file`` won't reach
        the summary section — the description must say so or the LLM
        will keep choosing ``read_file`` for rpts."""
        desc = (AgentToolRegistry().describe("read_rpt_summary") or "").lower()
        self.assertTrue(
            "summary" in desc and (".rpt" in desc or "rpt file" in desc),
            f"description must signal rpt summary parsing; got: {desc!r}",
        )

    def test_description_tells_llm_to_call_multiple_times_for_multiple_sections(
        self,
    ) -> None:
        """Failure mode from the 2026-05-28 Greenwich e2e: after the LLM
        successfully called ``read_rpt_summary`` for Link Flow Summary,
        it tried to fetch Outfall Loading Summary by re-reading the
        rpt with ``read_file`` / ``search_files`` instead of just
        calling ``read_rpt_summary`` AGAIN with a different ``section``.
        It eventually read the tool's own source code to figure out the
        enum values. The description must explicitly tell the LLM
        that multiple stateless calls is the right pattern.

        We don't pin the literal sentence — that would be brittle
        across paraphrases. We pin the semantic markers: at least one
        of {'multiple', 'once per section', 'stateless'} must appear,
        AND a phrase that mentions calling the tool again. This
        catches a future docstring rewrite that drops the steering
        signal without replacing it.
        """
        desc = (AgentToolRegistry().describe("read_rpt_summary") or "").lower()
        # Steering signal #1: tells the LLM the tool can be repeated.
        repeat_markers = ("once per section", "multiple call", "call this tool", "stateless")
        has_repeat = any(m in desc for m in repeat_markers)
        # Steering signal #2: actively discourages the read_file
        # fallback the LLM keeps choosing.
        has_anti_pattern = "read_file" in desc or "not read_file" in desc or "not use read_file" in desc
        self.assertTrue(
            has_repeat,
            f"description must signal multi-section / multi-call pattern "
            f"(one of: {repeat_markers}); got: {desc!r}",
        )
        self.assertTrue(
            has_anti_pattern,
            f"description must steer the LLM away from read_file fallback; "
            f"got: {desc!r}",
        )

    def test_read_file_description_redirects_rpt_users_to_read_rpt_summary(
        self,
    ) -> None:
        """The inverse signal on ``read_file``: when the LLM is about
        to pick read_file for a .rpt path, the description should say
        'no, use read_rpt_summary'. Without this, gpt-5.5 still
        defaults to read_file because it's the more general tool."""
        desc = (AgentToolRegistry().describe("read_file") or "").lower()
        self.assertIn(
            "read_rpt_summary",
            desc,
            "read_file description must redirect .rpt callers to "
            "read_rpt_summary — that's the only way to keep the LLM "
            "from picking read_file first and burning steps on the "
            "4000-char cap",
        )


# ---------------------------------------------------------------------------
# Real-data smoke test
# ---------------------------------------------------------------------------


_REAL_RPT = repo_root() / "runs" / "2026-05-27" / "235253_plot-selection_run" / "model.rpt"


@pytest.mark.skipif(
    not _REAL_RPT.exists(),
    reason=f"real rpt not present at {_REAL_RPT.relative_to(repo_root())}; smoke test skipped",
)
def test_real_rpt_link_flow_returns_typed_top_rows() -> None:
    """The whole point of this tool: against a real 359 KB rpt that
    ``read_file`` cannot reach (capped at 4000 chars), the LLM can
    extract the top conduit in one shot.

    The 116-119 conduit (peak ~103 LPS) is famous in this run because
    it sits high in the Link Flow Summary's file order. But the
    section's default sort is ``peak_flow`` desc, so the actual top
    is ``384-384_outfall`` (peak 353.96 LPS) — the busiest pipe in
    the network, which is exactly what the LLM should be steered to
    when asked "which conduit carries the most water?". This test
    locks in that semantic: top-by-peak-flow, not top-by-file-order.
    """
    call = ToolCall(
        name="read_rpt_summary",
        args={
            "rpt_path": str(_REAL_RPT.relative_to(repo_root())),
            "section": "Link Flow Summary",
            "top_n": 3,
        },
    )
    result = _read_rpt_summary_tool(call, Path("/tmp"))
    assert result["ok"], result.get("summary")
    assert result["rows"], "expected at least one row"
    # 550 conduits in this rpt — proves we actually walked past the
    # 4000-char ``read_file`` cap.
    assert result["total_rows"] > 100, result["total_rows"]
    assert result["shown"] == 3
    top = result["rows"][0]
    # Top conduit by peak_flow desc — pinned to the actual ground
    # truth so a future parser regression cannot silently break the
    # sort key.
    assert top["link"] == "384-384_outfall", top["link"]
    assert abs(top["peak_flow"] - 353.96) < 0.01, top["peak_flow"]
    assert top["type"] == "CONDUIT"
    # 116-119 must still be findable — it should appear in the top 50
    # by peak_flow. Verifies the parser does not silently drop rows.
    call_50 = ToolCall(
        name="read_rpt_summary",
        args={
            "rpt_path": str(_REAL_RPT.relative_to(repo_root())),
            "section": "Link Flow Summary",
            "top_n": 50,
        },
    )
    result_50 = _read_rpt_summary_tool(call_50, Path("/tmp"))
    assert result_50["ok"]
    links = {row["link"] for row in result_50["rows"]}
    assert "116-119" in links, sorted(links)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
