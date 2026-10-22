"""A correction stated in the session becomes a proposal (#566)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.memory import proposals as mod


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    initial = root / "memory" / "initial"
    initial.mkdir()
    (initial / "operational_memory.md").write_text("# Operational memory\n\n## Tool-use rules\n\n- I prefer the CLI.\n", encoding="utf-8")
    (initial / "tool_notes.md").write_text("# Tool notes\n", encoding="utf-8")
    (root / "skills" / "swmm-runner").mkdir(parents=True)
    (root / "skills" / "swmm-runner" / "SKILL.md").write_text("---\nname: swmm-runner\n---\n# Runner\n\n## Conventions\n\n- Read the .rpt.\n", encoding="utf-8")
    (root / "skills" / "swmm-lid-optimization").mkdir(parents=True)
    (root / "skills" / "swmm-lid-optimization" / "SKILL.md").write_text("---\nname: swmm-lid-optimization\n---\n# LID\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(initial / "tool_notes.md"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    monkeypatch.setattr(mod, "enabled_startup_memory_files", lambda: [initial / "operational_memory.md"])
    monkeypatch.setattr(mod, "_context_budget_chars", lambda: 4000)
    from agentic_swmm.memory import tool_notes

    tool_notes.clear_cache()
    yield root
    tool_notes.clear_cache()


def _call(**args):
    return AgentToolRegistry().execute(ToolCall("record_fact", args), session_dir=Path("/tmp/unused-session"))


def test_a_target_tool_writes_a_tool_note_proposal_and_no_file(workspace: Path) -> None:
    result = _call(text="read 06_runner/model.rpt first; a run that never executed SWMM has final_report.md", target="read_file", source_session_id="s1")
    assert result["ok"] is True and result["kind"] == "tool" and result["target"] == "memory/initial/tool_notes.md"
    (proposal,) = mod.list_proposals()
    assert proposal.section == "## read_file" and proposal.drafted_by == "user" and proposal.addition.startswith("- read 06_runner")
    assert "06_runner" not in (workspace / "memory" / "initial" / "tool_notes.md").read_text(encoding="utf-8")
    assert mod.promote(proposal.id)["ok"] is True
    assert "## read_file\n\n- read 06_runner" in (workspace / "memory" / "initial" / "tool_notes.md").read_text(encoding="utf-8")


def test_a_target_skill_or_operational_writes_the_matching_kind(workspace: Path) -> None:
    assert _call(text="Run the audit after every run.", target="swmm-runner")["kind"] == "skill"
    assert _call(text="List a run directory before reading a file in it.", target="operational")["kind"] == "rule"
    kinds = {p.kind: p for p in mod.list_proposals()}
    assert kinds["skill"].target == "skills/swmm-runner/SKILL.md" and kinds["skill"].section == "## Learned from runs"
    assert kinds["rule"].target == "memory/initial/operational_memory.md"
    assert all(p.evidence["affirmed_by"] == "user" for p in kinds.values())


def test_an_unknown_or_private_target_is_refused_with_the_known_names(workspace: Path) -> None:
    result = _call(text="x", target="swmm-nope")
    assert result["ok"] is False and "unknown target 'swmm-nope'" in result["summary"] and "swmm-runner" in result["summary"]
    assert "swmm-lid-optimization" not in result["summary"]
    assert _call(text="x", target="swmm-lid-optimization")["ok"] is False
    assert mod.list_proposals() == []


def test_a_user_affirmed_proposal_is_never_drafted(workspace: Path) -> None:
    from agentic_swmm.memory import proposal_draft

    _call(text="read 06_runner/model.rpt first", target="read_file")

    class Raising:
        def complete(self, **_):
            raise AssertionError("must not be called")

    assert proposal_draft.draft_pending(workspace / "memory" / "store", provider_factory=lambda: Raising()) == []


def test_without_a_target_record_fact_is_unchanged(workspace: Path) -> None:
    result = _call(text="the outfall of record is OUT_0", source_session_id="s9")
    assert result["ok"] is True and mod.list_proposals()[0].kind == "fact"


def test_a_tool_note_over_the_cap_is_refused(workspace: Path) -> None:
    result = _call(text="x" * 300, target="read_file")
    assert result["ok"] is False and "at most 200 characters" in result["summary"]
