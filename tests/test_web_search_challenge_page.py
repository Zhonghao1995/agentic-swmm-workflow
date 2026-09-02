"""web_search tells a blocked backend from an empty result (F-58).

Live finding 2026-09-02 (scenario S10, "10-year design storm for Victoria"):
the tool reported "0 web result(s)" twice because DuckDuckGo's html endpoint
answered with a challenge page. Zero results from a challenge is unknown,
never absent.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers import web
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall

CHALLENGE = "<html><body><div id=\"challenge-form\">Please complete the challenge to continue</div></body></html>"
HTML_RESULTS = (
    "<html><body><a class=\"result__a\" href=\"https://duckduckgo.com/l/?uddg=https%3A%2F%2Fclimate.weather.gc.ca%2Fidf&rut=1\">"
    "ECCC IDF files</a></body></html>"
)
LITE_RESULTS = (
    "<html><body><table><tr><td><a rel=\"nofollow\" href=\"https://www.victoria.ca/idf\" class=\"result-link\">"
    "City of Victoria IDF</a></td></tr></table></body></html>"
)
EMPTY = "<html><body><div class=\"results\">No results.</div></body></html>"


def _run(monkeypatch, pages):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        page = pages[len(calls) - 1] if len(calls) - 1 < len(pages) else pages[-1]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(web, "_fetch_search_page", fake_fetch)
    registry = AgentToolRegistry()
    return registry.execute(ToolCall(name="web_search", args={"query": "Victoria BC IDF curve"}), Path(".")), calls


def test_a_challenge_on_every_endpoint_is_an_honest_failure(monkeypatch):
    result, calls = _run(monkeypatch, [CHALLENGE, CHALLENGE])
    assert result["ok"] is False
    assert "unknown, not absent" in result["summary"]
    assert "web_fetch_url" in result["hint"]
    assert len(calls) == 2


def test_the_lite_endpoint_rescues_a_blocked_html_endpoint(monkeypatch):
    result, calls = _run(monkeypatch, [CHALLENGE, LITE_RESULTS])
    assert result["ok"] is True
    assert result["results"] == [{"title": "City of Victoria IDF", "url": "https://www.victoria.ca/idf"}]
    assert result["backend"] == "lite.duckduckgo.com"


def test_html_results_are_unwrapped_from_the_redirect(monkeypatch):
    result, _ = _run(monkeypatch, [HTML_RESULTS])
    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://climate.weather.gc.ca/idf"


def test_a_parseable_empty_page_is_a_genuine_zero(monkeypatch):
    result, calls = _run(monkeypatch, [EMPTY])
    assert result["ok"] is True
    assert result["results"] == []
    assert "parseable" in result["summary"]
    assert len(calls) == 1


def test_network_errors_on_every_endpoint_are_reported(monkeypatch):
    result, _ = _run(monkeypatch, [OSError("boom"), OSError("boom")])
    assert result["ok"] is False
    assert "web search failed" in result["summary"]
