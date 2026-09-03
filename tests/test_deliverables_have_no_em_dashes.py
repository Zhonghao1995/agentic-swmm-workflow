"""Deliverables the product writes on the user's behalf carry no em dashes.

Live test 2026-09-03 (S35): the Word report of scenario S34 held 11
generator-side captions such as "Table 1 — Run identification and
generation metadata." and the run README uses the same bullets. The
house style joins with a colon.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "swmm-report" / "scripts" / "generate_report.py"
EM_DASH = "\u2014"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_report_house_style", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_table_and_figure_captions_use_a_colon() -> None:
    gen = _load_generator()
    doc = docx.Document()
    gen._add_table_caption(doc, "Run identification and generation metadata.", [0])
    gen._add_figure_caption(doc, "rain_runoff", [0])
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert texts[0].startswith("Table 1: ")
    assert texts[1] == "Figure 1: rain_runoff"
    assert not any(EM_DASH in t for t in texts)


def test_generator_source_defaults_carry_no_em_dash_in_rendered_strings() -> None:
    gen = _load_generator()
    doc = docx.Document()
    gen._add_narrative(
        doc,
        "SHA-256 digests are computed at audit time; only the first 16 hex characters are shown "
        "for readability. The full digest is available in the provenance JSON.",
    )
    assert not any(EM_DASH in p.text for p in doc.paragraphs)


def test_run_readme_bullets_use_a_colon(tmp_path: Path) -> None:
    from agentic_swmm.reporting.run_readme import render_run_readme

    run_dir = tmp_path / "024635_downtown-victoria-bc_run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
    (run_dir / "06_runner").mkdir()
    (run_dir / "06_runner" / "model.rpt").write_text("", encoding="utf-8")
    (run_dir / "09_audit").mkdir()
    (run_dir / "final_report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "_agent").mkdir()
    (run_dir / "_agent" / "agent_trace.jsonl").write_text(json.dumps({"event": "session_start"}) + "\n", encoding="utf-8")
    text = render_run_readme(run_dir, goal="Fetch and run", status="PASS")
    assert EM_DASH not in text
    assert "`05_builder/`: " in text or "`05_builder/`:" in text
