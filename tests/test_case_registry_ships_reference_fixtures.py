"""Issue #122: ship at least two reference ``cases/<id>/case_meta.yaml`` fixtures.

Without these, the v0.6.2-alpha release-notes claim that "migration to a new
watershed requires only ``mkdir -p cases/<your-watershed>`` and writing a
``case_meta.yaml``" has no concrete on-disk example to copy from. PR #119 made
``cases/`` the canonical registry but left it empty (just a ``.gitkeep``). This
test asserts that the two reference fixtures (``tecnopolo`` and ``todcreek``)
ship as part of the repo so a fresh clone can verify the portability claim
without having to invent test fixtures.
"""

from __future__ import annotations

import unittest

from agentic_swmm.case import case_registry


class CaseRegistryShipsReferenceFixturesTests(unittest.TestCase):
    def test_tecnopolo_and_todcreek_are_registered_in_shipped_repo(self) -> None:
        cases = case_registry.list_cases()
        case_ids = {c.case_id for c in cases}
        self.assertIn(
            "tecnopolo",
            case_ids,
            "cases/tecnopolo/case_meta.yaml must ship as a reference fixture",
        )
        self.assertIn(
            "todcreek",
            case_ids,
            "cases/todcreek/case_meta.yaml must ship as a reference fixture",
        )

    def test_reference_fixtures_have_display_name_and_aliases(self) -> None:
        cases = {c.case_id: c for c in case_registry.list_cases()}
        tecnopolo = cases.get("tecnopolo")
        self.assertIsNotNone(tecnopolo)
        self.assertTrue(tecnopolo.display_name, "tecnopolo needs a display_name")
        # ``aliases`` is a top-level YAML key that lands directly under
        # ``meta.extra``; this matches what ``_match_registered_case``
        # in ``runtime_loop`` reads.
        aliases = (tecnopolo.extra.get("aliases") or []) if tecnopolo.extra else []
        self.assertTrue(
            aliases,
            "tecnopolo aliases must include colloquial forms so existing prompts route",
        )

        todcreek = cases.get("todcreek")
        self.assertIsNotNone(todcreek)
        self.assertTrue(todcreek.display_name, "todcreek needs a display_name")
        td_aliases = (todcreek.extra.get("aliases") or []) if todcreek.extra else []
        self.assertTrue(
            td_aliases,
            "todcreek aliases must include colloquial forms (Tod Creek, tod-creek)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
