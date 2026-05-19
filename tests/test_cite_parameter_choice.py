"""Tests for ``cite_parameter_choice`` reverse-lookup (PRD-06 §2.2 — Round 2).

Contract:
- ``cite_parameter_choice`` walks ``reference_benchmarks.yaml`` for the
  dotted parameter name, validates the leaf has numeric ``min/max`` +
  a ``citation`` key, then loads the citation entry.
- A value inside ``[min, max]`` returns ``in_range=True``; outside is
  ``False``.
- Missing parameter / null range / null citation key all return
  ``None`` (never raise).
- A citation key that does not resolve in ``citations.yaml`` still
  yields a ``ParameterCitation`` with ``citation_full=None``.
- The ``aiswmm cite-param`` CLI surface dispatches both default and
  ``--json`` outputs.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser
from agentic_swmm.memory.citations import (
    ParameterCitation,
    cite_parameter_choice,
)


_BENCHMARKS_YAML = """\
schema_version: "1.0"

manning_n_overland:
  asphalt:
    min: 0.011
    typical: 0.013
    max: 0.015
    citation: huber_dickinson_1988
  grass_short:
    min: 0.10
    typical: 0.20
    max: 0.30
    citation: missing_citation_token
  no_range_leaf:
    min: null
    typical: null
    max: null
    citation: null
  no_citation_leaf:
    min: 0.1
    typical: 0.2
    max: 0.3
    citation: null
"""


_CITATIONS_YAML = """\
schema_version: "1.0"

huber_dickinson_1988:
  authors: "<author-list-pending-verification>"
  year: 1988
  title: "<title-pending-verification>"
  work: "<container-pending-verification>"
  locator: "<locator-pending-verification>"
  url: ""
  verified_by: ""
  verified_on: ""
