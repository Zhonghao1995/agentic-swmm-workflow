from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


# CONCURRENCY-OWNER: PRD-GF-CORE
#
# ``supports_gap_fill`` and ``required_file_args`` are the two gap-fill
# (PRD-GF-CORE) hooks on every ToolSpec. Both default to fail-safe
# values so existing tools without explicit opt-in keep their pre-PRD
# behaviour. The handler-wrapping logic that actually intercepts
# ``gap_signal`` lives in ``agentic_swmm.agent.runtime_loop.invoke_tool_with_gap_fill``;
# this dataclass only declares the fields.
#
# Issue #358 PR B moved ToolSpec here (a leaf module) from
# ``tool_registry`` so family modules can declare their own specs via
# ``tool_specs()`` without importing the registry; the registry
# re-exports the name for every historical import site.
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolCall, Path], dict[str, Any]]
    # PRD_runtime: ``is_read_only=True`` lets ``Profile.QUICK`` auto-approve
    # the tool without prompting. Default ``False`` is fail-safe — a new
    # tool only joins the auto-approve set when its author explicitly
    # marks it read-only.
    is_read_only: bool = False
    # CONCURRENCY-OWNER: PRD-GF-CORE
    # ``supports_gap_fill=True`` opts the tool into the gap-fill state
    # machine. The runtime then:
    #   * runs the pre-flight L1 scanner over ``required_file_args``
    #     before each invocation,
    #   * intercepts ``{"ok": false, "gap_signal": {...}}`` results
    #     and routes them through the proposer/UI/recorder.
    # Default ``False`` is fail-safe: legacy tools without the flag
    # raise on missing inputs exactly as they did pre-PRD.
    supports_gap_fill: bool = False
    # The tuple of argument names that point at files which must
    # exist before the tool runs. Used by the L1 pre-flight scanner.
    # Empty tuple disables pre-flight scanning (the tool then only
    # surfaces L3 gaps via in-band ``gap_signal``).
    required_file_args: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description, "parameters": self.parameters}
