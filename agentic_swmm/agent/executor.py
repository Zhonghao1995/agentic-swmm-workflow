from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent import permissions
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class AgentExecutor:
    def __init__(
        self,
        registry: AgentToolRegistry,
        *,
        session_dir: Path,
        trace_path: Path,
        dry_run: bool = False,
        profile: Profile = Profile.SAFE,
    ) -> None:
        self.registry = registry
        self.session_dir = session_dir
        self.trace_path = trace_path
        self.dry_run = dry_run
        self.profile = profile
        self.results: list[dict[str, Any]] = []

    def execute(self, call: ToolCall, *, index: int | None = None) -> dict[str, Any]:
        event_index = index if index is not None else len(self.results) + 1
        write_event(self.trace_path, {"event": "tool_start", "index": event_index, "tool": call.name, "args": call.args})
        # PRD_runtime: consult the permission profile before prompting.
        # QUICK auto-approves read-only tools; SAFE always defers to
        # ``permissions.prompt_user`` (which itself auto-allows in
        # non-TTY contexts so CI never blocks on stdin).
        if not self.dry_run and not self.profile.auto_approve(call.name, self.registry):
            if not permissions.prompt_user(call.name):
                result = {
                    "tool": call.name,
                    "args": call.args,
                    "ok": False,
                    "summary": "tool not approved by user",
                }
                self.results.append(result)
                write_event(self.trace_path, {"event": "tool_result", "index": event_index, **result})
                return result
        if self.dry_run:
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "dry run; tool not executed"}
        else:
            result = self.registry.execute(call, self.session_dir)
        self.results.append(result)
        write_event(self.trace_path, {"event": "tool_result", "index": event_index, **result})
        return result
