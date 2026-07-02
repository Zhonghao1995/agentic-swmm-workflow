"""Startup welcome: ASCII logo + first-run vs returning-user banner.

Issue #57 (UX-2). The first time a user lands at the ``aiswmm``
interactive prompt we owe them a multi-line capability tour. Every
subsequent launch should collapse to a short banner with a pointer to
their last session, so the chrome stays out of the way.

Design constraints:

- Logo is ASCII, <= 8 lines tall, <= 80 columns wide (macOS Terminal
  default width). ANSI-coloured on a real tty via ``ui_colors``, plain
  text on ``NO_COLOR`` / non-tty.
- First-run detection is a single marker file under the aiswmm config
  dir (``~/.aiswmm/first_run.json`` by default; respects
  ``AISWMM_CONFIG_DIR``). Marker contents are advisory metadata —
  presence alone is the signal.
- Last-session lookup reads the PR #38 SessionDB. Every IO failure
  degrades to "No prior session" rather than crashing the boot.
- ``AISWMM_DISABLE_WELCOME=1`` short-circuits the whole module so
  scripted / CI invocations stay clean. The marker is NOT written
  when disabled — the user opted out, we don't consume "first run"
  on their behalf.

The module is pure-functional and exposes one IO entrypoint
(``print_welcome``) so ``runtime_loop`` only needs a single call.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from agentic_swmm import __version__
from agentic_swmm.agent import tui_chrome as _chrome
from agentic_swmm.agent import ui_colors
from agentic_swmm.config import config_dir
from agentic_swmm.memory.session_db import latest_session
from agentic_swmm.memory.session_sync import default_db_path


# Single chokepoint for the env-var name so it's easy to grep.
_DISABLE_ENV = "AISWMM_DISABLE_WELCOME"

# Raw ASCII logo. Plain ASCII so we never depend on terminal Unicode
# support. 5 lines tall, widest line is 43 columns -- well under the
# 80-column / 8-line PRD budget. Edit with care: every line must stay
# <= 80 cols and the block must stay <= 8 lines.
_LOGO_LINES: tuple[str, ...] = (
    r"    _    ___ ______        ____  __ __  __",
    r"   / \  |_ _/ ___\ \      / /  \/  |  \/  |",
    r"  / _ \  | |\___ \\ \ /\ / /| |\/| | |\/| |",
    r" / ___ \ | | ___) |\ V  V / | |  | | |  | |",
    r"/_/   \_\___|____/  \_/\_/  |_|  |_|_|  |_|",
)


# ---------------------------------------------------------------------------
# First-run marker
# ---------------------------------------------------------------------------


def first_run_marker_path() -> Path:
    """Return the path of the first-run marker file.

    Lives next to the other aiswmm runtime state files
    (``config.toml``, ``setup_state.json``, ...). ``config_dir`` already
    honours the ``AISWMM_CONFIG_DIR`` env var, so tests can redirect it
    cleanly.
    """
    return config_dir() / "first_run.json"


def is_first_run() -> bool:
    """Return True iff the first-run marker does not yet exist."""
    try:
        return not first_run_marker_path().exists()
    except OSError:
        # If we can't even stat the path we treat it as "not first
        # run" -- we'd rather skip the welcome than spam every boot.
        return False


def mark_first_run_complete() -> None:
    """Write the first-run marker so the next launch takes the short path.

    Failures are swallowed: a read-only home directory should never
    block the agent from booting. The next launch will simply show the
    extended welcome again, which is harmless.
    """
    path = first_run_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "first_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "version": __version__,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


# ---------------------------------------------------------------------------
# Last-session lookup
# ---------------------------------------------------------------------------


def lookup_last_session(*, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return the most recently-ended session row, or ``None``.

    Reads directly from the SessionDB (PR #38). ``end_utc IS NOT NULL``
    excludes still-running sessions so we never tell the user their
    "last session" was the one they crashed five seconds ago. Returns
    ``None`` on any IO error so the welcome falls back to "No prior
    session" gracefully.
    """
    if db_path is None:
        db_path = default_db_path()
    try:
        if not db_path.exists():
            return None
    except OSError:
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            return latest_session(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# Relative time
# ---------------------------------------------------------------------------


def format_relative_time(iso_utc: str, *, now: datetime | None = None) -> str:
    """Format an ISO-8601 UTC timestamp as ``"N <unit> ago"``.

    Buckets: seconds -> "just now", < 1 hour -> minutes,
    < 1 day -> hours, otherwise -> days. We never go finer than
    "just now" or coarser than days; the banner is a navigational
    cue, not a precise log.
    """
    try:
        parsed = datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - parsed
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"
    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit} ago"
    days = hours // 24
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} ago"


# ---------------------------------------------------------------------------
# Logo + render helpers
# ---------------------------------------------------------------------------


def render_logo() -> str:
    """Return the multi-line ASCII logo, ANSI-coloured on a tty."""
    body = "\n".join(_LOGO_LINES)
    # ui_colors.colorize() is the single chokepoint that respects
    # NO_COLOR / non-tty -- we never concatenate escapes by hand.
    return ui_colors.colorize(body, ui_colors.FG_BLUE)


