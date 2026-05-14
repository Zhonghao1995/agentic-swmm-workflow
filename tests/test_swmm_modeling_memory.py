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
    (run_dir / "model_diagnostics.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "swmm-experiment-audit",
                "status": "warning",
                "diagnostics": [
                    {
                        "id": "continuity_error_high",
                        "severity": "warning",
                        "message": "Continuity error exceeds the screening threshold.",
                    }
                ],
            }
        ),
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
        "project_memory_index.md",
        "run_memory_summaries.json",
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
    assert index["model_diagnostic_counts"]["continuity_error_high"] == 1
    assert (run_dir / "memory_summary.json").exists()
    run_summary = json.loads((run_dir / "memory_summary.json").read_text(encoding="utf-8"))
    assert run_summary["model_diagnostic_ids"] == ["continuity_error_high"]
    assert "routing_step / storage / inflow-outflow accounting" in run_summary["suspect_parameters"]
    assert (out_dir / "projects" / "case-a" / "project_memory.json").exists()
