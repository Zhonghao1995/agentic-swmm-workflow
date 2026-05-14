"""Lifecycle metadata for failure_pattern blocks in lessons_learned.md.

Each ``## <pattern>`` section in ``memory/modeling-memory/lessons_learned.md``
carries an HTML-comment-fenced YAML preamble that records when the
pattern was first/last seen, how many runs contributed evidence, the
list of evidence run ids, an active/dormant/retired ``status``, a
``confidence_score``, and the ``half_life_days`` used in the decay
formula.

The fence is a *single* HTML comment whose first and last lines carry
sentinel tokens so we can locate the payload deterministically:

```
<!-- aiswmm-metadata
metadata:
  ...
/aiswmm-metadata -->
```

The whole block — sentinels and YAML payload — sits inside ONE
``<!-- ... -->`` comment so CommonMark / Obsidian preview render it as
invisible. The literal ``<!-- aiswmm-metadata`` and
``/aiswmm-metadata -->`` tokens are the fence markers we parse for.

This module provides four public helpers used by the audit hook, the
one-shot migration, and the summariser:

- :func:`read_metadata` — parse the YAML payload from one ``##`` block.
- :func:`write_metadata` — round-trip new metadata back into one block.
- :func:`read_all_patterns` — scan the whole markdown document.
- :func:`replace_pattern_block` — swap a single section in-place.
- :func:`compute_confidence` — exponential decay formula.
- :func:`update_metadata_for_run` — audit-end hook that bumps
  ``evidence_count`` / ``last_seen_utc`` / ``evidence_runs`` for matched
  patterns and recomputes ``confidence_score`` for every pattern.

ME-1 only writes metadata; it does NOT mutate ``status``. Lifecycle
transitions (active -> dormant -> retired) belong to ME-2 (#62).
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


class _StringTimestampLoader(yaml.SafeLoader):
    """SafeLoader that keeps ISO-8601 timestamps as strings.

    Default PyYAML auto-converts ``2026-03-01T10:23:00Z`` to a
    ``datetime`` object, which then round-trips weirdly when re-dumped.
    Stripping the timestamp resolver leaves the value as a plain string,
    so reads and writes stay symmetric.
    """


_StringTimestampLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in _StringTimestampLoader.yaml_implicit_resolvers.items()
}


METADATA_OPEN = "<!-- aiswmm-metadata"
METADATA_CLOSE = "/aiswmm-metadata -->"
DEFAULT_HALF_LIFE_DAYS = 90

# ``## <name>`` headings whose name is a snake_case identifier are
# treated as failure-pattern sections. Summary sections like
# ``## Repeated Failure Patterns`` or ``## Successful Practices`` are
# skipped because their headings carry spaces / mixed case.
_PATTERN_HEADING_RE = re.compile(
    r"^##\s+(?P<name>[a-z][a-z0-9_]*)\s*$", re.MULTILINE
)

_METADATA_FENCE_RE = re.compile(
    re.escape(METADATA_OPEN) + r"\n(?P<payload>.*?)\n" + re.escape(METADATA_CLOSE),
    re.DOTALL,
)
# Strict invariant: the fence is one contiguous HTML comment. Any
# stray ``-->`` inside the payload would close the comment early and
# leak the YAML body into rendered output, so we forbid it.


def read_metadata(pattern_block: str) -> dict[str, Any] | None:
    """Return the parsed metadata dict for ``pattern_block``.

    The block is the substring from ``## <name>`` up to (but not
    including) the next ``## `` heading. Returns ``None`` when the block
    has no metadata fence yet — callers treat this as "needs migration"
    or "skip" depending on context.
    """
    match = _METADATA_FENCE_RE.search(pattern_block)
    if not match:
        return None
    try:
        parsed = yaml.load(match.group("payload"), Loader=_StringTimestampLoader)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    meta = parsed.get("metadata")
    if not isinstance(meta, dict):
        return None
    return meta


def _format_metadata_yaml(meta: dict[str, Any]) -> str:
    """Serialise ``meta`` with stable key order and human-friendly types.

    Key order matches the schema in issue #61 (first_seen, last_seen,
    evidence_count, evidence_runs, status, confidence_score,
    half_life_days). Unknown keys land at the end in sorted order.
    """
    canonical = [
        "first_seen_utc",
        "last_seen_utc",
        "evidence_count",
        "evidence_runs",
        "status",
        "confidence_score",
        "half_life_days",
    ]
    ordered: dict[str, Any] = {}
    for key in canonical:
        if key in meta:
            ordered[key] = meta[key]
    for key in sorted(k for k in meta if k not in canonical):
        ordered[key] = meta[key]

    body = yaml.safe_dump(
        {"metadata": ordered},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip()
    return body


def write_metadata(pattern_block: str, meta: dict[str, Any]) -> str:
    """Round-trip ``meta`` back into ``pattern_block``.

    If the block already carries a fence, the fence is replaced
    in-place. Otherwise the fence is inserted directly after the
    heading and the leading blank line. Body content past the fence is
    preserved verbatim.
    """
    payload = _format_metadata_yaml(meta)
    fence = f"{METADATA_OPEN}\n{payload}\n{METADATA_CLOSE}"

    if _METADATA_FENCE_RE.search(pattern_block):
        return _METADATA_FENCE_RE.sub(fence, pattern_block, count=1)

    lines = pattern_block.splitlines(keepends=True)
    if not lines:
        return fence + "\n"

    # The block must start with `## <name>`. Insert the fence after the
    # heading and a single blank line.
    out: list[str] = [lines[0]]
    idx = 1
    # Preserve the immediate blank line after the heading if present.
    if idx < len(lines) and lines[idx].strip() == "":
        out.append(lines[idx])
        idx += 1
    else:
        out.append("\n")
    out.append(fence + "\n")
    # Ensure a blank line between fence and body.
    if idx < len(lines) and lines[idx].strip() != "":
        out.append("\n")
    out.extend(lines[idx:])
    return "".join(out)


def _iter_pattern_spans(
    markdown_text: str,
) -> Iterable[tuple[str, int, int]]:
    """Yield ``(name, start, end)`` spans for each ``## <pattern>`` block.

    ``start`` points at the heading; ``end`` is exclusive and points at
    the next ``## `` heading (or end-of-file). Spans cover the full
    block including the trailing blank line(s) before the next section.
    """
    matches = list(_PATTERN_HEADING_RE.finditer(markdown_text))
    for i, match in enumerate(matches):
        name = match.group("name")
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        yield name, start, end


def read_all_patterns(markdown_text: str) -> dict[str, dict[str, Any] | None]:
    """Return ``{pattern_name: metadata_or_None}`` for every block."""
    out: dict[str, dict[str, Any] | None] = {}
    for name, start, end in _iter_pattern_spans(markdown_text):
        out[name] = read_metadata(markdown_text[start:end])
    return out


def replace_pattern_block(
    markdown_text: str, pattern_name: str, new_block: str
) -> str:
    """Swap the ``## <pattern_name>`` section in ``markdown_text``."""
    for name, start, end in _iter_pattern_spans(markdown_text):
        if name == pattern_name:
            replacement = new_block
            if not replacement.endswith("\n"):
                replacement += "\n"
            # Preserve trailing blank lines that separated the old
            # block from its neighbour.
            tail = markdown_text[end:]
            return markdown_text[:start] + replacement + tail
    return markdown_text


