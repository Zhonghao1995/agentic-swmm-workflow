from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Any

from agentic_swmm.agent import permissions
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.reporting import write_event
from agentic_swmm.agent.mcp_coverage import typed_tool_for
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.agent.ui import Spinner, SpinnerState, set_active_tool_spinner

# Issue #193 item 2: hoist the denial summary string to a module
# constant so both the executor and the planner share one source of
# truth. Anywhere that needs to recognise "this result is a user
# denial" imports ``DENIED_SUMMARY`` instead of repeating the literal.
DENIED_SUMMARY = "tool not approved by user"


#: Tools whose approval is never borrowed from the turn's chain grant.
NEVER_CHAINED = frozenset({"apply_patch"})

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
        verbose: bool = False,
    ) -> None:
        self.registry = registry
        self.session_dir = session_dir
        self.trace_path = trace_path
        self.dry_run = dry_run
        self.profile = profile
        self.results: list[dict[str, Any]] = []
        # PRD-185: in digest mode (verbose=False, the new default) the
        # per-tool spinner shows only the bare tool name; verbose=True
        # keeps the legacy ``Running <name> — <first sentence>`` text
        # so the debugging path is byte-identical.
        self.verbose = verbose
        # PRD_runtime: per-tool spinner — owned by the executor so the
        # planner does not have to keep printing ``[i/N] toolname``.
        self._progress_stream: IO[str] = progress_stream if progress_stream is not None else sys.stdout
        self._spinner: Spinner | None = None
        # One confirmation per turn (user decision 2026-08-09, "a chain
        # of four Y/n prompts is too heavy"): under the QUICK profile,
        # the first approved prompt of this executor's lifetime (one
        # turn) also approves the rest of the turn's prompted tools.
        # A denial does not arm it, and the SAFE profile keeps per-tool
        # prompts unconditionally.
        self._turn_chain_approved = False

    def _typed_redirect(self, call: ToolCall) -> dict[str, Any] | None:
        if call.name != "call_mcp_tool":
            return None
        server = str(call.args.get("server") or "")
        tool = str(call.args.get("tool") or "")
        typed = typed_tool_for(server, tool)
        if typed is None or typed not in getattr(self.registry, "names", ()):
            return None
        hint = (
            f"{server}.{tool} has a typed tool in this runtime: call {typed} directly "
            "with the same arguments in snake_case. It returns the typed result shape"
            + (" and needs no approval." if self.registry.is_read_only(typed) else ".")
        )
        return {
            "tool": call.name,
            "args": call.args,
            "ok": False,
            "summary": f"use the typed tool {typed} instead of the bridge for {server}.{tool}",
            "hint": hint,
            "redirect_to": typed,
            "permission": {"prompted": False, "approved": True},
        }

    def execute(self, call: ToolCall, *, index: int | None = None) -> dict[str, Any]:
        event_index = index if index is not None else len(self.results) + 1
        write_event(self.trace_path, {"event": "tool_start", "index": event_index, "tool": call.name, "args": call.args})
        redirect = self._typed_redirect(call)
        if redirect is not None:
            # Live finding F-56b (2026-09-02): a bridge call to a tool that
            # has a typed ToolSpec is answered before any prompt: the typed
            # tool carries the allowlisted output shape and the read-only
            # approval class; the generic bridge carries neither.
            self.results.append(redirect)
            write_event(self.trace_path, {"event": "tool_result", "index": event_index, **redirect})
            return redirect
        self._announce(call.name)
        # Issue #193 item 2: capture the permission decision once,
        # here, where it is actually made — and publish it on the
        # result dict so downstream renderers (digest mode planner)
        # do not have to reconstruct it.
        prompted = False
        approved = True
        # PRD_runtime: consult the permission profile before prompting.
        # QUICK auto-approves read-only tools; SAFE always defers to
        # ``permissions.request_approval`` (which fails closed in non-TTY
        # contexts rather than running unattended).
        # Finding F-11 (live 2026-09-02): the turn's one Y for a refused
        # command went on to cover apply_patch adding a script to the
        # repository. A repository write is its own consent class: it is
        # never covered by the chain grant and never arms it.
        chain_eligible = (
            getattr(self.profile, "name", "") == "QUICK" and call.name not in NEVER_CHAINED
        )
        if (
            not self.dry_run
            and not self.profile.auto_approve(call.name, self.registry)
            and chain_eligible
            and self._turn_chain_approved
        ):
            # Approved with the turn's first confirmation; record it as
            # an unprompted approval so the digest shows the tool ran
            # under the chain grant rather than a fresh question.
            prompted = False
        elif not self.dry_run and not self.profile.auto_approve(call.name, self.registry):
            prompted = True
            decision = permissions.request_approval(
                call.name, permissions.approval_detail(call.args)
            )
            if decision.approved and chain_eligible:
                self._turn_chain_approved = True
            if not decision.approved:
                approved = False
                result = {
                    "tool": call.name,
                    "args": call.args,
                    "ok": False,
                    "summary": DENIED_SUMMARY,
                    "permission": {"prompted": True, "approved": False},
                }
                # A refusal with nobody at the keyboard is the one worth
                # explaining: the summary alone reads identically to a
                # human typing `n`, so a CI run would learn neither the
                # cause nor the documented opt-in. Carried alongside the
                # summary, never inside it, because `run_failures`
                # recognises denials by exact string equality.
                if decision.needs_guidance:
                    result["hint"] = permissions.HEADLESS_DENIAL_HINT
                self.results.append(result)
                write_event(self.trace_path, {"event": "tool_result", "index": event_index, **result})
                return result
        if self.dry_run:
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "dry run; tool not executed"}
        else:
            result = self.registry.execute(call, self.session_dir)
        # Attach the permission record without clobbering existing
        # fields. Tool handlers return their own dicts; the seam adds
        # one new key so the planner / digest renderer can read it
        # directly.
        result["permission"] = {"prompted": prompted, "approved": approved}
        self.results.append(result)
        write_event(self.trace_path, {"event": "tool_result", "index": event_index, **result})
        if self._spinner is not None:
            # Hand the line back to the planner's THINKING spinner
            # until the next tool starts (ticker fight fix).
            self._spinner.pause()
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
            self._spinner.resume()
            self._spinner.update(rendered)
        # Register the live spinner so a blocking handler can repaint the
        # status line mid-call (ui.update_tool_status). Re-registering per
        # tool also resets the dedupe state for the new label stream.
        set_active_tool_spinner(self._spinner)

    def _tool_label(self, name: str) -> str:
        # PRD-185: in digest mode the spinner label is just the tool
        # name so the user sees motion without the re-printed
        # description. ``--verbose`` keeps the descriptive label.
        if not self.verbose:
            return name
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
            set_active_tool_spinner(None)
            self._spinner.finish()
            self._spinner = None
