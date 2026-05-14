"""Bounded forgetting for ``memory/modeling-memory/lessons_learned.md``.

ME-2 (issue #62) layers status transitions on top of the lifecycle
metadata written by ME-1 (#61). The pure-function entry point is
:func:`apply_decay`:

.. code-block:: python

    report = apply_decay(lessons_path, archive_path, config)

It does two things:

1. Recompute ``confidence_score`` for every ``## <pattern>`` block,
   reusing the exponential decay formula from
   :func:`agentic_swmm.memory.lessons_metadata.compute_confidence`.
2. Apply the active / dormant / retired status policy:

   - ``score >= active_threshold``   → ``active``
   - ``dormant_threshold <= score < active_threshold`` → ``dormant``
   - ``score < dormant_threshold``  → ``retired`` (moved out of
     ``lessons_learned.md`` and appended to
     ``memory/modeling-memory/lessons_archived.md``).

The function returns a :class:`DecayReport` summarising which patterns
moved between buckets — used by the audit hook to write
``09_audit/decay_report.json`` and by ``aiswmm memory compact`` to print
a human-readable summary.

The implementation is deliberately deterministic: callers may pass a
``now`` argument for tests. No filesystem writes happen unless at least
one pattern changed; ``apply_decay`` is safe to call repeatedly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory.lessons_metadata import (
    DEFAULT_HALF_LIFE_DAYS,
    _iter_pattern_spans,  # internal but stable within this package
    compute_confidence,
    read_metadata,
    replace_pattern_block,
    write_metadata,
)


DEFAULT_ACTIVE_THRESHOLD = 1.0
DEFAULT_DORMANT_THRESHOLD = 0.2


@dataclass
class DecayReport:
    """Outcome of one :func:`apply_decay` pass.

    Each field is a list of pattern names. A pattern shows up in *one*
    of ``promoted`` / ``demoted`` / ``retired`` / ``unchanged`` per
    call. ``promoted`` means status moved up the ladder (dormant ->
    active, retired -> active/dormant); ``demoted`` means it slid
    downward but did not retire; ``retired`` means it was moved into
    the archive file; ``unchanged`` means status did not change (the
    score may still have shifted).
    """

    promoted: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "promoted": list(self.promoted),
            "demoted": list(self.demoted),
            "retired": list(self.retired),
            "unchanged": list(self.unchanged),
        }


_STATUS_RANK = {"retired": 0, "dormant": 1, "active": 2}


def _classify(
    score: float, *, active_threshold: float, dormant_threshold: float
) -> str:
    if score >= active_threshold:
        return "active"
    if score >= dormant_threshold:
        return "dormant"
    return "retired"


def _config_value(
    config: dict[str, Any] | None, key: str, default: float | int
) -> float | int:
    if not config:
        return default
    value = config.get(key)
    if value is None:
        return default
    return value


def _strip_trailing_blank_lines(text: str) -> str:
    """Collapse trailing blank lines down to a single ``\\n``."""
    return re.sub(r"\n{2,}\Z", "\n", text)


def _ensure_archive_header(archive_path: Path) -> None:
    if archive_path.is_file():
        return
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        "<!-- schema_version: 1.1 -->\n"
        "# Lessons Archived\n"
        "\n"
        "Patterns that decayed below the dormant threshold land here.\n"
        "Each block keeps its full metadata fence so the pattern can be\n"
        "revived by moving the block back into ``lessons_learned.md``.\n"
        "\n",
        encoding="utf-8",
    )


def _append_to_archive(archive_path: Path, block: str) -> None:
    _ensure_archive_header(archive_path)
    existing = archive_path.read_text(encoding="utf-8")
    if not existing.endswith("\n"):
        existing += "\n"
    if not existing.endswith("\n\n"):
        existing += "\n"
    cleaned = _strip_trailing_blank_lines(block)
    archive_path.write_text(existing + cleaned + "\n", encoding="utf-8")


def _remove_pattern_block(text: str, pattern_name: str) -> str:
    """Drop the ``## <pattern_name>`` section from ``text`` entirely."""
    for name, start, end in _iter_pattern_spans(text):
        if name == pattern_name:
            head = text[:start]
            tail = text[end:]
            # Collapse the seam so we don't leave a double-blank line
            # where the section used to be.
            if head.endswith("\n\n") and tail.startswith("\n"):
                tail = tail.lstrip("\n")
                head = head.rstrip("\n") + "\n\n"
            return head + tail
    return text


