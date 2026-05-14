"""Tests for ``agentic_swmm.case.case_id`` (PRD-CASE-ID).

Validates the four resolution sources and the slug validator. The
resolver is the single seam every case-aware feature uses, so the
contract has to be tight: good slugs accepted, bad slugs (uppercase,
spaces, path traversal, empty, oversized) rejected.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.case.case_id import (
    CaseId,
    CaseIdResolutionError,
    CaseIdValidationError,
    is_valid_case_id,
    resolve_case_id,
    validate_case_id,
)


class ValidateCaseIdTests(unittest.TestCase):
    """Slug regex contract: ``^[a-z][a-z0-9-]{1,63}$``."""

    def test_accepts_simple_slug(self) -> None:
        self.assertTrue(is_valid_case_id("tod-creek"))
        validate_case_id("tod-creek")  # does not raise

    def test_accepts_alphanumeric(self) -> None:
        self.assertTrue(is_valid_case_id("urban-demo-001"))
        self.assertTrue(is_valid_case_id("saanich-east"))
        self.assertTrue(is_valid_case_id("a1"))

    def test_rejects_uppercase(self) -> None:
        self.assertFalse(is_valid_case_id("Tod-Creek"))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("Tod-Creek")

    def test_rejects_spaces(self) -> None:
        self.assertFalse(is_valid_case_id("tod creek"))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("tod creek")

    def test_rejects_path_traversal(self) -> None:
        self.assertFalse(is_valid_case_id("../etc/passwd"))
        self.assertFalse(is_valid_case_id("tod/creek"))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("../etc")

    def test_rejects_empty(self) -> None:
        self.assertFalse(is_valid_case_id(""))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("")

    def test_rejects_oversized(self) -> None:
        # 64 chars is the boundary — the regex allows up to 64 total
        # (1 lead + 63 tail = 64), so 65 must reject.
        self.assertFalse(is_valid_case_id("a" + "b" * 64))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("a" + "b" * 64)

    def test_rejects_leading_digit(self) -> None:
        # The lead char must be [a-z].
        self.assertFalse(is_valid_case_id("1-bad"))
        with self.assertRaises(CaseIdValidationError):
            validate_case_id("1-bad")

    def test_rejects_leading_hyphen(self) -> None:
        self.assertFalse(is_valid_case_id("-bad"))

    def test_rejects_underscore(self) -> None:
        # Hyphens only, mirrors GitHub slug conventions per the PRD.
        self.assertFalse(is_valid_case_id("tod_creek"))


class ResolveCaseIdTests(unittest.TestCase):
    """Resolution order: explicit -> session_state -> prior run -> fail."""

    def test_explicit_declared_wins(self) -> None:
        result = resolve_case_id(
            declared="tod-creek",
            run_dir=None,
            session_state={"case_id": "ignored-session"},
        )
        self.assertIsInstance(result, CaseId)
        self.assertEqual(result.value, "tod-creek")
        self.assertEqual(result.source, "explicit")

    def test_explicit_declared_validated(self) -> None:
        with self.assertRaises(CaseIdValidationError):
            resolve_case_id(
                declared="BAD CASE",
                run_dir=None,
                session_state=None,
            )

    def test_session_state_when_no_declared(self) -> None:
        result = resolve_case_id(
            declared=None,
            run_dir=None,
            session_state={"case_id": "saanich-east"},
        )
        self.assertEqual(result.value, "saanich-east")
        self.assertEqual(result.source, "session_state")

    def test_session_state_bad_slug_raises(self) -> None:
        with self.assertRaises(CaseIdValidationError):
            resolve_case_id(
                declared=None,
                run_dir=None,
                session_state={"case_id": "Bad Slug"},
            )

    def test_prior_run_in_workflow(self) -> None:
        """Falls back to a sibling run's experiment_provenance.json."""
        with TemporaryDirectory() as tmp:
            workflow_dir = Path(tmp) / "runs" / "2026-05-14"
            prior_audit = workflow_dir / "120000_run_prior" / "09_audit"
            prior_audit.mkdir(parents=True)
            (prior_audit / "experiment_provenance.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.3",
                        "case_id": "tod-creek",
                        "run_id": "120000_run_prior",
                    }
                ),
                encoding="utf-8",
            )
            new_run_dir = workflow_dir / "130000_run_new"
            new_run_dir.mkdir(parents=True)
            result = resolve_case_id(
                declared=None,
                run_dir=new_run_dir,
                session_state=None,
            )
        self.assertEqual(result.value, "tod-creek")
        self.assertEqual(result.source, "prior_run")

    def test_prior_run_ignored_when_no_case_id(self) -> None:
        """v1.2 provenance has no case_id; falls through to failure."""
        with TemporaryDirectory() as tmp:
            workflow_dir = Path(tmp) / "runs" / "2026-05-14"
            prior_audit = workflow_dir / "120000_run_prior" / "09_audit"
            prior_audit.mkdir(parents=True)
            (prior_audit / "experiment_provenance.json").write_text(
                json.dumps({"schema_version": "1.2", "run_id": "prior"}),
                encoding="utf-8",
            )
            new_run_dir = workflow_dir / "130000_run_new"
            new_run_dir.mkdir(parents=True)
            with self.assertRaises(CaseIdResolutionError):
                resolve_case_id(
                    declared=None,
                    run_dir=new_run_dir,
                    session_state=None,
                    interactive=False,
                )

    def test_no_source_fails_loud_non_interactive(self) -> None:
        """All sources exhausted in non-interactive mode -> raise."""
        with self.assertRaises(CaseIdResolutionError) as ctx:
            resolve_case_id(
                declared=None,
                run_dir=None,
                session_state=None,
                interactive=False,
            )
        # The error message must point the user at the remedy.
        self.assertIn("--case-id", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
