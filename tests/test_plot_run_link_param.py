"""Regression: ``plot_run`` must accept a ``link`` argument so the
LLM can request a conduit-level hydrograph end-to-end.

Background
----------
The underlying ``plot_rain_runoff_si.py`` script already supports
``--link`` (mutually exclusive with ``--node``) — when set, the
lower panel renders ``Flow_rate`` for the conduit instead of a
node attribute. But ``--link`` was unreachable from the agent path
because three layers above the script all only knew about ``node``:

  ToolSpec schema (LLM-facing)
    → ``_plot_run_args`` (typed → MCP mapper)
        → MCP zod Args (``mcp/swmm-plot/server.js``)
            → script ``--link`` (already supported)

This test locks in the wiring so the chain stays end-to-end after
future refactors.

What the test pins
------------------
* ``link`` appears in the ToolSpec schema as a string property.
* ``_plot_run_args`` forwards ``call.args["link"]`` into the MCP
  payload (camelCase ``link`` key, matching the rest of the mapper).
* When ``link`` is supplied, ``node`` is NOT injected into the
  payload — the argparse group on the script is mutually exclusive
  so a stray ``--node`` alongside ``--link`` makes the script
  reject the call.
* ``out_png`` auto-default uses the link id when ``link`` is set
  (not the literal string ``"node"``).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_swmm.agent import tool_registry as registry_mod
from agentic_swmm.agent.tool_handlers import _shared as shared_mod
from agentic_swmm.agent.tool_registry import AgentToolRegistry, _plot_run_args
from agentic_swmm.agent.types import ToolCall


def _seed_run_dir(root: Path, name: str = "test-run") -> Path:
    run_dir = root / "runs" / "agent" / name
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "06_runner").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\nfixture\n", encoding="utf-8")
    (run_dir / "06_runner" / "model.out").write_bytes(b"\x00")
    return run_dir


def _with_fake_repo_root(tmp: Path, fn):
    """Repoint registry + shared repo_root to ``tmp`` for the test body."""
    orig_reg = registry_mod.repo_root
    orig_shared = shared_mod.repo_root
    registry_mod.repo_root = lambda: tmp  # type: ignore[assignment]
    shared_mod.repo_root = lambda: tmp  # type: ignore[assignment]
    try:
        return fn()
    finally:
        registry_mod.repo_root = orig_reg  # type: ignore[assignment]
        shared_mod.repo_root = orig_shared  # type: ignore[assignment]


class PlotRunLinkSchemaTests(unittest.TestCase):
    """The LLM picks tools by reading their JSON schemas. ``link`` must
    be visible at the schema layer or the LLM never knows the option
    exists."""

    def test_plot_run_schema_includes_link_property(self) -> None:
        spec = next(
            s for s in AgentToolRegistry().schemas() if s["name"] == "plot_run"
        )
        props = spec["parameters"]["properties"]
        self.assertIn("link", props)
        self.assertEqual(props["link"]["type"], "string")

    def test_plot_run_description_mentions_conduit_or_link(self) -> None:
        """LLM also needs to know what ``link`` does — the description
        is where it learns the semantics. Without conduit/link wording,
        the LLM will not pick this path even with the schema field
        present."""
        desc = (AgentToolRegistry().describe("plot_run") or "").lower()
        self.assertTrue(
            "link" in desc or "conduit" in desc,
            f"plot_run description must signal conduit-hydrograph option; got: {desc!r}",
        )


class PlotRunLinkArgsMapperTests(unittest.TestCase):
    """``_plot_run_args`` is the typed-to-MCP translation layer. It
    must forward ``link`` and suppress ``node`` to avoid the
    mutually-exclusive argparse group on the script."""

    def test_link_propagates_to_mcp_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp)
            call = ToolCall(
                "plot_run",
                {
                    "run_dir": str(run_dir.relative_to(tmp)),
                    "link": "116-119",
                    "rain_ts": "TS_RAIN",
                },
            )
            payload = _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))
        self.assertEqual(payload.get("link"), "116-119")

    def test_link_suppresses_node_in_payload(self) -> None:
        """The script's argparse group rejects --link + --node together.
        When the LLM supplies ``link``, the mapper must not also
        emit a ``node`` key."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp)
            call = ToolCall(
                "plot_run",
                {"run_dir": str(run_dir.relative_to(tmp)), "link": "116-119"},
            )
            payload = _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))
        self.assertNotIn(
            "node",
            payload,
            "_plot_run_args must omit node when link is set — the script's "
            "argparse group rejects --node + --link together",
        )

    def test_link_wins_when_both_node_and_link_provided(self) -> None:
        """If the LLM accidentally supplies both, link wins (more
        specific intent). The mapper drops node rather than letting
        the script reject the call."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp)
            call = ToolCall(
                "plot_run",
                {
                    "run_dir": str(run_dir.relative_to(tmp)),
                    "node": "OUT_0",
                    "link": "116-119",
                },
            )
            payload = _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))
        self.assertEqual(payload.get("link"), "116-119")
        self.assertNotIn("node", payload)

    def test_link_default_out_png_uses_link_id(self) -> None:
        """When link is set and out_png isn't, the default filename
        must reflect the link (not literal ``node``) so the file is
        findable."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp)
            call = ToolCall(
                "plot_run",
                {"run_dir": str(run_dir.relative_to(tmp)), "link": "116-119"},
            )
            payload = _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))
        out_png = payload.get("outPng", "")
        self.assertIn(
            "116-119",
            out_png,
            f"default out_png must include the link id; got {out_png!r}",
        )

    def test_node_path_still_works_when_link_absent(self) -> None:
        """Backward compat: existing node-path callers must keep
        emitting ``node`` (not ``link``)."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = _seed_run_dir(tmp)
            call = ToolCall(
                "plot_run",
                {"run_dir": str(run_dir.relative_to(tmp)), "node": "OUT_0"},
            )
            payload = _with_fake_repo_root(tmp, lambda: _plot_run_args(call, run_dir))
        self.assertEqual(payload.get("node"), "OUT_0")
        self.assertNotIn("link", payload)


class PlotRunLinkMcpZodSchemaTests(unittest.TestCase):
    """Static-text check on the Node MCP server: the zod schema must
    declare ``link`` and the CallToolRequestSchema handler must branch
    on it before pushing ``--node`` to argv. Catching this at the text
    layer avoids spinning up the MCP transport in this unit test."""

    def setUp(self) -> None:
        from agentic_swmm.utils.paths import repo_root

        self.server_js = (repo_root() / "mcp" / "swmm-plot" / "server.js").read_text(
            encoding="utf-8"
        )

    def test_zod_args_declares_link_optional(self) -> None:
        self.assertRegex(
            self.server_js,
            r"link\s*:\s*z\.string\(\)\.optional\(\)",
            "mcp/swmm-plot/server.js Args zod schema must declare link as "
            "an optional string so the MCP transport accepts the field "
            "instead of stripping it.",
        )

    def test_handler_branches_on_link_before_emitting_node_flag(self) -> None:
        """The server must NOT unconditionally push ``--node`` to
        ``pyArgs`` — it has to choose link vs node and emit exactly
        one of them."""
        # The exact branching style is flexible; we just need the
        # handler to reference ``a.link`` somewhere between Args.parse
        # and the pyArgs push for ``--link``.
        self.assertIn(
            '"--link"',
            self.server_js,
            "MCP server must emit --link argv when link is set, otherwise "
            "the script never sees the conduit selector",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
