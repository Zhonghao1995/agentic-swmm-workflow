from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root


BLOCKED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
BLOCKED_FILENAMES = {".env", "config.toml"}
ALLOWED_COMMANDS = {
    "pytest",
    "python_module_cli",
    "node_script",
    "swmm5",
}


def repo_relative_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


#: Live finding F-60 (2026-09-02): with no typed ensemble tool, the planner
#: wrote scripts/run_peak_outflow_uncertainty.mjs into the repository and
#: ran it. Agent-authored code belongs under the run it serves
#: (``<run>/_agent/scripts/``); the product tree is not the agent's
#: scratch space. Documents and data files elsewhere are unaffected.
REPO_WRITES_ENV = "AISWMM_ALLOW_REPO_WRITES"
CODE_SUFFIXES = frozenset({".py", ".pyi", ".mjs", ".cjs", ".js", ".ts", ".sh", ".bash", ".zsh", ".rb", ".pl"})
CODE_WRITE_HINT = (
    "Agent-authored code goes under the run directory (for example "
    "<run>/_agent/scripts/), never into the product tree. "
    f"Set {REPO_WRITES_ENV}=1 for development."
)


def _repo_writes_allowed() -> bool:
    return os.environ.get(REPO_WRITES_ENV, "").strip() in ("1", "true", "yes")


AGENT_SCRATCH_DIRS = frozenset({"scripts", "inputs"})


def is_agent_scratch_path(path: Path) -> bool:
    """True under ``runs/<...>/_agent/scripts/`` or ``_agent/inputs/``.

    Helper code and hand-made inputs the planner writes for a run live
    here; they are neither product code nor run evidence, so apply_patch
    needs no evidence override for them. Live finding F-61 (2026-09-02):
    the calibration search space landed at the run root, which the
    canonical layout reserves for the product's own files.
    """
    try:
        relative = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    parts = relative.parts
    if parts[:1] != ("runs",):
        return False
    for index in range(len(parts) - 2):
        if parts[index] == "_agent" and parts[index + 1] in AGENT_SCRATCH_DIRS:
            return True
    return False


RUN_ROOT_WRITE_HINT = (
    "The run root is reserved for the product's own files. Put agent-authored "
    "inputs under <run>/_agent/inputs/ (no evidence override needed) or in the "
    "stage folder they feed."
)


def is_new_file_at_run_root(path: Path, session_dir: Path) -> bool:
    """True for a non-canonical file written directly into the session's run root."""
    from agentic_swmm.agent.swmm_runtime.run_layout import CANONICAL_ROOT_FILES

    try:
        resolved = path.resolve()
        root = session_dir.resolve()
    except OSError:
        return False
    if resolved.parent != root:
        return False
    return resolved.name not in CANONICAL_ROOT_FILES


def is_code_write_into_product_tree(path: Path) -> bool:
    """True for a code file that is neither run evidence nor agent scratch."""
    if path.suffix.lower() not in CODE_SUFFIXES:
        return False
    return not (is_evidence_path(path) or is_agent_scratch_path(path))


