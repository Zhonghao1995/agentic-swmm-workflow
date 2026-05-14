"""``aiswmm memory reflect --apply`` records a human_decisions entry.

ME-3 contract (PRD memory-evolution-with-forgetting, issue #63):

* With ``--apply`` the CLI walks each proposed change, asks the human
  "apply this? [y/N]" via stdin, and on ``y`` it:

    1. Applies the change to ``lessons_learned.md`` (this test uses a
       ``merge`` change — the merged-into pattern receives a section
       note recording the merge).
    2. Appends a ``human_decisions`` row to
       ``09_audit/experiment_provenance.json`` with
       ``action="memory_reflect_apply"``,
       ``pattern=<pattern_name>``,
       ``change_type`` in the decision_text,
       and the SHA of ``memory_reflection_proposal.md`` recorded as
       evidence_ref-style evidence so an auditor can trace which
       proposal version was ratified.

The LLM is stubbed (``AISWMM_MEMORY_REFLECT_STUB_JSON``) so the
assertion is on the apply mechanics, not on prose quality.

stdin is supplied via ``input="y\\n"`` so the
``permissions.prompt_user`` confirmation returns True without an
interactive TTY.
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


_FIXTURE_LESSONS = """\
<!-- schema_version: 1.1 -->
# Lessons Learned

## continuity_parse_missing

<!-- aiswmm-metadata
metadata:
  first_seen_utc: 2026-05-14T00:00:00Z
  last_seen_utc: 2026-05-14T00:00:00Z
  evidence_count: 3
  evidence_runs:
    - run-a
    - run-b
    - run-c
  status: active
  confidence_score: 3.0
  half_life_days: 90
/aiswmm-metadata -->

Continuity error line missing from RPT.

## peak_flow_parse_missing

<!-- aiswmm-metadata
metadata:
  first_seen_utc: 2026-05-14T00:00:00Z
  last_seen_utc: 2026-05-14T00:00:00Z
  evidence_count: 1
  evidence_runs:
    - run-a
  status: dormant
  confidence_score: 0.5
  half_life_days: 90
/aiswmm-metadata -->

Peak flow value not located in parsed RPT.
"""


_STUB_PROPOSAL = {
    "changes": [
        {
            "change_type": "merge",
            "pattern": "continuity_parse_missing",
            "merge_with": "peak_flow_parse_missing",
            "summary": "Both stem from partial SWMM runs — consolidate.",
        },
    ],
}


def _seed_workspace(tmp: Path) -> tuple[Path, Path, Path]:
    memory_dir = tmp / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "lessons_learned.md").write_text(
        _FIXTURE_LESSONS, encoding="utf-8"
    )

    runs_dir = tmp / "runs"
    (runs_dir / "run-a" / "09_audit").mkdir(parents=True)
    (runs_dir / "run-a" / "09_audit" / "experiment_note.md").write_text(
        "# Note for run-a\n", encoding="utf-8"
    )

    audit_dir = tmp / "out" / "09_audit"
    audit_dir.mkdir(parents=True)
    # Seed an existing provenance so append_decision finds the file.
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.1", "run_id": "out"}),
        encoding="utf-8",
    )
    return memory_dir, runs_dir, audit_dir


def _aiswmm(
    *args: str, env: dict[str, str], stdin_input: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        input=stdin_input,
    )


class MemoryReflectApplyRecordsHumanDecisionTests(unittest.TestCase):
    def test_apply_yes_records_human_decision(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            memory_dir, runs_dir, audit_dir = _seed_workspace(tmp)

            env = os.environ.copy()
            env["AISWMM_MEMORY_DIR"] = str(memory_dir)
            env["AISWMM_RUNS_ROOT"] = str(runs_dir)
            env["AISWMM_MEMORY_REFLECT_STUB_JSON"] = json.dumps(_STUB_PROPOSAL)
            # Force the prompt to actually consume our stdin "y" — the
            # auto-approve env var would short-circuit it.
            env.pop("AISWMM_AUTO_APPROVE", None)

            proc = _aiswmm(
                "memory",
                "reflect",
                "--apply",
                "--audit-dir",
                str(audit_dir),
                env=env,
                stdin_input="y\n",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            provenance_path = audit_dir / "experiment_provenance.json"
            payload = json.loads(provenance_path.read_text(encoding="utf-8"))
            decisions = payload.get("human_decisions") or []
            self.assertEqual(len(decisions), 1, decisions)
            entry = decisions[0]
            self.assertEqual(entry["action"], "memory_reflect_apply")
            self.assertEqual(entry["pattern"], "continuity_parse_missing")
            self.assertIn("merge", (entry.get("decision_text") or ""))
            self.assertIn(
                "memory_reflection_proposal.md",
                entry.get("evidence_ref") or "",
            )

            # The proposal was written to disk alongside the recorded
            # decision so an auditor can resolve evidence_ref locally.
            proposal = audit_dir / "memory_reflection_proposal.md"
            self.assertTrue(proposal.is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
