"""web_fetch_url must refuse private/loopback/metadata targets (review P1-3).

The guard resolves the host and rejects non-public addresses, blocks embedded
credentials, and is no longer auto-approved as a read-only tool. IP literals are
used so the tests need no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.tool_handlers.web import _assert_public_host, _web_fetch_url_tool
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.1/admin",
        "http://[::1]/",
        "http://user:pass@example.com/",
    ],
)
def test_assert_public_host_rejects_unsafe(url: str) -> None:
    with pytest.raises(ValueError):
        _assert_public_host(url)


def test_fetch_handler_refuses_loopback() -> None:
    result = _web_fetch_url_tool(ToolCall("web_fetch_url", {"url": "http://127.0.0.1:8080/"}), Path("."))
    assert result["ok"] is False
    assert "refused" in result["summary"]


def test_web_fetch_url_is_not_read_only() -> None:
    # Network egress must go through the approval gate, not auto-approve.
    assert AgentToolRegistry().is_read_only("web_fetch_url") is False


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
