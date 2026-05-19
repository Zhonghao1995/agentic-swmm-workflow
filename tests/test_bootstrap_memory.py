"""Tests for ``aiswmm bootstrap memory`` (PRD-06 Phase D.4).

The command scaffolds an empty memory directory and is idempotent.
These tests pin:

1. Creates the four stores + README on first invocation.
2. Re-running preserves existing files and reports them as ``skipped``.
3. The directory is created if missing.
4. The default location is ``./memory/modeling-memory/``.
5. The CLI smoke-tests via the cli main dispatcher.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agentic_swmm.commands.bootstrap_memory import (
    BootstrapResult,
    bootstrap_memory_dir,
    memory_main,
    register,
)


class BootstrapMemoryDirTests(unittest.TestCase):
    """Pin the on-disk side effects of ``bootstrap_memory_dir``."""

    def test_creates_skeleton_in_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "memory" / "modeling-memory"
            result = bootstrap_memory_dir(target)
            self.assertIsInstance(result, BootstrapResult)
            self.assertEqual(result.target_dir, target)
            self.assertEqual(len(result.created), 5)
            self.assertEqual(len(result.skipped), 0)

            # Confirm all five files exist on disk.
            for name in (
                "parametric_memory.jsonl",
                "calibration_memory.jsonl",
                "negative_lessons.jsonl",
                "project_overrides.yaml",
                "README.md",
            ):
                self.assertTrue(
                    (target / name).is_file(),
                    f"{name} should be present in target_dir after bootstrap",
                )

    def test_jsonl_stores_start_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            bootstrap_memory_dir(target)
            for name in (
                "parametric_memory.jsonl",
                "calibration_memory.jsonl",
                "negative_lessons.jsonl",
            ):
                self.assertEqual(
                    (target / name).read_text(encoding="utf-8"),
                    "",
                    f"{name} must start empty (no header)",
                )

    def test_project_overrides_has_schema_version_header(self) -> None:
        # The benchmark resolver expects ``schema_version`` to be
        # present in any overrides file; a missing line would make
        # the resolver fall back to library defaults silently.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            bootstrap_memory_dir(target)
            content = (target / "project_overrides.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn("schema_version", content)
            self.assertIn("1.0", content)

    def test_readme_links_to_memory_runtime_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            bootstrap_memory_dir(target)
            content = (target / "README.md").read_text(encoding="utf-8")
            self.assertIn("memory_runtime.md", content)

    def test_idempotent_rerun_skips_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            first = bootstrap_memory_dir(target)
            self.assertEqual(len(first.created), 5)

            # Run again — nothing should be created.
            second = bootstrap_memory_dir(target)
            self.assertEqual(len(second.created), 0)
            self.assertEqual(len(second.skipped), 5)

    def test_idempotent_preserves_user_edits(self) -> None:
        # A user might edit ``project_overrides.yaml`` between
        # bootstraps. The command must never overwrite their changes.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            bootstrap_memory_dir(target)

            edited_path = target / "project_overrides.yaml"
            edited_content = (
                "# user edited this file\n"
                "schema_version: \"1.0\"\n"
                "continuity_thresholds_pct:\n"
                "  runoff:\n"
                "    warn: 1.0\n"
                "    fail: 3.0\n"
            )
            edited_path.write_text(edited_content, encoding="utf-8")

            # Re-run bootstrap. The edited file must be untouched.
            bootstrap_memory_dir(target)
            self.assertEqual(
                edited_path.read_text(encoding="utf-8"),
                edited_content,
                "Bootstrap must never overwrite a user-edited file",
            )

    def test_partial_skeleton_only_creates_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            target.mkdir(parents=True, exist_ok=True)
            # Pre-seed two of the five files.
            (target / "parametric_memory.jsonl").write_text("", encoding="utf-8")
            (target / "README.md").write_text("# custom\n", encoding="utf-8")

            result = bootstrap_memory_dir(target)
            self.assertEqual(len(result.created), 3)
            self.assertEqual(len(result.skipped), 2)
            # The pre-seeded README is preserved verbatim.
            self.assertEqual(
                (target / "README.md").read_text(encoding="utf-8"),
                "# custom\n",
            )

    def test_default_target_dir(self) -> None:
        # When no target_dir is supplied, the default is
        # ./memory/modeling-memory relative to cwd. We test by
        # running inside a tempdir as cwd so we don't pollute the
        # real repo.
        import os

        original_cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.chdir(tmp)
                result = bootstrap_memory_dir(None)
                expected = Path("memory") / "modeling-memory"
                self.assertEqual(result.target_dir, expected)
                self.assertTrue(expected.is_dir())
        finally:
            os.chdir(original_cwd)

    def test_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deep" / "nested" / "path" / "memory"
            self.assertFalse(target.exists())
            result = bootstrap_memory_dir(target)
            self.assertTrue(target.is_dir())
            self.assertEqual(len(result.created), 5)

    def test_bootstrap_result_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            result = bootstrap_memory_dir(target)
            with self.assertRaises(Exception):
                # dataclass(frozen=True) raises FrozenInstanceError.
                result.target_dir = Path("/elsewhere")  # type: ignore[misc]


class MemoryMainCliTests(unittest.TestCase):
    """Pin the argparse-facing surface."""

    def test_memory_main_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            ns = argparse.Namespace(target_dir=target)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = memory_main(ns)
            self.assertEqual(code, 0)
            self.assertIn("created", buf.getvalue())
            self.assertIn("modeling-memory", buf.getvalue())

    def test_memory_main_reports_skipped_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "modeling-memory"
            ns = argparse.Namespace(target_dir=target)
            with redirect_stdout(io.StringIO()):
                memory_main(ns)

            buf = io.StringIO()
            with redirect_stdout(buf):
                memory_main(ns)
            output = buf.getvalue()
            self.assertIn("skipped", output)
            self.assertIn("(5)", output)

    def test_register_attaches_subparser(self) -> None:
        # Smoke test that the registration plumbing works against a
        # real argparse instance.
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        register(subparsers)
        # bootstrap memory --dir <path> must parse cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            args = parser.parse_args(["bootstrap", "memory", "--dir", tmp])
            self.assertEqual(args.command, "bootstrap")
            self.assertEqual(args.bootstrap_target, "memory")
            self.assertEqual(args.target_dir, Path(tmp))

    def test_register_default_dir(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        register(subparsers)
        args = parser.parse_args(["bootstrap", "memory"])
        self.assertIsNone(args.target_dir)


if __name__ == "__main__":
    unittest.main()
