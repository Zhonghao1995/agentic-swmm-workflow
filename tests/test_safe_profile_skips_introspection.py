"""SAFE asks about the user's world, not about the runtime's own catalogue.

Live finding F-48 (2026-09-02, scenario S14 under ``--safe``): the golden
fetch/run/audit turn asked thirteen questions, eight of them before the
user's request began (list_skills, list_mcp_servers, four list_mcp_tools,
select_skill, read_skill). Only fetch, run and audit guard a side effect.
"""

from __future__ import annotations

from agentic_swmm.agent.permissions_profile import INTROSPECTION_TOOLS, Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry


class _FakeRegistry:
    def __init__(self, read_only: set[str]) -> None:
        self._read_only = read_only

    def is_read_only(self, name: str) -> bool:
        return name in self._read_only


def test_safe_skips_the_runtime_introspection_tools():
    registry = _FakeRegistry(set(INTROSPECTION_TOOLS) | {"list_dir", "read_file"})
    for name in INTROSPECTION_TOOLS:
        assert Profile.SAFE.auto_approve(name, registry) is True, name


def test_safe_still_asks_before_reading_the_users_files():
    registry = _FakeRegistry({"list_dir", "read_file", "search_files", "read_rpt_summary"})
    for name in ("list_dir", "read_file", "search_files", "read_rpt_summary"):
        assert Profile.SAFE.auto_approve(name, registry) is False, name


def test_safe_asks_before_every_side_effect():
    registry = _FakeRegistry(set(INTROSPECTION_TOOLS))
    for name in ("fetch_swmm_from_canada", "run_swmm_inp", "audit_run", "apply_patch", "write_file"):
        assert Profile.SAFE.auto_approve(name, registry) is False, name


def test_an_introspection_name_the_registry_does_not_call_read_only_still_prompts():
    # Fail-safe: the exception is gated on the registry's own classification.
    registry = _FakeRegistry(set())
    assert Profile.SAFE.auto_approve("list_skills", registry) is False


def test_quick_is_unchanged():
    registry = _FakeRegistry({"list_dir", "list_skills"})
    assert Profile.QUICK.auto_approve("list_dir", registry) is True
    assert Profile.QUICK.auto_approve("run_swmm_inp", registry) is False


def test_the_real_registry_marks_every_introspection_tool_read_only():
    registry = AgentToolRegistry()
    for name in INTROSPECTION_TOOLS:
        assert registry.is_read_only(name), name