def _compact_header(*, session_label: str, profile_name: str) -> str:
    """Render the one-line returning-user header.

    Mirrors the existing ``format_startup_banner`` shape but lifts the
    AISWMM version forward so the user sees what they're running on
    every launch.
    """
    version_tag = ui_colors.colorize(f"AISWMM v{__version__}", ui_colors.BOLD)
    profile_segment = ui_colors.colorize(f"profile={profile_name}", ui_colors.DIM)
    return f"{version_tag}  ({session_label}, {profile_segment})"


def _format_last_session_line(last_session: dict[str, Any] | None) -> str:
    """Render the ``Last session: ...`` line, or the empty-DB fallback."""
    if not last_session:
        return "Last session: No prior session."
    case_name = last_session.get("case_name") or "unknown"
    end_utc = last_session.get("end_utc") or ""
    relative = format_relative_time(end_utc) if end_utc else ""
    if relative:
        return f'Last session: {relative} -- case "{case_name}"'
    return f'Last session: case "{case_name}"'


def _tip_line() -> str:
    """Render the slash-command tip line."""
    return "(/help  /exit  /new-session  --safe)"


def _missing_setup_tip(*, memory_dir: Path | None = None) -> str | None:
    """Return an ``aiswmm login`` hint when the user looks un-onboarded.

    The hint fires when *both* are true:

    * The default provider's API key is not reachable (env /
      ``~/.aiswmm/env`` / config) — so the interactive shell will be
      limited until they authenticate, AND
    * The default ``memory/modeling-memory/`` directory is missing or
      empty (so transfer / cite-param / cite will all fail with
      empty-store errors).

    A user who has already set the default provider's key is never
    nagged. A user genuinely without a key sees one proactive line
    pointing at the auth command.

    Returns ``None`` when at least one signal is healthy.
    """
    # Late import keeps welcome's import graph free of the preflight /
    # config at module load; the probe is cheap (env + file scan).
    from agentic_swmm.agent.provider_preflight import provider_key_present
    from agentic_swmm.config import DEFAULT_PROVIDER, load_config

    try:
        default_provider = str(load_config().get("provider.default", DEFAULT_PROVIDER))
    except Exception:  # pragma: no cover - defensive; config is shallow
        default_provider = DEFAULT_PROVIDER
    if provider_key_present(default_provider):
        return None
    target = memory_dir or (Path.cwd() / "memory" / "modeling-memory")
    try:
        if target.is_dir():
            # Any populated subdir / non-empty file means the user has
            # at least started onboarding. Skip the tip in that case.
            for child in target.iterdir():
                if child.is_file() and child.stat().st_size > 0:
                    return None
    except OSError:
        # Filesystem error: do not pester the user with the tip.
        return None
    return "Tip: run `aiswmm login` to set your provider API key (or `aiswmm doctor` to see what's set up)."


def render_returning_banner(
    *,
    session_label: str,
    profile_name: str,
    last_session: dict[str, Any] | None,
) -> str:
    """Render the compact returning-user banner (4 short lines).

    Layout:

        AISWMM v<X>  (session-XXXXXX, profile=quick)
        Last session: 2 hours ago -- case "<your-watershed>"
        (/help  /exit  /new-session  --safe)
    """
    return "\n".join(
        [
            _compact_header(session_label=session_label, profile_name=profile_name),
            _format_last_session_line(last_session),
            _tip_line(),
        ]
    )


# CONCURRENCY-OWNER: PRD-TUI-REDESIGN
def render_tagline_frame() -> str:
    """Render the retro-chrome ``[SYS] aiswmm vX.Y.Z ONLINE`` tagline.

    Sits below the PR #72 ASCII logo on both first-run and returning
    launches. ``[SYS]`` lives inside the title literal because the
    frame title already gets phosphor-green colouring; routing it
    through ``_chrome.sys()`` would double-wrap the escape codes.

    Plain mode (``AISWMM_TUI=plain``) collapses to ``== aiswmm vX.Y.Z
    ONLINE ==`` followed by the literal tagline — no frame characters,
    no prefix, no colour.
    """
    return _chrome.frame(
        title=f"[SYS] aiswmm v{__version__} ONLINE",
        lines=["I'm aiswmm. Type 'help' or describe what you want."],
    )


def _first_case_display_name() -> str | None:
    """Issue #122: first registered case's display name, or ``None`` if empty.

    Wrapping the registry lookup gives tests a single attribute to
    monkey-patch (so they don't need to mutate the real ``cases/``
    directory). Any IO failure degrades to ``None`` — the banner falls
    back to the generic "Run a SWMM demo" suggestion rather than
    crashing the boot.
    """
    try:
        # Local import: the welcome module ships even when ``yaml`` is missing.
        from agentic_swmm.case import case_registry

        cases = case_registry.list_cases()
    except Exception:
        return None
    for meta in cases:
        if meta.display_name:
            return meta.display_name
    return None


