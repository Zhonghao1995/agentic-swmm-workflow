"""Cover the defensive branches added in the v0.7.7 hardening pass.

These exercise the error/edge paths the behavior tests did not reach: the
install-aware handlers' "bundled script missing" branch (review P1-1), the
web-fetch host guard's no-host and redirect-revalidation paths (review P1-3),
and the fail-closed approval's EOF branch (review P1-2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent import permissions
from agentic_swmm.agent.tool_handlers import introspection, swmm_report, swmm_review, web
from agentic_swmm.agent.types import ToolCall


def _raise_missing(*_args, **_kwargs):
    raise FileNotFoundError("bundled script not found; run: pip install aiswmm")


def test_report_handler_surfaces_missing_script(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(swmm_report, "resource_path", _raise_missing)
    result = swmm_report._generate_report_tool(ToolCall("generate_report", {"run_dir": str(tmp_path)}), tmp_path)
    assert result["ok"] is False
    assert "not found" in result["summary"].lower()


def test_review_handler_surfaces_missing_script(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(swmm_review, "resource_path", _raise_missing)
    result = swmm_review._review_run_tool(ToolCall("review_run", {"run_dir": str(tmp_path)}), tmp_path)
    assert result["ok"] is False


def test_retrieve_memory_surfaces_missing_script(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(introspection, "resource_path", _raise_missing)
    result = introspection._retrieve_memory_tool(ToolCall("retrieve_memory", {"query": "flooding"}), tmp_path)
    assert result["ok"] is False


def test_assert_public_host_rejects_missing_host() -> None:
    with pytest.raises(ValueError):
        web._assert_public_host("http:///only-a-path")


def test_redirect_handler_revalidates_target() -> None:
    handler = web._PublicOnlyRedirectHandler()
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/internal")


def test_prompt_user_denies_on_eof(monkeypatch) -> None:
    monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)

    class _TTY:
        def isatty(self) -> bool:
            return True

    def _raise_eof(*_args, **_kwargs):
        raise EOFError()

    monkeypatch.setattr(permissions.sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", _raise_eof)
    assert permissions.prompt_user("apply_patch") is False


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
