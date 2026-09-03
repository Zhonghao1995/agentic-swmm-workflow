"""The doctor tool's one-line summary is its verdict (F-64).

Live finding 2026-09-02 (scenario S13e, unreachable SWMMCanada endpoint):
the summary was the tail of doctor's last printed line, "the service is up",
next to a fetch that had just failed on that very endpoint.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_handlers._shared import _summarize_cli_result, doctor_verdict

HEALTHY = """Install:
  OK      repo root - /repo
  OK      AISWMM_CODEX_API_KEY - set
  OK      SWMMCanada upstream - https://swmm.h2ox.me healthy
  OK      swmm5 executable - /opt/homebrew/bin/swmm5; 5.2.4

Environment overrides:
  UNSET      AISWMM_MEMORY_DIR                  - redirect memory stores
"""

BROKEN = """Install:
  OK      repo root - /repo
  FAIL    SWMMCanada upstream - https://swmm.invalid.example unreachable (gaierror); fetch_swmm_from_canada will fail until the service is up
  WARN    node executable - not found on PATH
"""


def test_healthy_doctor_counts_ok_only():
    assert doctor_verdict(HEALTHY) == "doctor: 4 OK"


def test_broken_doctor_names_the_first_problem():
    assert doctor_verdict(BROKEN) == "doctor: 1 OK, 1 WARN, 1 FAIL; first problem: SWMMCanada upstream"


def test_the_cli_summary_uses_the_verdict_for_doctor():
    summary = _summarize_cli_result("doctor", BROKEN, 0)
    assert summary.startswith("doctor: 1 OK, 1 WARN, 1 FAIL")
    assert "the service is up" not in summary


def test_a_nonzero_exit_keeps_the_verdict_visible():
    assert _summarize_cli_result("doctor", BROKEN, 2).startswith("doctor failed (exit 2); doctor: 1 OK")


def test_other_tools_keep_the_last_line():
    assert _summarize_cli_result("skill", "line one\nline two", 0) == "line two"
