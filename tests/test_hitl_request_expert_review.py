"""Tests for the ``request_expert_review`` agent tool (PRD-Z).

The tool is a runtime checkpoint: when a QA threshold has been crossed,
the agent must call it with ``pattern``, ``evidence_ref``, and
``message``. The handler:

* refuses if ``evidence_ref`` is missing on disk;
* prints a clearly visible block to stderr;
* prompts the human via ``permissions.prompt_user`` (mocked in tests);
* appends a ``human_decisions`` record (approved or denied);
* returns ``{ok, approved, decision_id}``.

A separate code path handles non-interactive single-shot: without
``--auto-approve-hitl`` the call exits non-zero; with the flag the
decision is recorded as ``auto_approve_hitl_enabled`` and the call
returns ``approved=True``.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


REGISTRY = AgentToolRegistry()


def _seed_run(tmp: Path) -> Path:
    run_dir = tmp / "runs" / "case-a"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    (audit / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
        encoding="utf-8",
    )
    qa_dir = run_dir / "06_qa"
    qa_dir.mkdir()
    (qa_dir / "qa_summary.json").write_text(
        json.dumps({"continuity": {"flow_routing": 6.5}}),
        encoding="utf-8",
    )
    return run_dir


class ToolRegistrationTests(unittest.TestCase):
    def test_request_expert_review_is_registered(self) -> None:
        self.assertIn("request_expert_review", REGISTRY.names)

    def test_request_expert_review_is_not_read_only(self) -> None:
        # PRD-Z: QUICK profile must NEVER auto-approve the HITL pause.
        self.assertFalse(REGISTRY.is_read_only("request_expert_review"))


class HandlerTests(unittest.TestCase):
    def test_y_answer_records_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            call = ToolCall(
                name="request_expert_review",
                args={
                    "run_dir": str(run_dir),
                    "pattern": "continuity_error_over_threshold",
                    "evidence_ref": "06_qa/qa_summary.json",
                    "message": "Continuity error 6.5% > 5%.",
                },
            )
            with mock.patch(
                "agentic_swmm.hitl.request_expert_review.permissions.prompt_user",
                return_value=True,
            ), mock.patch("sys.stdin.isatty", return_value=True):
                result = REGISTRY.execute(call, tmp_path)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["approved"])
        self.assertEqual(len(prov["human_decisions"]), 1)
        self.assertEqual(
            prov["human_decisions"][0]["action"], "expert_review_approved"
        )
        self.assertEqual(
            prov["human_decisions"][0]["pattern"],
            "continuity_error_over_threshold",
        )

    def test_n_answer_records_denial(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            call = ToolCall(
                name="request_expert_review",
                args={
                    "run_dir": str(run_dir),
                    "pattern": "continuity_error_over_threshold",
                    "evidence_ref": "06_qa/qa_summary.json",
                    "message": "Continuity error 6.5% > 5%.",
                },
            )
            with mock.patch(
                "agentic_swmm.hitl.request_expert_review.permissions.prompt_user",
                return_value=False,
            ), mock.patch("sys.stdin.isatty", return_value=True):
                result = REGISTRY.execute(call, tmp_path)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["approved"])
        self.assertEqual(
            prov["human_decisions"][0]["action"], "expert_review_denied"
        )

    def test_missing_evidence_ref_returns_ok_false(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            call = ToolCall(
                name="request_expert_review",
                args={
                    "run_dir": str(run_dir),
                    "pattern": "continuity_error_over_threshold",
                    "evidence_ref": "06_qa/this_file_does_not_exist.json",
                    "message": "Continuity error 6.5% > 5%.",
                },
            )
            # prompt_user should not be reached; if it is, the test must fail.
            with mock.patch(
                "agentic_swmm.hitl.request_expert_review.permissions.prompt_user",
                side_effect=AssertionError("prompt should not be reached"),
            ):
                result = REGISTRY.execute(call, tmp_path)
        self.assertFalse(result["ok"])
        self.assertIn("evidence_ref", result.get("summary", ""))

    def test_non_interactive_without_flag_returns_ok_false(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            call = ToolCall(
                name="request_expert_review",
                args={
                    "run_dir": str(run_dir),
                    "pattern": "continuity_error_over_threshold",
                    "evidence_ref": "06_qa/qa_summary.json",
                    "message": "Continuity error 6.5% > 5%.",
                },
            )
            # Force non-interactive: AISWMM_HITL_AUTO_APPROVE unset and isatty()=False.
            env = {k: v for k, v in os.environ.items() if k != "AISWMM_HITL_AUTO_APPROVE"}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch("sys.stdin.isatty", return_value=False):
                result = REGISTRY.execute(call, tmp_path)
        self.assertFalse(result["ok"])
        self.assertFalse(result["approved"])
        self.assertIn("--auto-approve-hitl", result.get("summary", ""))

    def test_non_interactive_with_flag_records_auto_approve_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = _seed_run(tmp_path)
            call = ToolCall(
                name="request_expert_review",
                args={
                    "run_dir": str(run_dir),
                    "pattern": "continuity_error_over_threshold",
                    "evidence_ref": "06_qa/qa_summary.json",
                    "message": "Continuity error 6.5% > 5%.",
                },
            )
            with mock.patch.dict(os.environ, {"AISWMM_HITL_AUTO_APPROVE": "1"}, clear=False), \
                 mock.patch("sys.stdin.isatty", return_value=False):
                result = REGISTRY.execute(call, tmp_path)
            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["approved"])
        actions = [d["action"] for d in prov["human_decisions"]]
        self.assertIn("auto_approve_hitl_enabled", actions)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
