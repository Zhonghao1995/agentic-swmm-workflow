"""A drafted body at review time, the template kept as fallback (#564).

Skill refinement loop, ticket 5: listing proposals drafts the undrafted
ones through an injected provider; a valid draft replaces the body and the
template survives in the front matter; invalid drafts fall back with a
reason; the sidecar records the call; drafting never runs at session end.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.memory import proposal_draft as draft_mod
from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory import session_db
from agentic_swmm.memory.run_failures import record_run_failures
from agentic_swmm.memory.store import db_path_for

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


@dataclass
class _Result:
    text: str
    model: str = "fake-model"
    raw: dict[str, Any] | None = None


class FakeProvider:
    """``complete`` returns the canned replies in order; counts the calls."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system_prompt: str, prompt: str) -> _Result:
        self.calls.append((system_prompt, prompt))
        if not self.replies:
            raise RuntimeError("no reply left")
        return _Result(self.replies.pop(0), raw={"usage": {"input_tokens": 1200, "output_tokens": 80}})


class RaisingProvider:
    def complete(self, **_: Any) -> _Result:
        raise AssertionError("the model must not be called here")


GOOD = json.dumps({
    "condition": "the report of a run made by the builder is asked for under 06_run/",
    "action": "read 06_runner/model.rpt; a run that never executed SWMM has final_report.md instead",
    "why": "the builder keeps the engine's report under 06_runner, and a chat-only run writes no report",
    "addition": "- A builder run keeps its report at 06_runner/model.rpt; a run that never executed SWMM has final_report.md and no model.rpt.",
    "diff": None, "confidence": 0.85, "verdict": "Worth promoting: it names the convention the planner kept rediscovering.", "retarget_skill": None,
})


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    initial = root / "memory" / "initial"
    initial.mkdir()
    (initial / "operational_memory.md").write_text("# Operational memory\n", encoding="utf-8")
    (initial / "tool_notes.md").write_text("# Tool notes\n", encoding="utf-8")
    (root / "skills" / "swmm-experiment-audit").mkdir(parents=True)
    (root / "skills" / "swmm-experiment-audit" / "SKILL.md").write_text(
        "---\nname: swmm-experiment-audit\ndescription: Audit a finished SWMM run and consolidate its provenance, artifacts and QA checks.\n---\n# Audit\n\n## Steps\n\n- Read the report under 06_run/model.rpt.\n", encoding="utf-8"
    )
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(initial / "tool_notes.md"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    monkeypatch.delenv("AISWMM_NO_DRAFT", raising=False)
    from agentic_swmm.memory import tool_notes

    tool_notes.clear_cache()
    yield root
    tool_notes.clear_cache()


def _seed(workspace: Path) -> mod.Proposal:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    db = db_path_for(store.parent)
    session_db.initialize(db)
    with session_db.connect(db) as conn:
        session_db.upsert_session(conn, session_id="20261021_100000_a_run", start_utc=None, end_utc=None, goal="Read the peak from the Kelowna run", case_name=None, planner=None, model=None, ok=None)
        for step, (kind, tool, args, ok, summary) in enumerate([
            ("tool_call", "select_skill", {"skill_name": "swmm-experiment-audit"}, None, None),
            ("tool_result", "select_skill", {"skill_name": "swmm-experiment-audit"}, 1, "selected"),
            ("tool_call", "read_file", {"path": f"{KELOWNA}/06_run/model.rpt"}, None, None),
            ("tool_result", "read_file", {"path": f"{KELOWNA}/06_run/model.rpt"}, 0, "file not found: /w/.../06_run/model.rpt"),
            ("tool_call", "list_dir", {"path": KELOWNA}, None, None),
            ("tool_result", "list_dir", {"path": KELOWNA}, 1, "12 entries"),
            ("tool_call", "read_file", {"path": f"{KELOWNA}/06_runner/model.rpt"}, None, None),
            ("tool_result", "read_file", {"path": f"{KELOWNA}/06_runner/model.rpt"}, 1, "2048 chars"),
        ], start=1):
            conn.execute(
                "INSERT INTO tool_events(session_id, step, kind, tool_name, args_json, ok, summary, utc) VALUES (?,?,?,?,?,?,?,?)",
                ("20261021_100000_a_run", step, kind, tool, json.dumps(args), ok, summary, "2026-10-21T00:00:00Z"),
            )
        conn.commit()
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (proposal,) = mod.propose_from_failures(store.parent)
    return proposal


# --- the drafter sees the evidence ---------------------------------------------------


def test_the_prompt_carries_evidence_excerpts_and_the_target(workspace: Path) -> None:
    proposal = _seed(workspace)
    excerpts = draft_mod.trace_excerpts(workspace / "memory" / "store", proposal)
    assert [item["session"] for item in excerpts] == ["100000_a_run"]
    assert [event["tool"] for event in excerpts[0]["events"]] == ["select_skill", "select_skill", "read_file", "read_file", "list_dir", "list_dir", "read_file", "read_file"]
    assert excerpts[0]["selected_skills"] == ["swmm-experiment-audit"]
    prompt = draft_mod.build_prompt(proposal, "# Tool notes\n", excerpts)
    assert "read_file:path_resolution" in prompt and "06_runner" in prompt and "# Tool notes" in prompt
    assert len(prompt) <= draft_mod.DRAFT_INPUT_CAP


def test_the_prompt_is_capped(workspace: Path) -> None:
    proposal = _seed(workspace)
    prompt = draft_mod.build_prompt(proposal, "x" * 100_000, [])
    assert len(prompt) <= draft_mod.DRAFT_INPUT_CAP and prompt.endswith("[target text cut at the input cap]")


# --- a good draft ------------------------------------------------------------------------


def test_a_valid_draft_replaces_the_body_and_keeps_the_template(workspace: Path) -> None:
    proposal = _seed(workspace)
    template = proposal.addition
    provider = FakeProvider(GOOD)
    draft = draft_mod.draft_proposal(proposal, provider=provider, store_dir=workspace / "memory" / "store")
    assert draft.ok and draft.model == "fake-model" and draft.tokens_in == 1200
    reloaded = mod.load_all()[0]
    assert reloaded.addition == "- A builder run keeps its report at 06_runner/model.rpt; a run that never executed SWMM has final_report.md and no model.rpt."
    assert reloaded.template_addition == template and reloaded.drafted_by == "fake-model" and reloaded.confidence == 0.85
    assert reloaded.why.startswith("the builder keeps") and reloaded.draft_failed == ""
    assert "+## read_file" in reloaded.diff and "06_runner/model.rpt; a run" in reloaded.diff
    sidecar = json.loads(draft_mod.sidecar_path(reloaded).read_text(encoding="utf-8"))
    assert sidecar["ok"] is True and sidecar["tokens_in"] == 1200 and "Trace excerpts" in sidecar["prompt"] and sidecar["response"] == GOOD
    assert mod.promote(reloaded.id)["ok"] is True
    assert "06_runner/model.rpt; a run" in (workspace / "memory" / "initial" / "tool_notes.md").read_text(encoding="utf-8")


def test_a_draft_may_retarget_to_a_skill_the_session_selected(workspace: Path) -> None:
    proposal = _seed(workspace)
    reply = json.loads(GOOD)
    reply["retarget_skill"] = "swmm-experiment-audit"
    reply["diff"] = "--- a/skills/swmm-experiment-audit/SKILL.md\n+++ b/skills/swmm-experiment-audit/SKILL.md\n@@ -1 +1 @@\n-- Read the report under 06_run/model.rpt.\n+- Read the report under 06_runner/model.rpt (final_report.md when no SWMM ran).\n"
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(json.dumps(reply)), store_dir=workspace / "memory" / "store")
    assert draft.ok, draft.reason
    reloaded = mod.load_all()[0]
    assert reloaded.kind == "skill" and reloaded.target == "skills/swmm-experiment-audit/SKILL.md"
    assert "-- Read the report under 06_run/model.rpt." in reloaded.diff and "+- Read the report under 06_runner/model.rpt" in reloaded.diff


