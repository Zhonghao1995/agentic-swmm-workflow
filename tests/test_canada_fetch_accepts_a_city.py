"""``fetch_swmm_from_canada`` accepts a published city name.

Live test 2026-09-03 (S40): "downtown Regina" ended in "give me a bounding
box". The service publishes every real-network city's coverage extent, so a
city name resolves to a 1 km window at the centre of that extent, the result
says so, and bbox narrows it. Nothing is guessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_canada import fetch_swmm_from_canada_tool
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.integrations.swmmcanada_runner import CITY_WINDOW_DEG, city_window

COVERAGE = {
    "real_network_cities": [
        {"key": "victoria", "label": "Victoria, BC", "coverage_bbox": [-123.43, 48.4, -123.33, 48.47]},
        {"key": "regina", "label": "Regina, SK", "coverage_bbox": [-104.8, 50.35, -104.45, 50.55]},
    ],
    "synthesis": "anywhere in Canada from open data",
}


def test_city_matches_by_key_or_label_and_centres_a_window() -> None:
    bbox, label, error = city_window("regina", COVERAGE)
    assert error is None and label == "Regina, SK"
    assert bbox == [-104.63, 50.4465, -104.62, 50.4535]
    assert round(bbox[2] - bbox[0], 4) == CITY_WINDOW_DEG[0]
    assert round(bbox[3] - bbox[1], 4) == CITY_WINDOW_DEG[1]
    assert city_window("Regina, SK", COVERAGE)[0] == bbox
    assert city_window("Victoria", COVERAGE)[1] == "Victoria, BC"


def test_unknown_city_lists_what_is_offered() -> None:
    bbox, label, error = city_window("Winnipeg", COVERAGE)
    assert bbox is None and label is None
    assert "not a published city" in error
    assert "Regina, SK" in error and "Victoria, BC" in error
    assert "Pass bbox" in error


def test_tool_resolves_the_city_and_says_so_in_the_result() -> None:
    seen: dict[str, object] = {}

    def fake_fetch(aoi, start, end, **kwargs):
        seen["aoi"] = aoi
        return mock.Mock(
            inp_path=Path("/tmp/run/05_builder/model.inp"),
            run_dir=Path("/tmp/run"),
            zip_path=Path("/tmp/run/10_upstream/swmmcanada/swmm_model.zip"),
            service_url="https://swmm.example",
            task_id="t1",
            mode="real",
            validation={},
            warnings=[],
        )

    with TemporaryDirectory() as tmp:
        call = ToolCall(
            name="fetch_swmm_from_canada",
            args={"city": "Regina", "start_date": "2023-06-10", "end_date": "2023-06-13", "run_dir": tmp},
        )
        with mock.patch("agentic_swmm.integrations.swmmcanada_runner.resolve_base_url", return_value="https://swmm.example"), mock.patch(
            "agentic_swmm.integrations.swmmcanada_runner.fetch_coverage", return_value=COVERAGE
        ), mock.patch("agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi", side_effect=fake_fetch):
            result = fetch_swmm_from_canada_tool(call, Path(tmp))
    assert result["ok"] is True, result
    ring = json.loads(seen["aoi"])["coordinates"][0]
    assert ring[0] == [-104.63, 50.4465]
    assert "published coverage for Regina, SK" in result["summary"]
    assert "pass bbox" in result["summary"]
    payload = next(v for k, v in result.items() if isinstance(v, dict) and "aoi_note" in v)
    assert payload["aoi_note"].startswith("AOI = a 1 km window")


def test_unknown_city_fails_with_the_offered_list() -> None:
    with TemporaryDirectory() as tmp:
        call = ToolCall(
            name="fetch_swmm_from_canada",
            args={"city": "Winnipeg", "start_date": "2023-06-10", "end_date": "2023-06-13", "run_dir": tmp},
        )
        with mock.patch("agentic_swmm.integrations.swmmcanada_runner.resolve_base_url", return_value="https://swmm.example"), mock.patch(
            "agentic_swmm.integrations.swmmcanada_runner.fetch_coverage", return_value=COVERAGE
        ):
            result = fetch_swmm_from_canada_tool(call, Path(tmp))
    assert result["ok"] is False
    assert "not a published city" in json.dumps(result)


def test_a_placeholder_bbox_next_to_a_city_does_not_block_the_city() -> None:
    """S40 r2: the planner sent bbox=[0,0,0,0] beside city="Regina"."""
    seen: dict[str, object] = {}

    def fake_fetch(aoi, start, end, **kwargs):
        seen["aoi"] = aoi
        return mock.Mock(
            inp_path=Path("/tmp/run/05_builder/model.inp"), run_dir=Path("/tmp/run"),
            zip_path=Path("/tmp/run/z.zip"), service_url="https://swmm.example", task_id="t2",
            mode="real", validation={}, warnings=[],
        )

    with TemporaryDirectory() as tmp:
        call = ToolCall(
            name="fetch_swmm_from_canada",
            args={
                "aoi_geojson": "", "base_url": "", "bbox": [0, 0, 0, 0], "city": "Regina",
                "start_date": "2023-06-10", "end_date": "2023-06-13", "run_dir": tmp,
            },
        )
        with mock.patch("agentic_swmm.integrations.swmmcanada_runner.resolve_base_url", return_value="https://swmm.example"), mock.patch(
            "agentic_swmm.integrations.swmmcanada_runner.fetch_coverage", return_value=COVERAGE
        ), mock.patch("agentic_swmm.integrations.swmmcanada_runner.fetch_from_aoi", side_effect=fake_fetch):
            result = fetch_swmm_from_canada_tool(call, Path(tmp))
    assert result["ok"] is True, result
    assert json.loads(seen["aoi"])["coordinates"][0][0] == [-104.63, 50.4465]


def test_a_zero_area_bbox_without_a_city_is_refused_clearly() -> None:
    with TemporaryDirectory() as tmp:
        call = ToolCall(
            name="fetch_swmm_from_canada",
            args={"bbox": [0, 0, 0, 0], "start_date": "2023-06-10", "end_date": "2023-06-13", "run_dir": tmp},
        )
        result = fetch_swmm_from_canada_tool(call, Path(tmp))
    assert result["ok"] is False
    assert "zero area" in json.dumps(result)


def test_timeout_hint_says_not_to_repeat_the_aoi_and_how_to_shrink_it() -> None:
    from agentic_swmm.agent.tool_handlers.swmm_canada import _stage_hint

    hint = _stage_hint("timeout")
    # F-163 (2026-09-05): since #544 a repeat of the same request resumes the build.
    assert "Ask again with the SAME area and dates in this run" in hint
    assert "Do not repeat" not in hint
    assert "city only" in hint


def test_spec_reserves_bbox_for_user_coordinates() -> None:
    from agentic_swmm.agent.tool_handlers.swmm_canada import tool_specs

    spec = next(s for s in tool_specs() if s.name == "fetch_swmm_from_canada")
    assert "pass city only" in spec.description
    assert "the user gave" in spec.parameters["properties"]["bbox"]["description"]
