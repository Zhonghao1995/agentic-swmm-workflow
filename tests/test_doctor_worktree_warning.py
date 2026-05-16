"""Doctor warns on stale editable install in a Claude worktree (#113).

If ``aiswmm`` is editable-installed (``pip install -e .``) from a
Claude Code worktree under ``.claude/worktrees/agent-XYZ/``, the Python
runtime keeps loading whatever code that worktree was sitting on when
the install was run. The main checkout can then move forward for days
while the runtime silently serves stale code. Doctor must call this
out so the user knows to re-run ``pip install -e .`` from the main
checkout.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from agentic_swmm.commands import doctor


def _run_doctor_capture(capsys: pytest.CaptureFixture[str]) -> str:
    doctor.main(argparse.Namespace())
    return capsys.readouterr().out


def test_doctor_warns_when_repo_root_is_inside_a_claude_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Synthesize a path that looks like a Claude Code worktree.
    worktree_root = tmp_path / "main" / ".claude" / "worktrees" / "agent-XYZABC"
    worktree_root.mkdir(parents=True)
    monkeypatch.setattr(doctor, "repo_root", lambda: worktree_root)

    output = _run_doctor_capture(capsys)

    # The warning must be a WARN row (not MISSING — non-fatal) and
    # must mention "worktree" plus the remediation hint so the user
    # knows what to do.
    warn_lines = [
        line for line in output.splitlines() if line.startswith("WARN")
    ]
    matching = [line for line in warn_lines if "worktree" in line.lower()]
    assert matching, (
        f"expected a WARN row mentioning 'worktree'; got:\n{output}"
    )
    assert any("pip install -e ." in line for line in matching), (
        f"expected remediation 'pip install -e .' in worktree WARN; got:\n{output}"
    )


def test_doctor_does_not_warn_when_repo_root_is_a_normal_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    normal_root = tmp_path / "Codex Project" / "Agentic SWMM"
    normal_root.mkdir(parents=True)
    monkeypatch.setattr(doctor, "repo_root", lambda: normal_root)

    output = _run_doctor_capture(capsys)

    # No WARN row should mention worktree for a normal checkout.
    for line in output.splitlines():
        if line.startswith("WARN"):
            assert "worktree" not in line.lower(), (
                f"unexpected worktree WARN for a normal checkout: {line}"
            )
