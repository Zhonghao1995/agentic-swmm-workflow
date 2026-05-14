"""Best-effort ``case_name`` inference from a session_state payload.

The planner persists ``session_state.json`` per turn. The audit pipeline
also writes ``experiment_provenance.json`` for SWMM runs. Either of
those structured fields wins; the path-slug regex is the fallback for
chat sessions or runs that pre-date the provenance writer.

Returning ``None`` is fine — those sessions simply don't participate
in the "previous session for this case" startup injection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_TURN_DIR_RE = re.compile(r"^\d+_(?P<case>.+?)_(?:run|chat)(?:_\d+)?$")


def infer_case_name(session_state: dict[str, Any]) -> str | None:
    """Return the case slug for ``session_state`` or ``None``.

    Lookup order:

    1. ``workflow_state.active_run_dir/09_audit/experiment_provenance.json``
       — when present, the ``case`` field is authoritative.
    2. The leaf of ``active_run_dir`` matched against the
       ``HHMMSS_<case>_<run|chat>`` convention created by
       ``runtime_loop._new_turn_dir``.
    3. ``None`` — caller should treat the session as case-less.
    """
    if not isinstance(session_state, dict):
        return None
    workflow_state = session_state.get("workflow_state")
    if not isinstance(workflow_state, dict):
        return None
    active_run_dir = workflow_state.get("active_run_dir")
    if not active_run_dir:
        return None
    run_dir = Path(str(active_run_dir))
    provenance = run_dir / "09_audit" / "experiment_provenance.json"
    if provenance.is_file():
        try:
            payload = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            case = payload.get("case")
            if isinstance(case, str) and case.strip():
                return case.strip()
    match = _TURN_DIR_RE.match(run_dir.name)
    if match:
        return match.group("case")
    return None
