from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Any

from agentic_swmm.agent import permissions
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import Spinner, SpinnerState


class AgentExecutor:
    def __init__(
        self,
        registry: AgentToolRegistry,
        *,
        session_dir: Path,
        trace_path: Path,
        dry_run: bool = False,
        profile: Profile = Profile.SAFE,
        progress_stream: IO[str] | None = None,
    ) -> None:
        self.registry = registry
        self.session_dir = session_dir
        self.trace_path = trace_path
        self.dry_run = dry_run
        self.profile = profile
        self.results: list[dict[str, Any]] = []
        # PRD_runtime: per-tool spinner — owned by the executor so the
        # planner does not have to keep printing ``[i/N] toolname``.
        self._progress_stream: IO[str] = progress_stream if progress_stream is not None else sys.stdout
        self._spinner: Spinner | None = None

    def execute(self, call: ToolCall, *, index: int | None = None) -> dict[str, Any]:
        event_index = index if index is not None else len(self.results) + 1
        write_event(self.trace_path, {"event": "tool_start", "index": event_index, "tool": call.name, "args": call.args})
        self._announce(call.name)
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

    def _announce(self, label: str) -> None:
        # Issue #58 (UX-3): show ``Running <toolname> — <first
        # sentence of description>`` instead of the bare tool name so
        # the user sees what the tool does, not just its identifier.
        # Unknown tools fall back to the raw label.
        rendered = self._tool_label(label)
        if self._spinner is None:
            self._spinner = Spinner(
                rendered,
                stream=self._progress_stream,
                state=SpinnerState.RUNNING,
            )
            self._spinner.__enter__()
        else:
            self._spinner.update(rendered)

    def _tool_label(self, name: str) -> str:
        description = self.registry.describe(name)
        if not description:
            return name
        first_sentence = description.split(".")[0].strip()
        if not first_sentence:
            return name
        return f"Running {name} — {first_sentence}"

    def close(self) -> None:
        """Close the progress spinner. Called once at the end of a run."""
        if self._spinner is not None:
            self._spinner.finish()
            self._spinner = None
