"""run_allowed_command must not become an arbitrary-code-execution path (review P1-2).

The name-only allowlist let ``pytest <any-file>``, ``pytest -p <plugin>`` and
``node scripts/../x.mjs`` through. These tests pin the tightened contract:
positional pytest targets must resolve inside the repo, plugin/config-injection
flags are refused, and the node script must resolve to something under
``scripts/`` (so a ``..`` cannot escape the intended sandbox).
"""

from __future__ import annotations

import pytest

from agentic_swmm.agent.tool_registry import _command_allowed


@pytest.mark.parametrize(
    "command",
    [
        ["pytest", "/tmp/attacker_controlled.py"],
        ["pytest", "-p", "attacker_plugin"],
        ["pytest", "--override-ini=addopts=-p attacker"],
        ["python", "-m", "pytest", "/etc/passwd"],
        ["node", "scripts/../attacker.mjs"],
        ["node", "attacker.mjs"],
        ["cmd", "/c", "dir"],
        ["bash", "-c", "rm -rf /"],
    ],
)
def test_disallowed_commands_are_rejected(command: list[str]) -> None:
    assert _command_allowed(command) is False


@pytest.mark.parametrize(
    "command",
    [
        ["pytest"],
        ["pytest", "tests/test_command_allowlist_hardening.py"],
        ["pytest", "-q", "-k", "smoke", "tests/test_x.py::TestY::test_z"],
        ["python", "-m", "pytest", "tests/"],
        ["python", "-m", "agentic_swmm.cli", "doctor"],
        ["node", "scripts/run_mcp_server.mjs", "swmm-runner"],
        ["swmm5", "model.inp", "model.rpt", "model.out"],
    ],
)
def test_allowed_commands_pass(command: list[str]) -> None:
    assert _command_allowed(command) is True


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
