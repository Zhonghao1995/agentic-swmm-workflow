"""The review surface and the rule budget guard (#563).

Skill refinement loop, ticket 4: the list says what each proposal is and
how much evidence it has; one proposal prints in full; a dry run changes
nothing; a rule promotion that would evict a startup file is refused with
the numbers; the session end and doctor say when proposals wait.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory.run_failures import record_run_failures

KELOWNA = "runs/live-test-2026-09/2026-09-05/103702_downtown-kelowna-bc_run"
TODCREEK = "runs/live-test-2026-09/2026-09-26/044009_todcreek_run"


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


def _missing_rpt(store: Path, session: str, run_dir: str) -> None:
    record_run_failures(store, session, [
        _fail("read_file", f"file not found: /w/{run_dir}/06_run/model.rpt", path=f"{run_dir}/06_run/model.rpt"),
        _ok("read_file", path=f"{run_dir}/06_runner/model.rpt"),
    ])


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    initial = root / "memory" / "initial"
    initial.mkdir()
    (initial / "identification_memory.md").write_text("# Identity\n\n" + "i" * 1000 + "\n", encoding="utf-8")
    (initial / "operational_memory.md").write_text("# Operational memory\n\n## Tool-use rules\n\n" + "o" * 1500 + "\n", encoding="utf-8")
    (initial / "evidence_memory.md").write_text("# Evidence\n\n" + "e" * 1200 + "\n", encoding="utf-8")
    (initial / "tool_notes.md").write_text("# Tool notes\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(initial / "tool_notes.md"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    monkeypatch.setattr(mod, "enabled_startup_memory_files", lambda: [initial / "identification_memory.md", initial / "operational_memory.md", initial / "evidence_memory.md"])
    monkeypatch.setattr(mod, "_context_budget_chars", lambda: 3000)
    from agentic_swmm.memory import tool_notes

    tool_notes.clear_cache()
    yield root
    tool_notes.clear_cache()


def _rule_proposal(root: Path, text: str) -> mod.Proposal:
    """A kind rule proposal written the way a user-affirmed experience will be."""
    target = mod.RULE_TARGET
    before = mod._read_target(target)
    addition = f"- {text}"
    proposal = mod.Proposal(
        id=mod._next_id(mod.proposals_dir()), kind="rule", status="open", target=target, key=f"rule|{text[:20]}",
        created_utc=mod._utcnow(), addition=addition,
        diff=mod._unified_diff(target, before, mod.apply_addition(before, addition, section=mod.SECTION)),
        evidence={"runs": ["s1"], "cases": []},
    )
    return mod._write(mod.proposals_dir(), proposal, "a rule", ["- stated by the user"], "rule")


# --- the list and one proposal ------------------------------------------------


def test_the_list_names_each_proposal_with_its_evidence(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    mod.propose_from_failures(store.parent)
    mod.propose_fact("the outfall of record is OUT_0", source_session_id="s9")
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=True, model=None)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("id   status     kind   evidence")
    assert lines[1] == "001  open       tool   3 runs, 2 cases    memory/initial/tool_notes.md"
    assert lines[2].startswith('     Fails with "file not found: <path>": calling it again with a different `path` worked')
    assert lines[3] == "002  open       fact   session s9         memory/facts.md"
    assert lines[4] == "     text: the outfall of record is OUT_0"
    assert "promote <id> [--dry-run]" in lines[-1]


def test_one_proposal_prints_in_full(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    proposal = mod.propose_fact("metric units", source_session_id="s1")
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id="1", draft=None, no_draft=True, model=None)) == 0
    assert capsys.readouterr().out == proposal.path.read_text(encoding="utf-8")
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id="7", draft=None, no_draft=True, model=None)) == 1


# --- dry run ------------------------------------------------------------------------


def test_a_dry_run_prints_the_diff_and_writes_nothing(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    proposal = mod.propose_fact("metric units", source_session_id="s1")
    facts = workspace / "memory" / "facts.md"
    before = facts.read_text(encoding="utf-8")
    assert memory_cmd.promote_main(argparse.Namespace(proposal_id="001", dry_run=True)) == 0
    out = capsys.readouterr().out
    assert out.startswith("--- a/memory/facts.md\n+++ b/memory/facts.md\n") and "nothing written" in out
    assert facts.read_text(encoding="utf-8") == before
    assert mod.load_all()[0].status == "open"
    result = mod.promote(proposal.id, dry_run=True)
    assert result["ok"] is True and result["dry_run"] is True and result["diff"] == proposal.diff
    assert mod.promote(proposal.id)["ok"] is True


# --- the rule budget guard -------------------------------------------------------------


def test_a_rule_that_evicts_a_startup_file_is_refused_with_the_numbers(workspace: Path) -> None:
    proposal = _rule_proposal(workspace, "x" * 500)
    result = mod.promote(proposal.id)
    assert result["ok"] is False
    assert "would push operational_memory.md out of the startup memory budget" in result["reason"]
    import re

    assert "3000 characters" in result["reason"]
    assert re.search(r"operational_memory\.md 1542 -> 2068 characters", result["reason"]), result["reason"]
    assert "o" * 1500 in (workspace / "memory" / "initial" / "operational_memory.md").read_text(encoding="utf-8")
    assert mod.load_all()[0].status == "open"


def test_a_rule_that_fits_is_applied(workspace: Path) -> None:
    proposal = _rule_proposal(workspace, "I list a run directory before reading a file in it.")
    assert mod.startup_budget_refusal(workspace / "memory" / "initial" / "operational_memory.md", "short") is None
    result = mod.promote(proposal.id)
    assert result["ok"] is True
    text = (workspace / "memory" / "initial" / "operational_memory.md").read_text(encoding="utf-8")
    assert text.endswith("## Learned from runs\n\n- I list a run directory before reading a file in it.\n")


def test_a_file_outside_the_startup_set_has_no_budget_guard(workspace: Path) -> None:
    assert mod.startup_budget_refusal(workspace / "memory" / "initial" / "tool_notes.md", "x" * 100000) is None


# --- the notices -----------------------------------------------------------------------


def test_the_session_end_says_when_proposals_wait(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.agent import runtime_loop

    runtime_loop._announce_open_proposals()
    assert capsys.readouterr().out == ""
    mod.propose_fact("metric units", source_session_id="s1")
    runtime_loop._announce_open_proposals()
    assert "1 proposal await your decision: aiswmm memory proposals" in capsys.readouterr().out


def test_doctor_counts_the_open_proposals(workspace: Path) -> None:
    from agentic_swmm.diagnostics.doctor_report import render_open_proposals_line

    assert render_open_proposals_line() == "  proposals awaiting a decision: 0"
    mod.propose_fact("metric units", source_session_id="s1")
    mod.propose_fact("imperial units", source_session_id="s2")
    assert render_open_proposals_line() == "  proposals awaiting a decision: 2 (aiswmm memory proposals)"