def is_allowed_write_path(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    if any(part in BLOCKED_PARTS for part in relative.parts):
        return False
    if path.name in BLOCKED_FILENAMES:
        return False
    if is_code_write_into_product_tree(path) and not _repo_writes_allowed():
        return False
    return True


def is_evidence_path(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    return relative.parts[:1] == ("runs",) or relative.parts[:2] == ("memory", "modeling-memory")


AUTO_APPROVE_ENV = "AISWMM_AUTO_APPROVE"

HEADLESS_DENIAL_HINT = (
    f"No terminal was attached, so the call was refused rather than run "
    f"unattended. For trusted automation, set {AUTO_APPROVE_ENV}=1."
)


@dataclass(frozen=True)
class ApprovalDecision:
    """Whether a tool may run, and why that was decided.

    ``reason`` is one of:

    * ``"auto"`` — trusted automation opted in via the env var;
    * ``"granted"`` — a human said yes;
    * ``"declined"`` — a human said no;
    * ``"headless"`` — there was no human to ask.

    The last two both deny, but only one of them is worth explaining: a
    user who typed ``n`` does not need a lecture, while a CI run needs to
    be told the opt-in exists.
    """

    approved: bool
    reason: str

    @property
    def needs_guidance(self) -> bool:
        """True when the caller should surface :data:`HEADLESS_DENIAL_HINT`."""
        return self.reason == "headless"


def _real_bbox(bbox: Any) -> bool:
    """A 4-number bbox with area; ``[0, 0, 0, 0]`` and other zero-area boxes are placeholders."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return False
    return min_lon != max_lon and min_lat != max_lat


def approval_detail(args: dict[str, Any] | None) -> str:
    """One short phrase naming the decisive argument of a tool call.

    Live finding F-03 (2026-09-02): "Run run_allowed_command? [Y/n]" asked
    the person to approve a command they had not seen (it was then refused
    as not allowlisted); "Run fetch_swmm_from_canada? [Y/n]" did not say
    which area would leave the machine. The same phrase is what a
    frontend would show beside an approve button.
    """
    if not isinstance(args, dict) or not args:
        return ""
    command = args.get("command")
    if isinstance(command, list) and command:
        return _clip(" ".join(str(part) for part in command))
    patch = args.get("patch")
    if isinstance(patch, str) and patch:
        targets = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch, re.M)
        if targets:
            return _clip("writes " + ", ".join(t.strip() for t in targets[:3]))
    parts: list[str] = []
    bbox = args.get("bbox")
    city = args.get("city")
    # Live finding F-102 (2026-09-03, S44): the planner sends a placeholder
    # bbox [0, 0, 0, 0] beside city=Toronto; the tool ignores the box, so
    # the person approving must see the city, not a nonsense box.
    if isinstance(city, str) and city.strip() and not _real_bbox(bbox):
        parts.append(f"city={city.strip()}")
    elif _real_bbox(bbox):
        parts.append("bbox [" + ", ".join(f"{float(v):.3f}" for v in bbox) + "]")
    elif args.get("aoi_geojson"):
        parts.append("polygon AOI")
    for key in ("inp_path", "inp", "base_inp", "rpt_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(_short_path(value))
            break
    if args.get("start_date") and args.get("end_date"):
        parts.append(f"{args['start_date']}..{args['end_date']}")
    for key in ("node", "section", "objective"):
        value = args.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "auto":
            parts.append(f"{key}={value.strip()}")
    if not parts:
        run_dir = args.get("run_dir")
        if isinstance(run_dir, str) and run_dir.strip():
            parts.append(_short_path(run_dir))
    return _clip(", ".join(parts))


def _short_path(value: str) -> str:
    text = str(value).strip()
    marker = "/runs/"
    if marker in text:
        text = "runs/" + text.split(marker, 1)[1]
    return text if len(text) <= 60 else "..." + text[-57:]


def _clip(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def request_approval(tool_name: str, detail: str | None = None) -> ApprovalDecision:
    """Decide whether ``tool_name`` may execute, and record why.

    This is the opt-in confirmation seam. Interactive shells get a
    minimal y/N prompt. When there is no human on the other end (stdin is
    not a TTY, or the prompt hits EOF) the call FAILS CLOSED — a
    side-effecting tool is denied rather than silently auto-approved
    (review P1-2), because a headless / CI / background / injection-driven
    run has no one to catch a bad call. Trusted automation opts back into
    auto-approval explicitly with ``AISWMM_AUTO_APPROVE=1``.

    The decision carries its reason so the denial the user sees can name
    the cause and the opt-in instead of a bare refusal.
    """
    if os.environ.get(AUTO_APPROVE_ENV) == "1":
        return ApprovalDecision(approved=True, reason="auto")
    if not sys.stdin.isatty():
        return ApprovalDecision(approved=False, reason="headless")
    _prepare_prompt_line()
    question = f"Run {tool_name} ({detail})? [Y/n] " if detail else f"Run {tool_name}? [Y/n] "
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return ApprovalDecision(approved=False, reason="headless")
    finally:
        _restore_after_prompt()
    if answer in {"", "y", "yes"}:
        return ApprovalDecision(approved=True, reason="granted")
    return ApprovalDecision(approved=False, reason="declined")


def _prepare_prompt_line() -> None:
    """Give the approval prompt a clean line and a clean input buffer.

    Two live failures from the same user session (2026-08-09):

    * The executor's RUNNING spinner repaints its line every ~120 ms
      with no trailing newline. ``input()`` printed the question at the
      current cursor, the next tick overwrote the line head, and the
      cursor ended up in the middle of the visible text. Pausing and
      wiping the spinner first gives the prompt column 0 of a blank
      line.
    * A control character the user pressed during the planner's
      Thinking wait (``^R``) sat in the tty line buffer and was
      consumed as the answer, silently recording "N (skipped)" against
      an explicit Y. An approval must reflect a keypress made AFTER the
      question was visible, so pending input is discarded first.
    """
    try:
        from agentic_swmm.agent import ui

        ui.pause_active_spinner_for_prompt()
    except Exception:  # pragma: no cover - chrome must never block approval
        pass
    try:
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        # No termios (non-POSIX) or stdin not flushable: type-ahead
        # keeps its historical behaviour rather than failing the prompt.
        pass


def _restore_after_prompt() -> None:
    """Restart the paused spinner so the approved tool animates again."""
    try:
        from agentic_swmm.agent import ui

        ui.resume_active_spinner_after_prompt()
    except Exception:  # pragma: no cover - chrome must never block approval
        pass


def prompt_user(tool_name: str) -> bool:
    """Return whether ``tool_name`` may execute.

    Thin bool view of :func:`request_approval`, kept because callers that
    only need the verdict (and the tests that patch this seam) read more
    plainly without unpacking a decision object.
    """
    return request_approval(tool_name).approved
