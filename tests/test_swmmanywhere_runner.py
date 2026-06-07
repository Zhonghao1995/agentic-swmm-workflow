"""Tests for ``agentic_swmm.integrations.swmmanywhere_runner``.

The wrapper hides three concrete gotchas discovered during the SWMManywhere
D1 spike. Tests below pin down their handling so future refactors cannot
silently re-introduce the macOS-arm64-only SIGKILL, the str-vs-Path crash,
or the SWMM-path-with-spaces parsing error.

Tests deliberately avoid invoking real SWMManywhere (heavy geo stack
behind the optional ``[anywhere]`` extra) — only the pure-Python
gotcha-handling helpers and dataclasses are exercised. A separate
``test_smoke.py`` under ``skills/swmm-anywhere/tests/`` is gated by the
extra being installed for end-to-end coverage.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.integrations.swmmanywhere_runner import (
    DEFAULT_OUTFALL_DERIVATION,
    SynthRunError,
    SynthRunResult,
    _build_config,
    _check_anywhere_extra_installed,
    _coerce_base_dir,
    _install_pyswmm_stub,
    _resolve_download_dir,
    _snapshot_raw_downloads,
    normalize_external_paths,
    override_rain_file,
    run_synth_from_bbox,
)


class PyswmmStubTests(unittest.TestCase):
    """Gotcha #1: pyswmm SIGKILL on macOS arm64 — runner installs a stub."""

    def setUp(self) -> None:
        self._saved = sys.modules.pop("pyswmm", None)

    def tearDown(self) -> None:
        if self._saved is None:
            sys.modules.pop("pyswmm", None)
        else:
            sys.modules["pyswmm"] = self._saved

    def test_stub_is_installed_in_sys_modules(self) -> None:
        self.assertNotIn("pyswmm", sys.modules)
        _install_pyswmm_stub()
        self.assertIn("pyswmm", sys.modules)
        self.assertTrue(hasattr(sys.modules["pyswmm"], "Simulation"))

    def test_stub_is_idempotent(self) -> None:
        _install_pyswmm_stub()
        first = sys.modules["pyswmm"]
        _install_pyswmm_stub()
        second = sys.modules["pyswmm"]
        self.assertIs(first, second, "stub install should not replace an already-installed pyswmm")


class CoerceBaseDirTests(unittest.TestCase):
    """Gotcha #2: SWMManywhere ``filepaths.py`` requires Path, not str."""

    def test_string_base_dir_becomes_path(self) -> None:
        config = {"base_dir": "/tmp/some_path", "project": "x"}
        out = _coerce_base_dir(config)
        self.assertIsInstance(out["base_dir"], Path)
        self.assertEqual(out["base_dir"], Path("/tmp/some_path"))

    def test_path_base_dir_passes_through(self) -> None:
        original = Path("/tmp/x")
        config = {"base_dir": original}
        out = _coerce_base_dir(config)
        self.assertIs(out["base_dir"], original)


class NormalizeExternalPathsTests(unittest.TestCase):
    """Gotcha #3: SWMM 5.2 ``ERROR 205`` when RAINGAGES FILE path has spaces."""

    def _make_inp_with_raingages_pointing_to(self, inp_path: Path, file_ref: str) -> None:
        inp_path.write_text(
            "[TITLE]\n;;Project Title/Notes\n\n"
            "[OPTIONS]\nFLOW_UNITS LPS\n\n"
            "[RAINGAGES]\n"
            ";;Name  Format    Interval  SCF  Source\n"
            f"1                INTENSITY 00:05    1        FILE       {file_ref} 1          mm   \n"
            "\n"
            "[SUBCATCHMENTS]\n"
        )

    def test_rewrites_absolute_path_with_spaces_to_relative(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external_dir = tmp_path / "dir with spaces"
            external_dir.mkdir()
            external_file = external_dir / "storm.dat"
            external_file.write_text("dummy storm payload")

            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, str(external_file))

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, (external_file,))
            rewritten = inp.read_text()
            self.assertIn("FILE       storm.dat 1", rewritten)
            self.assertNotIn(str(external_file), rewritten)
            self.assertTrue((inp.parent / "storm.dat").exists())
            self.assertEqual((inp.parent / "storm.dat").read_text(), "dummy storm payload")

    def test_leaves_relative_path_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, "storm.dat")

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, ())
            self.assertIn("FILE       storm.dat 1", inp.read_text())

    def test_leaves_absolute_path_without_spaces_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external_dir = tmp_path / "nospaces"
            external_dir.mkdir()
            external_file = external_dir / "storm.dat"
            external_file.write_text("dummy")

            inp = tmp_path / "model.inp"
            self._make_inp_with_raingages_pointing_to(inp, str(external_file))

            copied = normalize_external_paths(inp)

            self.assertEqual(copied, ())
            self.assertIn(str(external_file), inp.read_text())


