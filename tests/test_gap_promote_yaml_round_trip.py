"""Round-trip tests for ``cases/<id>/gap_defaults.yaml`` (PRD-GF-PROMOTE).

The case-level gap-defaults file is the cross-run memory of promoted
decisions. The tests below pin the schema down so a downstream consumer
(the proposer's case-level lookup layer; ``aiswmm gap list-case-defaults``)
can rely on the shape.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from agentic_swmm.commands.expert.gap_promote import (
    CaseDefaultEntry,
    read_case_defaults,
    write_case_defaults,
)


class CaseDefaultsRoundTripTests(unittest.TestCase):
    def test_empty_returns_empty_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            payload = read_case_defaults("tod-creek", repo_root=repo)
        self.assertEqual(payload.case_id, "tod-creek")
        self.assertEqual(payload.entries, {})
        self.assertEqual(payload.schema_version, 1)

    def test_write_then_read_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            entry = CaseDefaultEntry(
                value=0.013,
                source=(
                    "promoted from runs/2026-05-12/test-run/"
                    "09_audit/gap_decisions.json#dec-abc123"
                ),
                promoted_at="2026-05-14T19:50:00Z",
                promoted_by="human (expert CLI)",
                notes="round-trip test",
            )
            path = write_case_defaults(
                "tod-creek",
                {"manning_n_imperv": entry},
                repo_root=repo,
            )
            self.assertTrue(path.is_file())
            # Read raw YAML and the structured reader; both should agree.
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 1)
            self.assertEqual(raw["case_id"], "tod-creek")
            self.assertEqual(
                raw["entries"]["manning_n_imperv"]["value"], 0.013
            )
            payload = read_case_defaults("tod-creek", repo_root=repo)
        self.assertEqual(payload.case_id, "tod-creek")
        self.assertEqual(set(payload.entries), {"manning_n_imperv"})
        stored = payload.entries["manning_n_imperv"]
        self.assertEqual(stored.value, 0.013)
        self.assertEqual(stored.notes, "round-trip test")
        self.assertEqual(stored.promoted_by, "human (expert CLI)")

    def test_append_preserves_existing_entries(self) -> None:
        """Promoting a second field must not clobber the first."""

        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            entry_a = CaseDefaultEntry(
                value=0.013,
                source="promoted from runs/.../dec-1",
                promoted_at="2026-05-14T01:00:00Z",
                promoted_by="human",
                notes=None,
            )
            entry_b = CaseDefaultEntry(
                value=0.5,
                source="promoted from runs/.../dec-2",
                promoted_at="2026-05-14T02:00:00Z",
                promoted_by="human",
                notes=None,
            )
            write_case_defaults(
                "tod-creek", {"manning_n_imperv": entry_a}, repo_root=repo
            )
            # Read, mutate, write — mimics the CLI's append flow.
            payload = read_case_defaults("tod-creek", repo_root=repo)
            new_entries = dict(payload.entries)
            new_entries["horton_min_rate"] = entry_b
            write_case_defaults(
                "tod-creek", new_entries, repo_root=repo
            )
            payload = read_case_defaults("tod-creek", repo_root=repo)
        self.assertEqual(
            set(payload.entries), {"manning_n_imperv", "horton_min_rate"}
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
