"""Tests for the agent-shell chat-note behaviour.

PRD M8:
- At the end of a chat-only turn the session dir must contain
  chat_note.md generated via agentic_swmm.audit.chat_note.build_chat_note.
- final_report.md must NOT be written for chat sessions.
- The non-interactive single-shot path still produces final_report.md.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class WriteChatNoteHelperTests(unittest.TestCase):
    def test_chat_session_writes_chat_note_and_skips_final_report(self) -> None:
        from agentic_swmm.commands import agent as agent_cmd

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "chat-session"
            session_dir.mkdir()
            # Pre-existing session_state.json + agent_trace.jsonl as the
            # chat fixture would carry, plus a few trace events for the
            # generator to summarise.
            (session_dir / "session_state.json").write_text(
                '{"case_id": "demo-chat", "goal": "list skills", "status": "ok"}',
                encoding="utf-8",
            )
            (session_dir / "agent_trace.jsonl").write_text(
                '{"event":"user_prompt","text":"list skills"}\n'
                '{"event":"tool_call","tool":"list_skills","args":{}}\n'
                '{"event":"tool_result","tool":"list_skills","summary":"12 skills available"}\n'
                '{"event":"assistant_final","text":"done"}\n'
                '{"event":"session_end","ok":true}\n',
                encoding="utf-8",
            )

            agent_cmd._write_chat_note_for_session(session_dir)

            chat_note = session_dir / "chat_note.md"
            self.assertTrue(chat_note.exists(), "chat_note.md must be written")
            text = chat_note.read_text(encoding="utf-8")
            self.assertIn("type: chat-session", text)
            self.assertIn("list_skills", text)

    def test_swmm_run_dir_does_not_get_chat_note(self) -> None:
        from agentic_swmm.commands import agent as agent_cmd

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "swmm-run"
            # Looks like a SWMM run via the existing _is_swmm_run_dir hint.
            (run_dir / "05_runner").mkdir(parents=True)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "05_runner" / "model.out").write_text("x", encoding="utf-8")
            (run_dir / "05_runner" / "model.rpt").write_text("x", encoding="utf-8")
            agent_cmd._write_chat_note_for_session(run_dir)
            self.assertFalse((run_dir / "chat_note.md").exists())


class FinalReportSuppressedForChatTests(unittest.TestCase):
    def test_run_openai_planner_skips_final_report_for_chat_sessions(self) -> None:
        """When called from the interactive shell with a chat session
        dir, the planner harness must not write final_report.md.
        """
        from agentic_swmm.commands import agent as agent_cmd

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "chat-session"
            session_dir.mkdir()
            (session_dir / "session_state.json").write_text("{}", encoding="utf-8")
            (session_dir / "agent_trace.jsonl").write_text("", encoding="utf-8")

            args = SimpleNamespace(
                provider=None,
                model=None,
                planner="openai",
                dry_run=True,
                verbose=False,
                max_steps=2,
            )

            class FakeOutcome:
                ok = True
                plan: list = []
                results: list = []
                final_text = ""

            fake_outcome = FakeOutcome()

            def _config_get(key, default=None):
                if key == "provider.default":
                    return "openai"
                if key == "openai.model":
                    return "gpt-test"
                return default

            fake_config = mock.MagicMock()
            fake_config.get.side_effect = _config_get
            with mock.patch.object(agent_cmd, "load_config", return_value=fake_config):
                with mock.patch.object(agent_cmd, "OpenAIProvider", return_value=mock.MagicMock()):
                    with mock.patch.object(agent_cmd, "run_openai_plan", return_value=fake_outcome):
                        with mock.patch.object(agent_cmd, "AgentExecutor", return_value=mock.MagicMock()):
                            rc = agent_cmd._run_openai_planner(
                                args,
                                goal="say hi",
                                session_dir=session_dir,
                                trace_path=session_dir / "agent_trace.jsonl",
                                registry=mock.MagicMock(names={"doctor"}),
                                chat_session=True,
                            )
            self.assertEqual(rc, 0)
            self.assertFalse((session_dir / "final_report.md").exists())
            self.assertTrue((session_dir / "chat_note.md").exists())


if __name__ == "__main__":
    unittest.main()
