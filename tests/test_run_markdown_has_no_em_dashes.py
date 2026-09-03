"""Generated run-folder markdown carries no em dashes.

Live test 2026-09-03 (S36): a scan of every generated markdown file under
the campaign root found generator-side em dashes in the executor's
final_report.md "What I did" lines, the audit's experiment_note.md labels,
and the runs INDEX.md footer. The house style joins with a colon or a comma.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EM_DASH = "—"
REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


@dataclass
class _Call:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


def test_what_i_did_bullets_join_with_a_colon() -> None:
    from agentic_swmm.agent.reporting import _what_i_did

    plan = [_Call("doctor"), _Call("run_swmm_inp", {"inp_path": "examples/todcreek/model.inp"})]
    results = [{"ok": True, "summary": "healthy"}, {"ok": False, "summary": "INP not found"}]
    bullets = _what_i_did(plan, results)
    assert bullets[0] == "- 1. `doctor`: ok (healthy)"
    assert bullets[1].endswith(": failed (INP not found)")
    assert not any(EM_DASH in b for b in bullets)


def test_audit_note_labels_carry_no_em_dash(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("audit_run_no_em_dash", AUDIT_PATH)
    audit = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(audit)
    run_dir = tmp_path / "034722_todcreek_run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\ntodcreek\n", encoding="utf-8")
    (run_dir / "06_runner").mkdir()
    (run_dir / "06_runner" / "model.rpt").write_text("  Flow Units ............... CMS\n", encoding="utf-8")
    (run_dir / "06_runner" / "manifest.json").write_text(
        json.dumps({"return_code": 0, "run_ok": True, "files": {"rpt": str(run_dir / "06_runner" / "model.rpt")}}),
        encoding="utf-8",
    )
    provenance, runner_manifest = audit.collect_run(run_dir, repo_root=tmp_path)
    comparison = audit.build_comparison(provenance, None)
    note = audit.render_note(provenance, comparison, tmp_path, runner_manifest)
    assert "Continuity error, runoff quantity" in note
    assert EM_DASH not in note


def test_runs_index_footer_carries_no_em_dash() -> None:
    from agentic_swmm.audit.moc_generator import generate_moc

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit_dir = root / "2026-09-03" / "024635_downtown-victoria-bc_run" / "09_audit"
        audit_dir.mkdir(parents=True)
        (audit_dir / "experiment_note.md").write_text(
            "---\ntype: experiment-audit\nstatus: pass\ncase: victoria\ndate: 2026-09-03\n"
            "has_plot: false\nhas_qa: true\nfailed_qa_checks: 0\n---\n\n# Experiment Audit - victoria\n",
            encoding="utf-8",
        )
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps({"schema_version": "1.1", "status": "pass"}), encoding="utf-8"
        )
        md = generate_moc(root)
    assert "Do not edit by hand: re-run" in md
    assert EM_DASH not in md


def test_design_review_rule_headings_join_with_a_colon() -> None:
    spec = importlib.util.spec_from_file_location(
        "design_review_no_em_dash", REPO_ROOT / "skills" / "swmm-design-review" / "scripts" / "design_review.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = (REPO_ROOT / "skills" / "swmm-design-review" / "scripts" / "design_review.py").read_text(encoding="utf-8")
    assert 'f"#### {rule_id}: {title}"' in source
    for rulebook in (REPO_ROOT / "skills" / "swmm-design-review" / "rulebooks").glob("*.yaml"):
        assert EM_DASH not in rulebook.read_text(encoding="utf-8"), rulebook.name
