"""Unit tests for ``agentic_swmm.audit.llm_calls.record_llm_call``.

PRD-LLM-TRACE makes ``record_llm_call`` the single write seam every
caller (planner / gap_fill / memory_reflect) funnels through. These
tests pin its public contract:

* The schema fields the PRD lists land in the JSONL line.
* The full prompt is dumped alongside the JSONL line under
  ``09_audit/llm_prompts/<call_id>.txt``.
* One call produces exactly one JSONL line and one prompt dump.
* A missing ``09_audit/`` directory is auto-created — callers should
  not have to pre-seed the layout.
* A filesystem error inside the observer is fail-soft: stderr gets a
  ``LLM_TRACE_DROPPED:<call_id>`` line and ``record_llm_call`` returns
  the call_id without raising.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from agentic_swmm.audit.llm_calls import extract_usage_tokens, record_llm_call


# Schema fields the PRD's JSONL contract requires every entry to carry.
REQUIRED_FIELDS = {
    "call_id",
    "timestamp_utc",
    "caller",
    "model_role",
    "model_alias",
    "model_version",
    "prompt_summary",
    "prompt_full_ref",
    "response_text",
    "tool_calls_emitted",
    "tokens_input",
    "tokens_output",
    "duration_ms",
    "derived_decision_ref",
}


class _FakeToolCall:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeResponse:
    """Minimal stand-in for ``ProviderToolResponse``."""

    def __init__(
        self,
        *,
        text: str = "",
        model: str = "claude-opus-4-7",
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.text = text
        self.model = model
        self.tool_calls = tool_calls or []


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


class RecordLLMCallTests(unittest.TestCase):
    def test_happy_path_writes_jsonl_and_prompt_dump(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            call_id = record_llm_call(
                run_dir=run_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt=(
                    "system instructions here",
                    [{"role": "user", "content": "Goal: build a SWMM model"}],
                ),
                response=_FakeResponse(
                    text="I will call build_swmm_inp next.",
                    model="claude-opus-4-7",
                    tool_calls=[_FakeToolCall("build_swmm_inp")],
                ),
                model_version="claude-opus-4-7-20260420",
                tokens_in=1234,
                tokens_out=567,
                duration_ms=4321,
            )

            jsonl_path = run_dir / "09_audit" / "llm_calls.jsonl"
            prompt_dump = run_dir / "09_audit" / "llm_prompts" / f"{call_id}.txt"

            self.assertTrue(jsonl_path.is_file(), "JSONL file was not created")
            self.assertTrue(prompt_dump.is_file(), "prompt dump was not created")

            entries = _read_jsonl(jsonl_path)
            self.assertEqual(len(entries), 1, "exactly one JSONL line per call")
            entry = entries[0]

            # Schema completeness.
            self.assertEqual(
                set(entry.keys()),
                REQUIRED_FIELDS,
                f"missing fields: {REQUIRED_FIELDS - set(entry.keys())} / "
                f"extra: {set(entry.keys()) - REQUIRED_FIELDS}",
            )

            # Field values.
            self.assertEqual(entry["call_id"], call_id)
            self.assertEqual(entry["caller"], "planner")
            self.assertEqual(entry["model_role"], "decide_next_tool")
            self.assertEqual(entry["model_alias"], "claude-opus-4-7")
            self.assertEqual(entry["model_version"], "claude-opus-4-7-20260420")
            self.assertEqual(entry["tool_calls_emitted"], ["build_swmm_inp"])
            self.assertEqual(entry["tokens_input"], 1234)
            self.assertEqual(entry["tokens_output"], 567)
            self.assertEqual(entry["duration_ms"], 4321)
            self.assertIsNone(entry["derived_decision_ref"])
            self.assertEqual(
                entry["prompt_full_ref"],
                f"09_audit/llm_prompts/{call_id}.txt",
            )
            self.assertIn("system instructions here", entry["prompt_summary"])
            self.assertIn("build_swmm_inp", entry["response_text"])

            # Timestamp shape: ISO-8601 UTC with trailing Z.
            self.assertRegex(
                entry["timestamp_utc"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )

            # Prompt dump preserves both halves.
            dump_text = prompt_dump.read_text(encoding="utf-8")
            self.assertIn("system instructions here", dump_text)
            self.assertIn("Goal: build a SWMM model", dump_text)

    def test_auto_creates_audit_dir(self) -> None:
        """Missing ``09_audit/`` must not crash the observer."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            # Pre-condition: the dir does not exist.
            self.assertFalse((run_dir / "09_audit").exists())
            call_id = record_llm_call(
                run_dir=run_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt="hello",
                response=_FakeResponse(text="hi"),
            )
            self.assertTrue((run_dir / "09_audit").is_dir())
            self.assertTrue(
                (run_dir / "09_audit" / "llm_prompts" / f"{call_id}.txt").is_file()
            )

    def test_one_call_one_jsonl_line_one_prompt_dump(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            ids = []
            for i in range(3):
                ids.append(
                    record_llm_call(
                        run_dir=run_dir,
                        caller="planner",
                        model_role="decide_next_tool",
                        prompt=f"prompt-{i}",
                        response=_FakeResponse(text=f"resp-{i}"),
                    )
                )

            # Three call_ids → three JSONL lines → three prompt dumps.
            self.assertEqual(len(set(ids)), 3, "call_ids should be unique")
            entries = _read_jsonl(run_dir / "09_audit" / "llm_calls.jsonl")
            self.assertEqual(len(entries), 3)
            self.assertEqual([e["call_id"] for e in entries], ids)

            for call_id, expected in zip(ids, ["prompt-0", "prompt-1", "prompt-2"]):
                dump = run_dir / "09_audit" / "llm_prompts" / f"{call_id}.txt"
                self.assertTrue(dump.is_file())
                self.assertIn(expected, dump.read_text(encoding="utf-8"))

    def test_filesystem_error_is_fail_soft(self) -> None:
        """Disk-full / permission errors must not propagate."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            captured = io.StringIO()
            real_stderr = sys.stderr
            sys.stderr = captured
            try:
                with mock.patch(
                    "agentic_swmm.audit.llm_calls._append_jsonl_line",
                    side_effect=OSError("disk full"),
                ):
                    call_id = record_llm_call(
                        run_dir=run_dir,
                        caller="planner",
                        model_role="decide_next_tool",
                        prompt="hello",
                        response=_FakeResponse(text="hi"),
                    )
            finally:
                sys.stderr = real_stderr

            # No raise, call_id returned, stderr carries the marker.
            self.assertTrue(call_id)
            self.assertIn("LLM_TRACE_DROPPED", captured.getvalue())
            self.assertIn(call_id, captured.getvalue())

    def test_missing_tokens_default_to_none(self) -> None:
        """Provider may not surface usage — defaults must be None, not 0."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            record_llm_call(
                run_dir=run_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt="hi",
                response=_FakeResponse(text="ok"),
            )
            entry = _read_jsonl(run_dir / "09_audit" / "llm_calls.jsonl")[0]
            self.assertIsNone(entry["tokens_input"])
            self.assertIsNone(entry["tokens_output"])
            self.assertIsNone(entry["duration_ms"])

    def test_model_version_falls_back_to_alias_when_missing(self) -> None:
        """If no dated checkpoint is available, ``model_version`` mirrors the alias."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            record_llm_call(
                run_dir=run_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt="hi",
                response=_FakeResponse(text="ok", model="claude-opus-4-7"),
                model_version=None,
            )
            entry = _read_jsonl(run_dir / "09_audit" / "llm_calls.jsonl")[0]
            self.assertEqual(entry["model_alias"], "claude-opus-4-7")
            self.assertEqual(entry["model_version"], "claude-opus-4-7")

    def test_response_text_truncated_when_long(self) -> None:
        """Long responses are truncated inline; prompt dump stays full."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            long_text = "x" * 9000
            record_llm_call(
                run_dir=run_dir,
                caller="planner",
                model_role="decide_next_tool",
                prompt="hi",
                response=_FakeResponse(text=long_text),
            )
            entry = _read_jsonl(run_dir / "09_audit" / "llm_calls.jsonl")[0]
            self.assertLessEqual(len(entry["response_text"]), 4000)

    def test_derived_decision_ref_is_preserved(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            record_llm_call(
                run_dir=run_dir,
                caller="gap_fill.proposer",
                model_role="propose_param_value",
                prompt="hi",
                response=_FakeResponse(text="ok"),
                derived_decision_ref="09_audit/gap_decisions.json#dec-1",
            )
            entry = _read_jsonl(run_dir / "09_audit" / "llm_calls.jsonl")[0]
            self.assertEqual(
                entry["derived_decision_ref"],
                "09_audit/gap_decisions.json#dec-1",
            )


class ExtractUsageTokensTests(unittest.TestCase):
    """``extract_usage_tokens`` is a helper for callers — it should
    accept the common SDK shapes without crashing on absent fields.
    """

    def test_anthropic_shape(self) -> None:
        class _Usage:
            input_tokens = 100
            output_tokens = 50

        class _Resp:
            usage = _Usage()

        self.assertEqual(extract_usage_tokens(_Resp()), (100, 50))

    def test_openai_shape(self) -> None:
        class _Usage:
            prompt_tokens = 200
            completion_tokens = 75

        class _Resp:
            usage = _Usage()

        self.assertEqual(extract_usage_tokens(_Resp()), (200, 75))

    def test_dict_raw_payload(self) -> None:
        class _Resp:
            raw = {"usage": {"input_tokens": 11, "output_tokens": 22}}

        self.assertEqual(extract_usage_tokens(_Resp()), (11, 22))

    def test_missing_usage_returns_none(self) -> None:
        class _Resp:
            pass

        self.assertEqual(extract_usage_tokens(_Resp()), (None, None))


if __name__ == "__main__":
    unittest.main()
