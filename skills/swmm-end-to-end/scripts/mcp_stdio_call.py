#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


class McpStdioClient:
    def __init__(self, server_dir: Path) -> None:
        self.server_dir = server_dir
        self.proc: subprocess.Popen[str] | None = None
        self.next_id = 1

    def __enter__(self) -> "McpStdioClient":
        self.proc = subprocess.Popen(
            ["node", "server.js"],
            cwd=self.server_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP process is not running")

        request_id = self.next_id
        self.next_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

        while True:
            line = self.proc.stdout.readline()
            if line:
                response = json.loads(line)
                if response.get("id") == request_id:
                    if "error" in response:
                        raise RuntimeError(json.dumps(response["error"], indent=2))
                    return response
                continue

            if self.proc.poll() is not None:
                stderr = ""
                if self.proc.stderr is not None:
                    stderr = self.proc.stderr.read()
                raise RuntimeError(f"MCP server exited before response to {method}. stderr={stderr}")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP process is not running")
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Call an Agentic SWMM MCP tool over stdio and write the raw response.")
    ap.add_argument("--server-dir", type=Path, required=True, help="MCP server directory containing server.js.")
    ap.add_argument("--tool", required=True)
    ap.add_argument("--arguments-json", required=True, help="JSON object string for tool arguments.")
    ap.add_argument("--out-response", type=Path, required=True)
    ap.add_argument("--protocol-version", default="2024-11-05")
    return ap.parse_args()


def absolutize_path_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: absolutize_path_args(item) for key, item in value.items()}
    if isinstance(value, list):
        return [absolutize_path_args(item) for item in value]
    if not isinstance(value, str):
        return value
    if not value or value.startswith("/") or "://" in value:
        return value
    if "/" not in value:
        return value
    return str((REPO_ROOT / value).resolve())


def main() -> None:
    args = parse_args()
    server_dir = args.server_dir if args.server_dir.is_absolute() else REPO_ROOT / args.server_dir
    if not (server_dir / "server.js").exists():
        raise FileNotFoundError(f"MCP server.js not found: {server_dir / 'server.js'}")
    tool_args = json.loads(args.arguments_json)
    if not isinstance(tool_args, dict):
        raise ValueError("--arguments-json must decode to a JSON object")
    tool_args = absolutize_path_args(tool_args)

    with McpStdioClient(server_dir) as client:
        initialize = client.request(
            "initialize",
            {
                "protocolVersion": args.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "agentic-swmm-framework-harness", "version": "0.1.0"},
            },
        )
        client.notify("notifications/initialized", {})
        tools = client.request("tools/list", {})
        tool_names = [tool.get("name") for tool in tools.get("result", {}).get("tools", [])]
        if args.tool not in tool_names:
            raise ValueError(f"Tool '{args.tool}' not exposed by {server_dir}. Available tools: {tool_names}")
        response = client.request("tools/call", {"name": args.tool, "arguments": tool_args})

    args.out_response.parent.mkdir(parents=True, exist_ok=True)
    args.out_response.write_text(json.dumps(response, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "transport": "mcp_stdio",
                "server_dir": str(server_dir),
                "server": initialize.get("result", {}).get("serverInfo", {}),
                "tool": args.tool,
                "out_response": str(args.out_response),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
