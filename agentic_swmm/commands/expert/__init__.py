"""Expert-only CLI subcommand package (PRD-Z).

These commands carry the human modeller's authority — they promote a
calibration, confirm a pour point, override a threshold, or publish a
run. By PRD design **none** of them is registered as a ToolSpec or an
MCP tool. The agent can ``read_file`` the source to learn they exist,
but it cannot invoke them.

Each module exposes a single ``register(subparsers)`` function so
``agentic_swmm/cli.py`` can register them in a clearly-grouped block.
The CLI's top-level help labels the group "Expert-only commands".

The shared invariant is that every successful invocation appends a
``human_decisions`` record (via
:func:`agentic_swmm.hitl.decision_recorder.append_decision`) to the
target run's ``09_audit/experiment_provenance.json``. Schema 1.2 is
auto-upgraded on first write — see PRD-Z for the provenance contract.
"""

from agentic_swmm.commands.expert import (
    calibration,
    gap_promote,
    memory_reflect,
    pour_point,
    publish,
    thresholds,
)

__all__ = [
    "calibration",
    "gap_promote",
    "memory_reflect",
    "pour_point",
    "publish",
    "thresholds",
]
