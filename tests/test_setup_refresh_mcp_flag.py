"""``aiswmm setup --refresh-mcp`` regenerates mcp.json against the
active editable install without re-running the full interactive setup
(#114).

User story: after re-installing aiswmm from a different checkout
(e.g. moving the editable install from a stale worktree back to the
main checkout), the user wants a one-liner that re-points
``~/.aiswmm/mcp.json`` at the new launcher path without re-doing
provider / obsidian / model questions.

The flag must:

* regenerate mcp.json so every server's launcher path is under the
  current ``repo_root()``,
* NOT touch any other ``~/.aiswmm/`` file (config.toml, skills.json,
  memory.json, setup_state.json) — those are owned by the full setup.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agentic_swmm.commands import setup as setup_cmd
from agentic_swmm.config import mcp_registry_path


def _invoke_setup(refresh_mcp: bool) -> int:
    parser = argparse.ArgumentParser()
    setup_cmd.register(parser.add_subparsers(dest="command"))
    argv = ["setup"]
    if refresh_mcp:
        argv.append("--refresh-mcp")
    args = parser.parse_args(argv)
    return args.func(args)


def test_setup_refresh_mcp_regenerates_mcp_json_under_active_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))

    rc = _invoke_setup(refresh_mcp=True)
    capsys.readouterr()

    assert rc == 0
    mcp_path = mcp_registry_path()
    assert mcp_path.exists(), (
        f"--refresh-mcp must create mcp.json; not found at {mcp_path}"
    )
    payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    servers = payload.get("mcp_servers")
    assert isinstance(servers, list) and servers, (
        f"mcp.json must contain at least one server; got {payload}"
    )

    # The first server's launcher path must live under the active
    # repo_root() — i.e. mcp.json is aligned with the editable install.
    from agentic_swmm.utils.paths import repo_root

    active_root = repo_root().resolve()
    launcher = Path(servers[0]["args"][0]).resolve()
    try:
        launcher.relative_to(active_root)
    except ValueError:
        raise AssertionError(
            f"--refresh-mcp produced a launcher outside active repo: "
            f"launcher={launcher}, repo_root={active_root}"
        )


def test_setup_refresh_mcp_does_not_touch_other_aiswmm_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))
    # Seed pre-existing files that --refresh-mcp must NOT overwrite.
    untouched = {
        "config.toml": "[provider]\ndefault = \"openai\"\n",
        "skills.json": "{\"skills\": [\"sentinel\"]}",
        "memory.json": "{\"memory_files\": [\"sentinel\"]}",
        "setup_state.json": "{\"status\": \"sentinel\"}",
    }
    for name, content in untouched.items():
        (config_dir / name).write_text(content, encoding="utf-8")

    rc = _invoke_setup(refresh_mcp=True)
    capsys.readouterr()
    assert rc == 0

    for name, expected_content in untouched.items():
        actual = (config_dir / name).read_text(encoding="utf-8")
        assert actual == expected_content, (
            f"--refresh-mcp must not touch {name}; expected unchanged "
            f"sentinel, got {actual!r}"
        )


def test_setup_refresh_mcp_prints_what_it_regenerated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))

    rc = _invoke_setup(refresh_mcp=True)
    output = capsys.readouterr().out

    assert rc == 0
    # The user must see which file was rewritten so they can verify
    # the result without re-reading mcp.json by hand.
    assert "mcp.json" in output, (
        f"--refresh-mcp output must mention mcp.json; got:\n{output}"
    )
