from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "swmm-modeling-memory" / "scripts" / "summarize_memory.py"


def test_summarize_memory_outputs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "case-a"
    out_dir = tmp_path / "memory" / "modeling-memory"
    run_dir.mkdir(parents=True)

    (run_dir / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "run_id": "case-a",
                "case_name": "Case A",
                "workflow_mode": "test",
                "objective": "Verify modeling-memory summary.",
                "status": "pass",
                "artifacts": {
                    "model_inp": {"exists": True, "relative_path": "runs/case-a/model.inp"},
                    "runner_rpt": {"exists": False, "relative_path": "runs/case-a/model.rpt"},
                },
                "metrics": {"swmm_return_code": 0, "peak_flow": {"value": 1.0}},
                "qa": {"status": "pass", "fail_count": 0, "pass_count": 1},
                "warnings": ["test warning"],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "comparison.json").write_text(
        json.dumps({"comparison_available": False, "checks": [], "warnings": []}),
        encoding="utf-8",
    )
    (run_dir / "experiment_note.md").write_text(
        "# Experiment Note\n\n## Evidence Boundary\n\n- Fake test evidence only.\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runs-dir",
            str(runs_dir),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    expected = [
        "modeling_memory_index.json",
        "modeling_memory_index.md",
        "lessons_learned.md",
        "skill_update_proposals.md",
        "benchmark_verification_plan.md",
    ]
    for name in expected:
        assert (out_dir / name).exists()

    index = json.loads((out_dir / "modeling_memory_index.json").read_text(encoding="utf-8"))
    assert index["record_count"] == 1
    assert index["records"][0]["run_id"] == "case-a"
    assert "missing_rpt" in index["records"][0]["failure_patterns"]

    index_md = (out_dir / "modeling_memory_index.md").read_text(encoding="utf-8")
    assert "Source contract" in index_md
    assert "Missing evidence" in index_md
    assert "Assumptions" in index_md

    lessons = (out_dir / "lessons_learned.md").read_text(encoding="utf-8")
    assert "## Source Contract" in lessons
    assert "## Validation Boundary" in lessons
    assert "Assumptions, missing evidence, repeated failure patterns" in lessons

    proposals = (out_dir / "skill_update_proposals.md").read_text(encoding="utf-8")
    assert "Proposal boundaries" in proposals
    assert "must not claim the fix is correct" in proposals
