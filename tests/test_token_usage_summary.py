"""Phase G — per-session LLM token total in the final summary.

aiswmm is billed per token, but "how much did this session spend" had no
answer at the user surface — the maintainer's own evidence docs hand-summed
``llm_calls.jsonl``. ``aggregate_token_usage`` sums the per-call token counts
the LLM observer already records, and ``render_final_summary`` surfaces one
deterministic line.

The line only appears when real token data exists, so sessions/tests with no
``llm_calls.jsonl`` (and the existing chat-only / empty cases) are untouched.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.audit.llm_calls import aggregate_token_usage
from agentic_swmm.agent.digest_render import render_final_summary


def _write_calls(run_dir: Path, rows: list[dict]) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    with (audit / "llm_calls.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


_MANIFEST = {
    "metrics": {
        "peak": {"node": "O1", "peak": 1.23, "time_hhmm": "01:30"},
        "continuity": {"flow_routing": {"Continuity Error (%)": 0.1}},
    },
    "return_code": 0,
}


class AggregateTokenUsageTests(unittest.TestCase):
    def test_sums_input_and_output_across_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_calls(run, [
                {"tokens_input": 100, "tokens_output": 40},
                {"tokens_input": 50, "tokens_output": 10},
            ])
            usage = aggregate_token_usage([run])
            self.assertEqual(usage["calls"], 2)
            self.assertEqual(usage["tokens_input"], 150)
            self.assertEqual(usage["tokens_output"], 50)
            self.assertEqual(usage["tokens_total"], 200)

    def test_counts_calls_missing_tokens_but_still_sums_known(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_calls(run, [
                {"tokens_input": 100, "tokens_output": 40},
                {"tokens_input": None, "tokens_output": None},
            ])
            usage = aggregate_token_usage([run])
            self.assertEqual(usage["calls"], 2)
            self.assertEqual(usage["calls_with_tokens"], 1)
            self.assertEqual(usage["tokens_total"], 140)

    def test_no_file_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(aggregate_token_usage([Path(tmp)]))

    def test_all_none_tokens_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_calls(run, [{"tokens_input": None, "tokens_output": None}])
            self.assertIsNone(aggregate_token_usage([run]))

    def test_aggregates_across_multiple_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir(); b.mkdir()
            _write_calls(a, [{"tokens_input": 10, "tokens_output": 5}])
            _write_calls(b, [{"tokens_input": 20, "tokens_output": 7}])
            usage = aggregate_token_usage([a, b])
            self.assertEqual(usage["tokens_total"], 42)


class FinalSummaryTokenLineTests(unittest.TestCase):
    def test_token_line_appended_when_usage_present(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "manifest.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
            _write_calls(run, [{"tokens_input": 1000, "tokens_output": 234}])
            block = render_final_summary([run])
            self.assertIn("LLM usage", block)
            self.assertIn("1,234", block)  # total, thousands-separated
            self.assertIn("Peak:", block)  # run block still present

    def test_no_token_line_when_no_calls_file(self) -> None:
        # Existing behaviour preserved: a run with manifest but no
        # llm_calls.jsonl renders exactly the run block, no token line.
        with TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "manifest.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
            block = render_final_summary([run])
            self.assertNotIn("LLM usage", block)

    def test_chat_only_with_tokens_shows_token_line(self) -> None:
        # A chat-only turn (no manifest) still spends tokens — surface them.
        with TemporaryDirectory() as tmp:
            run = Path(tmp) / "chat"
            run.mkdir()
            _write_calls(run, [{"tokens_input": 300, "tokens_output": 12}])
            block = render_final_summary([run])
            self.assertIn("LLM usage", block)

    def test_empty_run_list_still_returns_empty(self) -> None:
        self.assertEqual(render_final_summary([]), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