class ExtraMissingTests(unittest.TestCase):
    """Gotcha #4: if user hasn't installed the [anywhere] extra, surface
    an actionable error before the SWMManywhere lazy import."""

    def test_check_raises_synthrunerror_with_actionable_stage(self) -> None:
        # Monkey-patch importlib.util.find_spec to simulate the extra being
        # absent from the current Python environment.
        import importlib.util as iu

        original = iu.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "swmmanywhere":
                return None
            return original(name, *args, **kwargs)

        iu.find_spec = fake_find_spec
        try:
            with self.assertRaises(SynthRunError) as ctx:
                _check_anywhere_extra_installed()
            self.assertEqual(ctx.exception.stage, "extra_missing")
            self.assertIn("pip install aiswmm[anywhere]", str(ctx.exception.original_exc))
        finally:
            iu.find_spec = original

    def test_run_synth_from_bbox_raises_extra_missing_first(self) -> None:
        # When the extra is absent, run_synth_from_bbox must raise the
        # 'extra_missing' stage *before* attempting to install pyswmm stub
        # or build a config. This gives the CLI a clean code path to print
        # the install hint.
        import importlib.util as iu

        original = iu.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "swmmanywhere":
                return None
            return original(name, *args, **kwargs)

        iu.find_spec = fake_find_spec
        try:
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as tmp:
                with self.assertRaises(SynthRunError) as ctx:
                    run_synth_from_bbox(
                        bbox=[0.0, 51.0, 0.01, 51.01],
                        run_dir=Path(tmp) / "out",
                    )
                self.assertEqual(ctx.exception.stage, "extra_missing")
        finally:
            iu.find_spec = original


class _SwmmanywhereModuleStub:
    """Context manager that installs a fake ``swmmanywhere`` package in
    ``sys.modules`` pointing at a temp ``defs/demo_config.yml``.

    Lets ``_build_config`` run without the real (heavy, optional) extra
    installed. The fake module ships a minimal demo config matching the
    schema ``_build_config`` mutates.
    """

    DEMO_CONFIG_YAML = (
        "base_dir: /tmp/placeholder\n"
        "project: placeholder\n"
        "bbox: [0, 0, 0, 0]\n"
        "real: {}\n"
        "metric_list: []\n"
        "run_model: true\n"
        "parameter_overrides: {}\n"
    )

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self._saved: dict[str, object | None] = {}

    def __enter__(self) -> "_SwmmanywhereModuleStub":
        pkg_dir = self.tmp_path / "swmmanywhere_fake_pkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        defs_dir = pkg_dir / "defs"
        defs_dir.mkdir(exist_ok=True)
        (defs_dir / "demo_config.yml").write_text(self.DEMO_CONFIG_YAML)
        # Module placeholders the runner imports.
        import types
        fake_pkg = types.ModuleType("swmmanywhere")
        fake_inner = types.ModuleType("swmmanywhere.swmmanywhere")
        fake_inner.__file__ = str(pkg_dir / "swmmanywhere.py")
        fake_pkg.swmmanywhere = fake_inner  # type: ignore[attr-defined]
        for name, mod in (
            ("swmmanywhere", fake_pkg),
            ("swmmanywhere.swmmanywhere", fake_inner),
        ):
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
        return self

    def __exit__(self, *_exc) -> None:
        for name, prev in self._saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


class UpstreamDefaultsTests(unittest.TestCase):
    """`use_upstream_defaults=True` must skip the spike-04 outfall_derivation
    overrides so SWMManywhere falls back to its own parameters.py defaults."""

    def test_default_path_applies_spike04_outfall_derivation(self) -> None:
        with TemporaryDirectory() as tmp:
            with _SwmmanywhereModuleStub(Path(tmp)):
                config = _build_config(
                    bbox=[0.0, 51.0, 0.01, 51.01],
                    run_dir=Path(tmp) / "out",
                    project_name="x",
                    config_overrides=None,
                )
        outfall = config["parameter_overrides"]["outfall_derivation"]
        self.assertEqual(outfall["method"], "withtopo")
        self.assertEqual(outfall["river_buffer_distance"], 300.0)
        self.assertEqual(outfall["outfall_length"], 200.0)

    def test_upstream_defaults_skips_outfall_derivation_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            with _SwmmanywhereModuleStub(Path(tmp)):
                config = _build_config(
                    bbox=[0.0, 51.0, 0.01, 51.01],
                    run_dir=Path(tmp) / "out",
                    project_name="x",
                    config_overrides=None,
                    use_upstream_defaults=True,
                )
        outfall = (config.get("parameter_overrides") or {}).get(
            "outfall_derivation", {}
        )
        # The three tuned keys must NOT appear when upstream defaults are
        # requested — SWMManywhere parameters.py supplies its own values.
        self.assertNotIn("method", outfall)
        self.assertNotIn("river_buffer_distance", outfall)
        self.assertNotIn("outfall_length", outfall)

    def test_upstream_defaults_still_honours_config_overrides(self) -> None:
        # Power-user pattern: opt into upstream defaults *and* nudge one knob.
        with TemporaryDirectory() as tmp:
            with _SwmmanywhereModuleStub(Path(tmp)):
                config = _build_config(
                    bbox=[0.0, 51.0, 0.01, 51.01],
                    run_dir=Path(tmp) / "out",
                    project_name="x",
                    config_overrides={"outfall_derivation": {"method": "withtopo"}},
                    use_upstream_defaults=True,
                )
        outfall = config["parameter_overrides"]["outfall_derivation"]
        self.assertEqual(outfall, {"method": "withtopo"})


