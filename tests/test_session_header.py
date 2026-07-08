"""Session header + agent snapshot (ADR-0003, layer 1).

The header makes every session dir self-describing: which agent
configuration ran (auto-derived snapshot, content-hashed), where it ran
(environment fingerprint), what it was asked (verbatim goal), and how it
ended (status lifecycle). The snapshot must be an export of what IS:
these tests pin that it reacts to real surface changes (a SKILL.md
edit) and stays identical when nothing changed.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

from agentic_swmm.agent import session_header as sh


class _FakeRegistry:
    """Minimal registry stand-in: schemas() is the only surface used."""

    def __init__(self, schemas: list[dict]) -> None:
        self._schemas = schemas

    def schemas(self) -> list[dict]:
        return list(self._schemas)


_SCHEMAS = [
    {"name": "run_swmm_inp", "parameters": {"type": "object"}},
    {"name": "audit_run", "parameters": {"type": "object"}},
]


class AgentSnapshotTests(unittest.TestCase):
    def test_snapshot_is_deterministic_for_unchanged_surface(self) -> None:
        one = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
        two = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
        self.assertEqual(one, two)

    def test_tool_names_are_sorted_and_hashed(self) -> None:
        snapshot = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
        self.assertEqual(snapshot["tools"], ["audit_run", "run_swmm_inp"])
        self.assertEqual(len(snapshot["tools_schema_sha256"]), 64)

    def test_snapshot_reacts_to_tool_schema_change(self) -> None:
        base = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
        changed_schemas = [dict(_SCHEMAS[0], parameters={"type": "object", "new": True}), _SCHEMAS[1]]
        changed = sh.build_agent_snapshot(registry=_FakeRegistry(changed_schemas), planner="rule")
        self.assertNotEqual(base["tools_schema_sha256"], changed["tools_schema_sha256"])
        self.assertEqual(base["tools"], changed["tools"])  # names unchanged, content changed

    def test_snapshot_reacts_to_skill_md_change(self) -> None:
        """The drift-detection property the ADR requires: edit one
        SKILL.md byte and the snapshot for that skill changes."""
        with TemporaryDirectory() as raw:
            fake_root = Path(raw)
            skill_dir = fake_root / "skills" / "swmm-demo"
            skill_dir.mkdir(parents=True)
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("# demo v1\n", encoding="utf-8")
            with mock.patch.object(sh, "repo_root", return_value=fake_root):
                before = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
                skill_md.write_text("# demo v2\n", encoding="utf-8")
                after = sh.build_agent_snapshot(registry=_FakeRegistry(_SCHEMAS), planner="rule")
        self.assertIn("swmm-demo", before["skills"])
        self.assertNotEqual(before["skills"]["swmm-demo"], after["skills"]["swmm-demo"])

    def test_system_prompt_hash_only_never_text(self) -> None:
        """The header stores a hash, not the prompt text: the prompt may
        embed run-specific context and the snapshot must stay small."""
        snapshot = sh.build_agent_snapshot(
            registry=_FakeRegistry(_SCHEMAS), planner="llm", system_prompt="SECRET PROMPT"
        )
        self.assertEqual(len(snapshot["system_prompt_sha256"]), 64)
        self.assertNotIn("SECRET PROMPT", json.dumps(snapshot))

    def test_real_registry_snapshot_covers_live_surface(self) -> None:
        """One integration point against the real registry: 50+ tools,
        19+ skills, intent map present."""
        snapshot = sh.build_agent_snapshot(planner="rule")
        self.assertGreaterEqual(len(snapshot["tools"]), 50)
        self.assertGreaterEqual(len(snapshot["skills"]), 19)
        self.assertIsNotNone(snapshot["intent_map_sha256"])


class SessionHeaderLifecycleTests(unittest.TestCase):
    def _write(self, session_dir: Path) -> Path:
        return sh.write_session_header(
            session_dir,
            goal="Generate a runnable model for Tod Creek",
            planner="rule",
            profile="quick",
            registry=_FakeRegistry(_SCHEMAS),
        )

    def test_header_and_snapshot_written_and_linked_by_hash(self) -> None:
        with TemporaryDirectory() as raw:
            session_dir = Path(raw)
            header_path = self._write(session_dir)
            header = yaml.safe_load(header_path.read_text(encoding="utf-8"))
            snapshot_text = (session_dir / sh.AGENT_SNAPSHOT_NAME).read_text(encoding="utf-8")
        self.assertEqual(header["session_id"], session_dir.name)
        self.assertEqual(header["goal"], "Generate a runnable model for Tod Creek")
        self.assertEqual(header["status"], "running")
        self.assertEqual(
            header["agent"]["snapshot_sha256"],
            sh._sha256_text(snapshot_text.rstrip("\n")),
        )
        self.assertEqual(len(header["environment"]["fingerprint_sha256"]), 64)
        self.assertEqual(header["environment"]["python"].count("."), 2)

    def test_finalize_stamps_terminal_status(self) -> None:
        with TemporaryDirectory() as raw:
            session_dir = Path(raw)
            self._write(session_dir)
            sh.finalize_session_header(session_dir, "completed")
            header = yaml.safe_load(
                (session_dir / sh.SESSION_HEADER_NAME).read_text(encoding="utf-8")
            )
        self.assertEqual(header["status"], "completed")
        self.assertIn("completed_utc", header)

    def test_finalize_without_header_is_a_silent_noop(self) -> None:
        with TemporaryDirectory() as raw:
            sh.finalize_session_header(Path(raw), "failed")  # must not raise

    def test_try_write_never_raises(self) -> None:
        missing = Path("/nonexistent-root/session")
        self.assertIsNone(
            sh.try_write_session_header(missing, goal="g", planner="rule")
        )


class SingleShotWiringTests(unittest.TestCase):
    """The CLI single-shot path writes the header at start and finalizes
    it by outcome: the dry-run branch is the cheapest full pass."""

    def test_dry_run_session_gets_completed_header(self) -> None:
        import argparse

        from agentic_swmm.agent.single_shot import run_single_shot

        with TemporaryDirectory() as raw:
            session_dir = Path(raw) / "session"
            args = argparse.Namespace(
                goal=["run", "doctor"],
                planner="rule",
                provider=None,
                model=None,
                session_id=None,
                session_dir=session_dir,
                dry_run=True,
                interactive=False,
                max_steps=4,
                verbose=False,
                safe=False,
                quick=False,
            )
            rc = run_single_shot(args)
            header = yaml.safe_load(
                (session_dir / sh.SESSION_HEADER_NAME).read_text(encoding="utf-8")
            )
            snapshot = json.loads(
                (session_dir / sh.AGENT_SNAPSHOT_NAME).read_text(encoding="utf-8")
            )
        self.assertEqual(rc, 0)
        self.assertEqual(header["goal"], "run doctor")
        self.assertEqual(header["planner"], "rule")
        self.assertEqual(header["status"], "completed")
        self.assertEqual(snapshot["permission_profile"], "quick")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
