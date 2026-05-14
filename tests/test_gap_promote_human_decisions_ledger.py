"""``human_decisions`` ledger entry after promote (PRD-GF-PROMOTE).

Promotion is a first-class human decision — the source run's
``experiment_provenance.json.human_decisions`` array gains an entry
with:

- ``action: gap_promote_to_case``
- ``evidence_ref`` pointing at the source decision
  (``09_audit/gap_decisions.json#<decision_id>``)
- ``decision_text`` referencing the case-defaults entry written
  (``cases/<case_id>/gap_defaults.yaml#<field>``)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def _aiswmm(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc_env = os.environ.copy()
    if env is not None:
        proc_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=proc_env,
    )


def _seed_run(tmp_path: Path, decision_id: str = "dec-abc123") -> Path:
    run_dir = tmp_path / "runs" / "2026-05-12" / "test-run"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    (audit / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "1.3",
                "run_id": "test-run",
                "case_id": "tod-creek",
            }
        ),
        encoding="utf-8",
    )
    (audit / "gap_decisions.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "decisions": [
                    {
                        "decision_id": decision_id,
                        "gap_id": "gap-test",
                        "severity": "L3",
                        "field": "manning_n_imperv",
                        "proposer": {
                            "source": "registry",
                            "confidence": "HIGH",
                        },
                        "proposed_value": 0.013,
                        "final_value": 0.013,
                        "proposer_overridden": False,
                        "decided_by": "human",
                        "decided_at": "2026-05-12T09:15:43Z",
                        "resume_mode": "tool_retry",
                        "human_decisions_ref": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_dir


class HumanDecisionsLedgerTests(unittest.TestCase):
    def test_promote_appends_human_decisions_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        decisions = [
            d
            for d in (prov.get("human_decisions") or [])
            if d.get("action") == "gap_promote_to_case"
        ]
        self.assertEqual(len(decisions), 1)
        entry = decisions[0]
        # evidence_ref must point at the gap_decisions.json decision.
        evidence = str(entry.get("evidence_ref") or "")
        self.assertIn("gap_decisions.json", evidence)
        self.assertIn("dec-abc123", evidence)
        # decision_text must point at the target case-defaults entry
        # (target_ref) so a reader knows which case file was written.
        decision_text = str(entry.get("decision_text") or "")
        self.assertIn("cases/tod-creek/gap_defaults.yaml", decision_text)
        self.assertIn("manning_n_imperv", decision_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
