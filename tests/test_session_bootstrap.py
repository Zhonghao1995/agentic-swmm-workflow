"""Session-bootstrap helpers (PRD-02).

These were ``_safe_name`` / ``_display_path`` / ``_new_interactive_session``
/ ``_case_slug`` / ``_match_registered_case`` on ``runtime_loop``. They
share a single concern — preparing the per-session filesystem location
and naming the case — so PRD-02 consolidates them into one module.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

from agentic_swmm.agent.session_bootstrap import (
    display_path,
    infer_case_slug,
    new_interactive_session,
    safe_name,
)


def _write_case_meta(repo: Path, case_id: str, *, display_name: str = "") -> None:
    case_dir = repo / "cases" / case_id
    case_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "display_name": display_name or case_id,
        "study_purpose": "fixture",
        "created_utc": "2026-05-19T00:00:00Z",
        "catchment": {},
        "inputs": {},
        "notes": "",
    }
    (case_dir / "case_meta.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )


class SafeNameTests(unittest.TestCase):
    """``safe_name`` is the filesystem-slug helper.

    Identical contract to ``agent/single_shot.py::_safe_name`` so the
    existing single-shot path can also adopt this module later.
    """

    def test_simple_word_passes_through(self) -> None:
        self.assertEqual(safe_name("tecnopolo"), "tecnopolo")

    def test_unsafe_characters_replaced_with_dash(self) -> None:
        self.assertEqual(safe_name("foo bar"), "foo-bar")
        self.assertEqual(safe_name("foo/bar"), "foo-bar")
        self.assertEqual(safe_name("a b c"), "a-b-c")

    def test_empty_string_returns_agent_fallback(self) -> None:
        # Single-shot parity: empty / whitespace-only / all-unsafe input
        # falls back to "agent" so callers can rely on a non-empty slug.
        self.assertEqual(safe_name(""), "agent")
        self.assertEqual(safe_name("   "), "agent")
        self.assertEqual(safe_name("///"), "agent")

    def test_leading_trailing_dashes_stripped(self) -> None:
        self.assertEqual(safe_name(" foo "), "foo")
        self.assertEqual(safe_name("--foo--"), "foo")


class DisplayPathTests(unittest.TestCase):
    """``display_path`` formats a ``Path`` for human-readable banners.

    Identical contract to ``agentic_swmm.agent.ui.display_path``; this
    module re-exports it so the REPL doesn't need a second import.
    """

    def test_returns_string(self) -> None:
        out = display_path(Path("/tmp/runs/2026-05-19"))
        self.assertIsInstance(out, str)
        self.assertIn("2026-05-19", out)


class NewInteractiveSessionTests(unittest.TestCase):
    """``new_interactive_session`` creates a date_dir and session label.

    The function is responsible for the on-disk side effect (creating
    the per-day run folder and writing a ``session_start`` event into
    ``_sessions.jsonl``). The REPL only needs the returned tuple.
    """

    def test_returns_date_dir_and_label(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            date_dir, label = new_interactive_session(base)
        self.assertTrue(label.startswith("session-"))
        # Date dir is under base.
        self.assertEqual(date_dir.parent, base)
        # Folder name matches YYYY-MM-DD shape.
        self.assertRegex(date_dir.name, r"^\d{4}-\d{2}-\d{2}$")

    def test_creates_date_dir_on_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            date_dir, _ = new_interactive_session(base)
            self.assertTrue(date_dir.exists())
            self.assertTrue(date_dir.is_dir())

    def test_writes_session_start_to_index(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            date_dir, label = new_interactive_session(base)
            index_path = date_dir / "_sessions.jsonl"
            self.assertTrue(index_path.exists())
            lines = index_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            import json as _json
            record = _json.loads(lines[0])
            self.assertEqual(record.get("event"), "session_start")
            self.assertEqual(record.get("session"), label)


class InferCaseSlugTests(unittest.TestCase):
    """``infer_case_slug`` is the case-id resolver formerly ``_case_slug``.

    Resolution order (preserved from runtime_loop._case_slug):

    1. ``examples/<name>/...`` path mention → ``<name>``,
    2. ``<name>.inp`` mention → ``<name>``,
    3. registered case (display_name / id / alias) → ``case_id``,
    4. plot vocab → ``"plot-selection"``,
    5. fallback: ``safe_name(prompt)[:32]``.
    """

    def test_examples_path_wins(self) -> None:
        self.assertEqual(
            infer_case_slug("run examples/tecnopolo/tecnopolo_r1.inp"),
            "tecnopolo",
        )

    def test_inp_filename_wins_when_no_examples_path(self) -> None:
        # The implementation truncates the captured group to 32 chars;
        # we only assert the prefix is preserved.
        slug = infer_case_slug("run mybasin.inp")
        self.assertEqual(slug, "mybasin")

    def test_plot_vocab_returns_plot_selection(self) -> None:
        self.assertEqual(infer_case_slug("plot the rainfall please"), "plot-selection")
        self.assertEqual(infer_case_slug("作图 一下"), "plot-selection")

    def test_unknown_prompt_falls_back_to_safe_name(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "cases").mkdir()
            with mock.patch(
                "agentic_swmm.case.case_registry.repo_root",
                return_value=repo,
            ):
                slug = infer_case_slug("what skills do you have?")
        self.assertNotEqual(slug, "plot-selection")
        # Fallback path uses safe_name and truncates to 32 chars.
        self.assertLessEqual(len(slug), 32)

    def test_registered_case_id_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_case_meta(repo, "mini-watershed")
            with mock.patch(
                "agentic_swmm.case.case_registry.repo_root",
                return_value=repo,
            ):
                slug = infer_case_slug("run the mini-watershed analysis")
        self.assertEqual(slug, "mini-watershed")


if __name__ == "__main__":
    unittest.main()
