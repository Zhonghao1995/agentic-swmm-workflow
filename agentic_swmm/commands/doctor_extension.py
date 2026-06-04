"""``aiswmm doctor`` extension — memory stores, runtime knobs, grouped WARNs.

PRD-08 Phase A.1 (Cluster 2). Doctor historically reported 20+ rows
about Python / swmm5 / MCP routing but stayed silent about the three
trust-breakers a new user is most likely to hit:

1. **Memory stores** — the JSONL append-only stores and the hand-edited
   YAML libraries that back ``aiswmm transfer / cite / cite-param /
   storm --from-library``. A fresh PyPI install has none of them, and
   the existing ``doctor`` could not tell the user that.

2. **Runtime knobs** — the four opt-out env vars
   (``AISWMM_DISABLE_MEMORY_INFORMED``, ``AISWMM_DISABLE_SWMM_GATES``,
   ``AISWMM_DISABLE_HONESTY_LAYER`` (new), ``AISWMM_DISABLE_WELCOME``)
   plus ``AISWMM_MEMORY_DIR`` redirection. All of them existed only in
   source comments + ``docs/memory_runtime.md`` — a user who set one
   had no way to see it from ``doctor``.

3. **Grouped WARNs** — when 11 of 12 MCP servers drift to the same
   stale checkout, doctor printed 11 nearly-identical 250-char WARN
   rows. We collapse those into one row with a single remediation
   line.

This module is intentionally a pure data layer. The render functions
return strings (no IO); the dispatcher in ``commands/doctor.py``
prints them. ``--fix`` actions are subprocess commands; the dispatcher
handles the interactive prompt loop.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO


# ---------------------------------------------------------------------------
# Memory stores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryStoreStatus:
    """Snapshot of one memory store on disk.

    ``row_count`` is the number of records the store contains:

    * JSONL: number of non-empty lines.
    * YAML libraries: top-level key count under the primary block
      (``chicago_hyetographs`` for storm_library, etc.).
    * Markdown lessons: number of ``## `` headings.

    ``verified_count`` is populated for the two hand-curated libraries
    that have a "verified entry" concept (citations.yaml,
    storm_library.yaml). For the others it stays ``None``.

    ``severity`` maps onto doctor's existing column vocabulary:

    * ``"OK"`` — store exists and has at least one verifiable row.
    * ``"PARTIAL"`` — store exists but has nulls / placeholders that
      would make the dependent verb fail.
    * ``"EMPTY"`` — store exists but has zero rows.
    * ``"MISSING"`` — file does not exist.
    * ``"CORRUPT"`` — file exists but failed integrity validation
      (issue #204, used by the sessions.sqlite row).
    """

    name: str
    path: Path
    exists: bool
    row_count: int | None
    verified_count: int | None
    last_modified_utc: str | None
    severity: str
    remediation: str | None


def _count_jsonl(path: Path) -> int:
    """Count non-empty lines in a JSONL file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _count_md_headings(path: Path) -> int:
    """Count ``## `` (level-2) headings — one per lesson entry."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.startswith("## "))


def _load_yaml(path: Path) -> dict:
    """Best-effort YAML load. Returns ``{}`` on any failure."""
    try:
        # Local import keeps yaml optional for the rest of the module.
        import yaml  # type: ignore[import]
    except Exception:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _last_modified_utc(path: Path) -> str | None:
    """Return ``path``'s mtime as an ISO8601 UTC string, or ``None``."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _citations_verified_count(data: dict) -> tuple[int, int]:
    """Return ``(verified, total)`` over citation entries.

    "Verified" ≔ ``verified_by`` AND ``verified_on`` are non-empty
    strings AND ``authors`` does not contain the pending-verification
    placeholder ("<author-list-pending-verification>" etc.).
    """
    total = 0
    verified = 0
    for key, entry in data.items():
        if key == "schema_version" or not isinstance(entry, dict):
            continue
        total += 1
        authors = str(entry.get("authors") or "")
        verified_by = str(entry.get("verified_by") or "").strip()
        verified_on = str(entry.get("verified_on") or "").strip()
        if (
            verified_by
            and verified_on
            and "pending-verification" not in authors
        ):
            verified += 1
    return verified, total


def _storm_library_verified_count(data: dict) -> tuple[int, int]:
    """Return ``(usable, total)`` over chicago_hyetographs entries.

    "Usable" ≔ ``idf_params.{a,b,c}`` are all non-null AND
    ``peak_position`` is non-null.
    """
    entries = data.get("chicago_hyetographs") or {}
    if not isinstance(entries, dict):
        return 0, 0
    total = 0
    usable = 0
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        total += 1
        idf = entry.get("idf_params") or {}
        peak = entry.get("peak_position")
        if (
            isinstance(idf, dict)
            and idf.get("a") is not None
            and idf.get("b") is not None
            and idf.get("c") is not None
            and peak is not None
        ):
            usable += 1
    return usable, total


def _benchmarks_partial(data: dict) -> bool:
    """Walk every leaf in reference_benchmarks.yaml; True iff any is null.

    The library ships with most numeric leaves intentionally null
    pending citation verification — that's "PARTIAL" rather than "OK".
    """

    def has_null_leaf(node: Any) -> bool:
        if node is None:
            return True
        if isinstance(node, dict):
            return any(has_null_leaf(v) for k, v in node.items() if k != "citation")
        if isinstance(node, list):
            return any(has_null_leaf(v) for v in node)
        return False

    # Only walk the metric blocks, not the schema_version literal.
    return any(
        has_null_leaf(v)
        for k, v in data.items()
        if k != "schema_version" and isinstance(v, (dict, list))
    )


def collect_memory_store_status(memory_dir: Path) -> list[MemoryStoreStatus]:
    """Return one :class:`MemoryStoreStatus` per known memory store.

    ``memory_dir`` is the ``memory/modeling-memory/`` directory. The
    function is read-only; missing stores produce ``MISSING`` rows so
    a caller can render the report even on a brand-new install.

    Stores reported (fixed list — additions require a code change):

    * ``parametric_memory.jsonl``  — calibration provenance rows
    * ``calibration_memory.jsonl`` — accepted calibration rows
    * ``negative_lessons``         — md preferred, jsonl fallback
    * ``reference_benchmarks.yaml`` — partial when any leaf null
    * ``citations.yaml``           — partial when any entry pending
    * ``storm_library.yaml``       — partial when chicago entries null
    * ``project_overrides.yaml``   — optional, OK if missing

    The transient ``run_progress.json`` is intentionally not reported.
    """
    statuses: list[MemoryStoreStatus] = []

    # ---- 1. parametric_memory.jsonl
    p_path = memory_dir / "parametric_memory.jsonl"
    if p_path.exists():
        rc = _count_jsonl(p_path)
        statuses.append(
            MemoryStoreStatus(
                name="parametric_memory.jsonl",
                path=p_path,
                exists=True,
                row_count=rc,
                verified_count=None,
                last_modified_utc=_last_modified_utc(p_path),
                severity="OK" if rc > 0 else "EMPTY",
                remediation=(
                    None
                    if rc > 0
                    else "run `aiswmm run` against a real INP to populate"
                ),
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="parametric_memory.jsonl",
                path=p_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="run `aiswmm bootstrap memory`",
            )
        )

    # ---- 2. calibration_memory.jsonl
    c_path = memory_dir / "calibration_memory.jsonl"
    if c_path.exists():
        rc = _count_jsonl(c_path)
        statuses.append(
            MemoryStoreStatus(
                name="calibration_memory.jsonl",
                path=c_path,
                exists=True,
                row_count=rc,
                verified_count=None,
                last_modified_utc=_last_modified_utc(c_path),
                severity="OK" if rc > 0 else "EMPTY",
                remediation=(
                    None
                    if rc > 0
                    else "accept a calibration to populate"
                ),
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="calibration_memory.jsonl",
                path=c_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="run `aiswmm bootstrap memory`",
            )
        )

    # ---- 3. negative lessons: md preferred, jsonl fallback
    md_path = memory_dir / "negative_lessons.md"
    jsonl_path = memory_dir / "negative_lessons.jsonl"
    if md_path.exists():
        rc = _count_md_headings(md_path)
        statuses.append(
            MemoryStoreStatus(
                name="negative_lessons.md",
                path=md_path,
                exists=True,
                row_count=rc,
                verified_count=None,
                last_modified_utc=_last_modified_utc(md_path),
                severity="OK" if rc > 0 else "EMPTY",
                remediation=None
                if rc > 0
                else "lessons accumulate as runs fail; no manual action",
            )
        )
    elif jsonl_path.exists():
        rc = _count_jsonl(jsonl_path)
        statuses.append(
            MemoryStoreStatus(
                name="negative_lessons.jsonl",
                path=jsonl_path,
                exists=True,
                row_count=rc,
                verified_count=None,
                last_modified_utc=_last_modified_utc(jsonl_path),
                severity="OK" if rc > 0 else "EMPTY",
                remediation=None
                if rc > 0
                else "lessons accumulate as runs fail; no manual action",
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="negative_lessons.md",
                path=md_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="run `aiswmm bootstrap memory`",
            )
        )

    # ---- 4. reference_benchmarks.yaml
    rb_path = memory_dir / "reference_benchmarks.yaml"
    if rb_path.exists():
        data = _load_yaml(rb_path)
        partial = _benchmarks_partial(data) if data else True
        statuses.append(
            MemoryStoreStatus(
                name="reference_benchmarks.yaml",
                path=rb_path,
                exists=True,
                row_count=len([k for k in data if k != "schema_version"])
                if data
                else 0,
                verified_count=None,
                last_modified_utc=_last_modified_utc(rb_path),
                severity="PARTIAL" if partial else "OK",
                remediation=(
                    "populate null leaves after verifying matching "
                    "citations.yaml entries"
                )
                if partial
                else None,
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="reference_benchmarks.yaml",
                path=rb_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="ship from package or copy from repo",
            )
        )

    # ---- 5. citations.yaml
    cit_path = memory_dir / "citations.yaml"
    if cit_path.exists():
        data = _load_yaml(cit_path)
        verified, total = _citations_verified_count(data) if data else (0, 0)
        if total == 0:
            severity = "EMPTY"
            remediation = "populate citations.yaml with verified entries"
        elif verified == 0:
            severity = "PARTIAL"
            remediation = (
                "verify pending entries (set `verified_by` and "
                "`verified_on`)"
            )
        elif verified < total:
            severity = "PARTIAL"
            remediation = f"{total - verified} entries still pending verification"
        else:
            severity = "OK"
            remediation = None
        statuses.append(
            MemoryStoreStatus(
                name="citations.yaml",
                path=cit_path,
                exists=True,
                row_count=total,
                verified_count=verified,
                last_modified_utc=_last_modified_utc(cit_path),
                severity=severity,
                remediation=remediation,
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="citations.yaml",
                path=cit_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="ship from package or copy from repo",
            )
        )

    # ---- 6. storm_library.yaml
    sl_path = memory_dir / "storm_library.yaml"
    if sl_path.exists():
        data = _load_yaml(sl_path)
        usable, total = _storm_library_verified_count(data) if data else (0, 0)
        if total == 0:
            severity = "EMPTY"
            remediation = "add chicago_hyetographs entries"
        elif usable == 0:
            severity = "PARTIAL"
            remediation = (
                f"{total} entries have null idf_params; populate one to enable "
                "`storm --from-library`"
            )
        elif usable < total:
            severity = "PARTIAL"
            remediation = f"{total - usable} entries still have null leaves"
        else:
            severity = "OK"
            remediation = None
        statuses.append(
            MemoryStoreStatus(
                name="storm_library.yaml",
                path=sl_path,
                exists=True,
                row_count=total,
                verified_count=usable,
                last_modified_utc=_last_modified_utc(sl_path),
                severity=severity,
                remediation=remediation,
            )
        )
    else:
        statuses.append(
            MemoryStoreStatus(
                name="storm_library.yaml",
                path=sl_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="MISSING",
                remediation="ship from package or copy from repo",
            )
        )

    # ---- 7. project_overrides.yaml (optional — OK if missing)
    po_path = memory_dir / "project_overrides.yaml"
    if po_path.exists():
        data = _load_yaml(po_path)
        rc = len([k for k in data if k != "schema_version"]) if data else 0
        statuses.append(
            MemoryStoreStatus(
                name="project_overrides.yaml",
                path=po_path,
                exists=True,
                row_count=rc,
                verified_count=None,
                last_modified_utc=_last_modified_utc(po_path),
                severity="OK",
                remediation=None,
            )
        )
    else:
        # Missing is fine — this is an optional overlay.
        statuses.append(
            MemoryStoreStatus(
                name="project_overrides.yaml",
                path=po_path,
                exists=False,
                row_count=None,
                verified_count=None,
                last_modified_utc=None,
                severity="OK",
                remediation="optional overlay; no action required",
            )
        )

    return statuses


# ---------------------------------------------------------------------------
# sessions.sqlite (issue #204)
# ---------------------------------------------------------------------------


def _format_size(size_bytes: int | None) -> str:
    """Render a byte count as ``X.X KB`` / ``X.X MB`` / ``X.X GB``.

    Sized down to KB for tiny dev databases; the doctor row prefers a
    compact display that still tells the user roughly how much room
    the store occupies on disk.
    """
    if size_bytes is None:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def collect_sessions_db_status(runs_dir: Path) -> MemoryStoreStatus:
    """Return the doctor row for ``runs_dir / sessions.sqlite``.

    Three branches per the issue spec:

    * ``state == "absent"`` -> ``OK`` row with a "will be created on
      first session" remediation hint (no real action required).
    * ``state == "ok"`` -> ``OK`` row with row counts encoded as
      ``row_count`` (sessions) + ``verified_count`` (messages) so the
      shared renderer naturally produces
      ``N sessions, M messages, X.X MB``.
    * ``state == "corrupt"`` -> ``CORRUPT`` row with a one-line
      remediation pointing at ``aiswmm memory repair-sessions``.
    """
    from agentic_swmm.memory import session_db

    db_path = runs_dir / "sessions.sqlite"
    report = session_db.integrity_check(db_path)

    if report.state == "absent":
        return MemoryStoreStatus(
            name="sessions.sqlite",
            path=db_path,
            exists=False,
            row_count=None,
            verified_count=None,
            last_modified_utc=None,
            severity="OK",
            remediation="file absent (will be created on first session)",
        )

    if report.state == "ok":
        last = _last_modified_utc(db_path)
        return MemoryStoreStatus(
            name="sessions.sqlite",
            path=db_path,
            exists=True,
            row_count=report.session_count,
            verified_count=report.message_count,
            last_modified_utc=last,
            severity="OK",
            # Stash the on-disk size in remediation? No: the renderer
            # only appends remediation when severity != "OK". We need a
            # dedicated formatter for this row, so we tag the size in a
            # synthetic OK-state remediation that the custom renderer
            # picks up.
            remediation=None,
        )

    if report.state == "unreadable":
        # Issue #204 review HIGH finding: file may be perfectly healthy
        # but locked / permission-denied / transient I/O. Do NOT advise
        # `repair-sessions` here — that would let the user overwrite a
        # healthy DB. Use a distinct severity + a permissions-focused
        # remediation hint instead.
        first_error = report.errors[0] if report.errors else "cannot read file"
        return MemoryStoreStatus(
            name="sessions.sqlite",
            path=db_path,
            exists=True,
            row_count=None,
            verified_count=None,
            last_modified_utc=_last_modified_utc(db_path),
            severity="UNREADABLE",
            remediation=(
                f"file is unreadable ({first_error.split(':')[0]}); "
                "fix file permissions or wait for another writer to release; "
                "do NOT run repair-sessions (the DB may be healthy)"
            ),
        )

    # state == "corrupt"
    corrupt_pages = len(report.errors)
    return MemoryStoreStatus(
        name="sessions.sqlite",
        path=db_path,
        exists=True,
        row_count=None,
        verified_count=None,
        last_modified_utc=_last_modified_utc(db_path),
        severity="CORRUPT",
        remediation=(
            f"integrity check failed ({corrupt_pages} corrupt pages); "
            "run aiswmm memory repair-sessions"
        ),
    )


def render_sessions_sqlite_row(status: MemoryStoreStatus) -> str:
    """Render the ``sessions.sqlite`` doctor row as a single line.

    The shared :func:`render_memory_stores_section` renderer is fine
    for the absent / corrupt branches, but the OK branch wants the
    on-disk size appended ("X.X MB"). We compute that here and emit
    one row in the exact shape the issue's acceptance criteria spell
    out.
    """
    if status.severity == "CORRUPT":
        detail = status.remediation or ""
        return f"  CORRUPT {status.name:30} - {detail}".rstrip()
    if status.severity == "UNREADABLE":
        # Issue #204 review HIGH finding: distinct from CORRUPT so the
        # user does not run the destructive repair-sessions verb
        # against a (possibly healthy) DB that just has a permissions
        # or lock problem.
        detail = status.remediation or ""
        return f"  WARN    {status.name:30} - {detail}".rstrip()
    if not status.exists:
        return f"  OK      {status.name:30} - {status.remediation}".rstrip()

    # OK + exists: build the "N sessions, M messages, X.X MB" detail.
    bits: list[str] = []
    if status.row_count is not None:
        word = "session" if status.row_count == 1 else "sessions"
        bits.append(f"{status.row_count} {word}")
    if status.verified_count is not None:
        word = "message" if status.verified_count == 1 else "messages"
        bits.append(f"{status.verified_count} {word}")
    try:
        size = status.path.stat().st_size
        bits.append(_format_size(size))
    except OSError:  # pragma: no cover - file vanished mid-render
        pass
    detail = ", ".join(bits)
    return f"  OK      {status.name:30} - {detail}".rstrip()


# ---------------------------------------------------------------------------
# LLM provider (PRD-09)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMProviderStatus:
    """Snapshot of the two API-key LLM backends for the doctor report.

    ``openai_key_present`` / ``anthropic_key_present`` reflect whether
    each provider's API key is reachable (env var, ``~/.aiswmm/env``, or
    the ``[<provider>]`` config section — the same tiers the preflight
    uses). ``default_provider`` is the configured ``provider.default``.
    """

    default_provider: str
    openai_key_present: bool
    anthropic_key_present: bool


def collect_llm_provider_status() -> LLMProviderStatus:
    """Return a :class:`LLMProviderStatus` for the doctor report.

    Read-only: probes whether each provider's API key is reachable and
    reads ``provider.default`` from config. Any failure resolving the
    config falls back to :data:`DEFAULT_PROVIDER` so the row always
    renders.
    """
    from agentic_swmm.agent.provider_preflight import provider_key_present

    try:
        from agentic_swmm.config import DEFAULT_PROVIDER, load_config

        default_provider = str(load_config().get("provider.default", DEFAULT_PROVIDER))
    except Exception:  # pragma: no cover - defensive; config is shallow
        from agentic_swmm.config import DEFAULT_PROVIDER

        default_provider = DEFAULT_PROVIDER
    return LLMProviderStatus(
        default_provider=default_provider,
        openai_key_present=provider_key_present("openai"),
        anthropic_key_present=provider_key_present("anthropic"),
    )


def render_llm_provider_section(status: LLMProviderStatus) -> str:
    """Produce the printable "LLM provider" doctor section.

    Two API-key rows — OpenAI (default) and Anthropic (opt-in). Output
    shape::

        LLM provider (default: openai):
          OpenAI API key        OPENAI_API_KEY      - present
          Anthropic API key     ANTHROPIC_API_KEY   - absent (opt-in: aiswmm login --anthropic)
    """
    openai_state = (
        "present" if status.openai_key_present else "absent (set it: aiswmm login --openai)"
    )
    anthropic_state = (
        "present"
        if status.anthropic_key_present
        else "absent (opt-in: aiswmm login --anthropic)"
    )
    lines = [
        f"LLM provider (default: {status.default_provider}):",
        f"  {'OpenAI API key':21} {'OPENAI_API_KEY':19} - {openai_state}",
        f"  {'Anthropic API key':21} {'ANTHROPIC_API_KEY':19} - {anthropic_state}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Opt-out flags
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptOutFlagStatus:
    """One entry in the doctor "Runtime knobs" section."""

    env_name: str
    current_value: str | None
    description: str


# Knobs whose value is a secret and must never be echoed to stdout (doctor
# prints presence only for these). Config flags below are NOT secret — seeing
# their value (e.g. SET=1) is useful.
_SECRET_KNOBS: frozenset[str] = frozenset({"ANTHROPIC_API_KEY"})


_OPTOUT_FLAGS: tuple[tuple[str, str], ...] = (
    (
        "ANTHROPIC_API_KEY",
        "API key for the opt-in anthropic provider (Anthropic Messages API)",
    ),
    (
        "AISWMM_DISABLE_MEMORY_INFORMED",
        "disable memory-informed policy decisions (LLM-only planner)",
    ),
    (
        "AISWMM_DISABLE_SWMM_GATES",
        "disable preflight/postflight gates (run goes through unconditionally)",
    ),
    (
        "AISWMM_DISABLE_HONESTY_LAYER",
        "disable post-run rpt-error scan; legacy 'exit 0 on SWMM ERROR' path",
    ),
    (
        "AISWMM_DISABLE_WELCOME",
        "skip the first-run / returning-user welcome banner",
    ),
    (
        "AISWMM_MEMORY_DIR",
        "redirect memory stores to a different directory (path override)",
    ),
)


def collect_optout_status() -> list[OptOutFlagStatus]:
    """Snapshot every documented opt-out env var.

    ``current_value`` is ``None`` when the var is unset; otherwise the
    raw string (so the user can see exactly what they exported).
    """
    out: list[OptOutFlagStatus] = []
    for name, description in _OPTOUT_FLAGS:
        out.append(
            OptOutFlagStatus(
                env_name=name,
                current_value=os.environ.get(name),
                description=description,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Grouped WARNs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupedWarnRow:
    """N rows collapsed into one summary line."""

    summary: str
    representative_remediation: str
    member_names: list[str] = field(default_factory=list)


# Strip a leading "<entity-name> - " segment so two WARN details with
# different entities but identical remainder text collapse into one
# group. The MCP-drift WARN format is
# "mcp.json: <name> - mcp.json routes <name> to a different checkout
# (<path>). Re-run ..." — both the entity name (between "mcp.json: "
# and " - ") and the inline ``<name>`` mention must be normalized.
_MCP_ENTITY_RE = re.compile(r"^mcp\.json:\s+(\S+)$")


def _mcp_drift_normalized_message(detail: str) -> str | None:
    """If ``detail`` is the MCP-drift remediation string, return its
    drift-target form (the launcher path) so two rows with different
    server names but the same launcher collapse together.

    Returns ``None`` for non-matching details.
    """
    # The remediation string is stable but the embedded server name
    # varies. Capture the path between the parens and use that as the
    # group key.
    m = re.search(
        r"mcp\.json routes \S+ to a different checkout \(([^)]+)\)\..*"
        r"aiswmm setup --refresh-mcp",
        detail,
    )
    if not m:
        return None
    return f"mcp.json drift -> {m.group(1)}"


def group_identical_warns(rows: list[dict]) -> list[Any]:
    """Walk doctor's WARN rows; collapse identical-cause groups.

    ``rows`` is a list of dicts with the shape ``{"name": ..., "passed":
    bool, "detail": str, "required": bool}``. Rows that share the same
    drift-normalized message are merged into a single
    :class:`GroupedWarnRow`. Rows that don't match the MCP-drift pattern
    pass through unchanged.

    The return list preserves input order: when N rows collapse, the
    grouped row appears at the position of the first member.
    """
    groups: dict[str, GroupedWarnRow] = {}
    group_first_position: dict[str, int] = {}
    out: list[Any] = []

    for index, row in enumerate(rows):
        detail = row.get("detail", "")
        key = _mcp_drift_normalized_message(detail)
        if key is None:
            out.append(row)
            continue
        name = str(row.get("name") or "?")
        # Strip the "mcp.json: " prefix on the server name display.
        m = _MCP_ENTITY_RE.match(name)
        entity = m.group(1) if m else name
        if key not in groups:
            groups[key] = GroupedWarnRow(
                summary=f"{key}",
                representative_remediation=(
                    "Run `aiswmm setup --refresh-mcp` to align all "
                    "drifted servers with the active install."
                ),
                member_names=[entity],
            )
            group_first_position[key] = len(out)
            out.append(groups[key])
        else:
            groups[key].member_names.append(entity)

    # Tidy up summaries to include the member count once all members
    # are known.
    for key, group in groups.items():
        pos = group_first_position[key]
        n = len(group.member_names)
        out[pos] = GroupedWarnRow(
            summary=f"{n} MCP server{'s' if n != 1 else ''} drift to {key.replace('mcp.json drift -> ', '')}",
            representative_remediation=group.representative_remediation,
            member_names=list(group.member_names),
        )

    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_memory_stores_section(
    statuses: list[MemoryStoreStatus],
) -> str:
    """Produce the printable "Memory stores" section.

    Output shape::

        Memory stores (7 known, 4 OK, 2 MISSING, 1 PARTIAL):
          OK      parametric_memory.jsonl  - 12 rows, last 2026-05-19T03:12:14Z
          MISSING citations.yaml           - run `aiswmm bootstrap memory`
          ...
    """
    if not statuses:
        return "Memory stores: (no known stores)"
    severities = {"OK": 0, "MISSING": 0, "PARTIAL": 0, "EMPTY": 0, "CORRUPT": 0, "UNREADABLE": 0}
    for s in statuses:
        severities[s.severity] = severities.get(s.severity, 0) + 1
    header_parts = [f"{n} {label}" for label, n in severities.items() if n]
    header = (
        f"Memory stores ({len(statuses)} known, "
        f"{', '.join(header_parts) if header_parts else '0 reported'}):"
    )
    lines = [header]
    for s in statuses:
        # sessions.sqlite has its own renderer: it needs the on-disk
        # size formatted compactly and the "N sessions, M messages"
        # phrasing instead of the generic "N rows".
        if s.name == "sessions.sqlite":
            lines.append(render_sessions_sqlite_row(s))
            continue

        bits: list[str] = []
        if s.row_count is not None:
            row_word = "row" if s.row_count == 1 else "rows"
            bits.append(f"{s.row_count} {row_word}")
        if s.verified_count is not None:
            bits.append(f"{s.verified_count} verified")
        if s.last_modified_utc:
            bits.append(f"last {s.last_modified_utc}")
        if s.remediation and s.severity != "OK":
            bits.append(s.remediation)
        detail = ", ".join(bits) if bits else ""
        lines.append(
            f"  {s.severity:7} {s.name:30} - {detail}".rstrip()
        )
    return "\n".join(lines)


def render_runtime_knobs_section(
    statuses: list[OptOutFlagStatus],
) -> str:
    """Produce the printable "Runtime knobs" section.

    Output shape::

        Runtime knobs:
          UNSET   AISWMM_DISABLE_MEMORY_INFORMED       - disable memory-...
          SET=1   AISWMM_DISABLE_HONESTY_LAYER         - disable post-run ...
    """
    if not statuses:
        return "Runtime knobs: (none documented)"
    lines = ["Runtime knobs:"]
    for s in statuses:
        if s.env_name in _SECRET_KNOBS:
            # Never echo a secret's value to stdout. Show presence only.
            if s.current_value is None:
                state_col = "UNSET"
            elif s.current_value == "":
                state_col = "SET(empty)"
            else:
                state_col = "SET"
        elif s.current_value is None:
            state_col = "UNSET"
        else:
            state_col = f"SET={s.current_value}"
        lines.append(
            f"  {state_col:10} {s.env_name:34} - {s.description}"
        )
    return "\n".join(lines)


def _terminal_width(default: int = 80) -> int:
    """Best-effort terminal width detection for WARN wrapping.

    Reads ``shutil.get_terminal_size()`` (which itself respects
    ``COLUMNS``). Falls back to ``default`` on any failure — wrapping
    at 80 columns is the right answer for the vast majority of
    terminals.
    """
    import shutil

    try:
        size = shutil.get_terminal_size(fallback=(default, 24))
    except (OSError, ValueError):
        return default
    width = int(size.columns or default)
    return width if width > 20 else default


def _wrap_warn_detail(
    severity: str,
    name: str,
    detail: str,
    *,
    width: int | None = None,
) -> list[str]:
    """Wrap a long WARN detail under a 2-space hanging indent.

    Returns one-or-more output lines. The first line is
    ``  {severity:7} {name} - <first chunk>`` when ``name`` is non-
    empty; otherwise the format collapses to
    ``  {severity:7} <first chunk>`` to match the existing
    grouped-WARN render (which carries the summary directly with no
    "name - " prefix). Continuation lines start with the column where
    the detail began (so they line up visually under the message
    rather than under the severity).

    The wrap only fires when the full text exceeds the available
    width; short rows are emitted verbatim.
    """
    if width is None:
        width = _terminal_width()
    if name:
        head = f"  {severity:7} {name} - "
    else:
        head = f"  {severity:7} "
    body_width = max(width - len(head), 20)
    full = head + detail
    if len(full) <= width:
        return [full]
    # Tokenise on whitespace; greedy-pack tokens onto each line. The
    # continuation indent is the column where the detail begins so
    # the wrapped lines visually nest under the first detail chunk.
    indent = " " * len(head)
    tokens = detail.split()
    if not tokens:
        return [full]
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip() if current else token
        if len(candidate) <= body_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            # Token longer than body_width: emit it on its own
            # (never split inside a token).
            if len(token) > body_width:
                lines.append(token)
                current = ""
            else:
                current = token
    if current:
        lines.append(current)
    out: list[str] = [head + lines[0]]
    for line in lines[1:]:
        out.append(indent + line)
    return out


def render_grouped_warns_section(
    rows: list[Any], *, width: int | None = None
) -> str:
    """Render grouped WARN rows under an "Issues" header.

    Returns empty string when ``rows`` is empty so the caller can skip
    the header.

    ``width`` controls the wrap point for long WARN details (PRD-08
    Phase B / audit #28). When ``None`` we honour the active
    terminal's reported width via ``shutil.get_terminal_size``.
    """
    if not rows:
        return ""
    lines = ["Issues:"]
    for row in rows:
        if isinstance(row, GroupedWarnRow):
            lines.extend(
                _wrap_warn_detail("WARN", "", row.summary, width=width)
            )
            if row.member_names:
                lines.append(
                    f"          members: {', '.join(row.member_names)}"
                )
            lines.append(f"          {row.representative_remediation}")
        else:
            name = row.get("name", "?")
            detail = row.get("detail", "")
            required = row.get("required", False)
            severity = "MISSING" if required and not row.get("passed") else "WARN"
            lines.extend(_wrap_warn_detail(severity, name, detail, width=width))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# --fix actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixAction:
    """One remediation doctor can run on the user's behalf."""

    label: str
    command: list[str]
    triggers: list[str]
    interactive_confirm: bool = True


def collect_fix_actions(doctor_report: dict) -> list[FixAction]:
    """Walk the doctor report; return safe remediations doctor can apply.

    Currently supports:

    * MCP-drift collapse → ``aiswmm setup --refresh-mcp``
    * Missing memory stores → ``aiswmm bootstrap memory``

    The report shape (see :func:`commands.doctor.main`) is::

        {
            "checks": [{"name": ..., "passed": bool, "detail": str, "required": bool}, ...],
            "memory_stores": [MemoryStoreStatus, ...],
            "optout_status": [OptOutFlagStatus, ...],
            "grouped_warns": [GroupedWarnRow | dict, ...],
        }
    """
    actions: list[FixAction] = []

    # ---- MCP drift?
    drifted_servers: list[str] = []
    for row in doctor_report.get("grouped_warns", []):
        if isinstance(row, GroupedWarnRow):
            drifted_servers.extend(row.member_names)
    if not drifted_servers:
        # Fallback: scan raw checks for the drift detail substring.
        for check in doctor_report.get("checks", []):
            detail = check.get("detail", "")
            if (
                "mcp.json routes" in detail
                and "different checkout" in detail
            ):
                drifted_servers.append(str(check.get("name", "")))
    if drifted_servers:
        actions.append(
            FixAction(
                label="Refresh mcp.json to current install",
                command=["aiswmm", "setup", "--refresh-mcp"],
                triggers=drifted_servers,
                interactive_confirm=True,
            )
        )

    # ---- Missing memory stores?
    missing_stores = [
        s
        for s in doctor_report.get("memory_stores", [])
        if isinstance(s, MemoryStoreStatus) and s.severity == "MISSING"
    ]
    # Only offer bootstrap when at least one of the four core JSONL/MD
    # stores is missing — the YAML libraries ship with the package and
    # don't need bootstrapping.
    bootstrap_candidates = {
        "parametric_memory.jsonl",
        "calibration_memory.jsonl",
        "negative_lessons.jsonl",
        "negative_lessons.md",
    }
    missing_bootstrap = [s for s in missing_stores if s.name in bootstrap_candidates]
    if missing_bootstrap:
        actions.append(
            FixAction(
                label="Create missing memory stores",
                command=["aiswmm", "bootstrap", "memory"],
                triggers=[s.name for s in missing_bootstrap],
                interactive_confirm=True,
            )
        )

    return actions


def apply_fix_actions(
    actions: list[FixAction],
    *,
    yes: bool = False,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    subprocess_runner: Any = None,
) -> dict[str, str]:
    """Apply each :class:`FixAction`, prompting unless ``yes=True``.

    Returns a dict ``{action.label: "applied" | "skipped" | "failed"}``.

    ``stdin``/``stdout`` default to the real streams; tests pass
    StringIO objects. ``subprocess_runner`` defaults to
    :func:`subprocess.run`; tests pass a stub recording the command.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if subprocess_runner is None:
        subprocess_runner = subprocess.run

    results: dict[str, str] = {}
    for action in actions:
        prompt = (
            f"\n* {action.label}\n"
            f"  Command: {' '.join(action.command)}\n"
            f"  Triggered by: {', '.join(action.triggers)}\n"
        )
        stdout.write(prompt)
        if action.interactive_confirm and not yes:
            stdout.write("  Apply now? [y/N] ")
            stdout.flush()
            response = stdin.readline().strip().lower()
            if response not in {"y", "yes"}:
                results[action.label] = "skipped"
                stdout.write("  skipped.\n")
                continue
        try:
            proc = subprocess_runner(
                action.command,
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # pragma: no cover - subprocess plumbing
            results[action.label] = "failed"
            stdout.write(f"  failed: {exc}\n")
            continue
        rc = getattr(proc, "returncode", 0)
        if rc == 0:
            results[action.label] = "applied"
            stdout.write("  applied.\n")
        else:
            results[action.label] = "failed"
            stdout.write(f"  failed (exit {rc}).\n")
    return results


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------


def memory_store_status_to_dict(s: MemoryStoreStatus) -> dict:
    return {
        "name": s.name,
        "path": str(s.path),
        "exists": s.exists,
        "row_count": s.row_count,
        "verified_count": s.verified_count,
        "last_modified_utc": s.last_modified_utc,
        "severity": s.severity,
        "remediation": s.remediation,
    }


def optout_status_to_dict(s: OptOutFlagStatus) -> dict:
    return {
        "env_name": s.env_name,
        "current_value": s.current_value,
        "description": s.description,
    }


def llm_provider_status_to_dict(s: LLMProviderStatus) -> dict:
    return {
        "default_provider": s.default_provider,
        "openai_key_present": s.openai_key_present,
        "anthropic_key_present": s.anthropic_key_present,
    }


def grouped_warn_to_dict(row: Any) -> dict:
    if isinstance(row, GroupedWarnRow):
        return {
            "kind": "group",
            "summary": row.summary,
            "representative_remediation": row.representative_remediation,
            "member_names": list(row.member_names),
        }
    return {"kind": "row", **row}


def fix_action_to_dict(action: FixAction) -> dict:
    return {
        "label": action.label,
        "command": list(action.command),
        "triggers": list(action.triggers),
        "interactive_confirm": action.interactive_confirm,
    }
