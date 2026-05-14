"""Tests for agentic_swmm.audit.chat_note.

PRD section "Module: ChatNote generator".
"""
from __future__ import annotations

import unittest


class BuildChatNoteTests(unittest.TestCase):
    def _sample_session_state(self) -> dict:
        return {
            "session_id": "session-171358",
            "case_id": "inspect-project",
            "goal": "inspect the project layout",
            "status": "ok",
            "created_at": "2026-05-13T17:13:58+00:00",
        }

    def _sample_agent_trace(self) -> list[dict]:
        return [
            {"event": "user_prompt", "text": "list the skills available"},
            {"event": "tool_call", "tool": "list_skills", "args": {}},
            {"event": "tool_result", "tool": "list_skills", "summary": "12 skills available"},
            {"event": "tool_call", "tool": "read_skill", "args": {"skill_name": "swmm-runner"}},
            {"event": "tool_result", "tool": "read_skill", "summary": "read skill swmm-runner"},
            {"event": "assistant_final", "text": "Listed and read swmm-runner."},
            {"event": "session_end", "ok": True},
        ]

    def test_frontmatter_has_required_fields(self) -> None:
        from agentic_swmm.audit.chat_note import build_chat_note

        text = build_chat_note(self._sample_session_state(), self._sample_agent_trace())
        # Frontmatter delimiters present.
        self.assertTrue(text.startswith("---\n"), text[:40])
        head = text.split("---", 2)[1]
        # PRD-locked fields.
        self.assertIn("type: chat-session", head)
        self.assertIn("case: inspect-project", head)
        self.assertIn("date:", head)
        self.assertIn("goal:", head)
        self.assertIn("status: ok", head)
        self.assertIn("tags:", head)

    def test_sections_render_in_order(self) -> None:
        from agentic_swmm.audit.chat_note import build_chat_note

        text = build_chat_note(self._sample_session_state(), self._sample_agent_trace())
        for heading in ("## Goal", "## What user asked", "## What agent did", "## Outcome"):
            self.assertIn(heading, text)
        self.assertLess(text.index("## Goal"), text.index("## What user asked"))
        self.assertLess(text.index("## What user asked"), text.index("## What agent did"))
        self.assertLess(text.index("## What agent did"), text.index("## Outcome"))

    def test_what_agent_did_lists_tool_sequence(self) -> None:
        from agentic_swmm.audit.chat_note import build_chat_note

        text = build_chat_note(self._sample_session_state(), self._sample_agent_trace())
        agent_section = text.split("## What agent did", 1)[1].split("##", 1)[0]
        self.assertIn("list_skills", agent_section)
        self.assertIn("read_skill", agent_section)
        # The order matters: list_skills must appear before read_skill.
        self.assertLess(agent_section.index("list_skills"), agent_section.index("read_skill"))

    def test_no_io_pure_function(self) -> None:
        """build_chat_note must accept already-parsed dicts and return a string."""
        from agentic_swmm.audit.chat_note import build_chat_note

        result = build_chat_note({}, [])
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("---\n"))
        # Missing fields fall back to safe defaults rather than crashing.
        self.assertIn("type: chat-session", result)


if __name__ == "__main__":
    unittest.main()
