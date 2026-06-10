"""Tests for memory-application provenance (P0-3).

Covers:
1. memory_provenance id helpers — calibration_memory_id, parametric_memory_id.
2. stamp_memories_applied — field written, merged, never duplicated.
3. ensure_memories_applied_present — default [] when absent.
4. swmm_runner.py cmd_run — memories_applied field always present in manifest.
5. swmm_runner.py cmd_run — no-application run records [].
6. swmm_runner.py cmd_run — explicit --memories-applied recorded verbatim.
7. TransferRecommendation.memory_id — correct cm- prefix.
8. transfer_recommendation_memory_ids helper.
9. collect_run (audit_run.py) copies memories_applied into provenance.
10. collect_run backward compat — old manifest without field yields [].
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"
AUDIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


# ── helpers ────────────────────────────────────────────────────────────────


def _load_runner_module():
    """Load swmm_runner.py as a module without executing __main__."""
    import importlib.util as ilu

    spec = ilu.spec_from_file_location("_swmm_runner", RUNNER_SCRIPT)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_audit_module():
    """Load audit_run.py as a module without executing __main__."""
    import importlib.util as ilu

    spec = ilu.spec_from_file_location("_audit_run", AUDIT_SCRIPT)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _minimal_rpt(run_dir: Path) -> Path:
    """Write a minimal SWMM .rpt that the parser handles without errors."""
    rpt = run_dir / "model.rpt"
    rpt.write_text(
        "\n".join([
            "  ***** Node Inflow Summary *****",
            "  O1    OUTFALL  0.001  1.250  2  12:47",
            "",
            "  ***** Runoff Quantity Continuity *****",
            "  Continuity Error (%) ............. 0.10",
            "",
            "  ***** Flow Routing Continuity *****",
            "  Continuity Error (%) ............. 0.05",
        ]),
        encoding="utf-8",
    )
    return rpt


# ── Section 1: memory_provenance helpers ──────────────────────────────────


class TestMemoryProvenanceIds(unittest.TestCase):
    """ID derivation functions produce the correct namespaced ids."""

    def test_calibration_memory_id_prefix(self):
        from agentic_swmm.memory.memory_provenance import calibration_memory_id

        result = calibration_memory_id("run-42")
        self.assertEqual(result, "cm-run-42")

    def test_parametric_memory_id_prefix(self):
        from agentic_swmm.memory.memory_provenance import parametric_memory_id

        result = parametric_memory_id("run-99")
        self.assertEqual(result, "pm-run-99")

    def test_different_stores_never_collide(self):
        from agentic_swmm.memory.memory_provenance import (
            calibration_memory_id,
            parametric_memory_id,
        )

        run_id = "shared-run-id"
        self.assertNotEqual(calibration_memory_id(run_id), parametric_memory_id(run_id))


# ── Section 2: stamp_memories_applied ─────────────────────────────────────


class TestStampMemoriesApplied(unittest.TestCase):
    """stamp_memories_applied writes the field and merges correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, content: dict) -> Path:
        path = self.tmp / "manifest.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_stamps_ids_into_manifest(self):
        from agentic_swmm.memory.memory_provenance import stamp_memories_applied

        path = self._write_manifest({"run_ok": True})
        stamp_memories_applied(path, ["cm-abc", "pm-xyz"])
        result = json.loads(path.read_text())
        self.assertEqual(result["memories_applied"], ["cm-abc", "pm-xyz"])

    def test_empty_list_writes_empty_field(self):
        from agentic_swmm.memory.memory_provenance import stamp_memories_applied

        path = self._write_manifest({"run_ok": True})
        stamp_memories_applied(path, [])
        result = json.loads(path.read_text())
        self.assertIn("memories_applied", result)
        self.assertEqual(result["memories_applied"], [])

    def test_merges_with_existing_ids_no_duplicates(self):
        from agentic_swmm.memory.memory_provenance import stamp_memories_applied

        path = self._write_manifest({"memories_applied": ["cm-abc"]})
        stamp_memories_applied(path, ["cm-abc", "pm-xyz"])
        result = json.loads(path.read_text())
        # "cm-abc" must not be duplicated.
        self.assertEqual(result["memories_applied"], ["cm-abc", "pm-xyz"])

    def test_raises_when_manifest_missing(self):
        from agentic_swmm.memory.memory_provenance import stamp_memories_applied

        with self.assertRaises(FileNotFoundError):
            stamp_memories_applied(self.tmp / "nonexistent.json", [])


# ── Section 3: ensure_memories_applied_present ────────────────────────────


