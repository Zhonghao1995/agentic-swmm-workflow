"""Unit tests for ``agentic_swmm.agent.tool_handlers._shared`` (PRD #128 follow-up).

The shared module hosts the cross-cutting helpers that every tool-handler
family uses — ``_failure``, ``_repo_path``, ``_run_cli_tool``, etc. These
tests pin the helpers' contracts so future moves stay surgical.

The complementary mapping test (``test_tool_handlers_skill_family_mapping``)
asserts the registry-level handler re-exports keep working; this file
asserts the helper-level public behaviour of the new ``_shared`` module.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.agent.types import ToolCall


class SharedHelpersTests(unittest.TestCase):
    def test_failure_returns_canonical_shape(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _failure

        call = ToolCall("demo_acceptance", {"keep_existing": True})
        result = _failure(call, "things went sideways")

        self.assertEqual(result["tool"], "demo_acceptance")
        self.assertEqual(result["args"], {"keep_existing": True})
        self.assertIs(result["ok"], False)
        self.assertEqual(result["summary"], "things went sideways")

    def test_repo_path_rejects_paths_outside_repo(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _repo_path

        # /tmp is never inside the repo — the resolver must return None
        # rather than risk an escape.
        self.assertIsNone(_repo_path("/tmp/somewhere-not-in-repo"))

    def test_repo_path_resolves_repo_relative_value(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _repo_path
        from agentic_swmm.utils.paths import repo_root

        # ``README.md`` always sits at the repo root in this project.
        resolved = _repo_path("README.md")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved, (repo_root() / "README.md").resolve())

    def test_strip_html_removes_tags_and_scripts(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _strip_html

        raw = (
            "<html><head><style>body{}</style></head>"
            "<body><script>x=1</script><p>hello&nbsp;world</p></body></html>"
        )
        self.assertEqual(_strip_html(raw), "hello world")

    def test_try_json_returns_none_on_bad_input(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _try_json

        self.assertIsNone(_try_json("not json at all"))
        self.assertEqual(_try_json('{"ok": true}'), {"ok": True})

    def test_tail_truncates_from_the_right(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _tail

        text = "abcdefghij"
        self.assertEqual(_tail(text, max_chars=4), "ghij")
        # No truncation when shorter than budget.
        self.assertEqual(_tail("hi", max_chars=10), "hi")

    def test_safe_name_strips_unsafe_characters(self) -> None:
        from agentic_swmm.agent.tool_handlers._shared import _safe_name

        # Disallowed punctuation collapses into ``-``.
        self.assertEqual(_safe_name("foo/bar baz"), "foo-bar-baz")
        # Fully-junk input falls back to the ``agent`` sentinel.
        self.assertEqual(_safe_name("///"), "agent")

    def test_run_cli_tool_routes_through_process_helper(self) -> None:
        """``_run_cli_tool`` must invoke ``_run_process_tool`` with the
        ``python -m agentic_swmm.cli`` prefix so audit trails stay uniform.

        We don't actually want to spawn a subprocess in unit tests, so we
        stub the process helper and assert on the command shape.
        """
        from agentic_swmm.agent.tool_handlers import _shared

        captured: dict[str, object] = {}

        def fake_run_process_tool(
            call: ToolCall,
            session_dir: Path,
            command: list[str],
            *,
            cwd: Path,
            timeout: int = 120,
        ) -> dict[str, object]:
            captured["command"] = command
            captured["cwd"] = cwd
            return {"ok": True, "command": command}

        original = _shared._run_process_tool
        _shared._run_process_tool = fake_run_process_tool  # type: ignore[assignment]
        try:
            call = ToolCall("doctor", {})
            _shared._run_cli_tool(call, Path("/tmp/session"), ["doctor"])
        finally:
            _shared._run_process_tool = original  # type: ignore[assignment]

        command = captured["command"]
        assert isinstance(command, list)
        self.assertEqual(command[1:3], ["-m", "agentic_swmm.cli"])
        self.assertEqual(command[-1], "doctor")


if __name__ == "__main__":
    unittest.main()
