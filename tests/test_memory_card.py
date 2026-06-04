"""`aiswmm memory show <case>` plain-text card.

Now that capture + case-grouping are fixed, a case actually accumulates per-run
signal — so a human-readable card is worth rendering. These tests pin: the card
shows the modeling signal, degrades gracefully when empty, and contains NO emoji
(plain ASCII only, as the maintainer requested).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.card import render_case_card


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _para(case: str, run_id: str, runoff: float, peak: float, node: str = "OU2") -> dict:
    return {
        "schema_version": "2.0",
        "case_name": case,
        "run_id": run_id,
        "swmm_version": "5.2.4",
        "recorded_utc": "2026-05-28T05:46:57Z",
        "qa_metrics": {
            "runoff_continuity_pct": runoff,
            "flow_continuity_pct": -0.004,
            "peak_flow_value": peak,
            "peak_flow_node": node,
        },
    }


class MemoryCardTests(unittest.TestCase):
    def _card(self, tmp: Path, case: str, para=None, calib=None, neg=None) -> str:
        if para:
            _write(tmp / "parametric_memory.jsonl", para)
        if calib:
            _write(tmp / "calibration_memory.jsonl", calib)
        if neg:
            _write(tmp / "negative_lessons.jsonl", neg)
        return render_case_card(tmp, case)

    def test_shows_signal_for_a_populated_case(self) -> None:
        with TemporaryDirectory() as t:
            tmp = Path(t)
            card = self._card(tmp, "tecnopolo", para=[
                _para("tecnopolo", "run1", -0.13, 0.061),
                _para("tecnopolo", "run2", -0.15, 0.058),
            ])
            self.assertIn("Memory card: tecnopolo", card)
            self.assertIn("runs recorded: 2", card)
            self.assertIn("5.2.4", card)
            self.assertIn("runoff continuity", card)
            self.assertIn("peak flow", card)
            self.assertIn("run1", card)
            self.assertIn("OU2", card)

    def test_empty_case_degrades_gracefully(self) -> None:
        with TemporaryDirectory() as t:
            card = render_case_card(Path(t), "nope")
            self.assertIn("No memory recorded", card)
            self.assertIn("nope", card)

    def test_calibration_and_known_bad_sections(self) -> None:
        with TemporaryDirectory() as t:
            tmp = Path(t)
            card = self._card(
                tmp, "tecnopolo",
                para=[_para("tecnopolo", "run1", -0.13, 0.061)],
                calib=[{"case_name": "tecnopolo", "run_id": "c1", "objective_name": "kge", "objective_value": 0.82}],
                neg=[{"case_name": "tecnopolo", "run_id": "n1", "parameters_tried": {"slope": 0.001}, "note": "all-pipe surcharge"}],
            )
            self.assertIn("kge = 0.82", card)
            self.assertIn("slope=0.001", card)
            self.assertIn("all-pipe surcharge", card)
            self.assertNotIn("(none yet)", card)  # both sections populated

    def test_empty_subsections_say_none_yet(self) -> None:
        with TemporaryDirectory() as t:
            tmp = Path(t)
            card = self._card(tmp, "tecnopolo", para=[_para("tecnopolo", "run1", -0.13, 0.061)])
            self.assertIn("Accepted calibrations", card)
            self.assertIn("(none yet)", card)

    def test_card_is_plain_ascii_no_emoji(self) -> None:
        with TemporaryDirectory() as t:
            tmp = Path(t)
            card = self._card(tmp, "tecnopolo", para=[_para("tecnopolo", "run1", -0.13, 0.061)])
            # No emoji / non-ASCII pictographs: the whole card must be ASCII.
            card.encode("ascii")  # raises UnicodeEncodeError if any emoji slipped in


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
