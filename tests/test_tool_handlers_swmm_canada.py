"""Unit tests for the ``fetch_swmm_from_canada`` typed-tool handler.

The handler is the LLM-facing surface for the SWMMCanada upstream INP
source (ADR-0001). It validates typed params, converts a bbox to a
GeoJSON polygon, and calls
``agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi``.

Tests stub ``fetch_from_aoi`` rather than hitting the network — the
runner itself is covered by ``tests/test_swmmcanada_runner.py``.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_canada import (
    _bbox_to_polygon,
    _stage_hint,
    fetch_swmm_from_canada_tool,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, CanadaFetchResult

BBOX = [-123.4, 48.4, -123.3, 48.5]  # Victoria-ish
DATES = {"start_date": "2022-06-01", "end_date": "2022-06-07"}


def _fake_result(run_dir: Path) -> CanadaFetchResult:
    # Paths mirror the runner's canonical layout (ADR-0004): model.inp in
    # 05_builder/, swmm_model.zip in the opaque 10_upstream/swmmcanada/ box.
    return CanadaFetchResult(
        inp_path=run_dir / "05_builder" / "model.inp",
        run_dir=run_dir,
        zip_path=run_dir / "10_upstream" / "swmmcanada" / "swmm_model.zip",
        service_url="http://svc",
        task_id="t1",
        mode="real",
        validation={"accepted": True},
        warnings=(),
    )


class StageHintTests(unittest.TestCase):
    def test_config_missing_hint_points_at_env_var(self) -> None:
        self.assertIn("AISWMM_SWMMCANADA_URL", _stage_hint("config_missing"))

    def test_task_failed_hint_mentions_supported_cities(self) -> None:
        hint = _stage_hint("task_failed")
        self.assertIn("Canadian", hint)
        self.assertIn("Regina", hint)  # 8th city, added upstream after v0.7.4

    def test_extract_hint_points_at_zip_not_service(self) -> None:
        hint = _stage_hint("extract")
        self.assertIn("zip", hint)
        self.assertNotIn("service URL", hint)

    def test_unknown_stage_has_default_hint(self) -> None:
        self.assertTrue(_stage_hint("whatever"))


class BboxToPolygonTests(unittest.TestCase):
    def test_bbox_becomes_closed_geojson_polygon(self) -> None:
        gj = json.loads(_bbox_to_polygon(BBOX))
        self.assertEqual(gj["type"], "Polygon")
        ring = gj["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])  # closed ring
        self.assertEqual(len(ring), 5)


class HandlerTests(unittest.TestCase):
    def test_success_payload_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                payload = fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["results"]["inp_path"], str(run_dir / "05_builder" / "model.inp"))
            self.assertEqual(
                payload["results"]["zip_path"],
                str(run_dir / "10_upstream" / "swmmcanada" / "swmm_model.zip"),
            )
            self.assertEqual(payload["results"]["task_id"], "t1")
            self.assertEqual(payload["results"]["service_url"], "http://svc")
            self.assertEqual(payload["results"]["mode"], "real")
            self.assertEqual(payload["results"]["validation"], {"accepted": True})
            fetch.assert_called_once()

    def test_bbox_is_converted_and_passed_as_geojson(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
            aoi_arg = fetch.call_args.args[0]
            self.assertEqual(json.loads(aoi_arg)["type"], "Polygon")

    def test_explicit_aoi_geojson_is_passed_through(self) -> None:
        # Victoria-ish — must sit inside Canada so only the passthrough is under test.
        aoi = '{"type":"Polygon","coordinates":[[[-123.4,48.4],[-123.3,48.4],[-123.3,48.5],[-123.4,48.5],[-123.4,48.4]]]}'
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"aoi_geojson": aoi, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertEqual(fetch.call_args.args[0], aoi)

    def test_infiltration_is_passed_through(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall(
                "fetch_swmm_from_canada",
                {"bbox": BBOX, "run_dir": str(run_dir), "infiltration": "GREEN_AMPT", **DATES},
            )
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertEqual(fetch.call_args.kwargs["infiltration"], "GREEN_AMPT")

    def test_infiltration_defaults_to_none(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertIsNone(fetch.call_args.kwargs["infiltration"])

    def test_aoi_outside_canada_fails_soft_before_any_http(self) -> None:
        tokyo = [139.6, 35.6, 139.8, 35.8]
        call = ToolCall("fetch_swmm_from_canada", {"bbox": tokyo, **DATES})
        with TemporaryDirectory() as tmp:
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi"
            ) as fetch:
                payload = fetch_swmm_from_canada_tool(call, Path(tmp))
        fetch.assert_not_called()  # rejected deterministically, zero round-trips
        self.assertFalse(payload["ok"])
        self.assertIn("outside Canada", json.dumps(payload))
        self.assertIn("synth_swmm_from_bbox", json.dumps(payload))

    def test_aoi_near_southern_border_is_allowed_through(self) -> None:
        windsor = [-83.1, 42.2, -82.9, 42.4]  # Windsor, ON — Canada's southern edge
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": windsor, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                payload = fetch_swmm_from_canada_tool(call, Path(tmp))
        fetch.assert_called_once()
        self.assertTrue(payload["ok"])

    def test_unreadable_aoi_geometry_is_not_blocked(self) -> None:
        # The pre-check never blocks geometry it can't read — upstream stays
        # the authority on anything beyond a plain GeoJSON Polygon.
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall(
                "fetch_swmm_from_canada",
                {"aoi_geojson": '{"type":"Feature","geometry":null}', "run_dir": str(run_dir), **DATES},
            )
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
        fetch.assert_called_once()

    def test_progress_callback_is_wired_into_the_fetch(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                return_value=_fake_result(run_dir),
            ) as fetch:
                fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertTrue(callable(fetch.call_args.kwargs["progress"]))

    def test_missing_aoi_and_bbox_fails_soft(self) -> None:
        call = ToolCall("fetch_swmm_from_canada", {**DATES})
        with TemporaryDirectory() as tmp:
            payload = fetch_swmm_from_canada_tool(call, Path(tmp))
        self.assertFalse(payload["ok"])

    def test_missing_dates_fails_soft(self) -> None:
        call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX})
        with TemporaryDirectory() as tmp:
            payload = fetch_swmm_from_canada_tool(call, Path(tmp))
        self.assertFalse(payload["ok"])

    def test_fetch_error_maps_to_failure_with_stage_and_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            call = ToolCall("fetch_swmm_from_canada", {"bbox": BBOX, "run_dir": str(run_dir), **DATES})
            with mock.patch(
                "agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi",
                side_effect=CanadaFetchError("config_missing", "no base url"),
            ):
                payload = fetch_swmm_from_canada_tool(call, Path(tmp))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["stage"], "config_missing")
            self.assertIn("AISWMM_SWMMCANADA_URL", payload["hint"])


if __name__ == "__main__":
    unittest.main()
