"""PRD-08 Phase B (#31): ``aiswmm trace <run-dir>`` CLI surface.

Covers the empty-run-dir, populated-run-dir, --last N, --source
filter, and --json passthrough paths. The follow-mode (``--tail``)
is exercised indirectly via the public API; the integration test for
the polling loop lives behind a separate unit test that mocks
``time.sleep`` to keep CI fast.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import main as cli_main


def _capture(argv: list[str]) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(argv) or 0
        except SystemExit as exc:
            code = int(exc.code or 0)
    return out.getvalue(), err.getvalue(), code


def _write_agent_trace(run_dir: Path, events: list[dict]) -> Path:
    path = run_dir / "agent_trace.jsonl"
    text = "\n".join(json.dumps(ev, sort_keys=True) for ev in events) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _write_memory_trace(run_dir: Path, events: list[dict]) -> Path:
    path = run_dir / "memory_trace.jsonl"
    text = "\n".join(json.dumps(ev, sort_keys=True) for ev in events) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


class EmptyRunDirTests(unittest.TestCase):
    def test_empty_run_dir_returns_0_with_no_events_message(self) -> None:
        with TemporaryDirectory() as tmp:
            argv = ["trace", str(tmp)]
            stdout, stderr, code = _capture(argv)
            self.assertEqual(code, 0)
            self.assertIn("no trace events found", stderr)
            self.assertEqual(stdout, "")

    def test_missing_run_dir_returns_1_with_error(self) -> None:
        argv = ["trace", "/tmp/definitely-not-a-real-dir-prd08"]
        _, stderr, code = _capture(argv)
        self.assertEqual(code, 1)
        self.assertIn("run_dir is not a directory", stderr)


class PrettyPrintTests(unittest.TestCase):
    def test_default_pretty_prints_events(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_agent_trace(
                run_dir,
                [
                    {
                        "event": "session_start",
                        "goal": "hi",
                        "model": "gpt-4o-mini",
                        "timestamp_utc": "2026-05-19T18:00:00Z",
                    },
                    {
                        "event": "planner_response",
                        "step": 1,
                        "text": "[mock]",
                        "timestamp_utc": "2026-05-19T18:00:01Z",
                    },
                ],
            )
            stdout, _, code = _capture(["trace", str(run_dir)])
            self.assertEqual(code, 0)
            self.assertIn("session_start", stdout)
            self.assertIn("planner_response", stdout)
            self.assertIn("2026-05-19T18:00:00Z", stdout)

    def test_last_keeps_only_n_most_recent(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            events = [
                {
                    "event": f"e{i}",
                    "timestamp_utc": f"2026-05-19T18:00:0{i}Z",
                }
                for i in range(8)
            ]
            _write_agent_trace(run_dir, events)
            stdout, _, code = _capture(
                ["trace", str(run_dir), "--last", "3"]
            )
            self.assertEqual(code, 0)
            # Only the three highest-timestamp events should appear.
            self.assertIn("e5", stdout)
            self.assertIn("e6", stdout)
            self.assertIn("e7", stdout)
            self.assertNotIn("e0", stdout)

    def test_source_memory_only_reads_memory_trace(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_agent_trace(
                run_dir,
                [{"event": "agent_only", "timestamp_utc": "2026-05-19T18:00:00Z"}],
            )
            _write_memory_trace(
                run_dir,
                [{"event": "memory_only", "timestamp_utc": "2026-05-19T18:00:01Z"}],
            )
            stdout, _, code = _capture(
                ["trace", str(run_dir), "--source", "memory"]
            )
            self.assertEqual(code, 0)
            self.assertIn("memory_only", stdout)
            self.assertNotIn("agent_only", stdout)

    def test_source_agent_only_reads_agent_trace(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_agent_trace(
                run_dir,
                [{"event": "agent_only", "timestamp_utc": "2026-05-19T18:00:00Z"}],
            )
            _write_memory_trace(
                run_dir,
                [{"event": "memory_only", "timestamp_utc": "2026-05-19T18:00:01Z"}],
            )
            stdout, _, code = _capture(
                ["trace", str(run_dir), "--source", "agent"]
            )
            self.assertEqual(code, 0)
            self.assertIn("agent_only", stdout)
            self.assertNotIn("memory_only", stdout)

    def test_source_both_merges_streams_by_timestamp(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_agent_trace(
                run_dir,
                [
                    {"event": "a1", "timestamp_utc": "2026-05-19T18:00:01Z"},
                    {"event": "a3", "timestamp_utc": "2026-05-19T18:00:03Z"},
                ],
            )
            _write_memory_trace(
                run_dir,
                [{"event": "m2", "timestamp_utc": "2026-05-19T18:00:02Z"}],
            )
            stdout, _, code = _capture(["trace", str(run_dir)])
            self.assertEqual(code, 0)
            # Expect chronological order by timestamp: a1, m2, a3.
            event_lines = [ln for ln in stdout.splitlines() if ln.strip()]
            event_types = [ln.split()[1] for ln in event_lines]
            self.assertEqual(event_types, ["a1", "m2", "a3"])


class JsonPassthroughTests(unittest.TestCase):
    def test_json_emits_one_object_per_line(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_agent_trace(
                run_dir,
                [
                    {
                        "event": "session_start",
                        "goal": "hi",
                        "timestamp_utc": "2026-05-19T18:00:00Z",
                    },
                    {
                        "event": "session_end",
                        "ok": True,
                        "timestamp_utc": "2026-05-19T18:00:01Z",
                    },
                ],
            )
            stdout, _, code = _capture(["trace", str(run_dir), "--json"])
            self.assertEqual(code, 0)
            lines = [ln for ln in stdout.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            for line in lines:
                payload = json.loads(line)
                self.assertIn("event", payload)


class HelpSurfaceTests(unittest.TestCase):
    def test_help_succeeds(self) -> None:
        stdout, _, code = _capture(["trace", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--source", stdout)
        self.assertIn("--last", stdout)
        self.assertIn("--tail", stdout)


if __name__ == "__main__":
    unittest.main()