class TestEnsureMemoriesAppliedPresent(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_empty_list_when_absent(self):
        from agentic_swmm.memory.memory_provenance import ensure_memories_applied_present

        path = self.tmp / "manifest.json"
        path.write_text(json.dumps({"run_ok": True}), encoding="utf-8")
        ensure_memories_applied_present(path)
        result = json.loads(path.read_text())
        self.assertEqual(result["memories_applied"], [])

    def test_noop_when_already_present(self):
        from agentic_swmm.memory.memory_provenance import ensure_memories_applied_present

        path = self.tmp / "manifest.json"
        path.write_text(json.dumps({"memories_applied": ["cm-abc"]}), encoding="utf-8")
        ensure_memories_applied_present(path)
        result = json.loads(path.read_text())
        self.assertEqual(result["memories_applied"], ["cm-abc"])


# ── Section 4-6: swmm_runner.py manifest writer ───────────────────────────


class TestRunnerManifestMemoriesApplied(unittest.TestCase):
    """The runner script always writes memories_applied into manifest.json."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_cmd_run(self, extra_args: list[str] | None = None) -> dict:
        """Invoke cmd_run via the runner module with a stubbed swmm5."""
        runner = _load_runner_module()

        run_dir = self.tmp / "run1"
        run_dir.mkdir()

        inp = self.tmp / "model.inp"
        inp.write_text("[OUTFALLS]\nO1  90.0  FREE\n", encoding="utf-8")

        # Stub out run_swmm so we don't need swmm5 installed.
        rpt = run_dir / "model.rpt"
        out_file = run_dir / "model.out"
        rpt.write_text(
            "  ***** Flow Routing Continuity *****\n"
            "  Continuity Error (%) ............. 0.00\n",
            encoding="utf-8",
        )
        out_file.write_text("placeholder", encoding="utf-8")

        def fake_run_swmm(inp_, rpt_, out_, stdout_, stderr_, timeout=600.0):
            # Already wrote the rpt above; just touch stdout/stderr.
            stdout_.write_text("", encoding="utf-8")
            stderr_.write_text("", encoding="utf-8")
            return 0

        import argparse

        args = argparse.Namespace(
            inp=inp,
            run_dir=run_dir,
            node="O1",
            rpt_name=None,
            out_name=None,
            timeout=600.0,
            gate=False,
            memories_applied=None,
        )
        if extra_args:
            for k, v in extra_args:
                setattr(args, k, v)

        with patch.object(runner, "run_swmm", fake_run_swmm):
            with patch.object(runner, "get_swmm5_version", return_value="5.2.4"):
                # capture stdout
                import io
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    runner.cmd_run(args)
                    stdout_text = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

        manifest_path = run_dir / "manifest.json"
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_memories_applied_always_present_default_empty(self):
        """No-application run records memories_applied: []."""
        manifest = self._run_cmd_run()
        self.assertIn("memories_applied", manifest)
        self.assertEqual(manifest["memories_applied"], [])

    def test_memories_applied_written_with_ids(self):
        """Explicit --memories-applied ids appear in manifest."""
        manifest = self._run_cmd_run(
            extra_args=[("memories_applied", '["cm-abc123", "pm-xyz"]')]
        )
        self.assertEqual(manifest["memories_applied"], ["cm-abc123", "pm-xyz"])

    def test_memories_applied_empty_json_array(self):
        """Passing an empty JSON array still records []."""
        manifest = self._run_cmd_run(
            extra_args=[("memories_applied", "[]")]
        )
        self.assertEqual(manifest["memories_applied"], [])


# ── Section 4b: _parse_memories_applied parser ────────────────────────────


class TestParseMemoriesApplied(unittest.TestCase):
    """_parse_memories_applied handles all valid and invalid inputs."""

    def setUp(self):
        self._runner = _load_runner_module()

    def test_none_returns_empty(self):
        self.assertEqual(self._runner._parse_memories_applied(None), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(self._runner._parse_memories_applied(""), [])

    def test_valid_json_array(self):
        result = self._runner._parse_memories_applied('["cm-abc", "pm-xyz"]')
        self.assertEqual(result, ["cm-abc", "pm-xyz"])

    def test_invalid_json_returns_empty(self):
        result = self._runner._parse_memories_applied("not-json")
        self.assertEqual(result, [])

    def test_non_array_json_returns_empty(self):
        result = self._runner._parse_memories_applied('{"key": "val"}')
        self.assertEqual(result, [])


# ── Section 7-8: TransferRecommendation.memory_id ─────────────────────────


class TestTransferRecommendationMemoryId(unittest.TestCase):
    """TransferRecommendation exposes memory_id with the correct cm- prefix."""

    def _make_rec(self, run_id: str):
        from agentic_swmm.memory.calibration_memory import CalibrationRecord
        from agentic_swmm.memory.cross_watershed_transfer import TransferRecommendation

        record = CalibrationRecord(
            run_id=run_id,
            case_name="test-case",
            objective_name="NSE",
            objective_value=0.75,
        )
        return TransferRecommendation(
            target_case="new-case",
            source_case="test-case",
            similarity=0.85,
            source_calibration_record=record,
            proposed_parameters={"n": 0.013},
        )

    def test_memory_id_has_cm_prefix(self):
        rec = self._make_rec("run-42")
        self.assertEqual(rec.memory_id, "cm-run-42")

    def test_memory_id_in_to_dict(self):
        rec = self._make_rec("run-42")
        d = rec.to_dict()
        self.assertIn("memory_id", d)
        self.assertEqual(d["memory_id"], "cm-run-42")

    def test_transfer_recommendation_memory_ids_helper(self):
        from agentic_swmm.memory.cross_watershed_transfer import (
            transfer_recommendation_memory_ids,
        )

        recs = [self._make_rec("run-1"), self._make_rec("run-2"), self._make_rec("run-1")]
        ids = transfer_recommendation_memory_ids(recs)
        self.assertEqual(ids, ["cm-run-1", "cm-run-2"])  # deduped, order-preserved

    def test_transfer_recommendation_memory_ids_empty(self):
        from agentic_swmm.memory.cross_watershed_transfer import (
            transfer_recommendation_memory_ids,
        )

        self.assertEqual(transfer_recommendation_memory_ids([]), [])


# ── Section 9-10: audit collect_run provenance copy ───────────────────────


class TestAuditCollectRunMemoriesApplied(unittest.TestCase):
    """collect_run copies memories_applied from the runner manifest into provenance."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._audit = _load_audit_module()

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_run_dir(self, memories_applied: list[str] | None = None) -> Path:
        """Create a minimal run dir with an optional memories_applied field."""
        run_dir = self.tmp / "test_run"
        runner_dir = run_dir / "05_runner"
        runner_dir.mkdir(parents=True)

        rpt = runner_dir / "model.rpt"
        rpt.write_text(
            "  ***** Flow Routing Continuity *****\n"
            "  Continuity Error (%) ............. 0.00\n",
            encoding="utf-8",
        )
        (runner_dir / "model.out").write_text("placeholder", encoding="utf-8")
        (runner_dir / "stdout.txt").write_text("", encoding="utf-8")
        (runner_dir / "stderr.txt").write_text("", encoding="utf-8")

        runner_manifest: dict = {
            "files": {
                "rpt": str(rpt),
                "out": str(runner_dir / "model.out"),
                "stdout": str(runner_dir / "stdout.txt"),
                "stderr": str(runner_dir / "stderr.txt"),
            },
            "metrics": {"peak": None, "continuity": {}},
            "return_code": 0,
            "run_ok": True,
        }
        if memories_applied is not None:
            runner_manifest["memories_applied"] = memories_applied

        (runner_dir / "manifest.json").write_text(
            json.dumps(runner_manifest), encoding="utf-8"
        )
        return run_dir

    def test_collect_run_copies_memories_applied(self):
        """When runner manifest has memories_applied, provenance gets it."""
        run_dir = self._seed_run_dir(memories_applied=["cm-abc123", "pm-xyz"])
        provenance, _ = self._audit.collect_run(
            run_dir,
            repo_root=REPO_ROOT,
        )
        self.assertIn("memories_applied", provenance)
        self.assertEqual(provenance["memories_applied"], ["cm-abc123", "pm-xyz"])

    def test_collect_run_empty_list_passes_through(self):
        """An explicit [] in the runner manifest produces [] in provenance."""
        run_dir = self._seed_run_dir(memories_applied=[])
        provenance, _ = self._audit.collect_run(
            run_dir,
            repo_root=REPO_ROOT,
        )
        self.assertEqual(provenance["memories_applied"], [])

    def test_collect_run_backward_compat_absent_field(self):
        """Old manifest without memories_applied yields [] in provenance (backward compat)."""
        run_dir = self._seed_run_dir(memories_applied=None)
        provenance, _ = self._audit.collect_run(
            run_dir,
            repo_root=REPO_ROOT,
        )
        # Field must be present in provenance and default to [].
        self.assertIn("memories_applied", provenance)
        self.assertEqual(provenance["memories_applied"], [])


if __name__ == "__main__":
    unittest.main()
