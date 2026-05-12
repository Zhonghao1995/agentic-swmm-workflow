from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.agent import mcp_client
from agentic_swmm.agent.policy import capability_summary
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall
from agentic_swmm.runtime.registry import discover_skills, load_mcp_registry
from agentic_swmm.utils.paths import repo_root, script_path
from agentic_swmm.utils.subprocess_runner import runtime_env


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolCall, Path], dict[str, Any]]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description, "parameters": self.parameters}


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools = _build_tools()

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def sorted_names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def validate(self, call: ProviderToolCall) -> ToolCall:
        if call.name not in self._tools:
            raise ValueError(f"planner requested unsupported tool: {call.name}")
        return ToolCall(call.name, dict(call.arguments))

    def execute(self, call: ToolCall, session_dir: Path) -> dict[str, Any]:
        spec = self._tools.get(call.name)
        if spec is None:
            return {"tool": call.name, "args": call.args, "ok": False, "summary": f"unsupported tool: {call.name}"}
        return spec.handler(call, session_dir)

    def output_for_model(self, result: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {
            "tool",
            "args",
            "ok",
            "return_code",
            "summary",
            "stdout_tail",
            "stderr_tail",
            "path",
            "chars",
            "excerpt",
            "results",
            "servers",
            "tools",
            "capabilities",
            "results",
        }
        return {key: value for key, value in result.items() if key in allowed_keys}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _build_tools() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec("audit_run", "Audit a run directory and write deterministic provenance/comparison/note artifacts.", _object({"run_dir": {"type": "string"}, "workflow_mode": {"type": "string"}, "objective": {"type": "string"}}, ["run_dir"]), _audit_run_tool),
        ToolSpec("build_inp", "Assemble a SWMM INP from explicit CSV/JSON/text inputs using the swmm-builder skill.", _object({"subcatchments_csv": {"type": "string"}, "params_json": {"type": "string"}, "network_json": {"type": "string"}, "rainfall_json": {"type": "string"}, "raingage_json": {"type": "string"}, "timeseries_text": {"type": "string"}, "config_json": {"type": "string"}, "default_gage_id": {"type": "string"}, "out_inp": {"type": "string"}, "out_manifest": {"type": "string"}}, ["subcatchments_csv", "params_json", "network_json", "out_inp", "out_manifest"]), _build_inp_tool),
        ToolSpec("capabilities", "Describe what this runtime can and cannot access.", _object({}), _capabilities_tool),
        ToolSpec("demo_acceptance", "Run the prepared acceptance demo through the Agentic SWMM CLI.", _object({"run_id": {"type": "string"}, "keep_existing": {"type": "boolean"}}), _demo_acceptance_tool),
        ToolSpec("doctor", "Run the built-in Agentic SWMM runtime doctor.", _object({}), _doctor_tool),
        ToolSpec("format_rainfall", "Format rainfall CSV into SWMM TIMESERIES text and metadata JSON using the swmm-climate skill.", _object({"input_csv": {"type": "string"}, "out_json": {"type": "string"}, "out_timeseries": {"type": "string"}, "series_name": {"type": "string"}, "timestamp_column": {"type": "string"}, "value_column": {"type": "string"}, "value_units": {"type": "string"}, "unit_policy": {"type": "string", "enum": ["strict", "convert_to_mm_per_hr"]}, "timestamp_policy": {"type": "string", "enum": ["strict", "sort"]}}, ["input_csv", "out_json", "out_timeseries"]), _format_rainfall_tool),
        ToolSpec("git_diff", "Read the current repository diff or diff stat.", _object({"stat_only": {"type": "boolean"}, "path": {"type": "string"}}), _git_diff_tool),
        ToolSpec("list_dir", "List a repository directory.", _object({"path": {"type": "string"}}), _list_dir_tool),
        ToolSpec("list_mcp_servers", "List configured local MCP servers.", _object({}), _list_mcp_servers_tool),
        ToolSpec("list_mcp_tools", "List tools exposed by one configured MCP server.", _object({"server": {"type": "string"}}, ["server"]), _list_mcp_tools_tool),
        ToolSpec("call_mcp_tool", "Call a tool exposed by a configured local MCP server.", _object({"server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}}, ["server", "tool"]), _call_mcp_tool_tool),
        ToolSpec("list_skills", "List available repository skills.", _object({}), _list_skills_tool),
        ToolSpec("network_qa", "Validate a SWMM network JSON using the swmm-network QA script.", _object({"network_json": {"type": "string"}, "report_json": {"type": "string"}}, ["network_json"]), _network_qa_tool),
        ToolSpec("network_to_inp", "Export a SWMM network JSON to INP section text using the swmm-network script.", _object({"network_json": {"type": "string"}, "out_path": {"type": "string"}}, ["network_json", "out_path"]), _network_to_inp_tool),
        ToolSpec("plot_run", "Create a rainfall-runoff plot from a run directory using the swmm-plot skill wrapper.", _object({"run_dir": {"type": "string"}, "node": {"type": "string"}, "rain_ts": {"type": "string"}, "rain_kind": {"type": "string", "enum": ["intensity_mm_per_hr", "depth_mm_per_dt", "cumulative_depth_mm"]}, "out_png": {"type": "string"}}, ["run_dir"]), _plot_run_tool),
        ToolSpec("read_file", "Read a repository file and return a bounded excerpt.", _object({"path": {"type": "string"}}, ["path"]), _read_file_tool),
        ToolSpec("read_skill", "Read a skill contract from skills/<skill_name>/SKILL.md.", _object({"skill_name": {"type": "string"}}, ["skill_name"]), _read_skill_tool),
        ToolSpec("run_swmm_inp", "Run a repository or imported external .inp file through the constrained swmm-runner CLI wrapper.", _object({"inp_path": {"type": "string"}, "run_id": {"type": "string"}, "run_dir": {"type": "string"}, "node": {"type": "string"}}, ["inp_path"]), _run_swmm_inp_tool),
        ToolSpec("search_files", "Search text files in the repository.", _object({"query": {"type": "string"}, "glob": {"type": "string"}, "max_results": {"type": "integer"}}), _search_files_tool),
        ToolSpec("select_workflow_mode", "Select the top-level swmm-end-to-end operating mode and report required/missing inputs before running tools.", _object({"goal": {"type": "string"}, "inp_path": {"type": "string"}, "run_dir": {"type": "string"}, "node": {"type": "string"}, "network_json": {"type": "string"}, "subcatchments_csv": {"type": "string"}, "rainfall_input": {"type": "string"}, "landuse_input": {"type": "string"}, "soil_input": {"type": "string"}, "observed_flow": {"type": "string"}, "fuzzy_config": {"type": "string"}, "baseline_run_dir": {"type": "string"}}, ["goal"]), _select_workflow_mode_tool),
        ToolSpec("summarize_memory", "Summarize audited runs into the modeling-memory directory.", _object({"runs_dir": {"type": "string"}, "out_dir": {"type": "string"}}, ["runs_dir"]), _summarize_memory_tool),
        ToolSpec("web_fetch_url", "Fetch and summarize a web page. Web evidence is not SWMM run evidence.", _object({"url": {"type": "string"}, "max_chars": {"type": "integer"}}), _web_fetch_url_tool),
        ToolSpec("web_search", "Run a lightweight web search and return cited result URLs. Web evidence is not SWMM run evidence.", _object({"query": {"type": "string"}, "allowed_domains": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}}), _web_search_tool),
    ]
    return {spec.name: spec for spec in specs}


def _doctor_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    return _run_cli_tool(call, session_dir, ["doctor"])


def _demo_acceptance_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["demo", "acceptance", "--run-id", str(call.args.get("run_id", "agent-latest"))]
    if call.args.get("keep_existing"):
        command.append("--keep-existing")
    return _run_cli_tool(call, session_dir, command)


def _audit_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["audit", "--run-dir", str(call.args["run_dir"])]
    if call.args.get("workflow_mode"):
        command.extend(["--workflow-mode", str(call.args["workflow_mode"])])
    if call.args.get("objective"):
        command.extend(["--objective", str(call.args["objective"])])
    return _run_cli_tool(call, session_dir, command)


def _summarize_memory_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["memory", "--runs-dir", str(call.args["runs_dir"])]
    if call.args.get("out_dir"):
        command.extend(["--out-dir", str(call.args["out_dir"])])
    return _run_cli_tool(call, session_dir, command)


def _read_file_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args["path"]))
    if path is None:
        return _failure(call, "refusing to read outside repository")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": text[:4000], "summary": f"read {path.relative_to(repo_root())}"}


