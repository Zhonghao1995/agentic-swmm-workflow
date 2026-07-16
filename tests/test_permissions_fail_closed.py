"""Headless approval fails closed (review P1-2).

With no human on the other end (non-TTY stdin) a side-effecting tool is denied
unless trusted automation opts in with AISWMM_AUTO_APPROVE=1. Before the fix a
non-TTY silently auto-approved every prompt.
"""

from __future__ import annotations

from agentic_swmm.agent import permissions


class _NonTTY:
    def isatty(self) -> bool:
        return False


def test_non_tty_denies_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)
    monkeypatch.setattr(permissions.sys, "stdin", _NonTTY())
    assert permissions.prompt_user("apply_patch") is False


def test_non_tty_auto_approves_with_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("AISWMM_AUTO_APPROVE", "1")
    monkeypatch.setattr(permissions.sys, "stdin", _NonTTY())
    assert permissions.prompt_user("apply_patch") is True


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
