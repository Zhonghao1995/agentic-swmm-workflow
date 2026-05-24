"""PRD-185 headline snapshot test.

Drives a fixture 10-tool trace (mix of read-only / write, mix of
success / failure) through the digest renderer and the legacy verbose
renderer. The PRD's headline measurable is::

    digest line count <= 50 % of verbose line count

This is the load-bearing metric the operator notices first — every
other acceptance criterion can be true, but if the volume of text
isn't actually halved, the change failed its mandate.

The fixture is built once and shared between both renderings, so the
ratio reflects pure rendering differences (not different paths
through the planner).
"""
from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

from agentic_swmm.agent.digest_render import brief_result, render_step


@dataclass
class _Step:
    """Minimal trace event shape — enough for both renderers."""

    index: int
    tool: str
    args: dict[str, Any]
    is_read_only: bool
    prompted: bool  # whether the user got a [Y/n] prompt
    approved: bool
    ok: bool
    result: dict[str, Any]
    error_detail: str | None = None


def _render_digest(steps: list[_Step]) -> str:
    """Render the fixture in digest mode (the new default).

    The runtime drops the standalone OK: confirmation line and the
    re-printed tool description, so the only stdout the user sees per
    step is the line ``render_step`` produces (which may carry an
    auto-expanded Detail block).
    """
    rendered: list[str] = []
    for step in steps:
        brief = brief_result(step.tool, step.result)
        rendered.append(
            render_step(
                step=step.index,
                tool=step.tool,
                is_read_only=step.is_read_only,
                prompted=step.prompted,
                approved=step.approved,
                ok=step.ok,
                brief=brief,
                error_detail=step.error_detail,
            )
        )
    return "\n".join(rendered) + "\n"


def _render_verbose(steps: list[_Step]) -> str:
    """Render the fixture in the legacy verbose path.

    Matches the planner's current two-line emit
    (``[N] tool {args}`` + ``OK|FAILED: <summary>``) plus the
    executor's ``Running <name> — <first sentence>`` spinner row.
    The spinner row is included because the digest path drops it and
    we want the ratio comparison to reflect the full user-visible
    text on each path.
    """
    lines: list[str] = []
    for step in steps:
        lines.append(f"Running {step.tool} — {step.tool} description sentence")
        lines.append(f"[{step.index}] {step.tool} {json.dumps(step.args, sort_keys=True)}")
        status = "OK" if step.ok else "FAILED"
        lines.append(f"{status}: {step.result.get('summary') or 'completed'}")
        if not step.ok and step.error_detail:
            # The legacy path also surfaces the error detail, but as
            # a separate emit instead of an indented continuation.
            for detail_line in step.error_detail.splitlines():
                lines.append(detail_line)
    return "\n".join(lines) + "\n"


