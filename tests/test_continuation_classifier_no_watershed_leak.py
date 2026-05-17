"""Cycle 2 of PRD #118: no hardcoded watershed names in classifier keywords.

The agent's keyword tables (intent classification, continuation
classification, etc.) must be language-driven (plot/figure/build/run...)
not watershed-driven; ``tecnopolo`` or ``todcreek`` appearing there is
a portability leak.

PRD #121 consolidated the keyword tables into
``agentic_swmm.agent.intent_classifier``. We sweep both modules so the
guard tracks the keywords wherever they live.

We do NOT enumerate the case registry from this test (the classifiers
are intentionally pure / no-I/O); instead we assert that the two known
example-case names appear in no module-level keyword tuple of strings.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent import continuation_classifier, intent_classifier


_KNOWN_EXAMPLE_WATERSHED_NAMES = ("tecnopolo", "todcreek", "tod creek")
_MODULES_TO_SCAN = (continuation_classifier, intent_classifier)


def _collect_leaks(module: object) -> list[tuple[str, str, str]]:
    leaks: list[tuple[str, str, str]] = []
    for name in dir(module):
        # Scan both module-private (_FOO) and public (FOO) constants
        # that are tuple-of-strings keyword tables.
        upper_part = name.lstrip("_")
        if not upper_part.isupper():
            continue
        value = getattr(module, name)
        if not isinstance(value, tuple):
            continue
        for entry in value:
            if not isinstance(entry, str):
                continue
            lowered = entry.lower()
            for watershed in _KNOWN_EXAMPLE_WATERSHED_NAMES:
                if watershed in lowered:
                    leaks.append((module.__name__, name, entry))
    return leaks


class NoWatershedLeakTests(unittest.TestCase):
    def test_no_watershed_names_in_module_keyword_tuples(self) -> None:
        leaks: list[tuple[str, str, str]] = []
        for module in _MODULES_TO_SCAN:
            leaks.extend(_collect_leaks(module))
        self.assertEqual(
            leaks,
            [],
            f"watershed names leaked into classifier keyword tuples: {leaks}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