def test_a_retarget_without_a_procedure_diff_keeps_the_tool_note(workspace: Path) -> None:
    # Measured 2026-10-22: a bullet moved into a SKILL.md is never read; only a diff may move.
    proposal = _seed(workspace)
    reply = json.loads(GOOD)
    reply["retarget_skill"] = "swmm-experiment-audit"
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(json.dumps(reply)), store_dir=workspace / "memory" / "store")
    assert draft.ok and draft.retarget_skill == ""
    assert mod.load_all()[0].kind == "tool" and mod.load_all()[0].target == "memory/initial/tool_notes.md"


def test_a_retarget_to_a_skill_the_session_never_selected_is_ignored(workspace: Path) -> None:
    proposal = _seed(workspace)
    reply = json.loads(GOOD)
    reply["retarget_skill"] = "swmm-runner"
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(json.dumps(reply)), store_dir=workspace / "memory" / "store")
    assert draft.ok and draft.retarget_skill == ""
    assert mod.load_all()[0].kind == "tool"


# --- bad drafts fall back --------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, reason",
    [
        ("not json at all", "no JSON object"),
        (json.dumps({"addition": ""}), "no addition"),
        (json.dumps({"addition": "- " + "x" * 300, "confidence": 0.9}), "the cap is 200"),
        (json.dumps({"addition": "- fine", "diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-nothing like this\n+other\n", "confidence": 0.9}), "a tool note cannot edit"),
    ],
)
def test_an_invalid_draft_keeps_the_template_and_records_the_reason(workspace: Path, reply: str, reason: str) -> None:
    proposal = _seed(workspace)
    template = proposal.addition
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(reply), store_dir=workspace / "memory" / "store")
    assert draft.ok is False and reason in draft.reason
    reloaded = mod.load_all()[0]
    assert reloaded.addition == template and reloaded.draft_failed == draft.reason and reloaded.drafted_by == ""
    assert json.loads(draft_mod.sidecar_path(reloaded).read_text(encoding="utf-8"))["reason"] == draft.reason


