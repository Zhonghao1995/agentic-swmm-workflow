"""PRD-08 A.3 CLI wiring tests for ``cite`` / ``cite-param``."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agentic_swmm.cli import build_parser


_BENCHMARKS_YAML = """\
manning_n_overland:
  asphalt:
    min: 0.011
    max: 0.025
    typical: 0.013
    citation: huber_dickinson_1988
  grass_short:
    min: null
    max: null
    typical: null
    citation: null
"""

_CITATIONS_YAML = """\
huber_dickinson_1988:
  authors: Huber & Dickinson
  year: 1988
  title: Storm Water Management Model User's Manual
  work: SWMM v4 reference
  locator: Table 4.5
"""


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


def _write_fixtures(dir_: Path) -> tuple[Path, Path]:
    b = dir_ / "benchmarks.yaml"
    c = dir_ / "citations.yaml"
    b.write_text(_BENCHMARKS_YAML, encoding="utf-8")
    c.write_text(_CITATIONS_YAML, encoding="utf-8")
    return b, c


# ----- cite -----


def test_cite_text_error_to_stderr():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "citations.yaml"
        path.write_text(_CITATIONS_YAML, encoding="utf-8")
        rc, out, err = _dispatch(
            ["cite", "bogus_key", "--citations-path", str(path)]
        )
    assert rc == 1
    assert "bogus_key" in err
    assert out == ""


def test_cite_json_error_to_stdout():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "citations.yaml"
        path.write_text(_CITATIONS_YAML, encoding="utf-8")
        rc, out, err = _dispatch(
            ["cite", "--json", "bogus_key", "--citations-path", str(path)]
        )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["reason"] == "citation_not_found"
    assert err == ""


# ----- cite-param: typo -> fuzzy suggestion -----


def test_cite_param_typo_includes_did_you_mean():
    with TemporaryDirectory() as tmp:
        b, c = _write_fixtures(Path(tmp))
        rc, out, err = _dispatch(
            [
                "cite-param",
                "--name",
                "maning_n_overland.asphalt",
                "--value",
                "0.013",
                "--benchmarks-path",
                str(b),
                "--citations-path",
                str(c),
            ]
        )
    assert rc == 1
    assert "did you mean" in err
    assert "manning_n_overland.asphalt" in err
    assert out == ""


def test_cite_param_leaf_null_says_uncurated():
    with TemporaryDirectory() as tmp:
        b, c = _write_fixtures(Path(tmp))
        rc, out, err = _dispatch(
            [
                "cite-param",
                "--name",
                "manning_n_overland.grass_short",
                "--value",
                "0.04",
                "--benchmarks-path",
                str(b),
                "--citations-path",
                str(c),
            ]
        )
    assert rc == 1
    assert "un-curated" in err


def test_cite_param_json_includes_hint_field():
    with TemporaryDirectory() as tmp:
        b, c = _write_fixtures(Path(tmp))
        rc, out, err = _dispatch(
            [
                "cite-param",
                "--json",
                "--name",
                "maning_n_overland.asphalt",
                "--value",
                "0.013",
                "--benchmarks-path",
                str(b),
                "--citations-path",
                str(c),
            ]
        )
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "hint" in payload
    assert payload["hint"]
    # Stderr is empty in JSON mode — payload carries the diagnostic.
    assert err == ""
