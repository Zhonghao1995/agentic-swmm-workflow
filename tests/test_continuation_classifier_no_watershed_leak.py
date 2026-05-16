"""Cycle 2 of PRD #118: no hardcoded watershed names in classifier keywords.

The continuation classifier carries keyword tuples (``_PLOT_KEYWORDS``,
``_NEW_RUN_KEYWORDS``, etc.) that decide whether a prompt is a plot
continuation or a fresh SWMM run. Those vocabularies must be language-
driven (plot/figure/build/run...) not watershed-driven; ``tecnopolo``
or ``todcreek`` appearing there is a portability leak.

We do NOT want to enumerate the registry from this test (the classifier
is intentionally pure / no-I/O); instead we assert that the two known
example-case names appear in no module-level keyword tuple of strings.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent import continuation_classifier


_KNOWN_EXAMPLE_WATERSHED_NAMES = ("tecnopolo", "todcreek", "tod creek")


class NoWatershedLeakTests(unittest.TestCase):
    def test_no_watershed_names_in_module_keyword_tuples(self) -> None:
        leaks: list[tuple[str, str]] = []
        for name in dir(continuation_classifier):
            if not name.isupper():
                continue
            value = getattr(continuation_classifier, name)
            if not isinstance(value, tuple):
                continue
            for entry in value:
                if not isinstance(entry, str):
                    continue
                lowered = entry.lower()
                for watershed in _KNOWN_EXAMPLE_WATERSHED_NAMES:
                    if watershed in lowered:
                        leaks.append((name, entry))
        self.assertEqual(
            leaks,
            [],
            f"watershed names leaked into classifier keyword tuples: {leaks}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