def apply_decay(
    lessons_path: Path,
    archive_path: Path,
    config: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> DecayReport:
    """Recompute confidence + apply status transitions on every block.

    Parameters
    ----------
    lessons_path:
        Path to ``memory/modeling-memory/lessons_learned.md``.
    archive_path:
        Path to ``memory/modeling-memory/lessons_archived.md``. Created
        on first use if missing.
    config:
        Optional dict with ``half_life_days``, ``active_threshold``,
        ``dormant_threshold``. Per-pattern ``half_life_days`` in the
        block metadata always wins; the config value is only used as
        the fallback when the block omits it.
    now:
        Injectable clock for deterministic tests; defaults to
        ``datetime.now(UTC)``.

    Returns
    -------
    DecayReport
        Lists of pattern names per outcome bucket.
    """
    report = DecayReport()
    if not lessons_path.is_file():
        return report

    default_half_life = int(
        _config_value(config, "half_life_days", DEFAULT_HALF_LIFE_DAYS)
    )
    active_threshold = float(
        _config_value(config, "active_threshold", DEFAULT_ACTIVE_THRESHOLD)
    )
    dormant_threshold = float(
        _config_value(config, "dormant_threshold", DEFAULT_DORMANT_THRESHOLD)
    )

    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    text = lessons_path.read_text(encoding="utf-8")
    spans = list(_iter_pattern_spans(text))
    # Snapshot block text upfront — we mutate ``updated_text`` as we go.
    blocks: list[tuple[str, str]] = [(name, text[start:end]) for name, start, end in spans]

    updated_text = text
    retired_blocks: list[tuple[str, str]] = []  # (name, archived_block)

    for name, original_block in blocks:
        meta = read_metadata(original_block)
        if meta is None:
            # No metadata fence → skip silently; ME-1 covered the
            # migration. The pattern stays where it is.
            continue

        prior_status = str(meta.get("status") or "active")
        evidence_count = int(meta.get("evidence_count", 0))
        last_seen = str(meta.get("last_seen_utc") or "")
        half_life = int(meta.get("half_life_days") or default_half_life)

        if not last_seen or evidence_count <= 0 or half_life <= 0:
            # Defensive fallback: leave the block alone if the
            # metadata is incomplete.
            continue

        score = compute_confidence(
            evidence_count, last_seen, half_life, now=current_now
        )
        new_status = _classify(
            score,
            active_threshold=active_threshold,
            dormant_threshold=dormant_threshold,
        )

        new_meta = dict(meta)
        new_meta["confidence_score"] = round(score, 3)
        new_meta["status"] = new_status

        new_block = write_metadata(original_block, new_meta)

        if new_status == "retired":
            retired_blocks.append((name, new_block))
            updated_text = _remove_pattern_block(updated_text, name)
            report.retired.append(name)
            continue

        # Non-retired: write back in place.
        updated_text = replace_pattern_block(updated_text, name, new_block)

        prior_rank = _STATUS_RANK.get(prior_status, _STATUS_RANK["active"])
        new_rank = _STATUS_RANK[new_status]
        if new_rank > prior_rank:
            report.promoted.append(name)
        elif new_rank < prior_rank:
            report.demoted.append(name)
        else:
            report.unchanged.append(name)

    if updated_text != text:
        lessons_path.write_text(updated_text, encoding="utf-8")

    for name, block in retired_blocks:
        _append_to_archive(archive_path, block)

    return report


# ---------------------------------------------------------------------------
# Config loader for memory_evolution_config.md


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


def load_config(config_path: Path) -> dict[str, Any]:
    """Parse the YAML front-matter from ``memory_evolution_config.md``.

    Returns a dict with keys ``half_life_days``, ``active_threshold``,
    ``dormant_threshold`` (defaults filled in when absent). Missing
    file or malformed YAML degrades to the in-code defaults so a
    typo cannot brick the audit hook.
    """
    defaults: dict[str, Any] = {
        "half_life_days": DEFAULT_HALF_LIFE_DAYS,
        "active_threshold": DEFAULT_ACTIVE_THRESHOLD,
        "dormant_threshold": DEFAULT_DORMANT_THRESHOLD,
    }
    if not config_path.is_file():
        return defaults
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return defaults
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return defaults
    try:
        import yaml

        parsed = yaml.safe_load(match.group("body")) or {}
    except Exception:
        return defaults
    if not isinstance(parsed, dict):
        return defaults
    result = dict(defaults)
    for key in ("half_life_days", "active_threshold", "dormant_threshold"):
        if key in parsed:
            result[key] = parsed[key]
    return result
