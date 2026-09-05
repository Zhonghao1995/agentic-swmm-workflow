"""F-149 (2026-09-04, S64): "which cities can you fetch?" gets the service's own list.

The only city names a user could see were the six examples baked into the
fetch tool's description. ``list_canada_cities`` reads the coverage listing
(one GET) and returns every published real-network city with its extent.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers import swmm_canada
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.integrations import swmmcanada_runner

COVERAGE = {
    "real_network_cities": [
        {"key": "victoria", "label": "Victoria, BC", "coverage_bbox": [-123.4, 48.4, -123.3, 48.5]},
        {"key": "kelowna", "label": "Kelowna, BC", "coverage_bbox": [-119.5, 49.8, -119.4, 49.9], "systems": ["storm", "sanitary"]},
        {"key": "regina", "label": "Regina, SK"},
        "not-a-dict",
    ]
}


def _call(**args):
    return ToolCall(name="list_canada_cities", args=args)


def test_the_tool_is_declared_read_only() -> None:
    spec = next(s for s in swmm_canada.tool_specs() if s.name == "list_canada_cities")
    assert spec.is_read_only is True
    assert "which cities" in spec.description


def test_the_list_comes_from_the_coverage_listing(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(swmmcanada_runner, "resolve_base_url", lambda base: "http://svc")
    monkeypatch.setattr(swmmcanada_runner, "fetch_coverage", lambda url, **kw: seen.setdefault("url", url) and COVERAGE or COVERAGE)
    result = swmm_canada.list_canada_cities_tool(_call(), tmp_path)
    assert result["ok"] is True
    assert seen["url"] == "http://svc"
    assert result["count"] == 3
    assert [c["label"] for c in result["cities"]] == ["Kelowna, BC", "Regina, SK", "Victoria, BC"]
    assert result["cities"][0]["systems"] == ["storm", "sanitary"]
    assert "coverage_bbox" not in result["cities"][1]
    assert result["summary"].startswith("3 published real-network cities at http://svc: Kelowna, BC, Regina, SK, Victoria, BC.")


def test_the_model_sees_the_list(monkeypatch, tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    monkeypatch.setattr(swmmcanada_runner, "resolve_base_url", lambda base: "http://svc")
    monkeypatch.setattr(swmmcanada_runner, "fetch_coverage", lambda url, **kw: COVERAGE)
    result = swmm_canada.list_canada_cities_tool(_call(), tmp_path)
    shown = AgentToolRegistry().output_for_model(result)
    assert shown["count"] == 3 and len(shown["cities"]) == 3 and shown["service_url"] == "http://svc"


def test_an_unreachable_service_is_a_stage_tagged_failure(monkeypatch, tmp_path: Path) -> None:
    def _down(url, **kw):
        raise swmmcanada_runner.CanadaFetchError("coverage", "could not read the service's coverage listing: refused")

    monkeypatch.setattr(swmmcanada_runner, "resolve_base_url", lambda base: "http://svc")
    monkeypatch.setattr(swmmcanada_runner, "fetch_coverage", _down)
    result = swmm_canada.list_canada_cities_tool(_call(), tmp_path)
    assert result["ok"] is False
    assert "coverage listing" in result["summary"]


def test_the_prompt_points_the_question_at_the_tool() -> None:
    from agentic_swmm.agent import prompts

    assert "call list_canada_cities" in Path(prompts.__file__).read_text(encoding="utf-8")
