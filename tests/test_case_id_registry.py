"""Tests for ``agentic_swmm.case.case_registry`` (PRD-CASE-ID).

The registry is a tiny readonly facade over ``cases/<id>/case_meta.yaml``.
list_cases on an empty repo is the most common path (every new clone
starts there); a populated cases dir reads back the metadata; a
non-existent case id fails cleanly with a typed error.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from agentic_swmm.case.case_registry import (
    CaseMeta,
    CaseMetaNotFoundError,
    list_cases,
    read_case_meta,
    write_case_meta,
)


def _write_meta(cases_dir: Path, case_id: str, *, display_name: str = "Test Case") -> Path:
    case_dir = cases_dir / case_id
    case_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "display_name": display_name,
        "study_purpose": "unit test fixture",
        "created_utc": "2026-05-14T00:00:00Z",
        "catchment": {
            "area_km2": 1.23,
            "land_use": "urban",
            "region_descriptor": "test region",
        },
        "inputs": {
            "dem": None,
            "observed_flow": None,
        },
        "notes": "fixture",
    }
    meta_path = case_dir / "case_meta.yaml"
    meta_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return meta_path


class ListCasesTests(unittest.TestCase):
    def test_empty_repo_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(list_cases(Path(tmp)), [])

    def test_missing_cases_dir_returns_empty_list(self) -> None:
        # Repo without a cases/ subdirectory must not raise.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "runs").mkdir()
            self.assertEqual(list_cases(repo), [])

    def test_populated_repo_lists_cases(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cases = repo / "cases"
            _write_meta(cases, "tod-creek", display_name="Tod Creek")
            _write_meta(cases, "saanich-east", display_name="Saanich East")
            result = list_cases(repo)
        ids = sorted(meta.case_id for meta in result)
        self.assertEqual(ids, ["saanich-east", "tod-creek"])
        for meta in result:
            self.assertIsInstance(meta, CaseMeta)

    def test_directory_without_meta_is_skipped(self) -> None:
        """A stray dir under cases/ without case_meta.yaml is not a case."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cases = repo / "cases"
            (cases / "stray").mkdir(parents=True)
            _write_meta(cases, "tod-creek")
            result = list_cases(repo)
        self.assertEqual([meta.case_id for meta in result], ["tod-creek"])


class ReadCaseMetaTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_meta(repo / "cases", "tod-creek", display_name="Tod Creek")
            meta = read_case_meta("tod-creek", repo_root=repo)
        self.assertEqual(meta.case_id, "tod-creek")
        self.assertEqual(meta.display_name, "Tod Creek")
        self.assertEqual(meta.catchment["area_km2"], 1.23)

    def test_nonexistent_case_raises_typed_error(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with self.assertRaises(CaseMetaNotFoundError) as ctx:
                read_case_meta("does-not-exist", repo_root=repo)
            # The error message must include the case_id so the caller
            # knows what the user asked for.
            self.assertIn("does-not-exist", str(ctx.exception))


class WriteCaseMetaTests(unittest.TestCase):
    def test_write_then_read(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            meta = CaseMeta(
                case_id="demo",
                display_name="Demo",
                study_purpose="throwaway",
                created_utc="2026-05-14T01:02:03Z",
                catchment={"area_km2": None, "land_use": None, "region_descriptor": None},
                inputs={"dem": None, "observed_flow": None},
                notes="",
            )
            write_case_meta(meta, repo_root=repo)
            roundtrip = read_case_meta("demo", repo_root=repo)
        self.assertEqual(roundtrip.case_id, "demo")
        self.assertEqual(roundtrip.display_name, "Demo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
