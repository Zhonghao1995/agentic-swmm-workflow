from __future__ import annotations

from typing import Any


def capability_summary(tool_names: list[str]) -> dict[str, Any]:
    return {
        "filesystem": {
            "read": "Repository files can be listed, searched, and read. User-provided external INP files can be imported into runs/<case>/00_inputs/model.inp.",
            "write": "Writes are limited to Agentic SWMM run artifacts, reports, traces, and explicit tool outputs under the repository.",
            "arbitrary_shell": False,
        },
        "swmm": {
            "run_repository_inp": True,
            "run_external_inp_import": True,
            "audit": True,
            "plot": True,
            "calibration_or_validation_claims": "Only when observed-data evidence and validation checks exist.",
        },
        "workspace": {
            "list_dir": "Repository-local only.",
            "search_files": "Repository-local text search only.",
            "git_diff": "Read-only git diff inspection.",
            "apply_patch": "Repository-local unified diffs only; .git/.venv/secrets and evidence paths are blocked unless explicitly allowed.",
            "run_tests": "Pytest only, scoped to repository paths.",
            "run_allowed_command": "Allowlisted commands only: pytest, python -m agentic_swmm.cli, node scripts/*.mjs, swmm5.",
        },
        "web": {
            "enabled": True,
            "tools": ["web_search", "web_fetch_url"],
            "boundary": "Web research is source context, not SWMM run evidence or validation evidence.",
        },
        "mcp": {
            "enabled": True,
            "boundary": "MCP tools are discovered from the local Agentic SWMM registry and called through a traced stdio client.",
            "preference": "Prefer CLI wrappers for core audited run/audit/plot workflows; use MCP when its schema is more specific or no CLI wrapper exists.",
            "failure_recovery": "Failed MCP calls should be retried only after refreshing schemas or by falling back to a listed CLI wrapper.",
        },
        "tools": tool_names,
    }
