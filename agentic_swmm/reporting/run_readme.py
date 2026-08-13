"""One page at the top of a run directory, written for a person.

A finished run is a folder of a dozen entries with names like ``09_audit``,
``manifest.json`` and ``session_state.json``. Someone opening it for the first
time reported the obvious thing: they could not tell what any of it was, or
which file was the one they wanted.

This writes ``README.md`` at the run root: what the run was, what came out of
it, and where to look. It describes only what is actually on disk, so it never
promises a report or a figure that was not produced.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.swmm_runtime.run_layout import AGENT_DIR, OBSIDIAN_DIR

#: Stage directory -> what a reader will find in it, in plain words.
_STAGE_BLURB = {
    "00_raw": "raw inputs exactly as they arrived",
    "01_gis": "GIS layers",
    "02_params": "parameter tables",
    "03_climate": "climate and rainfall inputs",
    "04_network": "network geometry",
    "05_builder": "the assembled SWMM model (.inp)",
    "06_runner": "the SWMM run: model.rpt is the engine's own report",
    "07_qa": "preflight and postflight gate results",
    "08_plot": "figures",
    "09_audit": "the audit trail: diagnostics, provenance, hydraulic summary",
    "10_upstream": "what an upstream service returned, kept verbatim",
    "11_review": "design review output",
}

#: Root deliverables, in the order a reader should meet them.
_DELIVERABLES = (
    ("report.docx", "the Word deliverable"),
    ("final_report.md", "what the agent concluded, in full"),
    ("chat_note.md", "the conversation note for this turn"),
    ("manifest.json", "machine-readable index of this run"),
    ("session.yaml", "which model and tools produced it"),
)


def render_run_readme(run_dir: Path, *, goal: str = "", status: str = "") -> str:
    """Render the README text for ``run_dir`` from what exists on disk."""
    lines = [f"# {run_dir.name}", ""]
    if goal:
        lines += [f"**Goal.** {goal}", ""]
    if status:
        lines += [f"**Status.** {status}", ""]

    present = [(name, blurb) for name, blurb in _DELIVERABLES if (run_dir / name).exists()]
    if present:
        lines += ["## Start here", ""]
        lines += [f"- `{name}` — {blurb}" for name, blurb in present]
        lines.append("")

    stages = [
        (path.name, _STAGE_BLURB.get(path.name, "stage output"))
        for path in sorted(run_dir.iterdir())
        if path.is_dir() and path.name[:2].isdigit()
    ]
    if stages:
        lines += ["## Stages", ""]
        lines += [f"- `{name}/` — {blurb}" for name, blurb in stages]
        lines.append("")

    machine = []
    if (run_dir / AGENT_DIR).is_dir():
        machine.append(
            f"- `{AGENT_DIR}/` — the agent's own record: the full trace, session state, "
            "and the environment snapshot. Kept for reproducibility and for rebuilding "
            "the session database; nothing here is a result."
        )
    if (run_dir / OBSIDIAN_DIR).is_dir():
        machine.append(f"- `{OBSIDIAN_DIR}/` — what was exported to your notes vault.")
    if machine:
        lines += ["## Not for reading", ""] + machine + [""]

    lines += [
        "---",
        "",
        "A completed run is not a calibrated or validated one. Numbers here are model "
        "outputs; treating them as predictions requires observed data and the checks "
        "that go with it.",
        "",
    ]
    return "\n".join(lines)


def write_run_readme(run_dir: Path, *, goal: str = "", status: str = "") -> Path | None:
    """Write ``<run_dir>/README.md``. Best-effort; never raises."""
    try:
        if not run_dir.is_dir():
            return None
        path = run_dir / "README.md"
        path.write_text(render_run_readme(run_dir, goal=goal, status=status), encoding="utf-8")
        return path
    except OSError:
        return None


__all__ = ["render_run_readme", "write_run_readme"]
