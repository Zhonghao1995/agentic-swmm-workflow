"""Doctor must judge the install the way the runtime resolves it.

Two live findings from a clean-venv `pip install aiswmm` (2026-08-11):

* Doctor checked required skill scripts as ``repo_root() / path``, which
  is source-tree only. On a pip install the scripts ship under the
  wheel's data directory, so doctor reported four core scripts MISSING
  while the runtime executed them without trouble. A brand-new user's
  first diagnostic said their install was broken when it was not.
* The Word deliverable is on the README front page, but python-docx is
  an optional extra and doctor never mentioned it, so a plain
  ``pip install aiswmm`` looked complete while report export could not
  work. The anywhere extra was already reported; report now is too.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.commands import doctor


class RequiredScriptsUseRuntimeResolutionTests(unittest.TestCase):
    def _script_checks(self, checks):
        return [c for c in checks if c[0].startswith("skills/")]

    def test_scripts_pass_when_only_the_packaged_path_has_them(self) -> None:
        """The pip-install shape: repo_root() has no skills/ tree at all."""
        with TemporaryDirectory() as tmp:
            empty_root = Path(tmp)

            def fake_resource_path(*parts: str) -> Path:
                # Stand in for the wheel's data dir: always resolvable.
                target = empty_root / "packaged" / Path(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# shipped in the wheel\n")
                return target

            with mock.patch.object(doctor, "resource_path", fake_resource_path):
                checks = doctor._build_install_checks(empty_root)

        script_checks = self._script_checks(checks)
        self.assertEqual(len(script_checks), 4, "expected the four core scripts")
        for name, passed, detail, required in script_checks:
            self.assertTrue(
                passed,
                f"{name} ships in the wheel but doctor called it missing ({detail})",
            )
            self.assertTrue(required)

    def test_scripts_fail_loudly_when_genuinely_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            empty_root = Path(tmp)

            def missing(*parts: str) -> Path:
                raise FileNotFoundError("not shipped")

            with mock.patch.object(doctor, "resource_path", missing):
                checks = doctor._build_install_checks(empty_root)

        for name, passed, _detail, _required in self._script_checks(checks):
            self.assertFalse(passed, f"{name} is absent and must be reported")


class ReportExtraIsReportedTests(unittest.TestCase):
    def _report_row(self, checks):
        rows = [c for c in checks if c[0] == "swmm-report extra"]
        self.assertEqual(len(rows), 1, f"expected one report-extra row, got {rows!r}")
        return rows[0]

    def test_absent_report_extra_names_the_install_command(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.object(
                doctor, "_module_available", lambda name: name != "docx"
            ):
                checks = doctor._build_install_checks(Path(tmp))
        name, passed, detail, required = self._report_row(checks)
        self.assertFalse(passed)
        self.assertIn("pip install aiswmm[report]", detail)
        self.assertFalse(required, "a missing optional extra must not fail the install")

    def test_present_report_extra_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.object(doctor, "_module_available", lambda name: True):
                checks = doctor._build_install_checks(Path(tmp))
        _name, passed, _detail, _required = self._report_row(checks)
        self.assertTrue(passed)


class InstallerShipsTheReportDependencyTests(unittest.TestCase):
    """The one-liner installer must deliver the advertised capability.

    scripts/requirements.txt is what `curl … | bash` installs. The Word
    report is advertised on the README front page and in cases/, so the
    dependency cannot be left to a follow-up command the user never sees.
    """

    def test_requirements_include_python_docx(self) -> None:
        req = (
            Path(__file__).resolve().parent.parent / "scripts" / "requirements.txt"
        ).read_text()
        self.assertIn(
            "python-docx",
            req,
            "the one-liner install must ship the Word-report dependency",
        )


if __name__ == "__main__":
    unittest.main()
