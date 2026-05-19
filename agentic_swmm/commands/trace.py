"""``aiswmm trace <run-dir>`` — pretty-print run-level traces (PRD-08 Phase B #31).

The runtime writes two JSONL trace streams under every run directory:

* ``<run-dir>/agent_trace.jsonl`` — every planner/tool event the agent
  took during the run (session_start, planner_response, tool_call,
  tool_result, session_end, etc.). One line per event.
* ``<run-dir>/memory_trace.jsonl`` — every memory-lookup decision made
  by the audit hook / cross-watershed transfer / etc. One line per
  decision.

Before this command landed there was no CLI surface for either file —
users had to ``cat`` raw JSON or write a one-off ``jq`` invocation. The
trace command produces a one-line-per-event human-readable summary by
default, and lets the user keep the raw JSONL with ``--json`` if a
downstream pipeline already understands it.

Layout (default):

::

    2026-05-19T18:37:54Z  session_start    goal="hi" model=gpt-4o-mini
    2026-05-19T18:37:54Z  planner_response step=1 text="[mocked-llm-response]"
    2026-05-19T18:37:54Z  session_end      ok=true

``--source {agent,memory,both}`` filters which JSONL stream gets
consumed. ``--last N`` keeps only the most recent ``N`` events (default
20). ``--tail`` is the live-follow mode for long-running calibrations.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from agentic_swmm.agent import tui_chrome
from agentic_swmm.agent.flag_naming import (
    register_example_flag,
    register_json_flag,
    register_quiet_flag,
)


_TRACE_EXAMPLE = "aiswmm trace runs/2026-05-19/calib_001 --last 30"


# Trace files this command knows about. The order matters for the
# "both" mode: we interleave streams by timestamp, so the file that
# carries the older first event drives the initial line.
_AGENT_TRACE_NAME = "agent_trace.jsonl"
_MEMORY_TRACE_NAME = "memory_trace.jsonl"


# Fields we surface when pretty-printing a row. Keys not in this list
# are dropped from the one-line summary; they still show up in
# ``--json`` mode. The list is intentionally short — a trace stream
# carries dozens of fields, only a handful matter at a glance.
_INTERESTING_FIELDS: tuple[str, ...] = (
    "step",
    "goal",
    "model",
    "planner",
    "ok",
    "tool",
    "tool_name",
    "response_id",
    "skill_name",
    "decision",
    "iter_index",
    "best_objective_so_far",
    "summary",
    "source",
    "reason",
    "verb",
    "text",
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``aiswmm trace <run-dir>``."""
    parser = subparsers.add_parser(
        "trace",
        help=(
            "Pretty-print agent_trace.jsonl / memory_trace.jsonl from a "
            "run directory. Use --tail to follow a live run."
        ),
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Run directory containing the JSONL trace files.",
    )
    parser.add_argument(
        "--source",
        choices=("agent", "memory", "both"),
        default="both",
        help=(
            "Which trace stream to read. Default: both. ``agent`` reads "
            "only agent_trace.jsonl; ``memory`` only memory_trace.jsonl."
        ),
    )
    parser.add_argument(
        "--last",
        type=int,
        default=20,
        metavar="N",
        help="Show only the last N events (default 20). 0 means all.",
    )
    parser.add_argument(
        "--tail",
        action="store_true",
        help=(
            "Follow the trace files like ``tail -f``. The command does "
            "not exit until the user sends SIGINT."
        ),
    )
    register_json_flag(
        parser,
        help_text=(
            "Emit each event as one raw JSONL line on stdout instead "
            "of the human-readable summary. Use for ``jq`` pipelines."
        ),
    )
    register_quiet_flag(parser)
    register_example_flag(parser, example_text=_TRACE_EXAMPLE)
    parser.set_defaults(func=main)


