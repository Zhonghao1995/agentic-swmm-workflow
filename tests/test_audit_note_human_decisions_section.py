"""``experiment_note.md`` renders ``## Human Decisions`` (PRD-Z).

When ``human_decisions`` is non-empty, the audit-note renderer must
emit a ``## Human Decisions`` section. The section disappears when the
array is empty so the existing note layout is unchanged for runs that
have not yet been touched by HITL.

The test inserts decisions into a freshly-audited provenance file via
:func:`agentic_swmm.hitl.decision_recorder.append_decision`, then
re-renders the note (via the same logic the audit script uses) and
asserts the new section is present.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.hitl.decision_recorder import HumanDecision, append_decision


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def _seed_runner(run_dir: Path) -> None:
    runner = run_dir / "05_runner"
    runner.mkdir(parents=True)
    (runner / "model.rpt").write_text(
        """
        ***** Node Inflow Summary *****
        ------------------------------------------------
          O1              OUTFALL       0.001       1.250      2    12:47

        ***** Flow Routing Continuity *****
        Continuity Error (%) ............. 0.00
        """,
        encoding="utf-8",
    )
    (runner / "model.out").write_text("binary-placeholder", encoding="utf-8")
    (runner / "stdout.txt").write_text("", encoding="utf-8")
    (runner / "stderr.txt").write_text("", encoding="utf-8")
    (runner / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "rpt": str(runner / "model.rpt"),
                    "out": str(runner / "model.out"),
                    "stdout": str(runner / "stdout.txt"),
                    "stderr": str(runner / "stderr.txt"),
                },
                "metrics": {
                    "peak": {
                        "node": "O1",
                        "peak": 1.25,
                        "time_hhmm": "12:47",
                        "source": "Node Inflow Summary",
                    }
                },
                "return_code": 0,
            }
        ),
        encoding="utf-8",
    )


def _run_audit(repo: Path, run_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--run-dir",
            str(run_dir),
            "--repo-root",
            str(repo),
            "--no-obsidian",
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class HumanDecisionsSectionTests(unittest.TestCase):
    def test_section_absent_when_no_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-a"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            _run_audit(repo, run_dir)
            note = (run_dir / "09_audit" / "experiment_note.md").read_text(
                encoding="utf-8"
            )
        self.assertNotIn("## Human Decisions", note)

    def test_section_present_after_decisions_appended_and_reaudit(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run_dir = repo / "runs" / "case-a"
            run_dir.mkdir(parents=True)
            _seed_runner(run_dir)
            _run_audit(repo, run_dir)
            # Append a human decision.
            prov_path = run_dir / "09_audit" / "experiment_provenance.json"
            append_decision(
                prov_path,
                HumanDecision(
                    id="dec-1",
                    action="expert_review_approved",
                    by="alice",
                    at_utc="2026-05-14T08:15:00+00:00",
                    pattern="continuity_error_over_threshold",
                    evidence_ref="06_qa/qa_summary.json",
                    decision_text="Alice approved despite the warning.",
                ),
            )
            # Re-audit: must preserve the decision and render the section.
            _run_audit(repo, run_dir)
            note = (run_dir / "09_audit" / "experiment_note.md").read_text(
                encoding="utf-8"
            )
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
        self.assertIn("## Human Decisions", note)
        self.assertIn("expert_review_approved", note)
        self.assertIn("alice", note)
        # Decisions must survive the re-audit (audit_run.py overwrites
        # the provenance file, so it has to read and re-emit the
        # human_decisions array).
        self.assertEqual(len(prov["human_decisions"]), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