def _list_skills_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skills = [{"name": str(r.get("name")), "enabled": bool(r.get("enabled", True)), "path": str(r.get("path"))} for r in discover_skills()]
    return {"tool": call.name, "args": call.args, "ok": True, "skills": skills, "summary": f"{len(skills)} skills available", "excerpt": json.dumps(skills, indent=2)[:4000]}


def _read_skill_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    skill_name = str(call.args["skill_name"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", skill_name):
        return _failure(call, "invalid skill name")
    path = _repo_path(f"skills/{skill_name}/SKILL.md")
    if path is None or not path.exists() or not path.is_file():
        return _failure(call, f"skill not found: {skill_name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"tool": call.name, "args": call.args, "ok": True, "path": str(path), "chars": len(text), "excerpt": text[:4000], "summary": f"read skill {skill_name}"}


def _run_swmm_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    inp = _resolve_inp_for_run(call)
    if isinstance(inp, dict):
        return inp
    run_dir = _optional_repo_output_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    if run_dir is None:
        run_id = str(call.args.get("run_id") or f"{_safe_name(inp.stem)}-{int(time.time())}")
        run_dir = repo_root() / "runs" / "agent" / _safe_name(run_id)
    command = ["run", "--inp", str(inp), "--run-dir", str(run_dir), "--node", str(call.args.get("node") or "O1")]
    return _run_cli_tool(call, session_dir, command)


def _select_workflow_mode_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    goal = str(call.args.get("goal") or "").lower()
    provided = {key: str(value).strip() for key, value in call.args.items() if isinstance(value, str) and value.strip()}

    wants_calibration = any(word in goal for word in ("calibration", "calibrate", "observed", "nse", "kge", "??", "??"))
    wants_uncertainty = any(word in goal for word in ("uncertainty", "fuzzy", "sensitivity", "???", "??"))
    wants_audit = "audit" in goal or "comparison" in goal or "compare" in goal or "??" in goal or "??" in goal
    wants_demo = any(word in goal for word in ("demo", "acceptance", "tecnopolo", "tuflow", "??", "??"))
    has_inp = bool(provided.get("inp_path"))
    full_build_inputs = ["network_json", "subcatchments_csv", "rainfall_input", "landuse_input", "soil_input"]
    has_full_build = all(provided.get(key) for key in full_build_inputs)

    if wants_calibration:
        mode = "calibration"
        required = ["inp_path", "observed_flow", "node"]
        next_tools = ["run_swmm_inp", "audit_run"]
        boundary = "Calibration requires observed flow evidence and recorded parameter-selection artifacts; a successful run alone is not calibration."
    elif wants_uncertainty:
        mode = "uncertainty"
        required = ["inp_path", "fuzzy_config", "node"]
        next_tools = ["run_swmm_inp", "audit_run"]
        boundary = "Uncertainty runs produce scenario evidence, not calibrated predictive uncertainty unless supported by observed-data validation."
    elif wants_demo:
        mode = "prepared_demo"
        required = []
        next_tools = ["demo_acceptance", "audit_run"]
        boundary = "Prepared demos are smoke or benchmark evidence, not proof of arbitrary greenfield modeling."
    elif wants_audit and not has_inp:
        mode = "audit_only_or_comparison"
        required = ["run_dir"]
        if "compare" in goal or "comparison" in goal or "??" in goal:
            required.append("baseline_run_dir")
        next_tools = ["audit_run"]
        boundary = "Audit records existing artifacts; it does not create missing SWMM execution evidence."
    elif has_inp:
        mode = "prepared_inp_cli"
        required = ["inp_path", "node"]
        next_tools = ["run_swmm_inp", "audit_run", "plot_run"]
        boundary = "Prepared INP execution is runnable/checkable/auditable evidence, not calibration or validation by itself."
    elif has_full_build:
        mode = "full_modular_build"
        required = full_build_inputs
        next_tools = ["format_rainfall", "network_qa", "build_inp", "run_swmm_inp", "audit_run"]
        boundary = "Full modular build requires explicit GIS/network/rainfall/parameter inputs; the agent must not invent missing model inputs."
    else:
        mode = "needs_user_inputs"
        required = ["inp_path or full modular build inputs"]
        next_tools = []
        boundary = "No SWMM execution should start until a prepared INP or complete build inputs are provided."

    missing = [item for item in required if item not in provided]
    if "inp_path or full modular build inputs" in required:
        missing = ["SWMM INP path, or network_json + subcatchments_csv + rainfall_input + landuse_input + soil_input"]
    if mode == "prepared_demo":
        missing = []

    result = {
        "mode": mode,
        "top_level_contract": "skills/swmm-end-to-end/SKILL.md",
        "required_inputs": required,
        "provided_inputs": sorted(provided),
        "missing_inputs": missing,
        "recommended_next_tools": [] if missing else next_tools,
        "stop_reason": "missing critical input" if missing else None,
        "evidence_boundary": boundary,
        "user_prompt": _workflow_user_prompt(mode, missing),
    }
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"mode={mode} missing={len(missing)}"}


def _plot_run_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    run_dir = _required_repo_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    command = ["plot", "--run-dir", str(run_dir), "--node", str(call.args.get("node") or "O1")]
    for key, flag in (("rain_ts", "--rain-ts"), ("rain_kind", "--rain-kind")):
        if call.args.get(key):
            command.extend([flag, str(call.args[key])])
    if call.args.get("out_png"):
        out_png = _repo_output_path(str(call.args["out_png"]))
        if out_png is None or out_png.suffix.lower() != ".png":
            return _failure(call, "out_png must be a repository-relative .png path")
        command.extend(["--out-png", str(out_png)])
    return _run_cli_tool(call, session_dir, command)


def _network_qa_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    command = [str(script_path("skills", "swmm-network", "scripts", "network_qa.py")), str(network_json)]
    if call.args.get("report_json"):
        report = _repo_output_path(str(call.args["report_json"]))
        if report is None or report.suffix.lower() != ".json":
            return _failure(call, "report_json must be a repository-relative .json path")
        command.extend(["--report-json", str(report)])
    return _run_script_tool(call, session_dir, command)


def _network_to_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    network_json = _required_repo_file(call, "network_json", suffix=".json")
    if isinstance(network_json, dict):
        return network_json
    out_path = _repo_output_path(str(call.args["out_path"]))
    if out_path is None or out_path.suffix.lower() not in {".inp", ".txt"}:
        return _failure(call, "out_path must be a repository-relative .inp or .txt path")
    command = [str(script_path("skills", "swmm-network", "scripts", "network_to_inp.py")), str(network_json), "--out", str(out_path)]
    return _run_script_tool(call, session_dir, command)


def _format_rainfall_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    input_csv = _required_repo_file(call, "input_csv", suffix=".csv")
    if isinstance(input_csv, dict):
        return input_csv
    out_json = _repo_output_path(str(call.args["out_json"]))
    out_timeseries = _repo_output_path(str(call.args["out_timeseries"]))
    if out_json is None or out_json.suffix.lower() != ".json":
        return _failure(call, "out_json must be a repository-relative .json path")
    if out_timeseries is None or out_timeseries.suffix.lower() not in {".txt", ".dat"}:
        return _failure(call, "out_timeseries must be a repository-relative .txt or .dat path")
    command = [str(script_path("skills", "swmm-climate", "scripts", "format_rainfall.py")), "--input", str(input_csv), "--out-json", str(out_json), "--out-timeseries", str(out_timeseries)]
    for arg_name, flag in (("series_name", "--series-name"), ("timestamp_column", "--timestamp-column"), ("value_column", "--value-column"), ("value_units", "--value-units"), ("unit_policy", "--unit-policy"), ("timestamp_policy", "--timestamp-policy")):
        if call.args.get(arg_name):
            command.extend([flag, str(call.args[arg_name])])
    return _run_script_tool(call, session_dir, command)


def _build_inp_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    resolved: dict[str, Path] = {}
    for key, suffix in {"subcatchments_csv": ".csv", "params_json": ".json", "network_json": ".json"}.items():
        path = _required_repo_file(call, key, suffix=suffix)
        if isinstance(path, dict):
            return path
        resolved[key] = path
    out_inp = _repo_output_path(str(call.args["out_inp"]))
    out_manifest = _repo_output_path(str(call.args["out_manifest"]))
    if out_inp is None or out_inp.suffix.lower() != ".inp":
        return _failure(call, "out_inp must be a repository-relative .inp path")
    if out_manifest is None or out_manifest.suffix.lower() != ".json":
        return _failure(call, "out_manifest must be a repository-relative .json path")
    command = [str(script_path("skills", "swmm-builder", "scripts", "build_swmm_inp.py")), "--subcatchments-csv", str(resolved["subcatchments_csv"]), "--params-json", str(resolved["params_json"]), "--network-json", str(resolved["network_json"]), "--out-inp", str(out_inp), "--out-manifest", str(out_manifest)]
    for key, (flag, suffix) in {"rainfall_json": ("--rainfall-json", ".json"), "raingage_json": ("--raingage-json", ".json"), "timeseries_text": ("--timeseries-text", None), "config_json": ("--config-json", ".json")}.items():
        if call.args.get(key):
            path = _required_repo_file(call, key, suffix=suffix)
            if isinstance(path, dict):
                return path
            command.extend([flag, str(path)])
    if call.args.get("default_gage_id"):
        command.extend(["--default-gage-id", str(call.args["default_gage_id"])])
    return _run_script_tool(call, session_dir, command)


def _list_dir_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    path = _repo_path(str(call.args.get("path") or "."))
    if path is None or not path.exists() or not path.is_dir():
        return _failure(call, "directory must exist inside repository")
    entries = [{"name": item.name, "type": "dir" if item.is_dir() else "file", "path": str(item.relative_to(repo_root()))} for item in sorted(path.iterdir())[:200]]
    return {"tool": call.name, "args": call.args, "ok": True, "results": entries, "summary": f"{len(entries)} entries"}


def _search_files_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    pattern = str(call.args.get("glob") or "*")
    max_results = int(call.args.get("max_results") or 50)
    results: list[dict[str, Any]] = []
    for path in repo_root().rglob(pattern):
        if len(results) >= max_results:
            break
        if not path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({"path": str(path.relative_to(repo_root())), "line": lineno, "text": line.strip()[:300]})
                break
    return {"tool": call.name, "args": call.args, "ok": True, "results": results, "summary": f"{len(results)} match(es)"}


def _git_diff_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    command = ["git", "diff", "--stat" if call.args.get("stat_only", True) else "--"]
    if call.args.get("path"):
        command.extend(["--", str(call.args["path"])])
    proc = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True)
    return {"tool": call.name, "args": call.args, "ok": proc.returncode == 0, "return_code": proc.returncode, "excerpt": proc.stdout[:8000], "stderr_tail": _tail(proc.stderr), "summary": "git diff read" if proc.returncode == 0 else "git diff failed"}


def _web_fetch_url_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    url = str(call.args.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _failure(call, "url must be http(s)")
    max_chars = int(call.args.get("max_chars") or 6000)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web fetch failed: {exc}")
    text = _strip_html(raw)
    return {"tool": call.name, "args": call.args, "ok": True, "path": url, "chars": len(text), "excerpt": text[:max_chars], "summary": f"fetched {url}"}


def _web_search_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    query = str(call.args.get("query") or "").strip()
    if not query:
        return _failure(call, "query is required")
    max_results = min(int(call.args.get("max_results") or 5), 10)
    allowed = [str(domain).lower() for domain in call.args.get("allowed_domains") or []]
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "aiswmm-agent/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return _failure(call, f"web search failed: {exc}")
    results = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        href = html.unescape(match.group(1))
        title = _strip_html(html.unescape(match.group(2))).strip()
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc == "duckduckgo.com":
            params = urllib.parse.parse_qs(parsed.query)
            href = params.get("uddg", [href])[0]
            parsed = urllib.parse.urlparse(href)
        if allowed and not any(parsed.netloc.lower().endswith(domain) for domain in allowed):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break
    return {"tool": call.name, "args": call.args, "ok": True, "results": results, "summary": f"{len(results)} web result(s); cite URLs and keep web evidence separate from run evidence"}


def _list_mcp_servers_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    servers = load_mcp_registry()
    return {"tool": call.name, "args": call.args, "ok": True, "servers": servers, "summary": f"{len(servers)} configured MCP server(s)"}


def _list_mcp_tools_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _failure(call, f"MCP server not found: {call.args['server']}")
    try:
        tools = mcp_client.list_tools(str(server["command"]), [str(arg) for arg in server.get("args", [])])
    except Exception as exc:
        return _failure(call, f"MCP tools/list failed: {exc}")
    return {"tool": call.name, "args": call.args, "ok": True, "tools": tools, "summary": f"{len(tools)} MCP tool(s) on {server['name']}"}


def _call_mcp_tool_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    server = _mcp_server(str(call.args["server"]))
    if server is None:
        return _failure(call, f"MCP server not found: {call.args['server']}")
    arguments = call.args.get("arguments") if isinstance(call.args.get("arguments"), dict) else {}
    try:
        result = mcp_client.call_tool(str(server["command"]), [str(arg) for arg in server.get("args", [])], str(call.args["tool"]), arguments)
    except Exception as exc:
        return _failure(call, f"MCP tools/call failed: {exc}")
    return {"tool": call.name, "args": call.args, "ok": True, "results": result, "summary": f"called MCP tool {server['name']}.{call.args['tool']}"}


def _capabilities_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    names = sorted(_build_tools())
    return {"tool": call.name, "args": call.args, "ok": True, "capabilities": capability_summary(names), "summary": "runtime capabilities returned"}


def _workflow_user_prompt(mode: str, missing: list[str]) -> str:
    if not missing:
        return "Inputs are sufficient for the selected workflow mode. Continue with the recommended tools."
    if mode == "needs_user_inputs":
        return "Please provide a SWMM INP path, or the complete full-build input set: network_json, subcatchments_csv, rainfall_input, landuse_input, and soil_input."
    return "Please provide: " + ", ".join(missing)


def _run_cli_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, "-m", "agentic_swmm.cli", *cli_args], cwd=repo_root())


