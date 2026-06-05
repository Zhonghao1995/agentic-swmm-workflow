"""swmm5 resolution (installer-engine wiring).

The one-line installer drops a built (macOS/Linux) or downloaded (Windows)
SWMM 5.2.4 engine at ``$AISWMM_CONFIG_DIR/swmm`` (default ``~/.aiswmm/swmm``).
Because the installer never edits the user's shell PATH, both the standalone
runner (``swmm_runner.resolve_swmm5``) and ``aiswmm doctor``
(``doctor._which_swmm5``) must look in that fixed directory *first*, then fall
back to PATH for users who installed swmm5 themselves. ``AISWMM_SWMM5`` is an
explicit override. These tests lock that order so a future refactor cannot
silently regress to PATH-only resolution (which left a fresh install unable to
run any model).
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_RUNNER_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("swmm_runner_under_test", _RUNNER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clear(env: dict) -> None:
    for key in ("AISWMM_SWMM5", "AISWMM_CONFIG_DIR"):
        env.pop(key, None)


class ResolveSwmm5RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _load_runner()

    def test_explicit_override_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "myswmm"
            exe.write_text("x")
            with mock.patch.dict(os.environ, {}, clear=False):
                _clear(os.environ)
                os.environ["AISWMM_SWMM5"] = str(exe)
                self.assertEqual(self.runner.resolve_swmm5(), str(exe))

    def test_config_dir_location_preferred_over_path(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            (cfg / "swmm").mkdir(parents=True)
            installed = cfg / "swmm" / "swmm5"
            installed.write_text("x")
            with mock.patch.dict(os.environ, {}, clear=False):
                _clear(os.environ)
                os.environ["AISWMM_CONFIG_DIR"] = str(cfg)
                # A swmm5 on PATH must NOT shadow the installer's copy.
                with mock.patch.object(self.runner.shutil, "which", return_value="/usr/bin/swmm5"):
                    self.assertEqual(self.runner.resolve_swmm5(), str(installed))

    def test_falls_back_to_path_when_not_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            cfg.mkdir()  # no swmm/ subdir
            with mock.patch.dict(os.environ, {}, clear=False):
                _clear(os.environ)
                os.environ["AISWMM_CONFIG_DIR"] = str(cfg)
                with mock.patch.object(
                    self.runner.shutil,
                    "which",
                    side_effect=lambda n: "/usr/bin/swmm5" if n == "swmm5" else None,
                ):
                    self.assertEqual(self.runner.resolve_swmm5(), "/usr/bin/swmm5")

    def test_last_resort_is_bare_name(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            cfg.mkdir()
            with mock.patch.dict(os.environ, {}, clear=False):
                _clear(os.environ)
                os.environ["AISWMM_CONFIG_DIR"] = str(cfg)
                with mock.patch.object(self.runner.shutil, "which", return_value=None):
                    self.assertEqual(self.runner.resolve_swmm5(), "swmm5")


class DoctorWhichSwmm5Tests(unittest.TestCase):
    def test_doctor_prefers_config_dir_over_path(self) -> None:
        from agentic_swmm.commands import doctor

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            (cfg / "swmm").mkdir(parents=True)
            installed = cfg / "swmm" / "swmm5"
            installed.write_text("x")
            with mock.patch.dict(os.environ, {"AISWMM_CONFIG_DIR": str(cfg)}, clear=False):
                os.environ.pop("AISWMM_SWMM5", None)
                with mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/swmm5"):
                    self.assertEqual(doctor._which_swmm5(), str(installed))


if __name__ == "__main__":
    unittest.main()
