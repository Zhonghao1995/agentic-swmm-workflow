"""Default permission profile must be QUICK, not SAFE.

Background: the SAFE-by-default policy added friction without security
gain — write/subprocess tools prompt regardless of profile, so SAFE only
ever bought extra prompts on read-only tools. We flip the default so the
shell auto-approves the read-only catalogue (``read_file``,
``list_skills``, ``list_mcp_*``, ``inspect_plot_options``, ...). Users
who want the old behaviour opt in with ``--safe`` (see the wiring tests
in ``test_agent_cli_safe_flag.py``).
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.permissions_profile import Profile, profile_from_string


class DefaultProfileIsQuickTests(unittest.TestCase):
    def test_none_returns_quick(self) -> None:
        self.assertIs(profile_from_string(None), Profile.QUICK)

    def test_empty_string_returns_quick(self) -> None:
        self.assertIs(profile_from_string(""), Profile.QUICK)

    def test_whitespace_string_returns_quick(self) -> None:
        # Whitespace-only strings have no requested profile, so the
        # caller gets the default. The strip() in profile_from_string
        # also has to handle this case.
        self.assertIs(profile_from_string("   "), Profile.QUICK)

    def test_safe_string_returns_safe(self) -> None:
        self.assertIs(profile_from_string("safe"), Profile.SAFE)

    def test_safe_string_is_case_insensitive(self) -> None:
        self.assertIs(profile_from_string("SAFE"), Profile.SAFE)
        self.assertIs(profile_from_string("Safe"), Profile.SAFE)

    def test_quick_string_returns_quick(self) -> None:
        self.assertIs(profile_from_string("quick"), Profile.QUICK)

    def test_unknown_string_returns_quick_default(self) -> None:
        # Unknown values fall back to the new default (QUICK). The old
        # behaviour fell back to SAFE; this test pins the new policy.
        self.assertIs(profile_from_string("garbage"), Profile.QUICK)


if __name__ == "__main__":
    unittest.main()
