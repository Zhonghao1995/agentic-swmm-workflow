"""Pre-flight L1 scanner (PRD-GF-CORE).

Pure function. Given:

- ``tool_name``: the tool that is about to be invoked,
- ``required_file_args``: the names of arguments that point to files
  the tool needs to exist on disk before it runs,
- ``args``: the actual ``ToolCall.args`` dict the planner produced,

returns a list of :class:`GapSignal` (severity ``L1``, kind
``file_path``) — one per missing file. An empty list means every
required path exists.

The function never raises. Anything that is not a non-empty string
pointing at an existing path is treated as "missing" so the runtime
sees a single uniform L1 signal regardless of whether the planner
omitted the key, passed an empty string, or supplied a path that
points nowhere. This is the right boundary because the proposer /
UI / recorder code does not need to distinguish those cases — they
all resolve via the same "ask the user to pick a real file" path.

Tool authors register their required file-arg names by declaring
them via the tool registry (``ToolSpec.required_file_args``); this
function is a plain helper that consumes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agentic_swmm.gap_fill.protocol import GapSignal, new_gap_id


def _path_exists(value: Any) -> bool:
    """Return True iff ``value`` is a non-empty string pointing to a file or dir.

    Anything else (``None``, empty string, non-string types, missing
    path) is treated as "does not exist". The check is intentionally
    forgiving: the proposer / UI deal with the user-facing "pick a
    file" interaction, so the scanner only has to give one bit per
    field.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return Path(value).exists()
    except OSError:
        # Pathological paths (too long, illegal chars) cannot exist.
        return False


def scan_required_files(
    *,
    tool_name: str,
    required_file_args: Iterable[str],
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> list[GapSignal]:
    """Return one :class:`GapSignal` per missing required file.

    ``required_file_args`` is the tool's declared list of file-arg
    names; the contract is "if any of these point at a path that does
    not exist on disk, surface an L1 gap". ``context`` is merged into
    each emitted signal's ``context`` field so callers can carry
    workflow / step labels through. ``tool_name`` is always written
    into the context under the ``"tool"`` key.

    The returned signals carry the original (or missing) path under
    ``context["provided_path"]`` so the UI can show a candidate
    pre-filled value when the user is choosing a replacement.
    """
    base_context = dict(context or {})
    base_context["tool"] = tool_name

    signals: list[GapSignal] = []
    for field in required_file_args:
        value = args.get(field)
        if _path_exists(value):
            continue
        sig_context = dict(base_context)
        sig_context["provided_path"] = value if isinstance(value, str) else None
        signals.append(
            GapSignal(
                gap_id=new_gap_id(),
                severity="L1",
                kind="file_path",
                field=field,
                context=sig_context,
            )
        )
    return signals


__all__ = ["scan_required_files"]
