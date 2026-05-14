"""End-to-end test for PRD-GF-PROMOTE.

Workflow under test:

1. Seed Run-1: a Manning's n gap-fill decision is recorded (human
   approval, not overridden).
2. ``aiswmm gap promote-to-case <run-1-dir> <decision_id>`` —
   case-defaults YAML appears under ``cases/<case_id>/``.
3. Run-2 in the same case: the proposer encounters the same parameter.
   It must hit the case-default with NO LLM call, NO human prompt, and
   record ``registry_ref`` pointing at the case file.
4. ``aiswmm gap list-case-defaults <case_id>`` prints the entry.
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


def _seed_run_with_decision(
    tmp_path: Path,
    run_name: str = "test-run-1",
    decision_id: str = "dec-promote-e2e",
    value: float = 0.0145,
) -> Path:
    """Seed a run with a human-decided gap_decision (non-overridden)."""

    run_dir = tmp_path / "runs" / "2026-05-12" / run_name
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    (audit / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "1.3",
                "run_id": run_name,
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
                        "final_value": value,
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


class GapPromoteE2ETests(unittest.TestCase):
    def test_promote_round_trip_run1_to_run2(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run1_dir = _seed_run_with_decision(
                tmp_path, run_name="run-1", value=0.0145
            )
            env = {"AISWMM_REPO_ROOT": str(tmp_path)}

            # --- Promote -----------------------------------------------------
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run1_dir),
                "dec-promote-e2e",
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            case_file = tmp_path / "cases" / "tod-creek" / "gap_defaults.yaml"
            self.assertTrue(case_file.is_file())

            # --- list-case-defaults prints the entry -------------------------
            proc_list = _aiswmm(
                "gap",
                "list-case-defaults",
                "tod-creek",
                env=env,
            )
            self.assertEqual(proc_list.returncode, 0, proc_list.stderr)
            self.assertIn("manning_n_imperv", proc_list.stdout)
            self.assertIn("0.0145", proc_list.stdout)

            # --- Run-2 in the same case: proposer must hit case-default -----
            # Drive the proposer directly so we can assert no LLM call.
            from agentic_swmm.gap_fill.protocol import GapSignal
            from agentic_swmm.gap_fill.proposer import propose

            saved = os.environ.get("AISWMM_REPO_ROOT")
            os.environ["AISWMM_REPO_ROOT"] = str(tmp_path)
            try:
                run2_dir = tmp_path / "runs" / "2026-05-13" / "run-2"
                run2_dir.mkdir(parents=True)
                signal = GapSignal(
                    gap_id="gap-run2",
                    severity="L3",
                    kind="param_value",
                    field="manning_n_imperv",
                    context={"tool": "build_inp"},
                )

                def _llm(*, signal, run_dir):  # pragma: no cover - guarded
                    raise AssertionError(
                        "LLM must not be called when case-default hits"
                    )

                decision = propose(
                    signal=signal,
                    run_dir=run2_dir,
                    llm_proposal_fn=_llm,
                    case_id="tod-creek",
                )
            finally:
                if saved is None:
                    os.environ.pop("AISWMM_REPO_ROOT", None)
                else:
                    os.environ["AISWMM_REPO_ROOT"] = saved

        # The case-default value (0.0145) wins; the global registry default
        # would have produced 0.013, and the LLM was forbidden from running.
        self.assertEqual(decision.final_value, 0.0145)
        self.assertEqual(decision.proposed_value, 0.0145)
        self.assertIn(
            "cases/tod-creek/gap_defaults.yaml",
            decision.proposer.registry_ref or "",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
