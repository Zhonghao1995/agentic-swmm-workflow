"""Workflow-mode protocol, ``WorkflowContext``, and registry primitives.

The protocol intentionally splits *spec* (class attributes that
``select_workflow_mode`` reads to validate inputs and recommend next
tools) from *behaviour* (a ``run`` method that drives the planner's
tool dispatch for the mode). Spec-only stub adapters can declare just
the attributes; runnable adapters add a ``run`` method too.

See PRD-04 "WorkflowMode adapter registry" for the motivation: avoid
the drift-bug shape where adding a new mode requires three coordinated
edits across ``planner.py``, ``tool_registry.py``, and the keyword
classifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from agentic_swmm.agent.executor import AgentExecutor
    from agentic_swmm.agent.planner import PlannerRun
    from agentic_swmm.agent.types import ToolCall


@runtime_checkable
class WorkflowMode(Protocol):
    """Spec contract every registered workflow mode exposes.

    ``run`` is optional — spec-only stubs (calibration, uncertainty,
    full_modular_build, prepared_demo) declare only the attributes so
    ``select_workflow_mode`` can describe required inputs / recommended
    next tools without committing to a planner-side adapter yet.
    """

    name: ClassVar[str]
    required_inputs: ClassVar[list[str]]
    recommended_next_tools: ClassVar[list[str]]
    evidence_boundary: ClassVar[str]


@dataclass
class WorkflowContext:
    """Inputs an adapter's ``run`` method needs without coupling to ``OpenAIPlanner``.

    Adapters drive the planner's tool dispatch through this context;
    they must not reach into ``OpenAIPlanner`` internals. This keeps
    each mode's behaviour testable in isolation.

    Round 1 memory integration adds three optional attributes that
    default to ``None`` so the existing dispatch path stays valid:

    * ``trace_path`` — where ``memory_consultation`` /
      ``memory_informed_decision`` events should be appended. When
      ``None``, the adapter skips mirror-event writes.
    * ``memory_integration`` — a :class:`MemoryIntegration` shim
      bundling the memory-consult and pre/postflight gate callables.
      When ``None``, the adapter skips memory consultation entirely
      (the planner can still invoke ``run`` against an old call site
      that has not been updated).
    * ``case_name`` — best-effort anchor for memory consultation. When
      ``None``, the consult fires but yields an empty context.
    * ``memory_context`` — populated by the adapter at the top of
      ``run`` so downstream helpers do not have to re-read.
    """

    goal: str
    session_dir: Path
    plan: list["ToolCall"]
    route: dict[str, Any]
    executor: "AgentExecutor"
    emit: Callable[[str], None]
    trace_path: Path | None = None
    memory_integration: Any | None = None
    case_name: str | None = None
    memory_context: Any | None = None

    def step(self, call: "ToolCall") -> dict[str, Any]:
        """Append ``call`` to the plan, emit the standard progress line, execute it."""
        # Late import keeps the planner package import-cycle safe.
        from agentic_swmm.agent.types import ToolCall  # noqa: F401

        self.plan.append(call)
        self.emit(f"[{len(self.plan)}] {call.name}")
        result = self.executor.execute(call, index=len(self.plan))
        status = "OK" if result.get("ok") else "FAILED"
        self.emit(f"{status}: {result.get('summary') or 'completed'}")
        return result


_REGISTRY: dict[str, type[WorkflowMode]] = {}


def register(cls: type[WorkflowMode]) -> type[WorkflowMode]:
    """Decorator that registers ``cls`` under ``cls.name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_mode_spec(name: str) -> type[WorkflowMode] | None:
    """Return the registered class for ``name``, or ``None`` if unknown."""
    return _REGISTRY.get(name)


def get_mode(name: str) -> WorkflowMode | None:
    """Return a fresh instance of the registered class, or ``None``."""
    cls = _REGISTRY.get(name)
    return cls() if cls else None


def all_modes() -> list[str]:
    """Return all registered mode names in sorted order."""
    return sorted(_REGISTRY.keys())
