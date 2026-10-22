"""The watch list and the stats of the proposals (ADR-0014, #565).

Evidence that counts but cannot propose: failed QA checks, error-level
and warning-level model diagnostics (no fix is known), negative lessons,
and promoted patterns that keep failing. Everything here is computed on
request from the store and the proposals folder; nothing is written.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory import session_db
from agentic_swmm.memory.proposals import MIN_CASES, MIN_RUNS, load_all
from agentic_swmm.memory.run_failures import _parse_stamp, read_run_failures
from agentic_swmm.memory.store import db_path_for, ledger_rows

#: A promoted pattern that failed again in this many runs since the promotion is flagged.
STILL_FAILING_RUNS = 3


def _loads(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in loaded] if isinstance(loaded, list) else []
    return []


def _runs_rows(store_dir: Path) -> list[dict[str, Any]]:
    db_path = db_path_for(Path(store_dir))
    if not db_path.is_file():
        return []
    with session_db.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        wanted = [c for c in ("run_id", "case_name", "qa_failed_checks", "diagnostic_errors", "diagnostic_warnings", "generated_at_utc") if c in columns]
        rows = conn.execute(f"SELECT {', '.join(wanted)} FROM runs").fetchall()
    return [dict(zip(wanted, row)) for row in rows]


def _group(entries: list[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
    """``(key, run_id, case, stamp)`` tuples into one entry per key."""
    by_key: dict[str, dict[str, Any]] = {}
    for key, run_id, case, stamp in entries:
        item = by_key.setdefault(key, {"id": key, "runs": set(), "cases": set(), "last": ""})
        item["runs"].add(run_id)
        if case:
            item["cases"].add(case)
        if stamp and stamp > item["last"]:
            item["last"] = stamp
    out = []
    for item in by_key.values():
        runs, cases = len(item["runs"]), len(item["cases"])
        out.append({
            "id": item["id"], "runs": runs, "cases": cases, "last": item["last"],
            "cleared_gate": runs >= MIN_RUNS and cases >= MIN_CASES,
        })
    return sorted(out, key=lambda item: (-item["runs"], item["id"]))


def watch_list(store_dir: Path, proposals_dir: Path | None = None) -> dict[str, Any]:
    """What keeps showing without a known fix, and what was promoted but still fails."""
    store_dir = Path(store_dir)
    qa: list[tuple[str, str, str, str]] = []
    errors: list[tuple[str, str, str, str]] = []
    warnings: list[tuple[str, str, str, str]] = []
    for row in _runs_rows(store_dir):
        run_id, case, stamp = str(row.get("run_id") or ""), str(row.get("case_name") or ""), str(row.get("generated_at_utc") or "")
        qa += [(check, run_id, case, stamp) for check in _loads(row.get("qa_failed_checks"))]
        errors += [(d, run_id, case, stamp) for d in _loads(row.get("diagnostic_errors"))]
        warnings += [(d, run_id, case, stamp) for d in _loads(row.get("diagnostic_warnings"))]
    lessons = [
        (str(row.get("lesson_type") or "lesson"), str(row.get("run_id") or ""), str(row.get("case_name") or ""), str(row.get("recorded_at") or ""))
        for row in ledger_rows(store_dir / "negative_lessons.jsonl")
    ]
    return {
        "qa_failed_checks": _group(qa),
        "diagnostic_errors": _group(errors),
        "diagnostic_warnings": _group(warnings),
        "negative_lessons": _group(lessons),
        "promoted_still_failing": promoted_still_failing(store_dir, proposals_dir),
    }


def promoted_still_failing(store_dir: Path, proposals_dir: Path | None = None) -> list[dict[str, Any]]:
    """Promoted proposals whose pattern failed again in enough runs since the promotion."""
    rows = read_run_failures(Path(store_dir) / "run_failures.jsonl")
    out = []
    for proposal in load_all(proposals_dir):
        if proposal.status != "promoted" or proposal.kind == "fact":
            continue
        pattern = str((proposal.evidence or {}).get("pattern") or "")
        since = _parse_stamp(proposal.decided_utc)
        if not pattern or since is None:
            continue
        runs = {
            row.run_id for row in rows
            if row.pattern == pattern and (stamp := _parse_stamp(row.recorded_at)) is not None and stamp > since
        }
        if len(runs) >= STILL_FAILING_RUNS:
            out.append({"id": proposal.id, "target": proposal.target, "pattern": pattern, "runs_since": len(runs), "promoted_utc": proposal.decided_utc})
    return out


def proposal_stats(store_dir: Path, proposals_dir: Path | None = None) -> dict[str, Any]:
    """Proposals, promotions and rejections per month; recurrence after promotion; drafted versus template promotions."""
    proposals = load_all(proposals_dir)
    rows = read_run_failures(Path(store_dir) / "run_failures.jsonl")
    months: dict[str, dict[str, int]] = defaultdict(lambda: {"proposed": 0, "promoted": 0, "rejected": 0})
    recurrence: list[dict[str, Any]] = []
    drafted = template = 0
    for proposal in proposals:
        months[proposal.created_utc[:7] or "unknown"]["proposed"] += 1
        if proposal.status in ("promoted", "rejected"):
            months[proposal.decided_utc[:7] or "unknown"][proposal.status] += 1
        if proposal.status == "promoted" and proposal.kind != "fact":
            if proposal.drafted_by:
                drafted += 1
            else:
                template += 1
            pattern = str((proposal.evidence or {}).get("pattern") or "")
            since = _parse_stamp(proposal.decided_utc)
            if pattern and since is not None:
                after = {row.run_id for row in rows if row.pattern == pattern and (s := _parse_stamp(row.recorded_at)) is not None and s > since}
                recurrence.append({"id": proposal.id, "target": proposal.target, "runs_since_promotion": len(after)})
    return {
        "months": {month: dict(counts) for month, counts in sorted(months.items())},
        "recurrence_after_promotion": recurrence,
        "promoted_drafted": drafted,
        "promoted_template": template,
    }


# --- rendering ------------------------------------------------------------


def _fmt(entry: dict[str, Any]) -> str:
    mark = "  <- cleared the gate, no fix known" if entry["cleared_gate"] else ""
    return f"  {entry['id']:<40} {entry['runs']:>3} run(s) {entry['cases']:>3} case(s)  last {entry['last'][:10] or '?'}{mark}"


def render_watch_list(data: dict[str, Any]) -> str:
    lines = ["Watch list (evidence without a known fix; computed from the store):"]
    for title, key in (
        ("QA checks that failed", "qa_failed_checks"),
        ("Model diagnostics, error level", "diagnostic_errors"),
        ("Model diagnostics, warning level", "diagnostic_warnings"),
        ("Negative lessons", "negative_lessons"),
    ):
        entries = data.get(key) or []
        lines.append(f"{title}: {len(entries)}")
        lines += [_fmt(entry) for entry in entries[:20]]
    flagged = data.get("promoted_still_failing") or []
    lines.append(f"Promoted but still failing: {len(flagged)}")
    lines += [f"  {item['id']} {item['target']}: {item['runs_since']} run(s) since {item['promoted_utc'][:10]}  {item['pattern'][:60]}" for item in flagged]
    return "\n".join(lines)


def render_stats(data: dict[str, Any]) -> str:
    lines = ["Proposal stats:", f"  {'month':<9} {'proposed':>9} {'promoted':>9} {'rejected':>9}"]
    for month, counts in (data.get("months") or {}).items():
        lines.append(f"  {month:<9} {counts.get('proposed', 0):>9} {counts.get('promoted', 0):>9} {counts.get('rejected', 0):>9}")
    lines.append(f"  promoted from a drafted body: {data.get('promoted_drafted', 0)}; from the template: {data.get('promoted_template', 0)}")
    rec = data.get("recurrence_after_promotion") or []
    lines.append(f"  recurrence after promotion ({len(rec)} promoted pattern(s)):")
    lines += [f"    {item['id']} {item['target']}: {item['runs_since_promotion']} run(s) since" for item in rec]
    return "\n".join(lines)
