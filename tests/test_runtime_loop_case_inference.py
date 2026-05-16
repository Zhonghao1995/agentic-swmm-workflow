"""Cycle 1 of PRD #118: ``_case_slug`` must come from the case registry.

The runtime currently hardcodes ``tecnopolo`` / ``todcreek`` substrings.
This test creates a synthetic case via the registry and asserts that
the slug inference picks it up. Since case_registry.list_cases reads
``cases/<id>/case_meta.yaml`` under ``repo_root()``, we monkey-patch
``case_registry.repo_root`` to point at a temporary directory.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

from agentic_swmm.agent.runtime_loop import _case_slug


def _write_case_meta(repo: Path, case_id: str, *, display_name: str = "") -> None:
    case_dir = repo / "cases" / case_id
    case_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "display_name": display_name or case_id,
        "study_purpose": "fixture",
        "created_utc": "2026-05-16T00:00:00Z",
        "catchment": {},
        "inputs": {},
        "notes": "",
    }
    (case_dir / "case_meta.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )


class CaseSlugRegistryDrivenTests(unittest.TestCase):
    def test_synthetic_case_is_recognised_via_registry(self) -> None:
        """A user-added case under cases/<id>/ is discovered without code change."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_case_meta(repo, "mini-watershed")
            with mock.patch(
                "agentic_swmm.case.case_registry.repo_root",
                return_value=repo,
            ):
                slug = _case_slug("run the mini-watershed analysis")
        self.assertEqual(slug, "mini-watershed")

    def test_unknown_prompt_falls_back_to_safe_name(self) -> None:
        """When no registered case matches, the slug derives from prompt text."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Empty registry.
            (repo / "cases").mkdir()
            with mock.patch(
                "agentic_swmm.case.case_registry.repo_root",
                return_value=repo,
            ):
                slug = _case_slug("what skills do you have?")
        # Must not be a hardcoded watershed name; the fallback derives
        # from the prompt itself (``_safe_name`` truncates to 32 chars).
        self.assertNotIn(slug, {"tecnopolo", "todcreek"})

    def test_display_name_match_is_recognised(self) -> None:
        """Casual references to display_name still resolve to the case_id."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_case_meta(repo, "mini-watershed", display_name="Mini Watershed")
            with mock.patch(
                "agentic_swmm.case.case_registry.repo_root",
                return_value=repo,
            ):
                slug = _case_slug("please run the Mini Watershed scenario")
        self.assertEqual(slug, "mini-watershed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
