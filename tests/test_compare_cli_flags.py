"""CLI smoke tests for the Round-3 ``aiswmm compare`` flag surface.

Three new flags landed in Round 3:

- ``--per-node`` expands the full per-node peak-flow table.
- ``--per-subcatch`` expands the full per-subcatch runoff table.
- ``--override-version`` forces a comparison through when the two runs
  report incompatible SWMM solver versions.

The default human-readable output now also includes "Top N nodes /
subcatches that moved most" blocks; we pin that here so a regression
that drops the block fails fast.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser
from tests.test_compare_per_element import _rpt_with_sections


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


def _write_run(parent: Path, name: str, rpt: str, version: str | None = None) -> Path:
    run_dir = parent / name
    run_dir.mkdir()
    (run_dir / "model.rpt").write_text(rpt, encoding="utf-8")
    audit = run_dir / "09_audit"
    audit.mkdir()
    payload: dict[str, object] = {"run_id": name}
    if version is not None:
        payload["swmm_version"] = version
    (audit / "experiment_provenance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return run_dir


class CompareCliFlagsTests(unittest.TestCase):
    def test_default_output_has_top_movers_block(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _rpt_with_sections())
            b = _write_run(
                base,
                "b",
                _rpt_with_sections(
                    node_inflow={
                        "J1": (1.184, 1.300, "2  13:54"),
                        "J2": (0.500, 0.800, "2  14:00"),
                        "O1": (0.000, 1.300, "2  12:47"),
                    },
                ),
            )
            rc, out, _ = _dispatch(
                ["compare", "--run-a", str(a), "--run-b", str(b)]
            )
        self.assertEqual(rc, 0)
        self.assertIn("nodes that moved most", out)
        self.assertIn("subcatches that moved most", out)

    def test_per_node_flag_expands(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _rpt_with_sections())
            b = _write_run(base, "b", _rpt_with_sections())
            rc, out, _ = _dispatch(
                [
                    "compare",
                    "--run-a",
                    str(a),
                    "--run-b",
                    str(b),
                    "--per-node",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Per-node peak inflow", out)

    def test_per_subcatch_flag_expands(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _rpt_with_sections())
            b = _write_run(base, "b", _rpt_with_sections())
            rc, out, _ = _dispatch(
                [
                    "compare",
                    "--run-a",
                    str(a),
                    "--run-b",
                    str(b),
                    "--per-subcatch",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Per-subcatch runoff", out)

    def test_json_includes_new_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _rpt_with_sections())
            b = _write_run(base, "b", _rpt_with_sections())
            rc, out, _ = _dispatch(
                [
                    "compare",
                    "--run-a",
                    str(a),
                    "--run-b",
                    str(b),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        for key in (
            "node_peak_diffs",
            "subcatch_runoff_diffs",
            "top_movers_nodes",
            "top_movers_subcatches",
        ):
            self.assertIn(key, payload)

    def test_override_version_flag_unblocks_cross_minor_compare(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _rpt_with_sections(), version="5.1.013")
            b = _write_run(base, "b", _rpt_with_sections(), version="5.2.4")
            # No override: incomparable
            rc_no, out_no, _ = _dispatch(
                ["compare", "--run-a", str(a), "--run-b", str(b)]
            )
            self.assertEqual(rc_no, 1)
            self.assertIn("incomparable", out_no)
            # With override: proceeds
            rc_ov, out_ov, _ = _dispatch(
                [
                    "compare",
                    "--run-a",
                    str(a),
                    "--run-b",
                    str(b),
                    "--override-version",
                ]
            )
        self.assertEqual(rc_ov, 0)
        self.assertNotIn("incomparable", out_ov)


if __name__ == "__main__":
    unittest.main()
