"""The evidence gate counts real cases, recent runs and known knowledge (#561).

Skill refinement loop, ticket 2: the case of a failure is the run
directory the failing call touched, never a chat session's slug; stale
evidence and refusals by policy never propose; knowledge that already
exists is reported, and a key already documented in its own target is
never proposed; a rejection may name a recurrence count; a proposal
carries the prompts that reproduce it and a baseline.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory import session_db
from agentic_swmm.memory.jsonl_store import append_row
from agentic_swmm.memory.run_failures import case_for, record_run_failures
from agentic_swmm.memory.store import db_path_for

KELOWNA = "runs/live-test-2026-09/2026-09-05/103702_downtown-kelowna-bc_run"
TODCREEK = "runs/live-test-2026-09/2026-09-26/044009_todcreek_run"


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


def _missing_rpt(store: Path, session: str, run_dir: str, fixed: str = "06_runner/model.rpt") -> None:
    record_run_failures(store, session, [
        _fail("read_file", f"file not found: /w/{run_dir}/06_run/model.rpt", path=f"{run_dir}/06_run/model.rpt"),
        _ok("read_file", path=f"{run_dir}/{fixed}"),
    ])


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    (root / "memory" / "initial").mkdir()
    (root / "memory" / "initial" / "operational_memory.md").write_text("# Operational memory\n\n## Tool-use rules\n\n- I prefer the CLI.\n", encoding="utf-8")
    (root / "memory" / "initial" / "modeling_workflow_memory.md").write_text("# Workflow\n\nArtifacts:\n\nruns/<case>/06_runner/model.rpt\n", encoding="utf-8")
    (root / "skills" / "swmm-end-to-end").mkdir(parents=True)
    (root / "skills" / "swmm-end-to-end" / "SKILL.md").write_text("---\nname: swmm-end-to-end\n---\n# E2E\n\n- `runs/<case>/06_runner/model.rpt`\n", encoding="utf-8")
    (root / "skills" / "swmm-lid-optimization").mkdir(parents=True)
    (root / "skills" / "swmm-lid-optimization" / "SKILL.md").write_text("---\nname: swmm-lid-optimization\n---\n# LID\n\n06_runner/model.rpt\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    return root


# --- the case axis -----------------------------------------------------------


def test_the_case_is_the_run_directory_the_call_touched_never_the_chat_slug() -> None:
    assert case_for("211138_model_chat", {"path": f"{KELOWNA}/06_run/model.rpt"}, "file not found") == "downtown-kelowna-bc"
    assert case_for("211138_model_chat", {}, f"file not found: /w/{TODCREEK}/06_run/model.rpt") == "todcreek"
    assert case_for("211138_model_chat", {"inp_path": "examples/todcreek/model_nope.inp"}, "x") == "todcreek"
    assert case_for("211403_todcreek_undefined_node_run", {}, "ERROR 209") == "todcreek_undefined_node"
    assert case_for("211138_model_chat", {}, "query is required") == ""


def test_two_chat_sessions_on_the_same_run_are_one_case(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session in ("210725_todcreek_chat", "210801_todcreek_chat", "211138_model_chat"):
        _missing_rpt(store, session, KELOWNA)
    assert mod.propose_from_failures(store.parent) == []  # three runs, one case
    _missing_rpt(store, "211300_model_chat", TODCREEK)
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.evidence["cases"] == ["downtown-kelowna-bc", "todcreek"]


def test_without_a_case_the_gate_needs_four_runs_on_two_days(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    stamps = ["2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z", "2026-10-21T10:00:00Z", "2026-10-21T11:00:00Z"]
    for n, stamp in enumerate(stamps):
        append_row(store, {
            "schema_version": "1.2", "run_id": f"21000{n}_x_chat", "tool": "search_files", "failure_class": "tool_error",
            "summary": "query is required", "recorded_at": stamp, "fix": "search_files again with query:  -> peak", "fix_tool": "search_files",
        })
        if n < 3:
            assert mod.propose_from_failures(store.parent, now=datetime(2026, 10, 22, tzinfo=timezone.utc)) == []
    assert len(mod.propose_from_failures(store.parent, now=datetime(2026, 10, 22, tzinfo=timezone.utc))) == 1


def test_stale_evidence_proposes_nothing(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for n, (session, run_dir) in enumerate((("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA))):
        append_row(store, {
            "schema_version": "1.2", "run_id": session, "tool": "read_file", "failure_class": "path_resolution",
            "summary": f"file not found: /w/{run_dir}/06_run/model.rpt", "recorded_at": "2026-08-01T00:00:00Z",
            "fix": "read_file again with path: .../06_run/model.rpt -> .../06_runner/model.rpt", "fix_tool": "read_file",
        })
    assert mod.propose_from_failures(store.parent, now=datetime(2026, 10, 1, tzinfo=timezone.utc)) == []
    assert len(mod.propose_from_failures(store.parent, now=datetime(2026, 9, 1, tzinfo=timezone.utc))) == 1


# --- the body and the dedupe finding ------------------------------------------


def test_the_bullet_names_the_change_and_the_dedupe_finding_names_the_existing_homes(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    _missing_rpt(store, "210725_todcreek_chat", TODCREEK, fixed="final_report.md")
    _missing_rpt(store, "210801_todcreek_chat", TODCREEK, fixed="final_report.md")
    _missing_rpt(store, "211138_model_chat", KELOWNA)
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.kind == "tool" and proposal.status == "open" and proposal.section == "## read_file"
    # the note cap drops the evidence tail and then the rarer change (the evidence stays in the file)
    assert proposal.addition == (
        '- Fails with "file not found: <path>": calling it again with a different `path` worked: '
        "`path: .../06_run/model.rpt -> .../final_report.md` (2 runs)."
    )
    assert proposal.evidence["changes"] == [["path: .../06_run/model.rpt -> .../final_report.md", 2], ["path: .../06_run/model.rpt -> .../06_runner/model.rpt", 1]]
    assert "103702" not in proposal.addition and "live-test" not in proposal.addition
    assert proposal.dedupe["tokens"] == ["06_runner"]
    assert proposal.dedupe["found"] == [
        {"file": "memory/initial/modeling_workflow_memory.md", "line": 5},
        {"file": "skills/swmm-end-to-end/SKILL.md", "line": 6},
    ]
    assert proposal.dedupe["in_target"] is False
    text = proposal.path.read_text(encoding="utf-8")
    assert "## Already written elsewhere" in text and "swmm-lid-optimization" not in text
    assert proposal.baseline == 3


def test_knowledge_already_in_the_target_is_documented_not_proposed(workspace: Path) -> None:
    target = workspace / "memory" / "initial" / "tool_notes.md"
    target.write_text("# Tool notes\n\n## read_file\n\n- Builder runs keep the report under 06_runner/.\n", encoding="utf-8")
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.status == "documented" and proposal.diff == "" and "already written in" in proposal.reason
    assert mod.list_proposals() == [] and mod.list_proposals(include_decided=True)[0].status == "documented"
    assert mod.promote(proposal.id)["ok"] is False
    _missing_rpt(store, "130000_d_run", TODCREEK)
    assert mod.propose_from_failures(store.parent) == []  # the key stays documented


# --- the verification prompts ----------------------------------------------------


def test_the_proposal_carries_the_prompts_of_the_evidence_sessions(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    db = db_path_for(store.parent)
    session_db.initialize(db)
    with session_db.connect(db) as conn:
        for session, goal in (("20261021_100000_a_run", "Read the peak from the Kelowna run"), ("20261021_110000_b_run", "Read the peak from the Tod Creek run"), ("20261021_120000_c_run", "")):
            session_db.upsert_session(conn, session_id=session, start_utc=None, end_utc=None, goal=goal, case_name=None, planner=None, model=None, ok=None)
        conn.commit()
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.verify == ["Read the peak from the Kelowna run", "Read the peak from the Tod Creek run"]
    assert "## Verify (prompts of the evidence sessions)" in proposal.path.read_text(encoding="utf-8")
    reloaded = mod.load_all()[0]
    assert reloaded.verify == proposal.verify and reloaded.dedupe == proposal.dedupe and reloaded.baseline == 3


# --- rejection with a recurrence count -----------------------------------------------


def test_a_rejection_with_until_allows_one_more_proposal_past_the_count(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (first,) = mod.propose_from_failures(store.parent)
    assert mod.reject(first.id, reason="look again later", until=5) == {"ok": True, "id": "001", "reason": "look again later", "until": 5}
    assert mod.load_all()[0].until == 5
    _missing_rpt(store, "130000_d_run", TODCREEK)
    assert mod.propose_from_failures(store.parent) == []  # four runs, the count is five
    _missing_rpt(store, "140000_e_run", KELOWNA)
    (second,) = mod.propose_from_failures(store.parent)
    assert second.id == "002" and second.key == first.key
    mod.reject(second.id, reason="still no")
    _missing_rpt(store, "150000_f_run", TODCREEK)
    assert mod.propose_from_failures(store.parent) == []


# --- the entry points ---------------------------------------------------------------


def test_the_audit_hook_runs_the_gate_after_the_runs_row(workspace: Path) -> None:
    from agentic_swmm.memory import audit_hook

    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    ctx = audit_hook._RefreshContext(run_dir=workspace, runs_dir=workspace / "runs", project_root=workspace, memory_dir=store.parent, result={"errors": []})
    audit_hook._phase_proposals(ctx)
    assert ctx.result["proposals"] == ["001"] and ctx.result["errors"] == []
    audit_hook._phase_proposals(ctx)
    assert ctx.result["proposals"] == ["001"]  # nothing new: the key is open


def test_the_cli_refresh_runs_the_gate_and_reject_takes_until(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=True)) == 0
    out = capsys.readouterr().out
    assert out.startswith("1 new proposal(s): 001 (open)") and "001" in out
    assert memory_cmd.reject_main(argparse.Namespace(proposal_id="001", reason="later", until=6)) == 0
    assert "after 6 runs" in capsys.readouterr().out
    assert memory_cmd.proposals_main(argparse.Namespace(all=True, json=True, refresh=False)) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["status"] == "rejected"
