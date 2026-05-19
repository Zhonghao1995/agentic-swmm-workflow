"""Pure helpers shared by workflow-mode adapters.

These functions were originally module-level helpers in
``agentic_swmm.agent.planner``. They are pure (input -> output) and
have no dependency on ``OpenAIPlanner`` state, so moving them out of
the planner module lets adapter classes import them without dragging
the planner along.

Behavioural parity with the planner versions is locked by the existing
end-to-end planner tests and by ``tests/test_workflow_mode_adapter_run_parity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentic_swmm.agent import intent_classifier
from agentic_swmm.utils.paths import repo_root


def workflow_route_args(goal: str) -> dict[str, Any]:
    """Extract the route arguments the planner passes to ``select_workflow_mode``."""
    args: dict[str, Any] = {"goal": goal}
    run_dir = extract_run_dir(goal)
    if run_dir:
        args["run_dir"] = run_dir
    inp = extract_inp_path(goal) or extract_example_inp_path(goal)
    if inp:
        args["inp_path"] = inp
    node = extract_after_label(goal, ("node", "outfall", "节点", "出口"))
    if node:
        args["node"] = node
    return args


def extract_inp_path(text: str) -> str | None:
    quoted = re.search(r"[\"']([^\"']+\.inp)[\"']", text, flags=re.I)
    if quoted:
        return quoted.group(1)
    match = re.search(
        r"([A-Za-z]:\\[^\n\r]+?\.inp|(?:\.{0,2}/)?[^\s\"']+\.inp)",
        text,
        flags=re.I,
    )
    return match.group(1).rstrip(".,;)]}") if match else None


def extract_run_dir(text: str) -> str | None:
    labelled = re.search(
        r"(?:run_dir|run folder|run directory|previous run directory|上一轮运行目录|运行目录)\s*[:=]\s*([^\n\r]+)",
        text,
        flags=re.I,
    )
    if labelled:
        return labelled.group(1).strip().rstrip(".,;)]}。")
    match = re.search(r"(runs/[^\s，。；;,)]+)", text, flags=re.I)
    return match.group(1).rstrip(".,;)]}。") if match else None


def extract_example_inp_path(text: str) -> str | None:
    match = re.search(r"(examples/[^\s，。；;,)]+)", text, flags=re.I)
    if not match:
        return None
    raw = match.group(1).rstrip("/.,;)]}。")
    candidate = (repo_root() / raw).resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".inp":
        return raw
    if candidate.is_dir():
        matches = sorted(path for path in candidate.glob("*.inp") if path.is_file())
        if len(matches) == 1:
            return matches[0].resolve().relative_to(repo_root().resolve()).as_posix()
    return raw


def extract_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[:=]\s*([A-Za-z0-9_.-]+)", text, flags=re.I
        )
        if match:
            return match.group(1)
    return None


def extract_plot_choice(goal: str, options: dict[str, Any]) -> dict[str, str] | None:
    lowered = goal.lower()
    explicit_plot = any(word in lowered for word in ("plot", "figure", "图", "画"))
    attrs = [
        str(item.get("name"))
        for item in options.get("node_attribute_options", [])
        if isinstance(item, dict)
    ]
    nodes = [str(item) for item in options.get("node_options", [])]
    rains = [
        str(item.get("name"))
        for item in options.get("rainfall_options", [])
        if isinstance(item, dict)
    ]

    node_attr = next(
        (
            attr
            for attr in attrs
            if attr.lower() in lowered and not _is_negated(lowered, attr.lower())
        ),
        None,
    )
    if node_attr is None:
        aliases = {
            "depth": "Depth_above_invert",
            "水深": "Depth_above_invert",
            "volume": "Volume_stored_ponded",
            "体积": "Volume_stored_ponded",
            "flood": "Flow_lost_flooding",
            "flooding": "Flow_lost_flooding",
            "淹没": "Flow_lost_flooding",
            "溢流": "Flow_lost_flooding",
            "head": "Hydraulic_head",
            "水头": "Hydraulic_head",
            "flow": "Total_inflow",
            "peak": "Total_inflow",
            "流量": "Total_inflow",
            "峰值": "Total_inflow",
        }
        node_attr = next(
            (
                value
                for key, value in aliases.items()
                if key in lowered and not _is_negated(lowered, key) and value in attrs
            ),
            None,
        )
    node = next((candidate for candidate in nodes if candidate.lower() in lowered), None)
    rain_ts = next((candidate for candidate in rains if candidate.lower() in lowered), None)

    if _asks_for_plot_options(lowered) and node_attr is None:
        return None
    if not explicit_plot and node_attr is None:
        return None
    defaults = options.get("defaults") if isinstance(options.get("defaults"), dict) else {}
    choice = {
        "node": node or str(defaults.get("node") or (nodes[0] if nodes else "O1")),
        "node_attr": node_attr or str(defaults.get("node_attr") or "Total_inflow"),
    }
    if rain_ts or defaults.get("rain_ts"):
        choice["rain_ts"] = rain_ts or str(defaults["rain_ts"])
    rain_kind = _default_rain_kind(options, choice.get("rain_ts"))
    if rain_kind:
        choice["rain_kind"] = rain_kind
    return choice


def _asks_for_plot_options(lowered: str) -> bool:
    return any(
        phrase in lowered
        for phrase in (
            "作图选项",
            "绘图选项",
            "别的图",
            "其他图",
            "换个图",
            "自己选",
            "我自己选",
            "有哪些图",
            "能画别的",
            "不想要",
            "不要 peak",
            "不要peak",
            "not peak",
            "not total_inflow",
        )
    )


def _is_negated(lowered: str, term: str) -> bool:
    return intent_classifier.is_negated(lowered, term)


def _default_rain_kind(options: dict[str, Any], rain_ts: str | None) -> str | None:
    for item in options.get("rainfall_options", []):
        if isinstance(item, dict) and item.get("name") == rain_ts and item.get("rain_kind"):
            return str(item["rain_kind"])
    return None


def plot_choice_prompt(session_dir: Path, options: dict[str, Any]) -> str:
    defaults = options.get("defaults") if isinstance(options.get("defaults"), dict) else {}
    nodes = [str(item) for item in options.get("node_options", [])]
    attrs = [
        str(item.get("name"))
        for item in options.get("node_attribute_options", [])
        if isinstance(item, dict)
    ]
    rains = [
        str(item.get("name"))
        for item in options.get("rainfall_options", [])
        if isinstance(item, dict)
    ]
    node_preview = ", ".join(nodes[:8]) + (" ..." if len(nodes) > 8 else "")
    attr_preview = ", ".join(attrs[:8])
    rain_preview = ", ".join(rains) if rains else "auto"
    return (
        "SWMM run and audit completed successfully.\n\n"
        f"Run folder: {session_dir}\n"
        f"Audit note: {session_dir / 'experiment_note.md'}\n\n"
        "Before plotting, choose what you want to see:\n"
        f"- rainfall series: {rain_preview}\n"
        f"- node/outfall options: {node_preview}\n"
        f"- plot variable options: {attr_preview}\n\n"
        "Common choices are `Total_inflow` for flow/peak hydrograph, `Depth_above_invert` for node water depth, "
        "`Volume_stored_ponded` for stored volume, and `Flow_lost_flooding` for flooding loss.\n\n"
        f"Default suggestion: node `{defaults.get('node')}`, variable `{defaults.get('node_attr')}`, rainfall `{defaults.get('rain_ts')}`. "
        "Reply with the node and variable you want to plot."
    )


def plot_output_path(run_dir: Path, choice: dict[str, str]) -> Path:
    node = re.sub(r"[^A-Za-z0-9_.-]+", "_", choice.get("node", "node")).strip("_") or "node"
    attr = re.sub(r"[^A-Za-z0-9_.-]+", "_", choice.get("node_attr", "series")).strip("_") or "series"
    return run_dir / "07_plots" / f"fig_{node}_{attr}.png"


def prepared_inp_done_text(session_dir: Path, *, plot_path: Path | None = None) -> str:
    plot_line = f"Plot: {plot_path}" if plot_path else "Plot: not generated"
    return (
        "SWMM run, audit, and plotting completed successfully.\n\n"
        f"Run folder: {session_dir}\n"
        f"Audit note: {session_dir / 'experiment_note.md'}\n"
        f"{plot_line}\n\n"
        "Evidence boundary: this is runnable/auditable SWMM evidence, not calibration or validation unless observed-data checks are added."
    )


def existing_run_plot_done_text(
    run_dir: Path, choice: dict[str, str], *, plot_path: Path
) -> str:
    details = ", ".join(f"{key}={value}" for key, value in choice.items())
    return (
        "Plot completed from the previous SWMM run.\n\n"
        f"Run folder: {run_dir}\n"
        f"Plot: {plot_path}\n"
        f"Selection: {details}\n\n"
        "Evidence boundary: the plot was generated from the existing run artifacts."
    )