def _run_script_tool(call: ToolCall, session_dir: Path, cli_args: list[str]) -> dict[str, Any]:
    return _run_process_tool(call, session_dir, [sys.executable, *cli_args], cwd=repo_root())


def _run_process_tool(call: ToolCall, session_dir: Path, command: list[str], *, cwd: Path) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=runtime_env())
    finished = datetime.now(timezone.utc)
    safe_name = _safe_name(call.name)
    stdout_path = session_dir / "tool_results" / f"{safe_name}.stdout.txt"
    stderr_path = session_dir / "tool_results" / f"{safe_name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {"tool": call.name, "args": call.args, "command": command, "ok": proc.returncode == 0, "return_code": proc.returncode, "started_at_utc": started.isoformat(timespec="seconds"), "finished_at_utc": finished.isoformat(timespec="seconds"), "stdout_file": str(stdout_path), "stderr_file": str(stderr_path), "stdout_tail": _tail(proc.stdout), "stderr_tail": _tail(proc.stderr), "summary": _summarize_cli_result(call.name, proc.stdout, proc.returncode)}


def _summarize_cli_result(tool: str, stdout: str, return_code: int) -> str:
    if return_code != 0:
        return f"{tool} failed"
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        if "run_dir" in parsed:
            return f"run_dir={parsed['run_dir']}"
        if "experiment_note" in parsed:
            return f"audit_note={parsed['experiment_note']}"
        if "ok" in parsed and "issue_count" in parsed:
            return f"ok={parsed['ok']} issue_count={parsed['issue_count']}"
        if "outputs" in parsed:
            return "outputs=" + json.dumps(parsed["outputs"], sort_keys=True)[:500]
    stripped = stdout.strip().splitlines()
    return stripped[-1] if stripped else "completed"