class OverrideRainFileTests(unittest.TestCase):
    """``--rain-file`` arg — user can swap the bundled demo storm for their
    own rainfall data without hand-editing the synth INP."""

    def _make_inp_with_raingages_pointing_to(self, inp_path: Path, file_ref: str) -> None:
        inp_path.write_text(
            "[TITLE]\n;;Project Title/Notes\n\n"
            "[OPTIONS]\nFLOW_UNITS LPS\n\n"
            "[RAINGAGES]\n"
            ";;Name  Format    Interval  SCF  Source\n"
            f"1                INTENSITY 00:05    1        FILE       {file_ref} 1          mm   \n"
            "\n"
            "[SUBCATCHMENTS]\n"
        )

    def test_rewrites_raingages_to_point_at_user_rain_file(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "synth.inp"
            self._make_inp_with_raingages_pointing_to(inp, "storm.dat")

            rain_src = tmp_path / "user_rain.dat"
            rain_src.write_text("0 0 0 0 0 0  1.23\n")

            dest = override_rain_file(inp, rain_src)

            # File copied next to the INP.
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_text(), "0 0 0 0 0 0  1.23\n")
            self.assertEqual(dest.name, "user_rain.dat")
            # INP rewritten to reference the new file.
            text = inp.read_text()
            self.assertIn("FILE       user_rain.dat 1", text)
            self.assertNotIn("storm.dat", text)

    def test_run_synth_raises_rain_file_missing_when_path_absent(self) -> None:
        # Missing rain file must fail fast (before the SWMManywhere pipeline)
        # with stage='rain_file_missing' so the CLI can surface the hint.
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SynthRunError) as ctx:
                run_synth_from_bbox(
                    bbox=[0.0, 51.0, 0.01, 51.01],
                    run_dir=Path(tmp) / "out",
                    rain_file=Path(tmp) / "does_not_exist.dat",
                )
            self.assertEqual(ctx.exception.stage, "rain_file_missing")
            self.assertIn("does not exist", str(ctx.exception.original_exc))

    def test_final_inp_references_user_rain_file_after_postprocess(self) -> None:
        # Simulate what `run_synth_from_bbox` does to the INP after the
        # SWMManywhere pipeline runs: synth INP starts off pointing at a
        # bundled storm.dat (path-with-spaces, absolute); we then run the
        # normalize-then-override sequence. The end-state INP must reference
        # the *user* rain file by bare name.
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # SWMManywhere-style: stash demo storm at an absolute path with
            # spaces (matches the real failure mode).
            bundled_dir = tmp_path / "dir with spaces"
            bundled_dir.mkdir()
            bundled = bundled_dir / "storm.dat"
            bundled.write_text("default storm payload")

            inp = tmp_path / "synth.inp"
            self._make_inp_with_raingages_pointing_to(inp, str(bundled))

            user_rain = tmp_path / "my_storm.dat"
            user_rain.write_text("real rain payload")

            # Mirror the order in run_synth_from_bbox().
            normalize_external_paths(inp)
            override_rain_file(inp, user_rain)

            text = inp.read_text()
            self.assertIn("FILE       my_storm.dat 1", text)
            self.assertNotIn("storm.dat 1", text.replace("my_storm.dat", ""))
            self.assertTrue((inp.parent / "my_storm.dat").exists())
            self.assertEqual(
                (inp.parent / "my_storm.dat").read_text(),
                "real rain payload",
            )


