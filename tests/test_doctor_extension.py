"""Tests for ``agentic_swmm.diagnostics.doctor_report`` (PRD-08 A.1, cluster 2).

The doctor extension is a pure data layer: every helper takes a path
or a list and returns dataclasses or strings. Tests cover the empty,
populated, and partially-populated branches for each store; the
opt-out env var snapshot; the MCP-drift collapsing; and the fix-action
prompt loop.
"""

from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.diagnostics.doctor_report import (
    GroupedWarnRow,
    MemoryStoreStatus,
    OptOutFlagStatus,
    collect_memory_store_status,
    collect_optout_status,
    group_identical_warns,
    grouped_warn_to_dict,
    memory_store_status_to_dict,
    optout_status_to_dict,
    render_grouped_warns_section,
    render_memory_stores_section,
    render_runtime_knobs_section,
)
from agentic_swmm.diagnostics.fixes import (
    FixAction,
    apply_fix_actions,
    collect_fix_actions,
    fix_action_to_dict,
)


_OPTOUT_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "AISWMM_DISABLE_MEMORY_INFORMED",
    "AISWMM_DISABLE_SWMM_GATES",
    "AISWMM_DISABLE_HONESTY_LAYER",
    "AISWMM_DISABLE_WELCOME",
    "AISWMM_MEMORY_DIR",
)


class _OptOutEnvCleaner(unittest.TestCase):
    """Mixin: snapshot + restore the opt-out env vars around each test."""

    def setUp(self) -> None:
        self._saved = {name: os.environ.pop(name, None) for name in _OPTOUT_ENV_NAMES}

    def tearDown(self) -> None:
        for name in _OPTOUT_ENV_NAMES:
            os.environ.pop(name, None)
            if self._saved.get(name) is not None:
                os.environ[name] = self._saved[name]


# ---------------------------------------------------------------------------
# collect_memory_store_status
# ---------------------------------------------------------------------------


class CollectMemoryStoreStatusEmptyDirTests(unittest.TestCase):
    def test_all_seven_stores_reported_missing_or_ok_on_empty_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            statuses = collect_memory_store_status(Path(tmp))
        names = [s.name for s in statuses]
        # 7 known stores reported; the order is stable so callers can
        # rely on it for rendering.
        self.assertEqual(len(statuses), 7)
        # parametric_memory.jsonl, calibration_memory.jsonl,
        # negative_lessons.md (preferred), reference_benchmarks.yaml,
        # citations.yaml, storm_library.yaml, project_overrides.yaml
        self.assertEqual(
            names,
            [
                "parametric_memory.jsonl",
                "calibration_memory.jsonl",
                "negative_lessons.md",
                "reference_benchmarks.yaml",
                "citations.yaml",
                "storm_library.yaml",
                "project_overrides.yaml",
            ],
        )
        # 6 are MISSING; project_overrides.yaml is OK when absent.
        severities = [s.severity for s in statuses]
        self.assertEqual(severities.count("MISSING"), 6)
        self.assertEqual(severities.count("OK"), 1)
        # Every MISSING store has an actionable remediation.
        for s in statuses[:-1]:
            self.assertIsNotNone(s.remediation)


class CollectMemoryStoreStatusPopulatedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _seed_jsonl(self, name: str, n: int) -> None:
        path = self.dir / name
        path.write_text(
            "\n".join('{"x": ' + str(i) + "}" for i in range(n)) + "\n",
            encoding="utf-8",
        )

    def _seed_md_headings(self, n: int) -> None:
        body = ["# Lessons"]
        for i in range(n):
            body.append(f"## lesson {i}\nbody.\n")
        (self.dir / "negative_lessons.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8"
        )

    def _seed_yaml(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text, encoding="utf-8")

    def test_populated_jsonl_reports_row_count_and_ok(self) -> None:
        self._seed_jsonl("parametric_memory.jsonl", 3)
        self._seed_jsonl("calibration_memory.jsonl", 2)
        self._seed_md_headings(1)
        statuses = {
            s.name: s for s in collect_memory_store_status(self.dir)
        }
        self.assertEqual(statuses["parametric_memory.jsonl"].row_count, 3)
        self.assertEqual(statuses["parametric_memory.jsonl"].severity, "OK")
        self.assertEqual(statuses["calibration_memory.jsonl"].row_count, 2)
        self.assertEqual(statuses["calibration_memory.jsonl"].severity, "OK")
        self.assertEqual(statuses["negative_lessons.md"].row_count, 1)
        self.assertEqual(statuses["negative_lessons.md"].severity, "OK")

    def test_reference_benchmarks_with_null_leaves_is_partial(self) -> None:
        self._seed_yaml(
            "reference_benchmarks.yaml",
            "schema_version: '1.0'\n"
            "nse_acceptable_thresholds:\n"
            "  stormwater_event:\n"
            "    acceptable: null\n"
            "    good: null\n",
        )
        statuses = {
            s.name: s for s in collect_memory_store_status(self.dir)
        }
        self.assertEqual(
            statuses["reference_benchmarks.yaml"].severity, "PARTIAL"
        )

    def test_citations_with_only_placeholder_entry_is_partial(self) -> None:
        self._seed_yaml(
            "citations.yaml",
            "schema_version: '1.0'\n"
            "worked_example_pending_verification:\n"
            "  authors: '<author-list-pending-verification>'\n"
            "  verified_by: ''\n"
            "  verified_on: ''\n",
        )
        statuses = {
            s.name: s for s in collect_memory_store_status(self.dir)
        }
        cit = statuses["citations.yaml"]
        self.assertEqual(cit.severity, "PARTIAL")
        self.assertEqual(cit.row_count, 1)
        self.assertEqual(cit.verified_count, 0)

    def test_citations_with_one_verified_entry_is_partial_when_others_pending(
        self,
    ) -> None:
        self._seed_yaml(
            "citations.yaml",
            "schema_version: '1.0'\n"
            "worked_example_pending_verification:\n"
            "  authors: '<author-list-pending-verification>'\n"
            "  verified_by: ''\n"
            "  verified_on: ''\n"
            "real_entry:\n"
            "  authors: 'Doe, J.'\n"
            "  year: 2020\n"
            "  title: 't'\n"
            "  work: 'w'\n"
            "  locator: 'p1'\n"
            "  verified_by: 'maintainer'\n"
            "  verified_on: '2026-05-19'\n",
        )
        cit = next(
            s for s in collect_memory_store_status(self.dir) if s.name == "citations.yaml"
        )
        self.assertEqual(cit.verified_count, 1)
        self.assertEqual(cit.row_count, 2)
        self.assertEqual(cit.severity, "PARTIAL")

    def test_storm_library_with_null_idf_params_is_partial(self) -> None:
        self._seed_yaml(
            "storm_library.yaml",
            "schema_version: '1.0'\n"
            "chicago_hyetographs:\n"
            "  placeholder:\n"
            "    idf_params:\n"
            "      a: null\n"
            "      b: null\n"
            "      c: null\n"
            "    peak_position: null\n",
        )
        sl = next(
            s
            for s in collect_memory_store_status(self.dir)
            if s.name == "storm_library.yaml"
        )
        self.assertEqual(sl.severity, "PARTIAL")
        self.assertEqual(sl.row_count, 1)
        self.assertEqual(sl.verified_count, 0)

    def test_negative_lessons_md_preferred_over_jsonl(self) -> None:
        self._seed_md_headings(2)
        # Even if a stale .jsonl exists, the md path wins.
        (self.dir / "negative_lessons.jsonl").write_text(
            '{"old": 1}\n', encoding="utf-8"
        )
        statuses = [s for s in collect_memory_store_status(self.dir)]
        names = [s.name for s in statuses]
        self.assertIn("negative_lessons.md", names)
        self.assertNotIn("negative_lessons.jsonl", names)


# ---------------------------------------------------------------------------
# collect_optout_status
# ---------------------------------------------------------------------------


class CollectOptOutStatusTests(_OptOutEnvCleaner):
    def test_unset_flags_report_none(self) -> None:
        statuses = collect_optout_status()
        self.assertEqual(len(statuses), 6)
        self.assertEqual(
            [s.env_name for s in statuses],
            [
                "ANTHROPIC_API_KEY",
                "AISWMM_DISABLE_MEMORY_INFORMED",
                "AISWMM_DISABLE_SWMM_GATES",
                "AISWMM_DISABLE_HONESTY_LAYER",
                "AISWMM_DISABLE_WELCOME",
                "AISWMM_MEMORY_DIR",
            ],
        )
        for s in statuses:
            self.assertIsNone(s.current_value)
            self.assertTrue(s.description)

    def test_set_flag_reports_raw_value(self) -> None:
        os.environ["AISWMM_DISABLE_MEMORY_INFORMED"] = "1"
        os.environ["AISWMM_MEMORY_DIR"] = "/tmp/custom"
        statuses = {s.env_name: s for s in collect_optout_status()}
        self.assertEqual(
            statuses["AISWMM_DISABLE_MEMORY_INFORMED"].current_value, "1"
        )
        self.assertEqual(
            statuses["AISWMM_MEMORY_DIR"].current_value, "/tmp/custom"
        )

    def test_includes_new_honesty_layer_env(self) -> None:
        statuses = {s.env_name: s for s in collect_optout_status()}
        self.assertIn("AISWMM_DISABLE_HONESTY_LAYER", statuses)


