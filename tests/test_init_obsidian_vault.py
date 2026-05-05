from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_SCRIPT = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "init_obsidian_vault.py"


class InitObsidianVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_init(self, *extra_args: str) -> dict:
        proc = subprocess.run(
            [sys.executable, str(INIT_SCRIPT), "--vault-dir", str(self.vault), *extra_args],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        return json.loads(proc.stdout)

    def test_initializes_first_user_vault_structure(self) -> None:
        result = self.run_init()

        self.assertTrue(result["ok"])
        self.assertTrue((self.vault / "00_Home" / "Agentic SWMM Home.md").exists())
        self.assertTrue((self.vault / "10_Memory_Layer" / "Project Memory.md").exists())
        self.assertTrue((self.vault / "20_Audit_Layer" / "Experiment Audit Index.md").exists())
        self.assertTrue((self.vault / "20_Audit_Layer" / "Experiment_Audits").is_dir())
        self.assertTrue((self.vault / "40_Skill_Evolution" / "Skill Proposal Log.md").exists())

    def test_does_not_overwrite_existing_files_without_flag(self) -> None:
        self.run_init()
        home = self.vault / "00_Home" / "Agentic SWMM Home.md"
        home.write_text("custom home", encoding="utf-8")

        result = self.run_init()

        skipped = [Path(path).name for path in result["skipped_existing"]]
        self.assertIn(home.name, skipped)
        self.assertEqual(home.read_text(encoding="utf-8"), "custom home")

    def test_overwrite_flag_rewrites_existing_files(self) -> None:
        self.run_init()
        home = self.vault / "00_Home" / "Agentic SWMM Home.md"
        home.write_text("custom home", encoding="utf-8")

        self.run_init("--overwrite")

        self.assertIn("Agentic SWMM Home", home.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
