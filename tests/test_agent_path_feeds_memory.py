"""The golden path feeds the memory it is meant to learn from (F-35, F-21, F-33).

2026-09-02 live campaign: four real interactive sessions audited their runs
and left zero parametric rows, because only the CLI verb ever called the
audit -> memory hook; the planner anchored memory on a 1-token sniff that a
place-name prompt never satisfies; and the first real re-render of the
lessons file dropped a curated pattern instead of archiving it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agentic_swmm.agent.tool_handlers import swmm_audit
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

SUMMARIZER = repo_root() / "skills" / "swmm-modeling-memory" / "scripts" / "summarize_memory.py"


class TestAgentAuditFeedsMemory:
    def test_a_successful_audit_triggers_the_memory_hook(self, tmp_path, monkeypatch):
        import agentic_swmm.memory as memory_pkg

        run_dir = repo_root() / "runs" / "_test_agent_feeds_memory"
        run_dir.mkdir(parents=True, exist_ok=True)
        seen: list[Path] = []

        def fake_refresh(path, **kwargs):
            seen.append(Path(path))
            return {"skipped": False, "reason": "", "lessons": "/mem/lessons_learned.md", "errors": []}

        monkeypatch.setattr(memory_pkg, "trigger_memory_refresh", fake_refresh)
        try:
            call = ToolCall("audit_run", {"run_dir": str(run_dir.relative_to(repo_root()))})
            result = swmm_audit._feed_memory_after_audit(call, tmp_path, {"tool": "audit_run", "ok": True, "summary": "called"})
        finally:
            run_dir.rmdir()
        assert seen == [run_dir.resolve()] or seen == [run_dir]
        assert result["memory_hook"]["skipped"] is False
        assert result["memory_hook"]["lessons"].endswith("lessons_learned.md")

    def test_a_failed_audit_never_touches_memory(self, tmp_path, monkeypatch):
        import agentic_swmm.memory as memory_pkg

        def boom(path, **kwargs):
            raise AssertionError("memory must not be refreshed after a failed audit")

        monkeypatch.setattr(memory_pkg, "trigger_memory_refresh", boom)
        call = ToolCall("audit_run", {"run_dir": "runs/does-not-matter"})
        result = swmm_audit._feed_memory_after_audit(call, tmp_path, {"tool": "audit_run", "ok": False, "summary": "boom"})
        assert "memory_hook" not in result

    def test_the_wrapped_handler_keeps_its_mcp_routing(self):
        routing = getattr(swmm_audit._audit_run_tool, "_mcp_routing", None)
        assert routing == {"server": "swmm-experiment-audit", "tool": "audit_run"}


class TestMemoryAnchorsOnThePlace:
    def test_a_place_prompt_anchors_on_the_run_slug(self):
        from agentic_swmm.agent.planner import _resolve_case_name_for_memory

        goal = ("Fetch a SWMM model from the Canada service for downtown Victoria BC, rainfall "
                "period November 1 to November 4 2023. Run the model and audit it.")
        assert _resolve_case_name_for_memory(goal, {}) == "downtown-victoria-bc"

    def test_prior_state_still_wins(self):
        from agentic_swmm.agent.planner import _resolve_case_name_for_memory

        assert _resolve_case_name_for_memory("Fetch a model for downtown Victoria BC", {"active_case_id": "tod-creek"}) == "tod-creek"

    def test_a_bare_case_token_still_works_and_a_question_stays_unanchored(self):
        from agentic_swmm.agent.planner import _resolve_case_name_for_memory

        assert _resolve_case_name_for_memory("todcreek", {}) == "todcreek"
        assert _resolve_case_name_for_memory("Which node flooded the most, and for how long?", {}) is None


def _load_summarizer():
    spec = importlib.util.spec_from_file_location("summarize_memory_f33", SUMMARIZER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


OLD_LESSONS = """<!-- schema_version: 1.1 -->
# Lessons Learned

## comparison_mismatch

<!-- aiswmm-metadata
metadata:
  first_seen_utc: 2026-05-14T00:00:00Z
  last_seen_utc: 2026-05-14T00:00:00Z
  evidence_count: 1
  evidence_runs:
    - curated
  status: active
  confidence_score: 1.0
  half_life_days: 90
/aiswmm-metadata -->

Baseline and current peaks disagree.

## missing_inp

<!-- aiswmm-metadata
metadata:
  first_seen_utc: 2026-01-15T00:00:00Z
  last_seen_utc: 2026-01-15T00:00:00Z
  evidence_count: 2
  evidence_runs: []
  status: active
  confidence_score: 2.0
  half_life_days: 90
/aiswmm-metadata -->

The INP was not found.
"""

NEW_RENDER = """<!-- schema_version: 1.1 -->
# Lessons Learned

## missing_inp

The INP was not found.

## qa_failed

QA gates failed.
"""


class TestReRenderArchivesInsteadOfDropping:
    def test_a_vanished_pattern_lands_in_the_archive_with_its_fence(self, tmp_path):
        mod = _load_summarizer()
        old = tmp_path / "lessons_learned.md"
        old.write_text(OLD_LESSONS, encoding="utf-8")
        merged = mod._merge_existing_metadata(old, NEW_RENDER)
        archive = (tmp_path / "lessons_archived.md").read_text(encoding="utf-8")
        assert "## comparison_mismatch" in archive
        assert "status: archived" in archive
        assert "Baseline and current peaks disagree." in archive
        assert "## missing_inp" not in archive
        # The surviving pattern keeps its fence; the new one is untouched.
        assert "evidence_count: 2" in merged
        assert "## qa_failed" in merged

    def test_nothing_vanished_means_no_archive_file(self, tmp_path):
        mod = _load_summarizer()
        old = tmp_path / "lessons_learned.md"
        old.write_text(OLD_LESSONS.replace("## comparison_mismatch", "## missing_inp_dup"), encoding="utf-8")
        mod._merge_existing_metadata(old, OLD_LESSONS.replace("## comparison_mismatch", "## missing_inp_dup"))
        assert not (tmp_path / "lessons_archived.md").exists()