def _build_fixture() -> list[_Step]:
    """Ten tool calls covering the four PRD shapes + one denial.

    Mix:
    * 6 read-only auto-approved successes
    * 1 read-only auto-approved failure (with stacktrace)
    * 2 write prompted+approved successes
    * 1 write prompted+denied (skipped)
    """
    return [
        _Step(
            index=1,
            tool="list_skills",
            args={},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "list_skills",
                "ok": True,
                "summary": "8 skills found",
            },
        ),
        _Step(
            index=2,
            tool="read_skill",
            args={"skill_name": "swmm-experiment-audit"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "read_skill",
                "ok": True,
                "summary": "loaded skills/swmm-experiment-audit/SKILL.md",
            },
        ),
        _Step(
            index=3,
            tool="select_skill",
            args={"skill_name": "swmm-experiment-audit"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "select_skill",
                "ok": True,
                "skill_name": "swmm-experiment-audit",
                "summary": "selected skill swmm-experiment-audit: 4 tool(s) (registry)",
            },
        ),
        _Step(
            index=4,
            tool="list_dir",
            args={"path": "examples"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "list_dir",
                "ok": True,
                "results": {"entries": ["a", "b", "c", "d", "e", "f", "g", "h"]},
                "summary": "listed 8 entries",
            },
        ),
        _Step(
            index=5,
            tool="read_file",
            args={"path": "examples/tecnopolo/model.inp"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "read_file",
                "ok": True,
                "summary": "read 4 kB excerpt",
            },
        ),
        _Step(
            index=6,
            tool="inspect_plot_options",
            args={"run_dir": "runs/2026-05-22/230510_tecnopolo_run"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            result={
                "tool": "inspect_plot_options",
                "ok": True,
                "summary": "rain=2 nodes=4 attrs=6",
            },
        ),
        _Step(
            index=7,
            tool="run_swmm_inp",
            args={"inp_path": "examples/tecnopolo/model.inp"},
            is_read_only=False,
            prompted=True,
            approved=True,
            ok=True,
            result={
                "tool": "run_swmm_inp",
                "ok": True,
                "results": {"runDir": "runs/agent/saanich-1779596754"},
                "summary": "ran swmm-runner.swmm_run",
            },
        ),
        _Step(
            index=8,
            tool="audit_run",
            args={"run_dir": "runs/agent/saanich-1779596754"},
            is_read_only=False,
            prompted=True,
            approved=True,
            ok=True,
            result={
                "tool": "audit_run",
                "ok": True,
                "results": {"status": "PASS"},
                "summary": "audited run",
            },
        ),
        _Step(
            index=9,
            tool="apply_patch",
            args={"path": "skills/swmm-experiment-audit/SKILL.md"},
            is_read_only=False,
            prompted=True,
            approved=False,
            ok=False,
            result={
                "tool": "apply_patch",
                "ok": False,
                "summary": "tool not approved by user",
            },
        ),
        _Step(
            index=10,
            tool="inspect_plot_options",
            args={"out_file": "missing.json"},
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=False,
            result={
                "tool": "inspect_plot_options",
                "ok": False,
                "summary": "out_file not in repo",
            },
            error_detail=(
                "out_file must be an existing repository file:\n"
                "  /Users/.../runs/2026-05-22/230510_tecnopolo_run/missing.json"
            ),
        ),
    ]


class Digest10ToolSnapshotTests(unittest.TestCase):
    def test_digest_line_count_is_at_most_half_of_verbose(self) -> None:
        steps = _build_fixture()
        digest = _render_digest(steps)
        verbose = _render_verbose(steps)
        digest_lines = digest.count("\n")
        verbose_lines = verbose.count("\n")
        self.assertGreater(
            verbose_lines,
            0,
            "verbose snapshot must have non-zero lines",
        )
        ratio = digest_lines / verbose_lines
        self.assertLessEqual(
            ratio,
            0.5,
            f"digest ({digest_lines}) must be <= 50% of verbose "
            f"({verbose_lines}); ratio={ratio:.2%}\n"
            f"--- digest ---\n{digest}\n"
            f"--- verbose ---\n{verbose}",
        )

    def test_digest_renders_four_pdr_shapes(self) -> None:
        # Sanity that the fixture+renderer cover the four shapes the
        # PRD table requires (so the line-count claim isn't fudged by
        # an unrealistic fixture).
        digest = _render_digest(_build_fixture())
        self.assertIn("(read-only, auto)  ✓", digest)
        self.assertIn("(read-only, auto)  ✗", digest)
        self.assertIn("-> [Y/n]: Y  ✓", digest)
        self.assertIn("-> [Y/n]: N  (skipped)", digest)

    def test_digest_failure_carries_auto_expanded_detail(self) -> None:
        # Per PRD: '✗ <reason>' on the row AND indented Detail: block
        # beneath, without --verbose.
        digest = _render_digest(_build_fixture())
        self.assertIn("✗ out_file not in repo", digest)
        self.assertIn(
            "    Detail: out_file must be an existing repository file:",
            digest,
        )


if __name__ == "__main__":
    unittest.main()