def _repo_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


def _repo_output_path(value: str) -> Path | None:
    path = _repo_path(value)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _required_repo_file(call: ToolCall, key: str, *, suffix: str | None = None) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required file argument: {key}")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    if suffix and path.suffix.lower() != suffix:
        return _failure(call, f"{key} must end with {suffix}")
    if not path.exists() or not path.is_file():
        return _failure(call, f"file not found: {path}")
    return path


def _resolve_inp_for_run(call: ToolCall) -> Path | dict[str, Any]:
    raw = str(call.args.get("inp_path", "")).strip()
    if not raw:
        return _failure(call, "missing required file argument: inp_path")
    repo_file = _required_repo_file(call, "inp_path", suffix=".inp")
    if not isinstance(repo_file, dict):
        return repo_file
    resolved = _find_repo_inp(raw)
    if resolved is not None:
        return resolved
    external = Path(raw).expanduser()
    try:
        external = external.resolve()
    except OSError:
        return _failure(call, f"inp_path could not be resolved: {raw}")
    if external.suffix.lower() != ".inp":
        return _failure(call, "inp_path must end with .inp")
    if not external.exists() or not external.is_file():
        return _failure(call, f"external INP file not found: {external}")
    return external


def _find_repo_inp(value: str) -> Path | None:
    if not value or Path(value).is_absolute() or "/" in value:
        return None
    root = repo_root() / "examples"
    if not root.exists():
        return None
    matches = sorted(path for path in root.rglob(value) if path.is_file() and path.suffix.lower() == ".inp")
    return matches[0] if matches else None


def _required_repo_dir(call: ToolCall, key: str) -> Path | dict[str, Any]:
    value = call.args.get(key)
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"missing required directory argument: {key}")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    if not path.exists() or not path.is_dir():
        return _failure(call, f"directory not found: {path}")
    return path


def _optional_repo_output_dir(call: ToolCall, key: str) -> Path | dict[str, Any] | None:
    value = call.args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return _failure(call, f"{key} must be a non-empty string")
    path = _repo_path(value)
    if path is None:
        return _failure(call, f"{key} must be inside repository")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mcp_server(name: str) -> dict[str, Any] | None:
    for server in load_mcp_registry():
        if str(server.get("name")) == name:
            return server
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _failure(call: ToolCall, summary: str) -> dict[str, Any]:
    return {"tool": call.name, "args": call.args, "ok": False, "summary": summary}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tail(text: str, max_chars: int = 2000) -> str:
    return text.strip()[-max_chars:]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "agent"