def _trace_files_for(run_dir: Path, source: str) -> list[Path]:
    """Return the existing trace files that match ``source``.

    Missing files are silently dropped here; the empty list is what
    drives the "no trace events found" exit branch in ``main``.
    """
    candidates: list[Path] = []
    if source in ("agent", "both"):
        candidates.append(run_dir / _AGENT_TRACE_NAME)
    if source in ("memory", "both"):
        candidates.append(run_dir / _MEMORY_TRACE_NAME)
    return [p for p in candidates if p.is_file()]


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Yield non-empty JSON objects from ``path``.

    Malformed lines are dropped silently — the trace files are append-
    only and a partial line at EOF (from a crashed writer) is normal.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _event_timestamp(event: dict[str, Any]) -> str:
    """Best-effort extraction of a timestamp string from an event row.

    Most events carry ``timestamp_utc``; older rows may carry
    ``timestamp`` or be timestamp-free. We fall back to an empty
    string so the merge-sort stays stable.
    """
    for key in ("timestamp_utc", "timestamp", "ts"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _event_type(event: dict[str, Any]) -> str:
    """Best-effort extraction of the event-type label.

    Standardise on ``event``; older rows use ``event_type``. Falls
    back to ``?`` so the column always has SOMETHING to print.
    """
    for key in ("event", "event_type"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return "?"


def _format_field_value(value: Any) -> str:
    """Format a value compact-enough to fit on one line.

    Strings are quoted (so spaces in goals do not break the column).
    Lists / dicts collapse to a length hint to keep the line short.
    """
    if isinstance(value, str):
        # Truncate ridiculously long strings; the user can drop into
        # --json for the full text.
        truncated = value if len(value) <= 60 else value[:57] + "..."
        return json.dumps(truncated)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return f"len={len(value)}"
    if isinstance(value, dict):
        return f"len={len(value)}"
    return str(value)


def _format_event_line(
    event: dict[str, Any], *, use_colour: bool
) -> str:
    """Return the one-line summary for ``event``.

    Format::

        <timestamp> <event_type:padded> key1=value1 key2=value2 ...

    The event type column is padded to 18 chars so the key=value tail
    lines up across rows. Only fields in :data:`_INTERESTING_FIELDS`
    appear in the summary; everything else is hidden until the user
    drops into ``--json``.
    """
    ts = _event_timestamp(event)
    etype = _event_type(event)
    parts: list[str] = []
    for key in _INTERESTING_FIELDS:
        if key not in event:
            continue
        rendered = _format_field_value(event[key])
        parts.append(f"{key}={rendered}")
    detail = " ".join(parts)
    etype_col = etype.ljust(18)
    line = f"{ts}  {etype_col} {detail}".rstrip()
    if not use_colour:
        return line
    # Apply phosphor green to event-type column for easier scanning.
    coloured_etype = tui_chrome.phosphor_dim(etype_col)
    return f"{ts}  {coloured_etype} {detail}".rstrip()


def _collect_events(files: list[Path]) -> list[dict[str, Any]]:
    """Read all JSONL files and merge-sort by timestamp.

    Events without a timestamp sort to the start of the result
    (stable-sort on empty-string).
    """
    events: list[dict[str, Any]] = []
    for path in files:
        for event in _read_jsonl(path):
            # Stash the source filename so the --json output can carry
            # provenance even after the merge.
            event = dict(event)
            event.setdefault("_source_file", path.name)
            events.append(event)
    events.sort(key=_event_timestamp)
    return events


def _is_tty() -> bool:
    return sys.stdout.isatty()


def _emit_events(
    events: list[dict[str, Any]],
    *,
    as_json: bool,
    quiet: bool,
) -> None:
    use_colour = (not as_json) and _is_tty() and not tui_chrome.is_plain()
    for event in events:
        if as_json:
            sys.stdout.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            )
        else:
            sys.stdout.write(_format_event_line(event, use_colour=use_colour) + "\n")
    sys.stdout.flush()
    if quiet:
        return


def _tail_loop(
    files: list[Path],
    *,
    as_json: bool,
    quiet: bool,
    poll_interval_s: float = 0.5,
) -> int:
    """Follow ``files`` until SIGINT.

    Stores a per-file ``offset`` cursor and on each tick reads any new
    bytes, splits them by newline, and emits new events. The empty
    files-list case returns 0 immediately so the caller never spins
    waiting on a missing path.
    """
    if not files:
        return 0
    offsets: dict[Path, int] = {p: 0 for p in files}
    use_colour = (not as_json) and _is_tty() and not tui_chrome.is_plain()
    try:
        while True:
            for path in files:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size <= offsets[path]:
                    continue
                with path.open("rb") as handle:
                    handle.seek(offsets[path])
                    new_bytes = handle.read(size - offsets[path])
                offsets[path] = size
                text = new_bytes.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event = dict(event)
                    event.setdefault("_source_file", path.name)
                    if as_json:
                        sys.stdout.write(
                            json.dumps(event, ensure_ascii=False, sort_keys=True)
                            + "\n"
                        )
                    else:
                        sys.stdout.write(
                            _format_event_line(event, use_colour=use_colour) + "\n"
                        )
                sys.stdout.flush()
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        return 0


def main(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    quiet = bool(getattr(args, "quiet", False))
    as_json = bool(getattr(args, "json", False))
    if not run_dir.is_dir():
        print(f"error: run_dir is not a directory: {run_dir}", file=sys.stderr)
        return 1

    files = _trace_files_for(run_dir, args.source)
    if not files:
        if not quiet:
            print(
                f"no trace events found in {run_dir} "
                f"(source={args.source}).",
                file=sys.stderr,
            )
        return 0

    if args.tail:
        # Tail mode replays existing events first (so the user has
        # context) and then streams new ones. ``--last`` controls how
        # much history they see before the follow starts.
        events = _collect_events(files)
        if args.last and args.last > 0:
            events = events[-args.last :]
        _emit_events(events, as_json=as_json, quiet=quiet)
        return _tail_loop(files, as_json=as_json, quiet=quiet)

    events = _collect_events(files)
    if args.last and args.last > 0:
        events = events[-args.last :]
    _emit_events(events, as_json=as_json, quiet=quiet)
    return 0


__all__ = ["main", "register"]