def render_extended_welcome() -> str:
    """Render the first-run welcome: logo + capability tour + CTA.

    The block is wide enough to feel substantial but every line stays
    under 80 columns so macOS Terminal default never auto-wraps. The
    bullet glyphs are ASCII so we never depend on terminal Unicode.

    PRD-TUI-REDESIGN: appends a retro-chrome ``[SYS] aiswmm ONLINE``
    tagline frame right below the logo. The first-run capability tour
    follows so the user reads ``logo → tagline → tour → CTA``.

    Issue #122: the first "Things to try" line is now driven by
    ``case_registry.list_cases()`` so a fresh clone with an empty
    ``cases/`` shows the generic ``Run a SWMM demo`` suggestion, and a
    user with a registered watershed sees that watershed's display
    name instead of a hardcoded example.
    """
    logo = render_logo()
    tagline = render_tagline_frame()
    greeting = ui_colors.colorize("Welcome to AISWMM!", ui_colors.BOLD)
    intro = "I'm an agentic stormwater modeling assistant. I can help you:"
    capabilities = [
        "  - Build EPA SWMM input files from your GIS, climate, and network data",
        "  - Run SWMM simulations and audit the results automatically",
        "  - Calibrate model parameters against observed flow data",
        "  - Quantify uncertainty in your stormwater predictions",
        "  - Remember lessons across modeling sessions",
        "  - Compare runs, transfer parameters, and cite choices",
        "      (`aiswmm compare/transfer/cite`)",
    ]
    things_header = ui_colors.colorize("Things to try:", ui_colors.BOLD)
    first_case = _first_case_display_name()
    demo_line = (
        f'  - "Run the {first_case} demo"' if first_case else '  - "Run a SWMM demo"'
    )
    # PRD-08 Phase B (audit #36): "Things to try" now surfaces at least
    # two memory-verb-based examples so a returning modeler discovers
    # ``compare`` / ``transfer`` from the warm-intro alone. The first-
    # case display name (when present) feeds the transfer suggestion
    # so the example is grounded in the user's own data.
    transfer_line = (
        f'  - "aiswmm transfer --inp examples/{first_case.lower()}/scenario.inp"'
        if first_case
        else '  - "aiswmm transfer --inp <case>.inp"'
    )
    things = [
        demo_line,
        '  - "Show me what skills you have"',
        '  - "Help me build an INP for my project"',
        '  - "aiswmm compare --run-a <run-dir> --run-b <run-dir>"',
        transfer_line,
    ]
    trust = "I'll always tell you what I've actually verified vs. what's still uncertain."
    closing = "Type /help anytime. Let's get started -- what would you like to do?"
    # PRD-08 Phase B (cross-cutting): when the user looks un-onboarded
    # (no OPENAI_API_KEY + empty memory stores), append a single proactive
    # ``aiswmm doctor`` hint so the next step is obvious.
    setup_tip = _missing_setup_tip()
    sections: list[str] = [
        logo,
        "",
        tagline,
        "",
        greeting,
        "",
        intro,
        *capabilities,
        "",
        things_header,
        *things,
        "",
        trust,
        "",
        closing,
    ]
    if setup_tip:
        sections.extend(["", setup_tip])
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def _is_disabled() -> bool:
    """Return True iff the user has set ``AISWMM_DISABLE_WELCOME=1``."""
    value = os.environ.get(_DISABLE_ENV)
    if value is None:
        return False
    return value.strip() not in {"", "0", "false", "False", "no", "No"}


def print_welcome(
    *,
    stream: IO[str] | None = None,
    session_label: str = "",
    profile_name: str = "quick",
    db_path: Path | None = None,
) -> None:
    """Print the welcome (first-run or returning) to ``stream``.

    Parameters
    ----------
    stream:
        Defaults to ``sys.stdout``. Tests pass a ``StringIO`` to
        capture output without spawning a subprocess.
    session_label:
        Current session label (``session-XXXXXX``) for the compact
        header. Ignored on the first-run path.
    profile_name:
        Active permission profile (``quick`` / ``safe``) for the
        compact header.
    db_path:
        SessionDB path override. Defaults to the canonical
        ``runs/sessions.sqlite`` honouring ``AISWMM_SESSION_DB``.

    Honours ``AISWMM_DISABLE_WELCOME``: when set to a truthy value the
    function returns immediately without touching stdout or the marker
    file. Any unexpected exception is swallowed so a broken welcome
    can never block the agent from booting.
    """
    if _is_disabled():
        return
    if stream is None:
        stream = sys.stdout
    try:
        if is_first_run():
            stream.write(render_extended_welcome())
            stream.write("\n")
            mark_first_run_complete()
            return
        last_session = lookup_last_session(db_path=db_path)
        stream.write(
            render_returning_banner(
                session_label=session_label,
                profile_name=profile_name,
                last_session=last_session,
            )
        )
        stream.write("\n")
    except Exception:
        # Welcome is decoration. A bug here must not prevent the
        # agent from coming up; swallow and continue.
        return
