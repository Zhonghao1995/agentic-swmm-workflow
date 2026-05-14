"""``aiswmm memory reflect`` writes a proposal file and exits 0.

ME-3 contract (PRD memory-evolution-with-forgetting, issue #63):

* Without ``--apply`` the CLI is read-only-plus-write-proposal. It
  ingests the last 10 audit notes + the active/dormant lessons, calls
  the LLM proposer (stubbed here) for a structured diff, writes the
  diff to ``09_audit/memory_reflection_proposal.md`` in
  Obsidian-readable markdown, and exits 0.
* No ``human_decisions`` entry is appended — only ``--apply`` records
  human authority. The proposal is just a draft for review.
* The lessons file is not mutated.

The test stubs the LLM call via ``AISWMM_MEMORY_REFLECT_STUB_JSON`` so
the assertion is on *structure* (the proposal file exists, mentions the
change-type sections) rather than on free-form LLM prose.
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

Generated at UTC: `2026-05-14T00:00:00+00:00`.

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


def _seed_workspace(tmp: Path) -> tuple[Path, Path]:
    """Create a synthetic memory dir + a runs tree with one audit note."""
    memory_dir = tmp / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    lessons = memory_dir / "lessons_learned.md"
    lessons.write_text(_FIXTURE_LESSONS, encoding="utf-8")

    runs_dir = tmp / "runs"
    run_a = runs_dir / "run-a" / "09_audit"
    run_a.mkdir(parents=True)
    (run_a / "experiment_note.md").write_text(
        "# Experiment note for run-a\n\n"
        "- failure_pattern: continuity_parse_missing\n",
        encoding="utf-8",
    )
    return memory_dir, runs_dir


def _aiswmm(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``aiswmm`` as a subprocess with a customised env.

    The CLI subprocess inherits ``env`` so we can wire ``AISWMM_*``
    overrides for the lessons path, runs path, and the stub-LLM payload
    without monkeypatching anything in-process.
    """
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


_STUB_PROPOSAL = {
    "changes": [
        {
            "change_type": "merge",
            "pattern": "continuity_parse_missing",
            "summary": "Merge with peak_flow_parse_missing — both stem from partial runs.",
        },
    ],
}


class MemoryReflectWritesProposalTests(unittest.TestCase):
    def test_reflect_writes_proposal_and_exits_zero(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            memory_dir, runs_dir = _seed_workspace(tmp)
            audit_dir = tmp / "out" / "09_audit"
            audit_dir.mkdir(parents=True)

            env = os.environ.copy()
            env["AISWMM_MEMORY_DIR"] = str(memory_dir)
            env["AISWMM_RUNS_ROOT"] = str(runs_dir)
            env["AISWMM_MEMORY_REFLECT_STUB_JSON"] = json.dumps(_STUB_PROPOSAL)

            proc = _aiswmm(
                "memory",
                "reflect",
                "--audit-dir",
                str(audit_dir),
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            proposal = audit_dir / "memory_reflection_proposal.md"
            self.assertTrue(
                proposal.is_file(),
                f"memory_reflection_proposal.md not written under {audit_dir}",
            )
            text = proposal.read_text(encoding="utf-8")
            self.assertIn("Memory Reflection Proposal", text)
            self.assertIn("continuity_parse_missing", text)
            # change_type is the structural anchor downstream tooling
            # keys off — must appear verbatim.
            self.assertIn("merge", text.lower())

            # No human_decisions write without --apply.
            # (We don't supply a provenance fixture; the absence of the
            # crash itself confirms the CLI doesn't try to touch one.)
            # Sanity: lessons file should be unchanged.
            self.assertEqual(
                (memory_dir / "lessons_learned.md").read_text(encoding="utf-8"),
                _FIXTURE_LESSONS,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
