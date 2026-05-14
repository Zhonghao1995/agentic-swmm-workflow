"""Proposer priority chain after PRD-GF-PROMOTE lands.

The new priority chain is:

1. case-level: ``cases/<case_id>/gap_defaults.yaml`` hit -> source
   ``registry`` with ``registry_ref`` pointing at the case file.
2. global registry: ``defaults_table.yaml`` hit -> source ``registry``
   with ``registry_ref`` pointing at ``defaults_table.yaml#<entry>``.
3. LLM-grounded -> source ``llm_grounded``.
4. human fallthrough -> source ``human``.

Each layer is exclusively activated when prior layers miss; the
case-level hit short-circuits LLM and registry lookups entirely.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from agentic_swmm.gap_fill.protocol import GapSignal
from agentic_swmm.gap_fill.proposer import LLMProposal, propose


def _l3_signal(field: str = "manning_n_imperv") -> GapSignal:
    return GapSignal(
        gap_id="gap-test",
        severity="L3",
        kind="param_value",
        field=field,
        context={"tool": "build_inp"},
    )


def _write_case_defaults(repo: Path, case_id: str, entries: dict) -> None:
    case_dir = repo / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "entries": entries,
    }
    (case_dir / "gap_defaults.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


class CaseDefaultHitTests(unittest.TestCase):
    """Layer 1: case-level hit short-circuits everything below."""

    def setUp(self) -> None:
        self._saved_repo = os.environ.get("AISWMM_REPO_ROOT")
        self._saved_defaults = os.environ.get("AISWMM_DEFAULTS_TABLE")

    def tearDown(self) -> None:
        for name, prev in (
            ("AISWMM_REPO_ROOT", self._saved_repo),
            ("AISWMM_DEFAULTS_TABLE", self._saved_defaults),
        ):
            if prev is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prev

    def test_case_default_hit_short_circuits_llm_and_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_case_defaults(
                repo,
                "tod-creek",
                {
                    "manning_n_imperv": {
                        "value": 0.012,
                        "source": "promoted from runs/.../dec-1",
                        "promoted_at": "2026-05-14T01:00:00Z",
                        "promoted_by": "human",
                    }
                },
            )
            os.environ["AISWMM_REPO_ROOT"] = str(repo)

            llm_called: list[str] = []

            def _llm(*, signal, run_dir):
                llm_called.append(signal.field)
                raise AssertionError("LLM must not be called on case-default hit")

            decision = propose(
                signal=_l3_signal("manning_n_imperv"),
                run_dir=repo / "runs" / "r1",
                llm_proposal_fn=_llm,
                case_id="tod-creek",
            )
        # The case-default layer satisfies the gap; final_value is the
        # promoted one (0.012), NOT the global registry default (0.013).
        self.assertEqual(decision.final_value, 0.012)
        self.assertEqual(decision.proposed_value, 0.012)
        self.assertEqual(decision.proposer.confidence, "HIGH")
        # registry_ref is the seam that distinguishes a case-default
        # hit from a global-registry hit. Case-defaults point inside
        # ``cases/<id>/gap_defaults.yaml``; global hits point at the
        # repo-root ``defaults_table.yaml``.
        self.assertIn(
            "cases/tod-creek/gap_defaults.yaml",
            decision.proposer.registry_ref or "",
        )
        self.assertIn(
            "manning_n_imperv",
            decision.proposer.registry_ref or "",
        )
        self.assertEqual(llm_called, [])

    def test_case_miss_falls_back_to_global_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Empty case file (or no case file at all) — must miss.
            _write_case_defaults(
                repo, "tod-creek", {"some_other_field": {"value": 1.0}}
            )
            os.environ["AISWMM_REPO_ROOT"] = str(repo)

            def _llm(*, signal, run_dir):  # pragma: no cover - guarded
                raise AssertionError("LLM must not be called on registry hit")

            decision = propose(
                signal=_l3_signal("manning_n_imperv"),
                run_dir=repo / "runs" / "r1",
                llm_proposal_fn=_llm,
                case_id="tod-creek",
            )
        # Global registry value for ``manning_n_paved`` (alias of
        # ``manning_n_imperv``) per the shipped ``defaults_table.yaml``.
        self.assertEqual(decision.proposed_value, 0.013)
        self.assertEqual(decision.proposer.source, "registry")
        self.assertIn(
            "defaults_table.yaml",
            decision.proposer.registry_ref or "",
        )

    def test_case_miss_and_registry_miss_consults_llm(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # No case file. Point the defaults table at an empty file
            # so the global registry misses too.
            empty_defaults = repo / "defaults_table.yaml"
            empty_defaults.write_text("schema_version: 1\nentries: {}\n")
            os.environ["AISWMM_REPO_ROOT"] = str(repo)
            os.environ["AISWMM_DEFAULTS_TABLE"] = str(empty_defaults)

            def _llm(*, signal, run_dir):
                return LLMProposal(
                    value=0.025,
                    literature_ref="textbook X",
                    confidence="HIGH",
                    call_id="call-99",
                )

            decision = propose(
                signal=_l3_signal("unknown_field"),
                run_dir=repo / "runs" / "r1",
                llm_proposal_fn=_llm,
                case_id="tod-creek",
            )
        self.assertEqual(decision.proposer.source, "llm_grounded")
        self.assertEqual(decision.proposed_value, 0.025)

    def test_no_case_id_skips_case_layer_cleanly(self) -> None:
        """When ``case_id=None`` the proposer goes straight to global registry.

        Existing callers pre-PRD-GF-PROMOTE do not pass ``case_id`` and
        must keep working unchanged.
        """

        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            os.environ["AISWMM_REPO_ROOT"] = str(repo)

            def _llm(*, signal, run_dir):  # pragma: no cover - guarded
                raise AssertionError("LLM must not be called on registry hit")

            decision = propose(
                signal=_l3_signal("manning_n_imperv"),
                run_dir=repo / "runs" / "r1",
                llm_proposal_fn=_llm,
                # No case_id passed — back-compat with pre-PRD callers.
            )
        self.assertEqual(decision.proposer.source, "registry")
        self.assertEqual(decision.proposed_value, 0.013)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
