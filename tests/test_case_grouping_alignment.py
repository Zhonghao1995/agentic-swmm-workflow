"""Second root-cut: align case identity so runs of one watershed group.

Diagnosed empirically: `aiswmm run --case-id tecnopolo` dropped the flag on the
floor (run.py never read args.case_id), so the manifest carried no case
identity, and audit fell back to the run-dir name. Every run of the same
watershed therefore got a UNIQUE case_name (run1, run2, ...), so the parametric
store never accumulated >=2 rows per case and the memory-informed policy could
never fire its multi-hit branches.

Two fixes, two tests here:
  1. audit_run._derive_case_name falls back to the manifest's case_id (so a
     --case-id run groups under that id).
  2. `aiswmm run --case-id X` stamps case_id=X into the run manifest
     (integration; needs swmm5).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"
TECNOPOLO_INP = REPO_ROOT / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_run", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeriveCaseNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit = load_audit_module()

    def test_explicit_case_name_arg_wins(self) -> None:
        out = self.audit._derive_case_name("explicit", {"case_id": "cid"}, Path("/x/run1"))
        self.assertEqual(out, "explicit")

    def test_manifest_case_name_is_second(self) -> None:
        out = self.audit._derive_case_name(None, {"case_name": "cn", "case_id": "cid"}, Path("/x/run1"))
        self.assertEqual(out, "cn")

    def test_manifest_case_id_groups_runs(self) -> None:
        # THE FIX: a run linked via --case-id groups under that id instead of
        # the unique run-dir name.
        out = self.audit._derive_case_name(None, {"case_id": "tecnopolo"}, Path("/x/run1"))
        self.assertEqual(out, "tecnopolo")

    def test_run_dir_name_is_last_resort(self) -> None:
        out = self.audit._derive_case_name(None, {}, Path("/x/run1"))
        self.assertEqual(out, "run1")


@unittest.skipUnless(shutil.which("swmm5") and TECNOPOLO_INP.exists(), "needs swmm5 + tecnopolo INP")
class RunStampsCaseIdTests(unittest.TestCase):
    def test_run_writes_case_id_into_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "r1"
            subprocess.run(
                [
                    sys.executable, "-m", "agentic_swmm.cli", "run",
                    "--inp", str(TECNOPOLO_INP), "--run-dir", str(run_dir),
                    "--node", "OU2", "--case-id", "tecnopolo", "--quiet",
                ],
                cwd=REPO_ROOT, check=True, capture_output=True, text=True,
            )
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest.get("case_id"), "tecnopolo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