class DataStructureTests(unittest.TestCase):
    """Frozen dataclasses + exception structure are part of the public API."""

    def test_default_outfall_derivation_has_three_tuned_keys(self) -> None:
        self.assertEqual(
            set(DEFAULT_OUTFALL_DERIVATION),
            {"method", "river_buffer_distance", "outfall_length"},
        )
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["method"], "withtopo")
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["river_buffer_distance"], 300.0)
        self.assertEqual(DEFAULT_OUTFALL_DERIVATION["outfall_length"], 200.0)

    def test_synth_run_result_is_frozen(self) -> None:
        result = SynthRunResult(
            inp_path=Path("/x/y/z.inp"),
            run_dir=Path("/x/y"),
            raw_manifest_path=Path("/x/y/00_raw/raw_manifest.json"),
            provenance={},
            stage_durations={},
            warnings=(),
        )
        with self.assertRaises(Exception):
            result.inp_path = Path("/other.inp")  # type: ignore[misc]

    def test_synth_run_error_carries_stage(self) -> None:
        original = ValueError("boom")
        err = SynthRunError("config_build", original)
        self.assertEqual(err.stage, "config_build")
        self.assertIs(err.original_exc, original)
        self.assertIn("config_build", str(err))


class ResolveDowloadDirTests(unittest.TestCase):
    """Bug #234: hardcoded bbox_1 silently captures nothing when upstream
    SWMManywhere uses bbox_2, bbox_3, etc. for a reused project directory.

    _resolve_download_dir must find the actual bbox_N directory that matches
    the current run's bbox — not blindly pick bbox_1.
    """

    def _write_bbox_info(self, path: Path, bbox: list[float]) -> None:
        """Write a minimal bounding_box_info.json in SWMManywhere's format."""
        import json
        data = {
            "crs": "EPSG:4326",
            "bbox": {"x_min": bbox[0], "y_min": bbox[1], "x_max": bbox[2], "y_max": bbox[3]},
        }
        path.write_text(json.dumps(data))

    def test_finds_bbox_2_when_that_dir_matches(self) -> None:
        """The core regression: upstream used bbox_2 but we were hardcoding bbox_1."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            bbox = [0.0, 51.0, 0.01, 51.01]

            # Create bbox_1 WITHOUT a download dir (stale from a previous run).
            (proj / "bbox_1").mkdir(parents=True, exist_ok=True)

            # Create bbox_2 WITH a download dir and matching bbox info.
            dl2 = proj / "bbox_2" / "download"
            dl2.mkdir(parents=True, exist_ok=True)
            (dl2 / "streets.json").write_text('{"type":"FeatureCollection"}')
            (dl2 / "elevation.tif").write_bytes(b"\x00" * 8)
            self._write_bbox_info(proj / "bbox_2" / "bounding_box_info.json", bbox)

            result = _resolve_download_dir(tmp_path, "proj", bbox)
            self.assertEqual(result, dl2)

    def test_finds_single_bbox_dir_without_bbox_info(self) -> None:
        """If only one bbox_* dir exists (and has a download subdir), use it."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj = tmp_path / "proj"
            dl = proj / "bbox_1" / "download"
            dl.mkdir(parents=True, exist_ok=True)
            (dl / "data.json").write_text("{}")

            result = _resolve_download_dir(tmp_path, "proj", [0.0, 51.0, 0.01, 51.01])
            self.assertEqual(result, dl)

    def test_falls_back_to_bbox_1_when_no_bbox_dir_exists(self) -> None:
        """Fallback: project dir missing entirely → return bbox_1/download path."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _resolve_download_dir(tmp_path, "proj", [0.0, 51.0, 0.01, 51.01])
            self.assertEqual(result, tmp_path / "proj" / "bbox_1" / "download")


class SnapshotRawDownloadsTests(unittest.TestCase):
    """_snapshot_raw_downloads: happy path captures both files with correct sha256."""

    def test_manifest_lists_both_files_with_sha256(self) -> None:
        import hashlib
        import json

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dl_dir = tmp_path / "dl"
            dl_dir.mkdir()
            file_a = dl_dir / "streets.json"
            file_b = dl_dir / "elevation.tif"
            file_a.write_bytes(b"streets-payload")
            file_b.write_bytes(b"tif-payload")

            raw_dir = tmp_path / "00_raw"
            manifest_path = _snapshot_raw_downloads(dl_dir, raw_dir)

            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text())
            # The manifest must record exactly 2 sources.
            sources = manifest.get("sources", [])
            self.assertEqual(len(sources), 2)
            paths = {s["path"] for s in sources}
            self.assertIn("streets.json", paths)
            self.assertIn("elevation.tif", paths)
            # Verify sha256 for streets.json.
            expected_sha = hashlib.sha256(b"streets-payload").hexdigest()
            streets_entry = next(s for s in sources if s["path"] == "streets.json")
            self.assertEqual(streets_entry["sha256"], expected_sha)


if __name__ == "__main__":
    unittest.main()
