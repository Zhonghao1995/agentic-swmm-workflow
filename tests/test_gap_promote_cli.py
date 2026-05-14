"""CLI argument parsing + refusal cases for ``aiswmm gap promote-to-case``
(PRD-GF-PROMOTE).

The promote-to-case CLI is the expert-only seam that records a previously-
resolved gap-fill decision as a case-level default. The tests in this
module pin down the failure modes that protect the audit chain:

* ``--help`` prints a non-empty help text (Done Criterion 1).
* No ``case_id`` resolvable -> fail with an ``aiswmm case init`` hint.
* Source decision has ``proposer_overridden: true`` and no
  ``--accept-override`` flag -> refuse.
* ``decision_id`` not present in the source run's
  ``09_audit/gap_decisions.json`` -> refuse with a clear message.
* Existing case-defaults entry would conflict and ``--force`` is not
  passed -> refuse.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


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
    *,
    decision_id: str = "dec-abc123",
    field: str = "manning_n_imperv",
    value: float = 0.013,
    proposer_overridden: bool = False,
    case_id: str | None = "tod-creek",
) -> Path:
    """Seed a run dir with a gap_decisions.json + provenance file."""

    run_dir = tmp_path / "runs" / "2026-05-12" / "test-run"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    prov_payload: dict = {
        "schema_version": "1.3",
        "run_id": "test-run",
    }
    if case_id is not None:
        prov_payload["case_id"] = case_id
    (audit / "experiment_provenance.json").write_text(
        json.dumps(prov_payload), encoding="utf-8"
    )
    gap_decision = {
        "decision_id": decision_id,
        "gap_id": "gap-test",
        "severity": "L3",
        "field": field,
        "proposer": {
            "source": "registry",
            "confidence": "HIGH",
            "registry_ref": "defaults_table.yaml#manning_n_paved",
            "literature_ref": "EPA SWMM 5 Reference Manual",
            "llm_call_id": None,
        },
        "proposed_value": value,
        "final_value": value,
        "proposer_overridden": proposer_overridden,
        "decided_by": "human",
        "decided_at": "2026-05-12T09:15:43Z",
        "resume_mode": "tool_retry",
        "human_decisions_ref": None,
    }
    (audit / "gap_decisions.json").write_text(
        json.dumps({"schema_version": "1", "decisions": [gap_decision]}),
        encoding="utf-8",
    )
    return run_dir


class HelpTextTests(unittest.TestCase):
    def test_help_prints_nonempty(self) -> None:
        proc = _aiswmm("gap", "promote-to-case", "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("promote-to-case", proc.stdout)
        self.assertIn("run_dir", proc.stdout)
        self.assertIn("decision_id", proc.stdout)

    def test_list_case_defaults_help_prints_nonempty(self) -> None:
        proc = _aiswmm("gap", "list-case-defaults", "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("list-case-defaults", proc.stdout)
        self.assertIn("case_id", proc.stdout)


class RefusalCasesTests(unittest.TestCase):
    """Refusal paths: each one must fail loudly with a clear hint."""

    def test_missing_decision_id_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(tmp_path)
            # Point at a decision_id that does not exist in the ledger.
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-not-found",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
        self.assertNotEqual(proc.returncode, 0)
        haystack = proc.stderr + proc.stdout
        self.assertIn("dec-not-found", haystack)

    def test_proposer_overridden_refuses_without_accept_override(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(
                tmp_path, proposer_overridden=True
            )
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
        self.assertNotEqual(proc.returncode, 0)
        haystack = proc.stderr + proc.stdout
        self.assertIn("--accept-override", haystack)

    def test_proposer_overridden_succeeds_with_accept_override(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(
                tmp_path, proposer_overridden=True
            )
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                "--accept-override",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_case_id_resolves_refuses_with_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(tmp_path, case_id=None)
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
        self.assertNotEqual(proc.returncode, 0)
        haystack = proc.stderr + proc.stdout
        # Per the PRD the failure must hint at ``aiswmm case init``.
        self.assertIn("case init", haystack)

    def test_explicit_case_id_overrides_inference(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No case_id in provenance — explicit flag provides it.
            run_dir = _seed_run_with_decision(tmp_path, case_id=None)
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                "--case-id",
                "explicit-case",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            case_file = tmp_path / "cases" / "explicit-case" / "gap_defaults.yaml"
            self.assertTrue(case_file.is_file())

    def test_conflict_without_force_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(tmp_path)
            # First promote — succeeds.
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Second promote with the same field but a different value
            # in a fresh decision — must refuse without --force.
            run_dir2 = tmp_path / "runs" / "2026-05-13" / "test-run-2"
            audit2 = run_dir2 / "09_audit"
            audit2.mkdir(parents=True)
            (audit2 / "experiment_provenance.json").write_text(
                json.dumps(
                    {"schema_version": "1.3", "run_id": "r2", "case_id": "tod-creek"}
                ),
                encoding="utf-8",
            )
            (audit2 / "gap_decisions.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "decisions": [
                            {
                                "decision_id": "dec-xyz456",
                                "gap_id": "gap-y",
                                "severity": "L3",
                                "field": "manning_n_imperv",
                                "proposer": {
                                    "source": "human",
                                    "confidence": "HIGH",
                                },
                                "proposed_value": None,
                                "final_value": 0.025,
                                "proposer_overridden": False,
                                "decided_by": "human",
                                "decided_at": "2026-05-13T09:15:43Z",
                                "resume_mode": "tool_retry",
                                "human_decisions_ref": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            proc2 = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir2),
                "dec-xyz456",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
        self.assertNotEqual(proc2.returncode, 0)
        haystack = proc2.stderr + proc2.stdout
        self.assertIn("--force", haystack)


class NoteFlagTests(unittest.TestCase):
    def test_note_is_recorded_on_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run_with_decision(tmp_path)
            proc = _aiswmm(
                "gap",
                "promote-to-case",
                str(run_dir),
                "dec-abc123",
                "--note",
                "validated against Tod Creek 2024 calibration",
                env={"AISWMM_REPO_ROOT": str(tmp_path)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            case_file = tmp_path / "cases" / "tod-creek" / "gap_defaults.yaml"
            payload = yaml.safe_load(case_file.read_text(encoding="utf-8"))
        entry = payload["entries"]["manning_n_imperv"]
        self.assertIn("validated against", entry.get("notes", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
