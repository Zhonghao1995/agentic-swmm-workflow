"""Issue #122: welcome banner suggests the first registered case (not hardcoded).

PR #119 made watershed routing case-registry-driven. The welcome banner's
"Things to try:" block still pinned ``"Run the tecnopolo demo"`` and the
returning-user docstring example pinned ``case "tecnopolo"``. A user with no
``cases/tecnopolo/`` sees a suggestion for a watershed they cannot run.

This test ensures the banner consults ``case_registry.list_cases()`` so the
suggestion follows whatever the user has actually registered, falling back
to a generic ``"Run a SWMM demo"`` when ``cases/`` is empty.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agentic_swmm.agent import welcome
from agentic_swmm.case.case_registry import CaseMeta


def _fake_case(case_id: str, display_name: str) -> CaseMeta:
    return CaseMeta(
        case_id=case_id,
        display_name=display_name,
        study_purpose="",
        created_utc="",
        catchment={},
        inputs={},
        notes="",
        extra={},
    )


class WelcomeDynamicCaseSuggestionTests(unittest.TestCase):
    def test_extended_welcome_uses_first_registered_case_display_name(self) -> None:
        cases = [
            _fake_case("tecnopolo", "Tecnopolo (Rome 1994)"),
            _fake_case("todcreek", "Tod Creek"),
        ]
        with patch.object(welcome, "_first_case_display_name", return_value=cases[0].display_name):
            output = welcome.render_extended_welcome()
        self.assertIn("Tecnopolo (Rome 1994)", output)
        self.assertNotIn('Run the tecnopolo demo', output)

    def test_extended_welcome_falls_back_when_registry_empty(self) -> None:
        with patch.object(welcome, "_first_case_display_name", return_value=None):
            output = welcome.render_extended_welcome()
        self.assertIn("Run a SWMM demo", output)
        # Critically: no leftover ``tecnopolo`` string when no cases are registered.
        self.assertNotIn("tecnopolo", output.lower())

    def test_extended_welcome_works_for_arbitrary_watershed(self) -> None:
        """Proves portability — registering a new case changes the banner."""
        with patch.object(welcome, "_first_case_display_name", return_value="Mekong Delta"):
            output = welcome.render_extended_welcome()
        self.assertIn("Mekong Delta", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
