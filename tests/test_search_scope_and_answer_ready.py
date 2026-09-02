"""A scoped search stays scoped, and a typed result says it is the answer.

Live session S04 turn 3 (2026-09-02): read_rpt_summary returned the ranked
conduits at step 3, and the planner then spent 20 more steps re-deriving them,
including a search_files call over the run directory that ran for two minutes
because Path.rglob walked the entire repository (1,175 run folders, every
node_modules tree) and tried to decode a 4 MB upstream zip and a binary .out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agentic_swmm.agent import prompts
from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.tool_handlers.runtime_ops import _search_files_tool
from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    run = root / "runs" / "2026-09-02" / "a_run" / "06_runner"
    run.mkdir(parents=True)
    (run / "model.rpt").write_text("  Link Flow Summary\n  DGM002314  CONDUIT  0.181\n", encoding="utf-8")
    (run / "model.out").write_bytes(bytes([0xFF, 0xFE, 0x00, 0x44, 0x47, 0x4D]))  # binary with "DGM" bytes
    (root / "runs" / "2026-09-02" / "a_run" / "10_upstream").mkdir()
    (root / "runs" / "2026-09-02" / "a_run" / "10_upstream" / "swmm_model.zip").write_bytes(b"PK\x03\x04DGM")
    decoy = root / "runs" / "elsewhere"
    decoy.mkdir(parents=True)
    for i in range(50):
        (decoy / f"other_{i}.txt").write_text("DGM decoy\n", encoding="utf-8")
    (root / "notes.txt").write_text("DGM at the root\n", encoding="utf-8")
    return root


class TestSearchScope:
    def test_a_run_scoped_glob_never_leaves_the_run(self, tmp_path, monkeypatch):
        root = _fake_repo(tmp_path)
        monkeypatch.setattr(runtime_ops, "repo_root", lambda: root)
        payload = _search_files_tool(
            ToolCall("search_files", {"glob": "runs/2026-09-02/a_run/**/*", "query": "DGM", "max_results": 500}),
            tmp_path,
        )
        assert payload["ok"] is True
        paths = [r["path"] for r in payload["results"]]
        assert paths == ["runs/2026-09-02/a_run/06_runner/model.rpt"]
        assert payload["scanned"] == 1  # the binary .out and the zip were never opened
        assert "elapsed_seconds" in payload

    def test_an_absolute_glob_inside_the_repo_is_anchored_too(self, tmp_path, monkeypatch):
        root = _fake_repo(tmp_path)
        monkeypatch.setattr(runtime_ops, "repo_root", lambda: root)
        payload = _search_files_tool(
            ToolCall("search_files", {"glob": str(root / "runs" / "2026-09-02" / "a_run" / "06_runner" / "model.rpt"), "query": "dgm"}),
            tmp_path,
        )
        assert [r["path"] for r in payload["results"]] == ["runs/2026-09-02/a_run/06_runner/model.rpt"]

    def test_an_absolute_glob_outside_the_repo_finds_nothing(self, tmp_path, monkeypatch):
        root = _fake_repo(tmp_path)
        monkeypatch.setattr(runtime_ops, "repo_root", lambda: root)
        payload = _search_files_tool(ToolCall("search_files", {"glob": "/etc/*", "query": "root"}), tmp_path)
        assert payload["ok"] is True and payload["results"] == []

    def test_a_bare_pattern_still_walks_the_repository(self, tmp_path, monkeypatch):
        root = _fake_repo(tmp_path)
        monkeypatch.setattr(runtime_ops, "repo_root", lambda: root)
        payload = _search_files_tool(ToolCall("search_files", {"glob": "*.rpt", "query": "DGM"}), tmp_path)
        assert [r["path"] for r in payload["results"]] == ["runs/2026-09-02/a_run/06_runner/model.rpt"]

    def test_the_time_budget_is_reported_when_hit(self, tmp_path, monkeypatch):
        root = _fake_repo(tmp_path)
        monkeypatch.setattr(runtime_ops, "repo_root", lambda: root)
        monkeypatch.setattr(runtime_ops, "_MAX_SEARCH_SECONDS", 0.0)
        payload = _search_files_tool(ToolCall("search_files", {"glob": "runs/**/*.txt", "query": "DGM", "max_results": 500}), tmp_path)
        assert payload["truncated"] is True
        assert "time budget" in payload["summary"]


class TestAnswerReady:
    def test_a_ranked_result_says_it_is_the_answer(self):
        scratch = repo_root() / "runs" / "_test_answer_ready"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        rpt = scratch / "model.rpt"
        rpt.write_text(
            "  *****************\n  Link Flow Summary\n  *****************\n\n"
            "  -----------------------------------------------------------------------------\n"
            "                                 Maximum      Time of Max       Maximum    Max/    Max/\n"
            "  Link                 Type          CMS  days hr:min           m/sec    Flow   Depth\n"
            "  -----------------------------------------------------------------------------\n"
            "  DGM002314            CONDUIT     0.181     1  05:00            0.84    0.23    1.00\n"
            "  DGM000758            CONDUIT     0.160     1  05:00            0.74    1.45    1.00\n\n",
            encoding="utf-8",
        )
        try:
            payload = _read_rpt_summary_tool(
                ToolCall("read_rpt_summary", {"rpt_path": str(rpt.relative_to(repo_root())), "section": "Link Flow Summary", "top_n": 3}),
                repo_root(),
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        assert payload["answer_ready"] is True
        assert "Answer from them" in payload["note"]
        assert "search_files" in payload["note"]
        assert payload["rows"][0]["link"] == "DGM002314"

    def test_the_base_prompt_tells_the_planner_to_stop(self):
        source = Path(prompts.__file__).read_text(encoding="utf-8")
        assert "that result IS the evidence" in source
        assert "never re-derive the same numbers" in source


class TestTheModelSeesTheRows:
    """Finding F-31: the model-facing allowlist dropped ``rows`` for weeks."""

    def test_output_for_model_keeps_the_typed_answer(self):
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        payload = {
            "tool": "read_rpt_summary", "args": {}, "ok": True,
            "section": "Node Flooding Summary", "total_rows": 2, "shown": 2,
            "sort_by": "total_flood_volume_10_6_ltr",
            "rows": [{"node": "N4", "hours_flooded": 21.61, "total_flood_volume_10_6_ltr": 8.102}],
            "answer_ready": True, "note": "These rows ARE the ranking.",
            "results": {"rows": [{"node": "N4"}]},
            "summary": "section=Node Flooding Summary total=2 shown=2",
            "_private_debug": "dropped",
        }
        seen = AgentToolRegistry().output_for_model(payload)
        assert seen["rows"][0]["node"] == "N4"
        assert seen["rows"][0]["hours_flooded"] == 21.61
        assert seen["results"]["rows"][0]["node"] == "N4"
        assert seen["answer_ready"] is True and "ranking" in seen["note"]
        assert seen["section"] == "Node Flooding Summary" and seen["sort_by"].startswith("total_flood")
        assert "_private_debug" not in seen

    def test_the_real_tool_payload_survives_the_allowlist(self):
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        scratch = repo_root() / "runs" / "_test_rows_visible"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        rpt = scratch / "model.rpt"
        rpt.write_text(
            "  *********************\n  Node Flooding Summary\n  *********************\n\n"
            "  Flooding refers to all water that overflows a node, whether it ponds or not.\n"
            "  --------------------------------------------------------------------------\n"
            "                                                             Total   Maximum\n"
            "                                 Maximum   Time of Max       Flood    Ponded\n"
            "                        Hours       Rate    Occurrence      Volume     Depth\n"
            "  Node                 Flooded       CMS   days hr:min    10^6 ltr    Meters\n"
            "  --------------------------------------------------------------------------\n"
            "  N4                     21.61     0.348      1  05:00       8.102     0.000\n\n",
            encoding="utf-8",
        )
        try:
            payload = _read_rpt_summary_tool(
                ToolCall("read_rpt_summary", {"rpt_path": str(rpt.relative_to(repo_root())), "section": "Node Flooding Summary"}),
                repo_root(),
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        seen = AgentToolRegistry().output_for_model(payload)
        assert seen["rows"] == payload["rows"]
        assert seen["rows"][0]["node"] == "N4" and seen["rows"][0]["hours_flooded"] == 21.61


    def test_the_climate_batch_answer_reaches_the_model_too(self):
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        payload = {
            "tool": "run_climate_scenarios", "ok": True, "run_dir": "/r", "node": "OUT_1",
            "summary_json": "/r/03_climate/summary.json", "summary_md": "/r/03_climate/summary.md",
            "scenarios": [{"name": "p1.20", "precip_factor": 1.2, "run_ok": True, "metrics": {"peak": 0.2}, "error": None}],
            "summary": "1/1 scenarios ran",
        }
        seen = AgentToolRegistry().output_for_model(payload)
        assert seen["scenarios"][0]["metrics"] == {"peak": 0.2}
        assert seen["summary_md"].endswith("summary.md") and seen["node"] == "OUT_1"
