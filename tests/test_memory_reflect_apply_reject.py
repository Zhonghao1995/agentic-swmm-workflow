"""``aiswmm memory reflect --apply`` honours a stdin ``n`` (reject).

ME-3 governance: the per-change prompt is the modeller's veto.
When the human types ``n`` (or any non-y answer) at the prompt:

* ``lessons_learned.md`` must remain byte-for-byte unchanged.
* The provenance file's ``human_decisions`` array must remain empty —
  a rejection is a non-event, not a recorded decision. (Recording
  rejections would let a malicious agent claim to "have asked" the
  human; the audit trail records only ratifications.)

The stub LLM proposes one change; stdin replies ``n``; assertions
verify nothing landed.
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
"""


_STUB_PROPOSAL = {
    "changes": [
        {
            "change_type": "retire",
            "pattern": "continuity_parse_missing",
            "summary": "Test reject — not actually retiring.",
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


class MemoryReflectApplyRejectTests(unittest.TestCase):
    def test_apply_no_leaves_state_unchanged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            memory_dir, runs_dir, audit_dir = _seed_workspace(tmp)
            original_lessons = (memory_dir / "lessons_learned.md").read_text(
                encoding="utf-8"
            )

            env = os.environ.copy()
            env["AISWMM_MEMORY_DIR"] = str(memory_dir)
            env["AISWMM_RUNS_ROOT"] = str(runs_dir)
            env["AISWMM_MEMORY_REFLECT_STUB_JSON"] = json.dumps(_STUB_PROPOSAL)
            env.pop("AISWMM_AUTO_APPROVE", None)

            proc = _aiswmm(
                "memory",
                "reflect",
                "--apply",
                "--audit-dir",
                str(audit_dir),
                env=env,
                stdin_input="n\n",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            # Lessons file untouched.
            self.assertEqual(
                (memory_dir / "lessons_learned.md").read_text(encoding="utf-8"),
                original_lessons,
            )
            # No human_decisions recorded — rejection is a non-event.
            payload = json.loads(
                (audit_dir / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload.get("human_decisions") or [], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
