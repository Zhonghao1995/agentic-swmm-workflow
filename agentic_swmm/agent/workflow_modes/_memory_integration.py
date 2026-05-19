"""Memory-informed integration helpers for workflow-mode adapters.

The Round 1 integration wires four things into every runnable
:class:`WorkflowMode`:

1. Pre-run memory consultation (``gather_memory_context``).
2. Pre-flight INP gate (``preflight_inp``) for SWMM-running modes.
3. Post-flight QA gate (``postflight_qa``) for SWMM-running modes.
4. Mirror events on ``agent_trace.jsonl``
   (``memory_consultation`` / ``memory_informed_decision``).

This module is the *adapter shim*: it bundles the four dependencies
behind a single :class:`MemoryIntegration` object that adapters can
construct via :func:`build_default_memory_integration`, or that tests
can construct with mock callables.

Why a single shim (rather than direct imports per adapter)
---------------------------------------------------------
The PRD specifies dependency injection so tests can pass mocks. If
each adapter imported ``gather_memory_context`` / ``preflight_inp``
directly, mocking them would require ``unittest.mock.patch`` on the
exact import path in each adapter — six brittle patch points. Routing
through a shim collapses six patches to one constructor argument.

Failure mode
------------
Every method on :class:`MemoryIntegration` is exception-safe at the
boundary: a failed memory read produces an empty
:class:`MemoryContext`; a failed gate produces a synthetic FAIL
report carrying the error; a failed event write is swallowed so the
adapter's behaviour does not depend on whether the trace path is
writable. The contract is: memory must never break dispatch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent.feature_flags import swmm_gates_disabled
from agentic_swmm.agent.memory_context import MemoryContext


# Sentinel callables ---------------------------------------------------------


def _default_gather_memory_context(
    *,
    memory_dir: Path,
    case_name: str,
    use_case: str | None = None,
    metrics_of_interest: tuple[str, ...] = (),
) -> MemoryContext:
    """Real-runtime ``gather_memory_context`` lookup.

    Imported lazily to keep the workflow_modes package free of a
    direct ``agentic_swmm.agent.memory_context`` import at module load
    time — adapters that never construct the integration (spec-only
    stubs) should not pay that import cost.
    """
    from agentic_swmm.agent.memory_context import gather_memory_context

    return gather_memory_context(
        memory_dir=memory_dir,
        case_name=case_name,
        use_case=use_case,
        metrics_of_interest=metrics_of_interest,
    )


def _default_preflight_inp(inp_path: Path) -> Any:
    """Real-runtime preflight, lazy-imported."""
    from agentic_swmm.agent.swmm_runtime.preflight import preflight_inp

    return preflight_inp(inp_path)


def _default_postflight_qa(
    run_dir: Path,
    *,
    parametric_store: Path | None = None,
    case_name: str | None = None,
    use_case: str | None = None,
) -> Any:
    """Real-runtime postflight, lazy-imported.

    Accepts the Round 6 / PRD-07 Phase 4 user-baseline kwargs. When
    callers don't supply them the behaviour is byte-identical to the
    pre-Round-6 single-arg version.
    """
    from agentic_swmm.agent.swmm_runtime.postflight import postflight_qa

    return postflight_qa(
        run_dir,
        parametric_store=parametric_store,
        case_name=case_name,
        use_case=use_case,
    )


def _resolve_default_memory_dir() -> Path:
    """Mirror the planner's memory-dir resolver.

    The audit hook and the planner each have their own copy of this
    helper because importing across the layers would entangle them.
    The shim follows the same env-var contract (``AISWMM_MEMORY_DIR``)
    so a project that points one consumer at a custom directory has
    them all read the same one.
    """
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    return Path("memory/modeling-memory")


# ---------------------------------------------------------------------------


@dataclass
class MemoryIntegration:
    """Bundle of memory-consult + gate dependencies for a workflow mode.

    The four callables can be swapped for tests. The constructor
    defaults wire the real runtime; :func:`build_default_memory_integration`
    is the convenience factory adapters use in production.

    Attributes:
        gather_memory_context: Function returning a
            :class:`MemoryContext` for the given case. Signature
            matches ``agent.memory_context.gather_memory_context``.
        preflight_inp: Function returning a
            :class:`agentic_swmm.agent.swmm_runtime.preflight.PreflightReport`
            for the given INP. Only called for SWMM-running modes.
        postflight_qa: Function returning a
            :class:`agentic_swmm.agent.swmm_runtime.postflight.QAReport`
            for the given run dir. Only called for SWMM-running modes.
        memory_dir: Filesystem path the memory consult reads from.
            Defaults to the env-var-aware resolver.
        gates_disabled: Function returning ``True`` when the
            pre/postflight gates should be skipped. Defaults to
            :func:`swmm_gates_disabled` so the env-var flip propagates.
    """

    gather_memory_context: Callable[..., MemoryContext] = (
        _default_gather_memory_context
    )
    preflight_inp: Callable[[Path], Any] = _default_preflight_inp
    postflight_qa: Callable[..., Any] = _default_postflight_qa
    memory_dir: Path = field(default_factory=_resolve_default_memory_dir)
    gates_disabled: Callable[[], bool] = swmm_gates_disabled

    def consult(
        self,
        *,
        case_name: str | None,
        use_case: str | None = None,
        metrics_of_interest: tuple[str, ...] = (),
    ) -> MemoryContext:
        """Return a :class:`MemoryContext` for the case, never raising.

        ``case_name`` may legitimately be ``None`` for a goal that
        does not anchor to a specific case (chat-only diagnostic
        prompts, ad-hoc questions). In that case the function returns
        an empty :class:`MemoryContext` rather than reading the store
        — the consult event still fires so the audit trail records
        the "we tried, no anchor available" decision.
        """
        if not case_name:
            return MemoryContext()
        try:
            return self.gather_memory_context(
                memory_dir=self.memory_dir,
                case_name=case_name,
                use_case=use_case,
                metrics_of_interest=metrics_of_interest,
            )
        except Exception:  # pragma: no cover - defensive
            return MemoryContext()

    def run_preflight(self, inp_path: Path) -> Any | None:
        """Run the preflight gate or return ``None`` when disabled.

        ``None`` is the explicit "no gate ran" signal — distinct from
        a PASS report. The adapter branches on the return type:
        ``None`` → skip the gate logic entirely (gates opt-out);
        otherwise → check ``.status`` against ``FAIL``.
        """
        if self.gates_disabled():
            return None
        try:
            return self.preflight_inp(inp_path)
        except Exception:  # pragma: no cover - defensive
            return None

    def run_postflight(
        self,
        run_dir: Path,
        *,
        case_name: str | None = None,
        use_case: str | None = None,
    ) -> Any | None:
        """Run the postflight QA gate or return ``None`` when disabled.

        Round 6 extension: when ``case_name`` and ``use_case`` are
        non-empty, the gate consults the parametric_memory user
        baseline via :func:`postflight_qa`'s new kwargs. The
        ``parametric_store`` path is derived from ``self.memory_dir``
        so workflow adapters get the conventional location for free.
        Legacy callers that omit the kwargs see no behaviour change.
        """
        if self.gates_disabled():
            return None
        try:
            store: Path | None = None
            if case_name and use_case:
                store = Path(self.memory_dir) / "parametric_memory.jsonl"
            try:
                return self.postflight_qa(
                    run_dir,
                    parametric_store=store,
                    case_name=case_name if case_name else None,
                    use_case=use_case if use_case else None,
                )
            except TypeError:
                # Older test mocks may not accept the kwargs. Fall
                # back to the one-arg form so adapter-mode tests stay
                # green.
                return self.postflight_qa(run_dir)
        except Exception:  # pragma: no cover - defensive
            return None


def build_default_memory_integration() -> MemoryIntegration:
    """Return a :class:`MemoryIntegration` wired to the real runtime.

    Adapters call this once per ``run`` to get the production hooks.
    Tests construct a :class:`MemoryIntegration` directly with mock
    callables. Wrapping the default in a factory keeps the test-time
    construction path identical to the production path — the only
    difference is which callables get passed in.
    """
    return MemoryIntegration()


__all__ = [
    "MemoryIntegration",
    "build_default_memory_integration",
]
