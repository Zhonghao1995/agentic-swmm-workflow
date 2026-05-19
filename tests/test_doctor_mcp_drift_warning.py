"""Doctor warns when ~/.aiswmm/mcp.json drifts from the active install (#114).

``aiswmm setup`` embeds **absolute paths** to the active checkout's
``scripts/run_mcp_server.mjs`` into ``~/.aiswmm/mcp.json``. On machines
with two checkouts (or an editable install pointing somewhere new),
those paths can drift away from the currently-active repo_root() and
the runtime ends up loading MCP servers from a stale checkout.

Doctor must surface this drift as a WARN row per server (or a single
combined WARN) so the user knows to ``aiswmm setup --refresh-mcp`` or
sync that checkout.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from agentic_swmm.commands import doctor


def _write_mcp_json(config_dir: Path, launcher_path: Path) -> Path:
    """Write a minimal mcp.json under ``config_dir`` with one server
    pointing at ``launcher_path``."""

    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcp_servers": [
            {
                "name": "swmm-builder",
                "enabled": True,
                "exists": True,
                "command": "/usr/bin/node",
                "args": [str(launcher_path), "swmm-builder"],
                "entrypoint": str(launcher_path.parent / "mcp" / "swmm-builder" / "server.js"),
                "package": str(launcher_path.parent / "mcp" / "swmm-builder" / "package.json"),
                "launcher": str(launcher_path),
            }
        ]
    }
    target = config_dir / "mcp.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def test_doctor_warns_when_mcp_json_routes_to_a_different_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Active repo: one path. mcp.json points into a different one.
    active_root = tmp_path / "active-checkout"
    active_root.mkdir()
    other_root = tmp_path / "other-checkout"
    other_root.mkdir()
    config_dir = tmp_path / "config"
    drifted_launcher = other_root / "scripts" / "run_mcp_server.mjs"
    drifted_launcher.parent.mkdir(parents=True)
    drifted_launcher.touch()
    _write_mcp_json(config_dir, drifted_launcher)

    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(doctor, "repo_root", lambda: active_root)

    # PRD-08 A.1 (audit #5/#28): the historical layout emitted one
    # ``WARN `` line per drifted server. The grouped-warns layout
    # collapses identical-cause rows into the new ``Issues:`` section,
    # so the test now checks that section instead.
    namespace = argparse.Namespace(json=False, fix=False, yes=False)
    doctor.main(namespace)
    output = capsys.readouterr().out

    # The grouped WARN must mention the drift target and the fix.
    assert "MCP server" in output and "drift" in output, (
        f"expected grouped MCP-drift summary; got:\n{output}"
    )
    assert str(other_root) in output, (
        f"expected drift summary to reference the other checkout; got:\n{output}"
    )
    assert "--refresh-mcp" in output, (
        f"expected --refresh-mcp remediation in grouped output; got:\n{output}"
    )


def test_doctor_does_not_warn_when_mcp_json_matches_active_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    active_root = tmp_path / "active-checkout"
    active_root.mkdir()
    aligned_launcher = active_root / "scripts" / "run_mcp_server.mjs"
    aligned_launcher.parent.mkdir(parents=True)
    aligned_launcher.touch()
    config_dir = tmp_path / "config"
    _write_mcp_json(config_dir, aligned_launcher)

    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(doctor, "repo_root", lambda: active_root)

    namespace = argparse.Namespace(json=False, fix=False, yes=False)
    doctor.main(namespace)
    output = capsys.readouterr().out

    # When paths align the grouped MCP-drift summary must not appear.
    assert "MCP server" not in output or "drift" not in output, (
        f"unexpected MCP-drift summary when paths are aligned: {output}"
    )


def test_doctor_skips_mcp_drift_check_when_no_mcp_json_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No mcp.json yet — typical pre-``aiswmm setup`` state. Doctor
    # must not crash and must not invent a drift WARN.
    active_root = tmp_path / "active-checkout"
    active_root.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(doctor, "repo_root", lambda: active_root)

    namespace = argparse.Namespace(json=False, fix=False, yes=False)
    rc = doctor.main(namespace)
    output = capsys.readouterr().out

    assert "MCP server" not in output or "drift" not in output, (
        f"unexpected MCP-drift summary when mcp.json is absent: {output}"
    )
    # Doctor still completes (no crash).
    assert isinstance(rc, int)
