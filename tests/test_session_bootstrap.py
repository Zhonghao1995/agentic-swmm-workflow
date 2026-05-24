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
    bootstrap_prior_state,
    bootstrap_session_dir,
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


class BootstrapSessionDirTests(unittest.TestCase):
    """Issue #205 / Phase ``bootstrap_session_dir``.

    Produces the per-turn run/chat directory under the day's date_dir.
    Formerly ``runtime_loop._new_turn_dir`` — extracting it here lets
    the phase be exercised in isolation (no need to mock the entire
    ``run_interactive_shell`` graph).

    Contract:

    - returned path is ``<date_dir>/HHMMSS_<case-slug>_<kind>``,
    - if that exact path exists, a ``_2`` / ``_3`` / ... suffix is
      appended until a fresh name is found (no clobbering),
    - the path is *not* created on disk — that's the caller's
      responsibility (preserves the prior ``_new_turn_dir`` contract).
    """

    def test_returns_path_under_date_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            date_dir = Path(tmp)
            path = bootstrap_session_dir(date_dir, "run mybasin.inp", kind="run")
            self.assertEqual(path.parent, date_dir)

    def test_path_has_case_slug_and_kind(self) -> None:
        with TemporaryDirectory() as tmp:
            date_dir = Path(tmp)
            path = bootstrap_session_dir(date_dir, "run mybasin.inp", kind="run")
            # Format: HHMMSS_<slug>_<kind>
            self.assertRegex(path.name, r"^\d{6}_mybasin_run$")

    def test_chat_kind_renders_chat_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            date_dir = Path(tmp)
            path = bootstrap_session_dir(date_dir, "what skills do you have?", kind="chat")
            self.assertTrue(path.name.endswith("_chat"))

    def test_collision_appends_counter(self) -> None:
        with TemporaryDirectory() as tmp:
            date_dir = Path(tmp)
            first = bootstrap_session_dir(date_dir, "run mybasin.inp", kind="run")
            # Materialise the first dir so the second call collides.
            first.mkdir(parents=True)
            second = bootstrap_session_dir(date_dir, "run mybasin.inp", kind="run")
            self.assertNotEqual(first, second)
            self.assertTrue(second.name.endswith("_2"))

    def test_does_not_create_dir_on_disk(self) -> None:
        # Caller is responsible for mkdir; the phase is pure path-making.
        with TemporaryDirectory() as tmp:
            date_dir = Path(tmp)
            path = bootstrap_session_dir(date_dir, "run mybasin.inp", kind="run")
            self.assertFalse(path.exists())


class BootstrapPriorStateTests(unittest.TestCase):
    """Issue #205 / Phase ``bootstrap_prior_state``.

    Loads ``aiswmm_state.json`` from a prior run dir. The planner
    consumes this to skip re-introspection on continuation turns.
    Formerly ``runtime_loop._load_prior_session_state``.
    """

    def test_returns_none_when_no_active_run(self) -> None:
        self.assertIsNone(bootstrap_prior_state(None))

    def test_returns_none_when_state_file_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.assertIsNone(bootstrap_prior_state(run_dir))

    def test_loads_existing_state_payload(self) -> None:
        import json as _json

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "aiswmm_state.json").write_text(
                _json.dumps({"goal": "x", "skills_listed": True}),
                encoding="utf-8",
            )
            payload = bootstrap_prior_state(run_dir)
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload.get("goal"), "x")
            self.assertTrue(payload.get("skills_listed"))

    def test_returns_none_for_malformed_json(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "aiswmm_state.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(bootstrap_prior_state(run_dir))

    def test_returns_none_for_non_dict_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "aiswmm_state.json").write_text("[1,2,3]", encoding="utf-8")
            self.assertIsNone(bootstrap_prior_state(run_dir))


if __name__ == "__main__":
    unittest.main()