"""


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


class CiteParameterChoiceTests(unittest.TestCase):
    def _write_fixtures(self, dir_: Path) -> tuple[Path, Path]:
        b = dir_ / "benchmarks.yaml"
        c = dir_ / "citations.yaml"
        b.write_text(_BENCHMARKS_YAML, encoding="utf-8")
        c.write_text(_CITATIONS_YAML, encoding="utf-8")
        return b, c

    def test_in_range_hit_returns_parameter_citation(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.013,
                benchmarks_path=b,
                citations_path=c,
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIsInstance(result, ParameterCitation)
            self.assertEqual(result.parameter_name, "manning_n_overland.asphalt")
            self.assertTrue(result.in_range)
            self.assertEqual(result.range_min, 0.011)
            self.assertEqual(result.range_max, 0.015)
            self.assertEqual(result.range_typical, 0.013)
            self.assertEqual(result.citation_key, "huber_dickinson_1988")
            self.assertIsNotNone(result.citation_full)

    def test_value_outside_range_marks_out_of_range(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.1,  # way above max=0.015
                benchmarks_path=b,
                citations_path=c,
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result.in_range)

    def test_value_below_range_marks_out_of_range(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.001,  # below min=0.011
                benchmarks_path=b,
                citations_path=c,
            )
            assert result is not None
            self.assertFalse(result.in_range)

    def test_boundary_value_at_min_is_in_range(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.011,
                benchmarks_path=b,
                citations_path=c,
            )
            assert result is not None
            self.assertTrue(result.in_range)

    def test_boundary_value_at_max_is_in_range(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.015,
                benchmarks_path=b,
                citations_path=c,
            )
            assert result is not None
            self.assertTrue(result.in_range)

    def test_unknown_parameter_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            self.assertIsNone(
                cite_parameter_choice(
                    parameter_name="manning_n_overland.tundra",
                    value=0.05,
                    benchmarks_path=b,
                    citations_path=c,
                )
            )

    def test_partial_path_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            # Stops at a dict, not a leaf with min/max.
            self.assertIsNone(
                cite_parameter_choice(
                    parameter_name="manning_n_overland",
                    value=0.013,
                    benchmarks_path=b,
                    citations_path=c,
                )
            )

    def test_null_range_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            self.assertIsNone(
                cite_parameter_choice(
                    parameter_name="manning_n_overland.no_range_leaf",
                    value=0.013,
                    benchmarks_path=b,
                    citations_path=c,
                )
            )

    def test_null_citation_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            self.assertIsNone(
                cite_parameter_choice(
                    parameter_name="manning_n_overland.no_citation_leaf",
                    value=0.2,
                    benchmarks_path=b,
                    citations_path=c,
                )
            )

    def test_missing_citation_entry_returns_partial(self) -> None:
        """When the leaf names a citation_key that is not in
        citations.yaml, the result is still returned but with
        ``citation_full=None`` — the renderer surfaces the gap."""
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.grass_short",
                value=0.2,
                benchmarks_path=b,
                citations_path=c,
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.citation_key, "missing_citation_token")
            self.assertIsNone(result.citation_full)
            self.assertTrue(result.in_range)

    def test_empty_parameter_name_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            self.assertIsNone(
                cite_parameter_choice(
                    parameter_name="",
                    value=0.013,
                    benchmarks_path=b,
                    citations_path=c,
                )
            )

    def test_missing_benchmarks_file_returns_none(self) -> None:
        self.assertIsNone(
            cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.013,
                benchmarks_path=Path("/nope/b.yaml"),
                citations_path=Path("/nope/c.yaml"),
            )
        )

    def test_to_dict_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            result = cite_parameter_choice(
                parameter_name="manning_n_overland.asphalt",
                value=0.013,
                benchmarks_path=b,
                citations_path=c,
            )
            assert result is not None
            payload = result.to_dict()
            self.assertEqual(payload["parameter_name"], "manning_n_overland.asphalt")
            self.assertTrue(payload["in_range"])
            self.assertIn("citation_full", payload)
            self.assertIsInstance(payload["citation_full"], dict)


class CiteParamCliTests(unittest.TestCase):
    def _write_fixtures(self, dir_: Path) -> tuple[Path, Path]:
        b = dir_ / "benchmarks.yaml"
        c = dir_ / "citations.yaml"
        b.write_text(_BENCHMARKS_YAML, encoding="utf-8")
        c.write_text(_CITATIONS_YAML, encoding="utf-8")
        return b, c

    def test_cite_param_subcommand_registers(self) -> None:
        parser = build_parser()
        names: set[str] = set()
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:  # type: ignore[attr-defined]
                names.update(action.choices.keys())  # type: ignore[attr-defined]
        self.assertIn("cite-param", names)

    def test_cite_param_in_range_default_output(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            rc, out, _ = _dispatch(
                [
                    "cite-param",
                    "--name",
                    "manning_n_overland.asphalt",
                    "--value",
                    "0.013",
                    "--benchmarks-path",
                    str(b),
                    "--citations-path",
                    str(c),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("IN range", out)
            self.assertIn("citation:", out)

    def test_cite_param_out_of_range_default_output(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            rc, out, _ = _dispatch(
                [
                    "cite-param",
                    "--name",
                    "manning_n_overland.asphalt",
                    "--value",
                    "0.5",
                    "--benchmarks-path",
                    str(b),
                    "--citations-path",
                    str(c),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("OUT OF range", out)

    def test_cite_param_json_output(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            rc, out, _ = _dispatch(
                [
                    "cite-param",
                    "--name",
                    "manning_n_overland.asphalt",
                    "--value",
                    "0.013",
                    "--benchmarks-path",
                    str(b),
                    "--citations-path",
                    str(c),
                    "--json",
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["parameter_name"], "manning_n_overland.asphalt")
            self.assertTrue(payload["in_range"])

    def test_cite_param_unknown_returns_nonzero(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            rc, out, _ = _dispatch(
                [
                    "cite-param",
                    "--name",
                    "no_such.parameter",
                    "--value",
                    "0.5",
                    "--benchmarks-path",
                    str(b),
                    "--citations-path",
                    str(c),
                ]
            )
            self.assertEqual(rc, 1)
            self.assertIn("no_such.parameter", out)

    def test_cite_param_missing_citation_partial_renders(self) -> None:
        with TemporaryDirectory() as tmp:
            b, c = self._write_fixtures(Path(tmp))
            rc, out, _ = _dispatch(
                [
                    "cite-param",
                    "--name",
                    "manning_n_overland.grass_short",
                    "--value",
                    "0.2",
                    "--benchmarks-path",
                    str(b),
                    "--citations-path",
                    str(c),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("missing_citation_token", out)
            self.assertIn("missing", out.lower())


if __name__ == "__main__":
    unittest.main()
