"""Lock-in: deterministic-SWMM ToolSpec handlers must not subprocess Python.

PRD-Y user-story 12 + Done Criteria: after this PRD lands, no
``_build_tools()`` deterministic-SWMM handler is allowed to
``subprocess.run python3 skills/...``. They must all route through
``_make_mcp_routed_handler`` (which calls ``MCPPool.call_tool``).

The allow-list at the bottom captures agent-internal handlers that
legitimately shell out / fork for in-process work — e.g. ``apply_patch``
shells to ``git apply``, ``git_diff`` to ``git diff``, ``run_tests`` to
``pytest``. None of those execute deterministic-SWMM scripts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agentic_swmm.agent.tool_registry as tool_registry


# Handlers we explicitly accept may still subprocess — they touch git,
# pytest, or a generic CLI wrapper, never a ``skills/<skill>/scripts``
# Python script.
_ALLOWLISTED_HANDLERS: set[str] = {
    "_apply_patch_tool",
    "_git_diff_tool",
    "_run_process_tool",  # generic worker — only invoked by allow-listed callers
    "_run_cli_tool",
    "_run_script_tool",  # generic worker — must not be called by deterministic handlers
    "_run_tests_tool",
    "_run_allowed_command_tool",
    "_demo_acceptance_tool",
    "_doctor_tool",
}

# Deterministic-SWMM handler functions that PRD-Y rewires through MCP.
# Each must lose its subprocess.run / sys.executable / script_path call
# and become a thin ``_make_mcp_routed_handler(...)``-built handler
# attached to its ToolSpec.
_DETERMINISTIC_HANDLERS: set[str] = {
    "_audit_run_tool",
    "_build_inp_tool",
    "_format_rainfall_tool",
    "_network_qa_tool",
    "_network_to_inp_tool",
    "_plot_run_tool",
    "_run_swmm_inp_tool",
    "_summarize_memory_tool",
}


def _collect_function_calls(node: ast.AST) -> list[str]:
    """Return every callable name referenced inside ``node`` (best effort).

    We capture both ``foo(...)`` and ``module.foo(...)`` patterns. The
    test is not trying to be a perfect tree walker — it's an audit trip
    wire against the well-known sin of shelling out to Python scripts.
    """

    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


def _function_source_uses_helpers(
    func_node: ast.FunctionDef, banned: set[str]
) -> set[str]:
    """Return the set of ``banned`` helper names called from ``func_node``."""

    return set(_collect_function_calls(func_node)) & banned


def test_deterministic_handlers_do_not_call_run_script_or_run_cli() -> None:
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)

    # ``_run_script_tool`` directly subprocess-invokes ``python skills/...``
    # in the old in-process handlers. ``_run_cli_tool`` subprocess-invokes
    # ``python -m agentic_swmm.cli`` which itself routes to a skills script.
    # After this PRD lands, neither helper may be called from the body
    # of a deterministic-SWMM handler.
    banned = {"_run_script_tool", "_run_cli_tool"}

    offenders: dict[str, set[str]] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in _DETERMINISTIC_HANDLERS:
            continue
        used = _function_source_uses_helpers(node, banned)
        if used:
            offenders[node.name] = used

    assert not offenders, (
        "deterministic-SWMM handlers must route through MCP pool, not "
        f"subprocess: {offenders}"
    )


def test_deterministic_handlers_do_not_call_subprocess_run() -> None:
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)

    # If a deterministic handler shells out to ``subprocess.run`` it has
    # bypassed MCP. Allow-listed names are explicitly cleared above so
    # this test stays surgical.
    offenders: dict[str, list[str]] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in _DETERMINISTIC_HANDLERS:
            continue
        names = _collect_function_calls(node)
        if "run" in names and any("subprocess" in raw for raw in names):
            # ``subprocess.run`` shows up as call to ``.run`` after a
            # ``subprocess`` attribute walk; the second clause keeps it
            # specific.
            offenders[node.name] = sorted(set(names))
    assert not offenders, (
        f"deterministic-SWMM handlers must not subprocess.run: {offenders}"
    )


def test_deterministic_handlers_are_built_via_mcp_routed_factory() -> None:
    """Each deterministic ToolSpec name must have a handler whose body
    delegates to ``_make_mcp_routed_handler``.

    We assert this by looking up the handler attached to the ToolSpec,
    inspecting its closure (the factory returns a closed-over function
    with ``server`` and ``tool`` in scope), and verifying the closure
    cells exist.
    """

    registry_module = tool_registry
    tools = registry_module._build_tools()

    deterministic_to_skill = {
        "audit_run": "swmm-experiment-audit",
        "build_inp": "swmm-builder",
        "format_rainfall": "swmm-climate",
        "network_qa": "swmm-network",
        "network_to_inp": "swmm-network",
        "plot_run": "swmm-plot",
        "run_swmm_inp": "swmm-runner",
        "summarize_memory": "swmm-modeling-memory",
    }
    for tool_name, server in deterministic_to_skill.items():
        spec = tools[tool_name]
        handler = spec.handler
        # Handlers built by ``_make_mcp_routed_handler`` carry a
        # synthetic ``_mcp_routing`` attribute we set on them.
        routing = getattr(handler, "_mcp_routing", None)
        assert routing is not None, (
            f"{tool_name} is not built via _make_mcp_routed_handler — "
            "its handler is the legacy subprocess shim"
        )
        assert routing["server"] == server, (
            f"{tool_name} routes to {routing['server']}, expected {server}"
        )
