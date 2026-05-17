"""Explicit read-only inventory lock (P1-5 in #79).

The architecture review observed that ``capabilities`` and
``select_workflow_mode`` were unflagged on ``ToolSpec.is_read_only`` despite
performing only inspect/describe work. In the ``QUICK`` profile that forced
an interactive approval prompt for two tools that write nothing — a UX
regression with no policy upside.

This test holds the *expected read-only inventory* in an ``{name:
is_read_only}`` mapping rather than a bare set, so any future ToolSpec
addition fails this test (with a clear diff) until the new tool's
read-only stance is explicitly declared here. That matches the PRD-Z
"fail-safe drift detection" pattern in ``test_tool_registry_is_read_only``
but at higher resolution: every tool, not just the True subset.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_registry import AgentToolRegistry


# name -> is_read_only.
# True  = pure read / inspect — safe to auto-approve in QUICK.
# False = writes a file, runs a subprocess, mutates external state.
EXPECTED_INVENTORY: dict[str, bool] = {
    # Writes / runs / mutations.
    "apply_patch": False,
    "audit_run": False,
    "build_inp": False,
    "call_mcp_tool": False,
    "demo_acceptance": False,
    "doctor": False,
    "format_rainfall": False,
    "network_qa": False,
    "network_to_inp": False,
    "plot_run": False,
    # Issue #124 Part B1: generates per-scenario INP files.
    "propose_lid_scenarios": False,
    "record_fact": False,
    "request_expert_review": False,
    # request_gap_judgement (PRD-GF-L5) — L5 subjective judgement
    # cannot be auto-approved by QUICK; the prompt is human-only.
    "request_gap_judgement": False,
    "run_allowed_command": False,
    "run_swmm_inp": False,
    "run_tests": False,
    "summarize_memory": False,
    # Pure read / inspect.
    "capabilities": True,
    "git_diff": True,
    "inspect_plot_options": True,
    "list_dir": True,
    "list_mcp_servers": True,
    "list_mcp_tools": True,
    "list_skills": True,
    "read_file": True,
    "read_skill": True,
    "recall_memory": True,
    "recall_memory_search": True,
    "recall_session_history": True,
    # Issue #124 Part A: hybrid retrieval over audited-run memory.
    "retrieve_memory": True,
    "search_files": True,
    "select_skill": True,
    "select_workflow_mode": True,
    "web_fetch_url": True,
    "web_search": True,
}


def test_read_only_inventory_matches_registry() -> None:
    registry = AgentToolRegistry()
    registry_names = set(registry.names)
    inventory_names = set(EXPECTED_INVENTORY)

    missing_from_inventory = registry_names - inventory_names
    extra_in_inventory = inventory_names - registry_names
    assert not missing_from_inventory, (
        "New ToolSpec(s) in the registry are not declared in "
        f"EXPECTED_INVENTORY: {sorted(missing_from_inventory)}. Add each "
        "with an explicit `True`/`False` read-only stance."
    )
    assert not extra_in_inventory, (
        "EXPECTED_INVENTORY lists tools no longer in the registry: "
        f"{sorted(extra_in_inventory)}. Drop them."
    )

    mismatches = {
        name: (EXPECTED_INVENTORY[name], registry.is_read_only(name))
        for name in registry_names
        if EXPECTED_INVENTORY[name] != registry.is_read_only(name)
    }
    assert not mismatches, (
        "Read-only classification drift; (expected, actual) per tool: "
        f"{mismatches}"
    )


def test_capabilities_is_read_only() -> None:
    """Direct lock — `capabilities` was the canonical drift case in P1-5."""
    registry = AgentToolRegistry()
    assert registry.is_read_only("capabilities") is True


def test_select_workflow_mode_is_read_only() -> None:
    """Direct lock — `select_workflow_mode` was the second drift case."""
    registry = AgentToolRegistry()
    assert registry.is_read_only("select_workflow_mode") is True
