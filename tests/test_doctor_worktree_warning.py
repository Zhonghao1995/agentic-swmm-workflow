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
    # PRD-08 A.1: doctor's argparse namespace now carries --json / --fix
    # / --yes attributes. Tests that synthesize a namespace must supply
    # the defaults so attribute lookups don't fall through to argparse.
    doctor.main(argparse.Namespace(json=False, fix=False, yes=False))
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

    # PRD-08 A.1 (audit #28): non-passing rows live under the grouped
    # ``Issues:`` section now. The worktree WARN is single-cause so it
    # passes through as a plain row; it still must mention worktree
    # plus the remediation hint.
    assert "worktree" in output.lower(), (
        f"expected output mentioning 'worktree'; got:\n{output}"
    )
    assert "pip install -e ." in output, (
        f"expected remediation 'pip install -e .' in output; got:\n{output}"
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

    # No WARN/MISSING row should mention worktree for a normal checkout.
    # We still allow the phrase "worktree" to appear in section headers
    # or other contexts; pin to the specific WARN row shape that the
    # editable-install detector would emit.
    assert "editable install" not in output, (
        f"unexpected worktree WARN for a normal checkout: {output}"
    )
