"""Tool notes ride the tool description (#562).

Skill refinement loop, ticket 3: a promoted tool-level experience lives
in the shipped notes file and is appended to that tool's description when
the registry is built; caps keep the cost bounded; a trace event records
what was injected; proposals for unbound tools target the notes file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory import tool_notes
from agentic_swmm.memory.run_failures import record_run_failures

NOTES = """# Tool notes

## read_file

- Run directories made by the builder keep the SWMM report under `06_runner/model.rpt`.
- A run that never executed SWMM has `final_report.md` and no `model.rpt`.

## plot_run

- Pass `rain_ts` as a path, never a placeholder.
"""


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


@pytest.fixture
def notes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tool_notes.md"
    path.write_text(NOTES, encoding="utf-8")
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(path))
    tool_notes.clear_cache()
    yield path
    tool_notes.clear_cache()


def test_the_file_parses_into_notes_per_tool() -> None:
    notes = tool_notes.parse_tool_notes(NOTES)
    assert set(notes) == {"read_file", "plot_run"}
    assert notes["read_file"][1] == "A run that never executed SWMM has `final_report.md` and no `model.rpt`."


def test_a_note_lands_in_its_tools_description_and_nowhere_else(notes_file: Path) -> None:
    registry = AgentToolRegistry()
    schemas = {schema["name"]: schema["description"] for schema in registry.schemas()}
    assert "Learned from runs: (1) Run directories made by the builder" in schemas["read_file"]
    assert "(2) A run that never executed SWMM" in schemas["read_file"]
    assert "rain_ts" in schemas["plot_run"] and "06_runner" not in schemas["plot_run"]
    assert all("Learned from runs" not in text for name, text in schemas.items() if name not in ("read_file", "plot_run"))
    report = registry.tool_notes_report({"read_file", "list_dir"})
    assert report["tools"] == ["read_file"] and report["chars"] > 100
    assert registry.tool_notes_report({"list_dir"}) == {"tools": [], "chars": 0}


def test_notes_are_capped_at_three_per_tool_and_two_hundred_characters(notes_file: Path) -> None:
    notes_file.write_text("## read_file\n\n" + "".join(f"- note {n} " + "x" * 300 + "\n" for n in range(5)), encoding="utf-8")
    tool_notes.clear_cache()
    (text,) = [s["description"] for s in AgentToolRegistry().schemas({"read_file"})]
    assert text.count("note ") == 3 and "(4)" not in text
    import re

    tail = text.split(tool_notes.NOTES_LEAD, 1)[1]
    notes = [part.strip() for part in re.split(r"\(\d\) ", tail) if part.strip()]
    assert len(notes) == 3 and all(len(note) <= tool_notes.MAX_NOTE_CHARS for note in notes)


def test_a_missing_file_adds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(tmp_path / "absent.md"))
    tool_notes.clear_cache()
    assert all("Learned from runs" not in s["description"] for s in AgentToolRegistry().schemas())


def test_the_planner_records_what_was_injected(notes_file: Path, tmp_path: Path) -> None:
    from agentic_swmm.agent.planner import Planner

    trace = tmp_path / "agent_trace.jsonl"
    planner = Planner.__new__(Planner)
    planner.registry = AgentToolRegistry()
    planner._trace_tool_notes({"read_file", "list_dir", "plot_run"}, trace)
    (event,) = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert event["event"] == "tool_notes_injected" and event["tools"] == ["plot_run", "read_file"] and event["chars"] > 0
    planner._trace_tool_notes({"list_dir"}, trace)
    assert len(trace.read_text(encoding="utf-8").splitlines()) == 1  # nothing injected, nothing recorded


# --- proposals of kind tool ---------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    (root / "memory" / "initial").mkdir()
    (root / "memory" / "initial" / "operational_memory.md").write_text("# Operational memory\n", encoding="utf-8")
    (root / "memory" / "initial" / "tool_notes.md").write_text("# Tool notes\n\nOne section per tool.\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(root / "memory" / "initial" / "tool_notes.md"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    tool_notes.clear_cache()
    yield root
    tool_notes.clear_cache()


KELOWNA = "runs/live-test-2026-09/2026-09-05/103702_downtown-kelowna-bc_run"
TODCREEK = "runs/live-test-2026-09/2026-09-26/044009_todcreek_run"


def _missing_rpt(store: Path, session: str, run_dir: str) -> None:
    record_run_failures(store, session, [
        _fail("read_file", f"file not found: /w/{run_dir}/06_run/model.rpt", path=f"{run_dir}/06_run/model.rpt"),
        _ok("read_file", path=f"{run_dir}/06_runner/model.rpt"),
    ])


def test_an_unbound_tools_fix_becomes_a_tool_note_proposal(workspace: Path) -> None:
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (proposal,) = mod.propose_from_failures(store.parent)
    assert proposal.kind == "tool" and proposal.target == "memory/initial/tool_notes.md" and proposal.section == "## read_file"
    assert len(proposal.addition) <= tool_notes.MAX_NOTE_CHARS + 2 and proposal.addition.startswith("- ")
    assert "+## read_file" in proposal.diff
    assert mod.promote(proposal.id)["ok"] is True
    text = (workspace / "memory" / "initial" / "tool_notes.md").read_text(encoding="utf-8")
    assert text.endswith("## read_file\n\n" + proposal.addition + "\n")
    assert tool_notes.parse_tool_notes(text)["read_file"] == [proposal.addition[2:]]


def test_promote_refuses_a_fourth_note_for_a_tool(workspace: Path) -> None:
    notes = workspace / "memory" / "initial" / "tool_notes.md"
    notes.write_text("# Tool notes\n\n## read_file\n\n- one\n- two\n- three\n", encoding="utf-8")
    store = workspace / "memory" / "store" / "run_failures.jsonl"
    for session, run_dir in (("100000_a_run", KELOWNA), ("110000_b_run", TODCREEK), ("120000_c_run", KELOWNA)):
        _missing_rpt(store, session, run_dir)
    (proposal,) = mod.propose_from_failures(store.parent)
    result = mod.promote(proposal.id)
    assert result["ok"] is False and "3 notes" in result["reason"]
    assert notes.read_text(encoding="utf-8").count("- ") == 3