# ---------------------------------------------------------------------------
# group_identical_warns
# ---------------------------------------------------------------------------


class GroupIdenticalWarnsTests(unittest.TestCase):
    def _mcp_row(self, server: str, path: str = "/old/checkout") -> dict:
        return {
            "name": f"mcp.json: {server}",
            "passed": False,
            "detail": (
                f"mcp.json routes {server} to a different checkout "
                f"({path}). Re-run 'aiswmm setup --refresh-mcp' to align "
                "with the active install, or sync that checkout manually."
            ),
            "required": False,
        }

    def test_eleven_drifted_servers_collapse_into_one_grouped_row(self) -> None:
        rows = [
            self._mcp_row(f"server-{i}") for i in range(11)
        ]
        result = group_identical_warns(rows)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], GroupedWarnRow)
        self.assertEqual(len(result[0].member_names), 11)
        self.assertIn("11 MCP servers drift", result[0].summary)
        self.assertIn(
            "aiswmm setup --refresh-mcp",
            result[0].representative_remediation,
        )

    def test_three_unrelated_warns_pass_through_unchanged(self) -> None:
        rows = [
            {"name": "swmm5", "passed": False, "detail": "not found", "required": True},
            {"name": "node", "passed": False, "detail": "not found", "required": True},
            {"name": "OPENAI_API_KEY", "passed": False, "detail": "not set", "required": False},
        ]
        result = group_identical_warns(rows)
        self.assertEqual(len(result), 3)
        for row in result:
            self.assertIsInstance(row, dict)

    def test_drifted_servers_to_different_paths_form_separate_groups(self) -> None:
        rows = [
            self._mcp_row("a", "/path/one"),
            self._mcp_row("b", "/path/one"),
            self._mcp_row("c", "/path/two"),
        ]
        result = group_identical_warns(rows)
        self.assertEqual(
            sum(1 for r in result if isinstance(r, GroupedWarnRow)),
            2,
        )

    def test_grouped_row_position_matches_first_member_position(self) -> None:
        rows = [
            {"name": "swmm5", "passed": True, "detail": "ok", "required": True},
            self._mcp_row("a"),
            {"name": "node", "passed": True, "detail": "ok", "required": True},
            self._mcp_row("b"),
        ]
        result = group_identical_warns(rows)
        # 2 non-mcp rows + 1 grouped row = 3.
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[1], GroupedWarnRow)


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


class RenderTests(unittest.TestCase):
    def test_memory_stores_section_has_header_and_per_store_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            statuses = collect_memory_store_status(Path(tmp))
        body = render_memory_stores_section(statuses)
        self.assertIn("Memory stores (7 known", body)
        self.assertIn("parametric_memory.jsonl", body)
        self.assertIn("MISSING", body)

    def test_runtime_knobs_section_lists_all_five_envs(self) -> None:
        with TemporaryDirectory():
            statuses = collect_optout_status()
        body = render_runtime_knobs_section(statuses)
        self.assertIn("Runtime knobs:", body)
        for name in _OPTOUT_ENV_NAMES:
            self.assertIn(name, body)

    def test_grouped_warns_section_renders_group_with_members(self) -> None:
        group = GroupedWarnRow(
            summary="11 MCP servers drift to /old/checkout",
            representative_remediation="run aiswmm setup --refresh-mcp",
            member_names=["a", "b", "c"],
        )
        body = render_grouped_warns_section([group])
        self.assertIn("Issues:", body)
        self.assertIn("11 MCP servers drift", body)
        self.assertIn("members: a, b, c", body)

    def test_grouped_warns_section_empty_returns_empty(self) -> None:
        self.assertEqual(render_grouped_warns_section([]), "")


# ---------------------------------------------------------------------------
# Fix actions
# ---------------------------------------------------------------------------


