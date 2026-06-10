"""Unit tests for the ``synth_swmm_from_bbox`` typed-tool handler.

This handler is the LLM-facing surface for the swmm-anywhere skill
(which wraps ImperialCollegeLondon/SWMManywhere). The tests below pin
the typed-param validation, the stage-aware error hints, and the
success-path payload shape so future moves stay surgical.

Tests deliberately stub ``run_synth_from_bbox`` rather than invoking
the real SWMManywhere pipeline — the wrapper itself is covered by
``tests/test_swmmanywhere_runner.py`` and the heavy geo stack lives
behind the optional ``[anywhere]`` extra.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_anywhere import (
    _resolve_run_dir,
    _stage_hint,
    _synth_swmm_from_bbox_tool,
    _validate_bbox,
)
from agentic_swmm.agent.types import ToolCall


VALID_BBOX = [-0.05, 51.48, -0.04, 51.49]


class StageHintTests(unittest.TestCase):
    """The stage hint is the LLM-visible recovery instruction. It must
    mirror the CLI script's wording so users see the same message
    regardless of entry point."""

    def test_extra_missing_hint_points_at_install_command(self) -> None:
        hint = _stage_hint("extra_missing")
        self.assertIn("aiswmm[anywhere]", hint)
        self.assertIn("Imperial College London", hint)

    def test_rain_file_missing_hint_mentions_dat_format(self) -> None:
        hint = _stage_hint("rain_file_missing")
        self.assertIn("DAT", hint)
        self.assertIn("absolute path", hint)

    def test_unknown_stage_falls_through_to_default_hint(self) -> None:
        hint = _stage_hint("any_other_stage")
        self.assertIn("refresh_raw", hint)
        self.assertIn("smaller bbox", hint)


class ValidateBboxTests(unittest.TestCase):
    """The typed bbox check is the LLM-facing gate. It must fail fast
    with a clear message before the heavy SWMManywhere import runs."""

    def test_valid_bbox_returns_coerced_floats(self) -> None:
        bbox, error = _validate_bbox(VALID_BBOX)
        self.assertIsNone(error)
        self.assertEqual(bbox, [-0.05, 51.48, -0.04, 51.49])
        # Even integer inputs coerce to float so the downstream runner
        # sees a uniform shape.
        bbox, _err = _validate_bbox([0, 0, 1, 1])
        self.assertEqual(bbox, [0.0, 0.0, 1.0, 1.0])

    def test_missing_bbox_returns_clear_error(self) -> None:
        bbox, error = _validate_bbox(None)
        self.assertIsNone(bbox)
        self.assertIn("missing required argument", error or "")

    def test_wrong_length_bbox_is_rejected(self) -> None:
        for raw in ([1.0, 2.0, 3.0], [1, 2, 3, 4, 5], []):
            _, error = _validate_bbox(raw)
            self.assertIn("4-element", error or "")

    def test_non_numeric_bbox_is_rejected(self) -> None:
        _, error = _validate_bbox(["a", "b", "c", "d"])
        self.assertIn("numbers", error or "")

    def test_boolean_bbox_entries_are_rejected(self) -> None:
        # bools are a subclass of int — guard explicitly so the LLM
        # cannot smuggle through ``[True, False, True, False]``.
        _, error = _validate_bbox([True, False, True, False])
        self.assertIn("booleans", error or "")

    def test_inverted_bbox_is_rejected(self) -> None:
        # min must be strictly < max on each axis.
        _, error = _validate_bbox([1.0, 1.0, 0.0, 0.0])
        self.assertIn("min_lon<max_lon", error or "")


class ResolveRunDirTests(unittest.TestCase):
    def test_explicit_run_dir_passes_through(self) -> None:
        call = ToolCall("synth_swmm_from_bbox", {"bbox": VALID_BBOX, "run_dir": "/tmp/x"})
        self.assertEqual(_resolve_run_dir(call), Path("/tmp/x"))

    def test_default_run_dir_uses_safe_project_name(self) -> None:
        call = ToolCall("synth_swmm_from_bbox", {"bbox": VALID_BBOX, "project_name": "my run/v1"})
        path = _resolve_run_dir(call)
        # ``_safe_name`` collapses path separators and spaces into ``-``;
        # the default dir is timestamped, so the safe name is a prefix.
        self.assertTrue(path.name.startswith("my-run-v1-"), path.name)
        self.assertEqual(path.parent.name, "agent")

    def test_default_run_dir_never_reuses_an_existing_run_dir(self) -> None:
        """Issue #246/#234: re-running the same project name must never
        resolve to a directory that already holds a previous run —
        a collision silently overwrites the earlier results."""
        import tempfile

        from agentic_swmm.agent.tool_handlers import swmm_anywhere as mod

        call = ToolCall("synth_swmm_from_bbox", {"bbox": VALID_BBOX, "project_name": "todcreek"})
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mod, "repo_root", return_value=Path(tmp)):
                first = _resolve_run_dir(call)
                first.mkdir(parents=True)
                second = _resolve_run_dir(call)
                self.assertNotEqual(first, second)
                # And the bumped path itself is also respected once taken.
                second.mkdir(parents=True)
                third = _resolve_run_dir(call)
                self.assertNotIn(third, {first, second})


class SynthSwmmFromBboxToolTests(unittest.TestCase):
    """The handler is the planner-facing seam. Each branch (validation
    failure, SynthRunError, success) emits a distinct fail-soft shape
    so the planner can route on ``ok`` without parsing the message."""

    def test_missing_bbox_returns_fail_soft_payload(self) -> None:
        call = ToolCall("synth_swmm_from_bbox", {})
        with TemporaryDirectory() as tmp:
            result = _synth_swmm_from_bbox_tool(call, Path(tmp))
        self.assertFalse(result["ok"])
        self.assertIn("bbox", result["summary"])

    def test_synth_run_error_maps_to_stage_aware_payload(self) -> None:
        call = ToolCall("synth_swmm_from_bbox", {"bbox": VALID_BBOX})

        # Stub the integration import path so the test does not need
        # the optional [anywhere] extra installed.
        from agentic_swmm.integrations import swmmanywhere_runner

        def _raises(**_kwargs):
            raise swmmanywhere_runner.SynthRunError(
                "extra_missing", ModuleNotFoundError("no swmmanywhere")
            )

        with mock.patch.object(swmmanywhere_runner, "run_synth_from_bbox", _raises), TemporaryDirectory() as tmp:
            result = _synth_swmm_from_bbox_tool(call, Path(tmp))

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "extra_missing")
        self.assertIn("aiswmm[anywhere]", result["hint"])

    def test_success_path_returns_inp_and_provenance(self) -> None:
        call = ToolCall(
            "synth_swmm_from_bbox",
            {"bbox": VALID_BBOX, "project_name": "case-x"},
        )

        from agentic_swmm.integrations import swmmanywhere_runner

        fake_result = swmmanywhere_runner.SynthRunResult(
            inp_path=Path("/tmp/synth.inp"),
            run_dir=Path("/tmp/run"),
            raw_manifest_path=Path("/tmp/run/00_raw/raw_manifest.json"),
            provenance={"tool": "swmmanywhere", "bbox_wgs84": VALID_BBOX},
            stage_durations={"swmmanywhere_pipeline": 1.2},
            warnings=("note about raingages",),
        )

        captured: dict = {}

        def _ok(**kwargs):
            captured.update(kwargs)
            return fake_result

        with mock.patch.object(swmmanywhere_runner, "run_synth_from_bbox", _ok), TemporaryDirectory() as tmp:
            result = _synth_swmm_from_bbox_tool(call, Path(tmp))

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"]["inp_path"], "/tmp/synth.inp")
        self.assertEqual(result["results"]["warnings"], ["note about raingages"])
        self.assertIn("synth_inp=", result["summary"])
        # The handler forwards typed args to the runner.
        self.assertEqual(captured["bbox"], VALID_BBOX)
        self.assertEqual(captured["project_name"], "case-x")


class ToolRegistrationTests(unittest.TestCase):
    """Lock the registry-visible surface for the LLM so a future refactor
    cannot silently drop the tool."""

    def test_synth_swmm_from_bbox_is_registered(self) -> None:
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        self.assertIn("synth_swmm_from_bbox", registry.names)

    def test_schema_declares_required_bbox(self) -> None:
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        spec = registry._tools["synth_swmm_from_bbox"]
        self.assertEqual(spec.parameters["required"], ["bbox"])
        bbox_schema = spec.parameters["properties"]["bbox"]
        self.assertEqual(bbox_schema["minItems"], 4)
        self.assertEqual(bbox_schema["maxItems"], 4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
