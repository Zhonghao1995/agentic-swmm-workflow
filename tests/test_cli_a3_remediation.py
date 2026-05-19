"""PRD-08 A.3 CLI wiring tests: case/transfer/storm/memory."""
from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from agentic_swmm.cli import build_parser, main as cli_main


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


# ----- case show fuzzy match -----


def test_case_show_unknown_slug_suggests_close_match(tmp_path, monkeypatch):
    """A typo like ``tod-creek`` should surface "did you mean: todcreek?"."""
    repo = tmp_path / "repo"
    (repo / "cases" / "todcreek").mkdir(parents=True)
    (repo / "cases" / "todcreek" / "case_meta.yaml").write_text(
        "case_id: todcreek\ndisplay_name: Tod Creek\nstudy_purpose: x\ncreated_utc: '2026-01-01T00:00:00Z'\ncatchment:\n  area_km2: 1\ninputs:\n  dem: null\n",
        encoding="utf-8",
    )
    with mock.patch(
        "agentic_swmm.case.case_registry.repo_root", return_value=repo
    ):
        rc, _, err = _dispatch(["case", "show", "tod-creek"])
    assert rc == 1
    assert "tod-creek" in err
    assert "did you mean: todcreek?" in err


def test_case_show_unknown_no_candidates(tmp_path):
    repo = tmp_path / "repo"
    (repo / "cases").mkdir(parents=True)
    with mock.patch(
        "agentic_swmm.case.case_registry.repo_root", return_value=repo
    ):
        rc, _, err = _dispatch(["case", "show", "anything"])
    assert rc == 1
    assert "did you mean" not in err
    assert "anything" in err


# ----- transfer empty result differentiation -----


def _write_min_inp(path: Path) -> None:
    path.write_text(
        """[TITLE]
Tiny INP
[OPTIONS]
FLOW_UNITS  CFS
START_DATE 01/01/2024
END_DATE 01/01/2024
[SUBCATCHMENTS]
S1 G1 N1 1.0 50 100 0.5 0
[RAINGAGES]
G1 INTENSITY 0:05 1.0 TIMESERIES TS1
""",
        encoding="utf-8",
    )


def test_transfer_no_calibration_store(tmp_path):
    inp = tmp_path / "new.inp"
    _write_min_inp(inp)
    cal_store = tmp_path / "calibration_memory.jsonl"
    # Store does not exist on disk.
    rc, _, err = _dispatch(
        [
            "transfer",
            "--inp",
            str(inp),
            "--calibration-memory-path",
            str(cal_store),
        ]
    )
    assert rc == 0  # the verb doesn't exit non-zero on empty result
    assert "bootstrap" in err.lower()


def test_transfer_empty_calibration_store(tmp_path):
    inp = tmp_path / "new.inp"
    _write_min_inp(inp)
    cal_store = tmp_path / "calibration_memory.jsonl"
    cal_store.write_text("", encoding="utf-8")
    rc, _, err = _dispatch(
        [
            "transfer",
            "--inp",
            str(inp),
            "--calibration-memory-path",
            str(cal_store),
        ]
    )
    assert rc == 0
    assert "calibrate at least one" in err


# ----- storm library lookup differentiation -----


def test_storm_library_missing(tmp_path):
    lib = tmp_path / "storm_library.yaml"
    # Don't create the file.
    rc, _, err = _dispatch(
        [
            "storm",
            "--storm-library-entry",
            "rome_100yr",
            "--storm-library-path",
            str(lib),
        ]
    )
    assert rc == 1
    assert "bootstrap memory" in err
    assert "does not exist" in err


def test_storm_library_entry_missing(tmp_path):
    lib = tmp_path / "storm_library.yaml"
    lib.write_text(
        "chicago_hyetographs:\n  alpha:\n    idf_params: {a: 1, b: 1, c: 0.8}\n    peak_position: 0.5\n    duration_min: 60\n",
        encoding="utf-8",
    )
    rc, _, err = _dispatch(
        [
            "storm",
            "--storm-library-entry",
            "bogus",
            "--storm-library-path",
            str(lib),
        ]
    )
    assert rc == 1
    assert "available keys" in err
    assert "alpha" in err


def test_storm_library_entry_placeholder(tmp_path):
    lib = tmp_path / "storm_library.yaml"
    lib.write_text(
        "chicago_hyetographs:\n  rome_100yr:\n    idf_params: null\n    peak_position: null\n    duration_min: null\n",
        encoding="utf-8",
    )
    rc, _, err = _dispatch(
        [
            "storm",
            "--storm-library-entry",
            "rome_100yr",
            "--storm-library-path",
            str(lib),
        ]
    )
    assert rc == 1
    assert "placeholder" in err.lower()
    assert "idf_params" in err


# ----- memory promote-facts empty staging -----


def test_memory_promote_facts_empty_emits_hint(tmp_path, monkeypatch):
    facts_dir = tmp_path / "facts"
    facts_dir.mkdir()
    (facts_dir / "facts_staging.md").write_text("", encoding="utf-8")
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(facts_dir))
    rc, _, err = _dispatch(["memory", "promote-facts"])
    assert rc == 0  # not a hard failure
    assert "record_fact" in err
