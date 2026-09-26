"""Proposals with a human decision (memory simplification PR 4).

A fix the failure loop recorded in enough runs and cases becomes a
proposal file; a candidate fact from ``record_fact`` becomes one too.
Nothing takes effect until ``promote``; ``reject`` is remembered.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory.run_failures import record_run_failures


def _fail(tool: str, summary: str, **args):
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args):
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


def _record_fix(store: Path, run_id: str, tool: str = "run_swmm_inp") -> None:
    record_run_failures(store, run_id, [
        _fail(tool, f"external INP file not found: /w/runs/{run_id}/model_x.inp", inp_path=f"runs/{run_id}/model_x.inp"),
        _ok(tool, inp_path=f"runs/{run_id}/06_run/model.inp"),
    ])


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace with a store, a proposals folder and a fake checkout for targets."""
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    (root / "memory" / "initial").mkdir()
    (root / "memory" / "initial" / "operational_memory.md").write_text("# Operational memory\n\n## Tool-use rules\n\n- I prefer the CLI.\n", encoding="utf-8")
    (root / "skills" / "swmm-runner").mkdir(parents=True)
    (root / "skills" / "swmm-runner" / "SKILL.md").write_text("---\nname: swmm-runner\n---\n# Runner\n\n## Conventions\n\n- Read the .rpt.\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    return root


# --- the evidence gate ------------------------------------------------------


def test_same_fix_in_three_runs_across_two_cases_becomes_one_skill_proposal(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for run_id in ("100000_tod-creek_run", "110000_tod-creek_run", "120000_regina_run"):
        _record_fix(store, run_id)
    created = mod.propose_from_failures(store.parent)
    assert [proposal.kind for proposal in created] == ["skill"]
    proposal = created[0]
    assert proposal.id == "001"
    assert proposal.target == "skills/swmm-runner/SKILL.md"
    assert proposal.evidence["runs"] == ["100000_tod-creek_run", "110000_tod-creek_run", "120000_regina_run"]
    assert proposal.evidence["cases"] == ["regina", "tod-creek"]
    assert "calling it again with a different `inp_path` worked" in proposal.addition
    assert proposal.diff.startswith("--- a/skills/swmm-runner/SKILL.md\n+++ b/skills/swmm-runner/SKILL.md\n")
    assert "+## Learned from runs" in proposal.diff
    assert proposal.path is not None and proposal.path.name == "001-run-swmm-inp-external-inp-file-not-found-path.md"
    text = proposal.path.read_text(encoding="utf-8")
    assert text.startswith("---\nid: 001\nkind: skill\nstatus: open\ntarget: skills/swmm-runner/SKILL.md\n")
    assert "aiswmm memory promote 001" in text
    # a fourth run with the same fix does not propose again
    _record_fix(store, "130000_vancouver_run")
    assert mod.propose_from_failures(store.parent) == []
    assert len(mod.load_all()) == 1


def test_too_few_runs_or_cases_propose_nothing(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    _record_fix(store, "100000_tod-creek_run")
    _record_fix(store, "110000_regina_run")
    assert mod.propose_from_failures(store.parent) == []
    _record_fix(store, "120000_tod-creek_run")  # three runs, still two cases -> proposes
    assert len(mod.propose_from_failures(store.parent)) == 1
    store2 = workspace / "memory" / "store2" / "run_failures.jsonl"
    store2.parent.mkdir()
    for run_id in ("100000_tod-creek_run", "110000_tod-creek_run", "120000_tod-creek_run"):
        _record_fix(store2, run_id, tool="read_file")
    assert mod.propose_from_failures(store2.parent) == []  # one case only


def test_agent_internal_tool_targets_the_operational_memory_rule(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for run_id in ("100000_a_run", "110000_b_run", "120000_c_run"):
        record_run_failures(store, run_id, [
            _fail("run_allowed_command", "command is not allowlisted", command="python -c 1"),
            _ok("read_rpt_summary", rpt_path="r.rpt"),
        ])
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.kind == "rule" and proposal.target == "memory/initial/operational_memory.md"
    assert "using `read_rpt_summary` instead worked" in proposal.addition
    assert proposal.key.endswith("|read_rpt_summary|instead")


def test_a_rejected_key_is_never_proposed_again(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for run_id in ("100000_a_run", "110000_b_run", "120000_c_run"):
        _record_fix(store, run_id)
    (proposal,) = mod.propose_from_failures(store.parent)
    result = mod.reject(proposal.id, reason="the runner already resolves that path")
    assert result == {"ok": True, "id": "001", "reason": "the runner already resolves that path"}
    reloaded = mod.load_all()[0]
    assert reloaded.status == "rejected" and reloaded.reason == "the runner already resolves that path" and reloaded.decided_utc
    _record_fix(store, "130000_d_run")
    assert mod.propose_from_failures(store.parent) == []
    assert mod.list_proposals() == [] and len(mod.list_proposals(include_decided=True)) == 1
    assert mod.reject("001")["ok"] is False


# --- promote ---------------------------------------------------------------


def test_promote_appends_under_the_section_and_marks_the_proposal(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for run_id in ("100000_a_run", "110000_b_run", "120000_c_run"):
        _record_fix(store, run_id)
    (proposal,) = mod.propose_from_failures(store.parent)
    result = mod.promote(proposal.id)
    assert result["ok"] is True and result["kind"] == "skill"
    skill = (workspace / "skills" / "swmm-runner" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.endswith("## Conventions\n\n- Read the .rpt.\n\n## Learned from runs\n\n" + proposal.addition + "\n")
    assert mod.load_all()[0].status == "promoted"
    assert mod.promote("001")["ok"] is False  # already decided
    # a second proposal for another tool lands in the same section, after the first bullet
    for run_id in ("100000_a_run", "110000_b_run", "120000_c_run"):
        record_run_failures(store, run_id, [_fail("plot_run", "MCP transport failed: process ended", run_dir="r"), _ok("plot_run", run_dir="r")])
    (second,) = mod.propose_from_failures(store.parent)
    assert second.target == "skills/swmm-plot/SKILL.md"


def test_promote_refuses_a_target_that_changed_since_the_proposal(workspace: Path) -> None:
    proposal = mod.propose_fact("the outfall of record is OUT_0", source_session_id="s1")
    facts = workspace / "memory" / "facts.md"
    facts.write_text(facts.read_text(encoding="utf-8") + "\n§\ntext: edited by hand\n§\n", encoding="utf-8")
    result = mod.promote(proposal.id)
    assert result["ok"] is False and "changed since the proposal" in result["reason"]
    assert mod.load_all()[0].status == "open"


def test_promote_of_a_skill_or_rule_needs_a_checkout(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for run_id in ("100000_a_run", "110000_b_run", "120000_c_run"):
        _record_fix(store, run_id)
    (proposal,) = mod.propose_from_failures(store.parent)
    monkeypatch.setattr(mod, "_checkout_root", lambda: None)
    result = mod.promote(proposal.id)
    assert result["ok"] is False and "source checkout" in result["reason"]


# --- facts -----------------------------------------------------------------


def test_record_fact_becomes_a_fact_proposal_and_promote_appends_it(workspace: Path) -> None:
    proposal = mod.propose_fact("user prefers metric units", source_session_id="20260513_120000_todcreek_run")
    assert proposal.kind == "fact" and proposal.target == "memory/facts.md" and proposal.id == "001"
    assert "text: user prefers metric units\nsource_session: 20260513_120000_todcreek_run\n" in proposal.addition
    facts = workspace / "memory" / "facts.md"
    assert "metric" not in facts.read_text(encoding="utf-8")
    assert mod.promote("1")["ok"] is True
    text = facts.read_text(encoding="utf-8")
    assert text.count("§") == 2 and "text: user prefers metric units" in text
    assert text.startswith("<!-- WHEN TO PROPOSE")
    with pytest.raises(ValueError):
        mod.propose_fact("   ")


def test_record_fact_tool_writes_a_proposal_not_the_facts_file(workspace: Path, tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    registry = AgentToolRegistry()
    assert registry.is_read_only("record_fact") is False
    result = registry.execute(ToolCall("record_fact", {"text": "the project standard outfall is O1", "source_session_id": "s9"}), session_dir=tmp_path / "session")
    assert result["ok"] is True
    assert result["proposal_id"] == "001" and "aiswmm memory promote 001" in result["summary"]
    assert not (workspace / "memory" / "facts_staging.md").exists()
    assert "standard outfall" not in (workspace / "memory" / "facts.md").read_text(encoding="utf-8")
    assert mod.list_proposals()[0].evidence == {"source_session": "s9"}


# --- the CLI ----------------------------------------------------------------


def test_cli_lists_promotes_and_rejects(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from agentic_swmm.commands import memory as memory_cmd

    mod.propose_fact("first fact", source_session_id="s1")
    mod.propose_fact("second fact", source_session_id="s2")
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False)) == 0
    out = capsys.readouterr().out
    assert "001" in out and "002" in out and "fact" in out
    assert memory_cmd.promote_main(argparse.Namespace(proposal_id="001")) == 0
    assert memory_cmd.reject_main(argparse.Namespace(proposal_id="002", reason="not a fact")) == 0
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=True)) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == []
    assert memory_cmd.proposals_main(argparse.Namespace(all=True, json=True)) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(row["id"], row["status"]) for row in rows] == [("001", "promoted"), ("002", "rejected")]
    assert memory_cmd.promote_main(argparse.Namespace(proposal_id="009")) == 1


def test_no_proposals_folder_reads_empty(workspace: Path) -> None:
    assert mod.load_all() == []
    assert mod.list_proposals(include_decided=True) == []
    assert mod.propose_from_failures(workspace / "memory" / "store") == []
