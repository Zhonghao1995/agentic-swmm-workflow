"""Cycle 3 of PRD #118: memory summarizer ``project_key`` is registry-driven.

The summarizer maps each run record to a ``project_key`` (used for
grouping under ``memory/modeling-memory/projects/<key>/``). The current
implementation hardcodes ``tod-creek`` / ``tecnopolo``. After PRD #118
the mapping is keyed off ``cases/<id>/case_meta.yaml`` instead, with
optional ``aliases:`` accepted via the registry's ``extra`` field.

We import the script module by file path (it lives outside the Python
package), then verify that records referencing a registered case_id
or alias are labelled correctly.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

# ``sys`` is used by ``_load_summarizer`` below to register the script
# under a synthetic module name.


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "skills" / "swmm-modeling-memory" / "scripts" / "summarize_memory.py"


def _load_summarizer():
    """Import the standalone summarizer script as a module."""
    spec = importlib.util.spec_from_file_location("aiswmm_summarize_memory", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _FakeMeta:
    case_id: str
    display_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class RegistryDrivenProjectKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_summarizer()

    def test_registered_case_id_wins(self) -> None:
        # The case registry only accepts slugs matching CASE_ID_PATTERN
        # (lowercase + hyphen, no underscores). A record whose case_name
        # contains the slug should be labelled with that slug.
        with mock.patch(
            "agentic_swmm.case.case_registry.list_cases",
            return_value=[_FakeMeta(case_id="foo-creek")],
        ):
            key = self.mod.project_key(
                {"case_name": "foo-creek run", "workflow_mode": "test", "run_dir": ""}
            )
        self.assertEqual(key, "foo-creek")

    def test_alias_in_registry_extra_is_recognised(self) -> None:
        with mock.patch(
            "agentic_swmm.case.case_registry.list_cases",
            return_value=[
                _FakeMeta(
                    case_id="mini-watershed",
                    display_name="Mini Watershed",
                    extra={"aliases": ["mini_ws"]},
                )
            ],
        ):
            key = self.mod.project_key(
                {"case_name": "the mini_ws scenario", "workflow_mode": "", "run_dir": ""}
            )
        self.assertEqual(key, "mini-watershed")

    def test_unmapped_text_falls_back_to_safe_slug(self) -> None:
        with mock.patch(
            "agentic_swmm.case.case_registry.list_cases",
            return_value=[],
        ):
            key = self.mod.project_key(
                {"case_name": "Mystery Case", "workflow_mode": "", "run_dir": ""}
            )
        # safe_slug lowercases and dasherises.
        self.assertEqual(key, "mystery-case")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