# ---------------------------------------------------------------------------
# Confidence-score formula


def _parse_iso(stamp: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting both ``Z`` and ``+00:00``.

    Naive timestamps are interpreted as UTC.
    """
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_confidence(
    evidence_count: int,
    last_seen_utc: str,
    half_life_days: int,
    *,
    now: datetime | None = None,
) -> float:
    """Exponential decay: ``count * exp(-age_days / half_life_days)``.

    ``last_seen_utc`` is an ISO-8601 string (Z or +00:00). ``now`` is
    injectable for deterministic tests; it defaults to ``datetime.now``
    in UTC.

    ``half_life_days`` must be strictly positive.
    """
    if half_life_days <= 0:
        raise ValueError(
            f"half_life_days must be > 0, got {half_life_days!r}"
        )
    if evidence_count <= 0:
        return 0.0
    last_seen = _parse_iso(last_seen_utc)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days = max(0.0, (current - last_seen).total_seconds() / 86400.0)
    return float(evidence_count) * math.exp(-age_days / float(half_life_days))


# ---------------------------------------------------------------------------
# Audit-end hook


def _now_utc_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _read_provenance(run_dir: Path) -> dict[str, Any]:
    for relative in ("09_audit/experiment_provenance.json", "experiment_provenance.json"):
        candidate = run_dir / relative
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}
    return {}


def update_metadata_for_run(
    *,
    lessons_path: Path,
    run_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh lifecycle metadata after an audit run.

    Returns a small summary dict describing which patterns were
    bumped vs only had their confidence recomputed. Failures
    (missing file, malformed YAML) are downgraded to ``{"skipped":
    True, "reason": ...}`` so a buggy metadata block can never crash
    the audit pipeline.
    """
    summary: dict[str, Any] = {
        "skipped": False,
        "reason": "",
        "matched_patterns": [],
        "recomputed_patterns": [],
    }

    if not lessons_path.is_file():
        summary["skipped"] = True
        summary["reason"] = "lessons_learned.md not found"
        return summary

    provenance = _read_provenance(run_dir)
    run_id = str(provenance.get("run_id") or run_dir.name)
    matched = [
        str(pattern)
        for pattern in provenance.get("failure_patterns") or []
        if isinstance(pattern, str) and pattern and pattern != "no_detected_failure"
    ]

    audit_stamp = _now_utc_iso()
    text = lessons_path.read_text(encoding="utf-8")

    updated_text = text
    for name, start, end in list(_iter_pattern_spans(text)):
        block = text[start:end]
        meta = read_metadata(block)
        if meta is None:
            continue
        new_meta = dict(meta)
        is_match = name in matched
        if is_match:
            new_meta["evidence_count"] = int(new_meta.get("evidence_count", 0)) + 1
            new_meta["last_seen_utc"] = audit_stamp
            evidence_runs = list(new_meta.get("evidence_runs") or [])
            if run_id not in evidence_runs:
                evidence_runs.append(run_id)
            new_meta["evidence_runs"] = evidence_runs
            summary["matched_patterns"].append(name)

        half_life = int(new_meta.get("half_life_days", DEFAULT_HALF_LIFE_DAYS))
        new_meta["confidence_score"] = round(
            compute_confidence(
                int(new_meta.get("evidence_count", 0)),
                str(new_meta.get("last_seen_utc") or audit_stamp),
                half_life,
                now=now,
            ),
            3,
        )
        summary["recomputed_patterns"].append(name)

        new_block = write_metadata(block, new_meta)
        updated_text = replace_pattern_block(updated_text, name, new_block)

    if updated_text != text:
        lessons_path.write_text(updated_text, encoding="utf-8")
    return summary
