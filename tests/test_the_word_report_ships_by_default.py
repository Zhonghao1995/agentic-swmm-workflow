"""F-155 (2026-09-05): the Word report is the headline deliverable; it ships by default.

Live test S59 r2 on the 0.9.4 wheel: fetch, run and audit passed on a plain
``pip install aiswmm`` and the report step failed because python-docx was an
opt-in extra the README never mentioned. The dependency is core now; the
``report`` extra stays as an alias so the documented command keeps working.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib")
    return tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))


def test_python_docx_is_a_core_dependency() -> None:
    deps = _pyproject()["project"]["dependencies"]
    assert any(d.lower().startswith("python-docx") for d in deps)


def test_the_report_extra_stays_as_an_alias() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "report" in extras
    assert any(d.lower().startswith("python-docx") for d in extras["report"])


def test_no_remedy_sends_the_user_to_the_extra() -> None:
    root = Path(__file__).resolve().parent.parent
    for rel in ("agentic_swmm/commands/doctor.py", "agentic_swmm/agent/tool_handlers/swmm_report.py", "skills/swmm-report/scripts/generate_report.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "pip install aiswmm[report]" not in text and "pip install 'aiswmm[report]'" not in text, rel
