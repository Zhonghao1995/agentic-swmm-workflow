"""``aiswmm doctor`` names bundled MCP servers whose node_modules is missing.

Live finding F-122 (2026-09-03, S55): two servers had never been installed on
the machine, every listing of them failed with "MCP process ended before
sending a complete line", and nothing in the product said why.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.commands import doctor


def _bundle(root: Path, name: str, *, installed: bool) -> None:
    server_dir = root / "mcp" / name
    server_dir.mkdir(parents=True)
    (server_dir / "package.json").write_text("{}\n", encoding="utf-8")
    if installed:
        (server_dir / "node_modules").mkdir()


def test_servers_without_node_modules_are_named(tmp_path: Path) -> None:
    _bundle(tmp_path, "swmm-plot", installed=True)
    _bundle(tmp_path, "swmm-uncertainty", installed=False)
    _bundle(tmp_path, "swmm-modeling-memory", installed=False)
    (tmp_path / "mcp" / "README.md").write_text("not a server\n", encoding="utf-8")
    assert doctor._mcp_servers_without_deps(tmp_path) == ["swmm-modeling-memory", "swmm-uncertainty"]


def test_no_mcp_directory_means_nothing_to_report(tmp_path: Path) -> None:
    assert doctor._mcp_servers_without_deps(tmp_path) == []