def test_a_skill_diff_that_breaks_the_validator_is_refused(workspace: Path) -> None:
    proposal = _seed(workspace)
    reply = json.loads(GOOD)
    reply["retarget_skill"] = "swmm-experiment-audit"
    reply["diff"] = "--- a/skills/swmm-experiment-audit/SKILL.md\n+++ b/skills/swmm-experiment-audit/SKILL.md\n@@ -1 +1 @@\n-name: swmm-experiment-audit\n+name: Something Else\n"
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(json.dumps(reply)), store_dir=workspace / "memory" / "store")
    assert draft.ok is False and "fails the validator" in draft.reason


def test_a_failing_model_call_is_a_recorded_reason(workspace: Path) -> None:
    proposal = _seed(workspace)
    draft = draft_mod.draft_proposal(proposal, provider=FakeProvider(), store_dir=workspace / "memory" / "store")
    assert draft.ok is False and "the model call failed" in draft.reason


# --- when drafting happens ---------------------------------------------------------------


def test_listing_drafts_the_undrafted_open_proposals_up_to_the_cap(workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    _seed(workspace)
    mod.propose_fact("metric units", source_session_id="s1")  # never drafted
    provider = FakeProvider(GOOD, GOOD, GOOD, GOOD, GOOD, GOOD)
    monkeypatch.setattr(draft_mod, "configured_provider", lambda model=None: provider)
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=False, model=None)) == 0
    out = capsys.readouterr().out
    assert "drafted 001 with fake-model (confidence 0.85)" in out and len(provider.calls) == 1
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=False, model=None)) == 0
    assert len(provider.calls) == 1  # already drafted: no second call
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft="1", no_draft=False, model=None)) == 0
    assert len(provider.calls) == 2  # an explicit --draft drafts again


def test_no_draft_and_session_end_make_no_call(workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    _seed(workspace)
    monkeypatch.setattr(draft_mod, "configured_provider", lambda model=None: RaisingProvider())
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=True, model=None)) == 0
    assert "drafted" not in capsys.readouterr().out
    store = workspace / "memory" / "store"
    assert mod.propose_all(store) == []  # the session end's entry point drafts nothing


def test_a_weak_draft_is_marked_in_the_list(workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    _seed(workspace)
    reply = json.loads(GOOD)
    reply["confidence"] = 0.2
    monkeypatch.setattr(draft_mod, "configured_provider", lambda model=None: FakeProvider(json.dumps(reply)))
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=False, model=None)) == 0
    out = capsys.readouterr().out
    assert "[weak 0.2]" in out


def test_the_draft_cap_per_listing_holds(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = workspace / "memory" / "store"
    _seed(workspace)
    # five more open tool proposals for other tools
    for n, tool in enumerate(("list_dir", "search_files", "read_rpt_summary", "apply_patch", "run_allowed_command", "map_run")):
        for session, run_dir in ((f"20000{n}_a_run", KELOWNA), (f"21000{n}_b_run", TODCREEK), (f"22000{n}_c_run", KELOWNA)):
            record_run_failures(store / "run_failures.jsonl", session, [_fail(tool, f"{tool} failed oddly {n}", x="a"), _ok(tool, x="b")])
    mod.propose_from_failures(store)
    assert len(mod.list_proposals()) >= 6
    provider = FakeProvider(*([GOOD] * 10))
    drafts = draft_mod.draft_pending(store, provider_factory=lambda: provider)
    assert len(drafts) == draft_mod.MAX_DRAFTS_PER_LISTING and len(provider.calls) == draft_mod.MAX_DRAFTS_PER_LISTING


# --- the strict diff -----------------------------------------------------------------------


def test_the_diff_applies_only_when_the_context_matches_exactly_once() -> None:
    text = "a\nb\nc\nb\n"
    assert draft_mod.apply_unified_diff("a\nb\nc\n", "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n") == "a\nB\nc\n"
    with pytest.raises(ValueError):
        draft_mod.apply_unified_diff(text, "@@ -2 +2 @@\n-b\n+B\n")  # 'b' occurs twice
    with pytest.raises(ValueError):
        draft_mod.apply_unified_diff(text, "@@ -2 +2 @@\n-z\n+B\n")  # no such line
