"""SWMMCanada was advertised on the front page and off for everyone.

`fetch_swmm_from_canada` reads AISWMM_SWMMCANADA_URL, nothing set it, and
nothing offered to. A user who asked for a Canadian network got a
configuration error, and the planner relaying it repeated the address the old
hint contained:

    set the SWMMCanada service URL via the AISWMM_SWMMCANADA_URL environment
    variable (a local container at http://localhost:8000 or a hosted backend)

That localhost address was not invented by the model. It was read out of this
repository and handed to a user as a thing to configure.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_canada import HOSTED_SERVICE_URL, _stage_hint
from agentic_swmm.commands import upstream_optin


class HintNamesTheServiceTests(unittest.TestCase):
    def test_the_missing_url_hint_names_the_real_deployment(self) -> None:
        hint = _stage_hint("config_missing")
        self.assertIn(HOSTED_SERVICE_URL, hint)

    def test_it_no_longer_offers_a_localhost_address(self) -> None:
        # The single line that produced the wrong advice.
        self.assertNotIn("localhost", _stage_hint("config_missing"))

    def test_it_says_the_area_leaves_the_machine(self) -> None:
        # Enabling this sends the requested area to a service. Say so where
        # the user is being told to enable it.
        self.assertIn("sends the area", _stage_hint("config_missing"))

    def test_doctor_names_it_too(self) -> None:
        from agentic_swmm.commands.doctor import _swmmcanada_upstream_check

        name, ok, detail, _ = _swmmcanada_upstream_check()
        if "not configured" in detail:
            self.assertIn(HOSTED_SERVICE_URL, detail)


class OptInTests(unittest.TestCase):
    def _run(self, answer: str, env=None):
        printed: list[str] = []
        written: list[str] = []

        def write(url: str) -> Path:
            written.append(url)
            return Path("/tmp/env")

        result = upstream_optin.offer(
            ask=lambda _p: answer,
            print_fn=printed.append,
            env={} if env is None else env,
            write=write,
        )
        return result, printed, written

    def test_the_default_is_off(self) -> None:
        # Bare Enter must not enable a network service.
        result, _printed, written = self._run("")
        self.assertIsNone(result)
        self.assertEqual(written, [])

    def test_an_explicit_yes_enables_it(self) -> None:
        result, _printed, written = self._run("y")
        self.assertEqual(result, HOSTED_SERVICE_URL)
        self.assertEqual(written, [HOSTED_SERVICE_URL])

    def test_declining_leaves_the_command_behind(self) -> None:
        _result, printed, _written = self._run("n")
        self.assertIn(upstream_optin.ENV_VAR, "\n".join(printed))

    def test_the_prompt_states_where_the_data_goes(self) -> None:
        _result, printed, _written = self._run("n")
        body = "\n".join(printed)
        self.assertIn("is sent to", body)
        self.assertIn(HOSTED_SERVICE_URL, body)

    def test_an_already_configured_install_is_never_asked(self) -> None:
        asked: list[str] = []

        def ask(prompt: str) -> str:
            asked.append(prompt)
            return "y"

        result = upstream_optin.offer(
            ask=ask, print_fn=lambda _m: None, env={upstream_optin.ENV_VAR: "https://mine"}
        )
        self.assertIsNone(result)
        self.assertEqual(asked, [], "re-asking would override a deliberate choice")

    def test_a_write_failure_is_reported_not_swallowed(self) -> None:
        printed: list[str] = []

        def boom(_url: str) -> Path:
            raise OSError("read-only")

        result = upstream_optin.offer(
            ask=lambda _p: "y", print_fn=printed.append, env={}, write=boom
        )
        self.assertIsNone(result)
        self.assertIn(upstream_optin.ENV_VAR, "\n".join(printed))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
