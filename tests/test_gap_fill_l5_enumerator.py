"""Tests for ``agentic_swmm.gap_fill.llm_enumerator`` (PRD-GF-L5).

The enumerator is the LLM half of the L5 subjective-judgement path. It
must:

1. Ask the LLM to enumerate N candidates with each one's hydrological
   tradeoff cited.
2. Hard-constrain the prompt with a "do not recommend" instruction —
   the enumerator's job is to *present*, not to choose.
3. Record the call through :func:`record_llm_call` with
   ``caller="gap_fill.enumerator"`` so the L5 decision can cross-link
   back to the prompt dump in ``09_audit/llm_prompts/``.
4. Parse the LLM's structured JSON output into ``GapCandidate``
   dataclasses (id / summary / tradeoff).

These tests stub out the LLM provider so the contract is exercised
without making a real API call.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class _FakeProviderResponse:
    """Minimal stand-in for the provider's response object.

    The real provider returns dataclass / SDK objects with ``.text``,
    ``.tool_calls``, ``.model``. ``record_llm_call`` and the
    enumerator's parser both fall back gracefully when those are
    missing, but supplying them here keeps the test exercising the
    same path the production response uses.
    """

    def __init__(self, text: str, model: str = "test-model") -> None:
        self.text = text
        self.model = model
        self.tool_calls: list = []


def _make_provider(text: str) -> mock.MagicMock:
    """Build a mocked provider whose ``respond_with_tools`` returns ``text``."""
    provider = mock.MagicMock()
    provider.respond_with_tools.return_value = _FakeProviderResponse(text)
    return provider


_VALID_ENUMERATOR_PAYLOAD = json.dumps(
    {
        "candidates": [
            {
                "id": "cand_1",
                "summary": "Cell (123, 456)",
                "tradeoff": "highest flow accumulation; slope-direction noisy.",
            },
            {
                "id": "cand_2",
                "summary": "Cell (124, 456)",
                "tradeoff": "matches DEM ridge; lower flow accumulation.",
            },
            {
                "id": "cand_3",
                "summary": "Cell (123, 457)",
                "tradeoff": "noise candidate; useful as a control.",
            },
        ]
    }
)


class EnumeratorContractTests(unittest.TestCase):
    """Public contract: parsed candidates + recorded LLM call."""

    def test_returns_three_candidates_with_tradeoffs(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import enumerate_candidates

        provider = _make_provider(_VALID_ENUMERATOR_PAYLOAD)
        with TemporaryDirectory() as tmp:
            candidates, call_id = enumerate_candidates(
                gap_kind="pour_point",
                context={"workflow": "swmm-gis", "step": "qa"},
                evidence_ref="06_qa/pour_point_qa.json",
                n_candidates=3,
                llm_provider=provider,
                run_dir=Path(tmp),
            )

        self.assertEqual(len(candidates), 3)
        self.assertEqual([c.id for c in candidates], ["cand_1", "cand_2", "cand_3"])
        for cand in candidates:
            self.assertTrue(cand.summary, "summary must not be empty")
            self.assertTrue(cand.tradeoff, "tradeoff must not be empty")
        self.assertTrue(isinstance(call_id, str) and call_id)

    def test_records_llm_call_via_record_llm_call(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import enumerate_candidates

        provider = _make_provider(_VALID_ENUMERATOR_PAYLOAD)
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _, call_id = enumerate_candidates(
                gap_kind="storm_event_selection",
                context={"workflow": "calibrate", "step": "pick_event"},
                evidence_ref="06_qa/rainfall_event_summary.json",
                n_candidates=3,
                llm_provider=provider,
                run_dir=run_dir,
            )

            jsonl = run_dir / "09_audit" / "llm_calls.jsonl"
            self.assertTrue(jsonl.is_file(), "llm_calls.jsonl must be written")
            lines = [
                json.loads(line)
                for line in jsonl.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            entry = lines[0]
            self.assertEqual(entry["caller"], "gap_fill.enumerator")
            self.assertEqual(entry["model_role"], "enumerate_options")
            self.assertEqual(entry["call_id"], call_id)


class EnumeratorPromptTests(unittest.TestCase):
    """The 'do not recommend' constraint is a hard prompt requirement."""

    def test_prompt_contains_do_not_recommend(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import enumerate_candidates

        provider = _make_provider(_VALID_ENUMERATOR_PAYLOAD)
        with TemporaryDirectory() as tmp:
            enumerate_candidates(
                gap_kind="pour_point",
                context={"workflow": "swmm-gis", "step": "qa"},
                evidence_ref="06_qa/qa.json",
                n_candidates=3,
                llm_provider=provider,
                run_dir=Path(tmp),
            )

        provider.respond_with_tools.assert_called_once()
        kwargs = provider.respond_with_tools.call_args.kwargs
        # The provider seam takes ``system_prompt`` plus ``input_items``;
        # the substring must appear in one or the other.
        prompt_blob = (kwargs.get("system_prompt") or "") + "\n" + json.dumps(
            kwargs.get("input_items") or []
        )
        self.assertRegex(
            prompt_blob,
            re.compile(r"do\s+not\s+recommend", re.IGNORECASE),
            "enumerator prompt must hard-constrain 'do not recommend'",
        )

    def test_prompt_mentions_tradeoff_requirement(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import enumerate_candidates

        provider = _make_provider(_VALID_ENUMERATOR_PAYLOAD)
        with TemporaryDirectory() as tmp:
            enumerate_candidates(
                gap_kind="pour_point",
                context={},
                evidence_ref="x",
                n_candidates=3,
                llm_provider=provider,
                run_dir=Path(tmp),
            )

        kwargs = provider.respond_with_tools.call_args.kwargs
        prompt_blob = (kwargs.get("system_prompt") or "") + "\n" + json.dumps(
            kwargs.get("input_items") or []
        )
        self.assertIn("tradeoff", prompt_blob.lower())


class EnumeratorParsingTests(unittest.TestCase):
    """Lenient parsing: handle JSON wrapped in prose or code fences."""

    def test_parses_json_in_fenced_code_block(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import enumerate_candidates

        fenced = (
            "Here are the candidates I have enumerated:\n\n"
            "```json\n"
            f"{_VALID_ENUMERATOR_PAYLOAD}\n"
            "```\n"
        )
        provider = _make_provider(fenced)
        with TemporaryDirectory() as tmp:
            candidates, _ = enumerate_candidates(
                gap_kind="pour_point",
                context={},
                evidence_ref="x",
                n_candidates=3,
                llm_provider=provider,
                run_dir=Path(tmp),
            )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[1].id, "cand_2")

    def test_invalid_json_raises_clean_error(self) -> None:
        from agentic_swmm.gap_fill.llm_enumerator import (
            EnumeratorParseError,
            enumerate_candidates,
        )

        provider = _make_provider("totally not JSON")
        with TemporaryDirectory() as tmp:
            with self.assertRaises(EnumeratorParseError):
                enumerate_candidates(
                    gap_kind="pour_point",
                    context={},
                    evidence_ref="x",
                    n_candidates=3,
                    llm_provider=provider,
                    run_dir=Path(tmp),
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