class CollectFixActionsTests(unittest.TestCase):
    def test_no_fix_actions_when_report_is_clean(self) -> None:
        actions = collect_fix_actions(
            {"checks": [], "memory_stores": [], "grouped_warns": []}
        )
        self.assertEqual(actions, [])

    def test_mcp_drift_group_yields_refresh_mcp_action(self) -> None:
        group = GroupedWarnRow(
            summary="11 MCP servers drift",
            representative_remediation="run refresh",
            member_names=["a", "b"],
        )
        actions = collect_fix_actions(
            {"checks": [], "memory_stores": [], "grouped_warns": [group]}
        )
        labels = {a.label for a in actions}
        self.assertIn("Refresh mcp.json to current install", labels)
        refresh = next(a for a in actions if a.label.startswith("Refresh"))
        self.assertEqual(
            refresh.command, ["aiswmm", "setup", "--refresh-mcp"]
        )

    def test_missing_memory_store_yields_bootstrap_action(self) -> None:
        missing = MemoryStoreStatus(
            name="parametric_memory.jsonl",
            path=Path("/tmp/missing.jsonl"),
            exists=False,
            row_count=None,
            verified_count=None,
            last_modified_utc=None,
            severity="MISSING",
            remediation="run `aiswmm bootstrap memory`",
        )
        actions = collect_fix_actions(
            {"checks": [], "memory_stores": [missing], "grouped_warns": []}
        )
        labels = {a.label for a in actions}
        self.assertIn("Create missing memory stores", labels)


class ApplyFixActionsTests(unittest.TestCase):
    def test_yes_skips_prompt_and_applies(self) -> None:
        recorded: list[list[str]] = []

        class _StubProc:
            returncode = 0

        def _stub_runner(cmd, **_kwargs):
            recorded.append(list(cmd))
            return _StubProc()

        actions = [
            FixAction(
                label="t",
                command=["echo", "hi"],
                triggers=["x"],
                interactive_confirm=True,
            )
        ]
        out = io.StringIO()
        results = apply_fix_actions(
            actions,
            yes=True,
            stdin=io.StringIO(),
            stdout=out,
            subprocess_runner=_stub_runner,
        )
        self.assertEqual(results, {"t": "applied"})
        self.assertEqual(recorded, [["echo", "hi"]])

    def test_interactive_y_applies(self) -> None:
        class _StubProc:
            returncode = 0

        def _stub_runner(cmd, **_kwargs):
            return _StubProc()

        actions = [
            FixAction(label="t", command=["x"], triggers=[], interactive_confirm=True)
        ]
        results = apply_fix_actions(
            actions,
            yes=False,
            stdin=io.StringIO("y\n"),
            stdout=io.StringIO(),
            subprocess_runner=_stub_runner,
        )
        self.assertEqual(results, {"t": "applied"})

    def test_interactive_n_skips(self) -> None:
        def _stub_runner(cmd, **_kwargs):  # pragma: no cover - should not be called
            raise AssertionError("subprocess should not run when user says no")

        actions = [
            FixAction(label="t", command=["x"], triggers=[], interactive_confirm=True)
        ]
        results = apply_fix_actions(
            actions,
            yes=False,
            stdin=io.StringIO("n\n"),
            stdout=io.StringIO(),
            subprocess_runner=_stub_runner,
        )
        self.assertEqual(results, {"t": "skipped"})

    def test_nonzero_return_marks_failed(self) -> None:
        class _StubProc:
            returncode = 2

        def _stub_runner(cmd, **_kwargs):
            return _StubProc()

        actions = [
            FixAction(label="t", command=["x"], triggers=[], interactive_confirm=False)
        ]
        results = apply_fix_actions(
            actions,
            yes=True,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            subprocess_runner=_stub_runner,
        )
        self.assertEqual(results, {"t": "failed"})


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


class JsonSerializationTests(unittest.TestCase):
    def test_memory_store_status_to_dict_round_trips_required_keys(self) -> None:
        s = MemoryStoreStatus(
            name="x",
            path=Path("/tmp/x"),
            exists=True,
            row_count=3,
            verified_count=None,
            last_modified_utc="2026-05-19T00:00:00Z",
            severity="OK",
            remediation=None,
        )
        d = memory_store_status_to_dict(s)
        for key in (
            "name",
            "path",
            "exists",
            "row_count",
            "verified_count",
            "last_modified_utc",
            "severity",
            "remediation",
        ):
            self.assertIn(key, d)

    def test_grouped_warn_to_dict_distinguishes_kinds(self) -> None:
        g = GroupedWarnRow(
            summary="s", representative_remediation="r", member_names=["a"]
        )
        self.assertEqual(grouped_warn_to_dict(g)["kind"], "group")
        self.assertEqual(
            grouped_warn_to_dict({"name": "x", "detail": "y"})["kind"], "row"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
