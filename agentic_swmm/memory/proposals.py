"""Proposals: changes to a skill, the shipped memory or the facts, awaiting a human.

A proposal is one Markdown file under ``memory/proposals/``
(``NNN-<slug>.md``): a front matter with its id, kind, status, target
and dedupe key, then the evidence, the proposed addition, a unified
diff of the target and the prompts that reproduce the evidence. Nothing
in it takes effect until a human runs ``aiswmm memory promote <id>``;
``reject`` records the decision so the same key is never proposed again
(unless the rejection named a recurrence count). The program never
edits ``memory/initial/``, ``memory/facts.md`` or a ``SKILL.md`` on its
own.

Two sources:

- the failure loop: the same fix for the same failure pattern seen
  often enough (:func:`clears_gate`) becomes a bullet for the skill
  that owns the failed tool (kind ``skill``) or, for an agent-internal
  tool, for ``memory/initial/operational_memory.md`` (kind ``rule``);
- the planner's ``record_fact`` tool: a candidate project fact becomes
  a kind ``fact`` proposal for ``memory/facts.md``.

The evidence gate (skill refinement loop, ADR-0014): at least
:data:`MIN_RUNS` runs across :data:`MIN_CASES` cases, the case being the
run directory the failing call touched (never a chat session's slug),
the latest occurrence within :data:`FRESH_DAYS`; refusals by policy are
never evidence. Before a proposal is written, the tracked skills and the
initial memory are searched for the fix's distinctive tokens: knowledge
that already exists is reported, and a key whose knowledge is already in
its own target is marked ``documented`` instead of proposed.

The folder is the truth (status lives in the file); the queries this
module needs, "is this key open or decided" and "list what is open",
scan it.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory.run_failures import RunFailure, _parse_stamp, read_run_failures

KINDS = ("tool", "skill", "rule", "fact")
STATUSES = ("open", "promoted", "rejected", "documented")

#: Evidence gate for a failure-derived proposal.
MIN_RUNS = 3
MIN_CASES = 2
#: When not every row carries a case: more runs, on more than one day.
MIN_RUNS_WITHOUT_CASES = 4
MIN_DAYS_WITHOUT_CASES = 2
#: The latest occurrence must be this recent (days).
FRESH_DAYS = 60

#: The section a skill or rule addition is appended to (created when absent).
SECTION = "## Learned from runs"
RULE_TARGET = "memory/initial/operational_memory.md"
FACTS_TARGET = "memory/facts.md"
#: Tool-level experience lives in the tool notes (one section per tool).
TOOL_TARGET = "memory/initial/tool_notes.md"
#: Skills that never receive a public proposal.
PRIVATE_SKILLS = frozenset({"swmm-lid-optimization"})
#: Tracked files the dedupe search reads, relative to the checkout.
_DEDUPE_GLOBS = ("skills/*/SKILL.md", "memory/initial/*.md")
#: Prompts of the evidence sessions carried for verification.
MAX_VERIFY_PROMPTS = 3

_FACT_DELIMITER = "§"
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_ID_RE = re.compile(r"^(\d{3})-")


@dataclass
class Proposal:
    """One proposal file, parsed."""

    id: str
    kind: str
    status: str
    target: str
    key: str
    created_utc: str
    addition: str
    diff: str
    evidence: dict[str, Any] = field(default_factory=dict)
    decided_utc: str = ""
    reason: str = ""
    until: int = 0
    baseline: int = 0
    verify: list[str] = field(default_factory=list)
    dedupe: dict[str, Any] = field(default_factory=dict)
    section: str = ""
    path: Path | None = None

    @property
    def slug(self) -> str:
        return self.path.stem[4:] if self.path is not None else ""


# --- location ---------------------------------------------------------------


def proposals_dir(explicit: Path | None = None) -> Path:
    """``memory/proposals`` in the workspace (next to the store), or an override."""
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("AISWMM_PROPOSALS_DIR")
    if override:
        return Path(override)
    from agentic_swmm.utils.paths import resolve_memory_dir

    return resolve_memory_dir().parent / "proposals"


def _checkout_root() -> Path | None:
    """The source checkout a skill or rule promotion edits, or ``None`` on a pip install."""
    from agentic_swmm.utils.paths import is_checkout, repo_root

    root = repo_root()
    return root if is_checkout(root) else None


def _tracked_root() -> Path:
    """Where the tracked skills and the initial memory are read from."""
    root = _checkout_root()
    if root is None:
        from agentic_swmm.utils.paths import resource_root

        root = resource_root()
    return root


def _read_target(target: str) -> str:
    """Current text of ``target`` (a repo-relative path), read where it lives."""
    if target == FACTS_TARGET:
        from agentic_swmm.memory.facts import ensure_facts_md_exists, resolve_paths

        paths = resolve_paths()
        ensure_facts_md_exists(paths)
        return paths.facts_md.read_text(encoding="utf-8")
    path = _tracked_root() / target
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _target_path_for_write(proposal: Proposal) -> Path:
    if proposal.kind == "fact":
        from agentic_swmm.memory.facts import ensure_facts_md_exists, resolve_paths

        paths = resolve_paths()
        ensure_facts_md_exists(paths)
        return paths.facts_md
    root = _checkout_root()
    if root is None:
        raise RuntimeError(
            f"promoting a {proposal.kind} proposal edits {proposal.target}, a tracked file; "
            "run this in a source checkout of the repository"
        )
    return root / proposal.target


# --- text operations --------------------------------------------------------


def apply_addition(text: str, addition: str, *, section: str | None) -> str:
    """Return ``text`` with ``addition`` appended, under ``section`` when given.

    The section is created at the end when absent; when present the
    addition goes at its end, before the next ``## `` heading.
    """
    body = text if text.endswith("\n") or not text else text + "\n"
    block = addition.rstrip("\n") + "\n"
    if section is None:
        return body + ("\n" if body and not body.endswith("\n\n") else "") + block
    lines = body.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.rstrip("\n") == section), None)
    if start is None:
        return body + ("\n" if body and not body.endswith("\n\n") else "") + f"{section}\n\n" + block
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    # keep one blank line between the section's last line and the next heading
    insert_at = end
    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    trailing = lines[insert_at:end]
    return "".join(lines[:insert_at]) + block + "".join(trailing) + "".join(lines[end:])


def _unified_diff(target: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
        )
    )


def _section_for(proposal: Proposal) -> str | None:
    if proposal.section:
        return proposal.section
    return None if proposal.kind == "fact" else SECTION


# --- files ------------------------------------------------------------------


def _render(proposal: Proposal, title: str, evidence_lines: list[str]) -> str:
    front = "\n".join(
        [
            "---",
            f"id: {proposal.id}",
            f"kind: {proposal.kind}",
            f"status: {proposal.status}",
            f"target: {proposal.target}",
            f"section: {proposal.section}",
            f"key: {proposal.key}",
            f"created_utc: {proposal.created_utc}",
            f"decided_utc: {proposal.decided_utc}",
            f"reason: {proposal.reason}",
            f"until: {int(proposal.until)}",
            f"baseline_60d: {int(proposal.baseline)}",
            f"addition_json: {json.dumps(proposal.addition, ensure_ascii=False)}",
            f"evidence_json: {json.dumps(proposal.evidence, ensure_ascii=False, sort_keys=True)}",
            f"verify_json: {json.dumps(proposal.verify, ensure_ascii=False)}",
            f"dedupe_json: {json.dumps(proposal.dedupe, ensure_ascii=False, sort_keys=True)}",
            "---",
        ]
    )
    section = _section_for(proposal)
    where = f'appended under "{section}" of `{proposal.target}`' if section else f"appended to `{proposal.target}`"
    body = ["", f"# Proposal {proposal.id}: {title}", "", "## Evidence", "", *evidence_lines]
    found = (proposal.dedupe or {}).get("found") or []
    if found:
        body += ["", "## Already written elsewhere", ""]
        body += [f"- `{hit['file']}` line {hit['line']}" for hit in found]
        if proposal.dedupe.get("in_target"):
            body += ["", f"The target already carries this knowledge; nothing to promote (status `{proposal.status}`)."]
        else:
            body += ["", "Written, but not where the planner reads at the moment it matters; this proposal puts it in the target instead of duplicating prose."]
    body += ["", f"## Proposed addition ({where})", "", proposal.addition.rstrip("\n")]
    if proposal.diff.strip():
        body += ["", "## Diff", "", "```diff", proposal.diff.rstrip("\n"), "```"]
    if proposal.verify:
        body += ["", "## Verify (prompts of the evidence sessions)", ""]
        body += [f"- {prompt}" for prompt in proposal.verify]
    if proposal.status == "open":
        body += ["", f"Decide with `aiswmm memory promote {proposal.id}` or `aiswmm memory reject {proposal.id} --reason ...`."]
    body.append("")
    return front + "\n" + "\n".join(body)


def _parse(path: Path) -> Proposal | None:
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
    try:
        addition = json.loads(fields.get("addition_json") or '""')
        evidence = json.loads(fields.get("evidence_json") or "{}")
        verify = json.loads(fields.get("verify_json") or "[]")
        dedupe = json.loads(fields.get("dedupe_json") or "{}")
    except json.JSONDecodeError:
        return None
    diff_match = re.search(r"```diff\n(.*?)\n```", text, re.S)
    return Proposal(
        id=fields.get("id", path.name[:3]),
        kind=fields.get("kind", ""),
        status=fields.get("status", "open"),
        target=fields.get("target", ""),
        key=fields.get("key", ""),
        created_utc=fields.get("created_utc", ""),
        addition=str(addition),
        diff=(diff_match.group(1) + "\n") if diff_match else "",
        evidence=evidence if isinstance(evidence, dict) else {},
        decided_utc=fields.get("decided_utc", ""),
        reason=fields.get("reason", ""),
        section=fields.get("section", ""),
        until=_int(fields.get("until")),
        baseline=_int(fields.get("baseline_60d")),
        verify=[str(item) for item in verify] if isinstance(verify, list) else [],
        dedupe=dedupe if isinstance(dedupe, dict) else {},
        path=path,
    )


def _int(value: str | None) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def load_all(directory: Path | None = None) -> list[Proposal]:
    """Every proposal in the folder, by id."""
    folder = proposals_dir(directory)
    if not folder.is_dir():
        return []
    out: list[Proposal] = []
    for path in sorted(folder.glob("[0-9][0-9][0-9]-*.md")):
        proposal = _parse(path)
        if proposal is not None:
            out.append(proposal)
    return out


def list_proposals(directory: Path | None = None, *, include_decided: bool = False) -> list[Proposal]:
    proposals = load_all(directory)
    if include_decided:
        return proposals
    return [proposal for proposal in proposals if proposal.status == "open"]


def _next_id(folder: Path) -> str:
    ids = [int(m.group(1)) for p in folder.glob("*.md") if (m := _ID_RE.match(p.name))]
    return f"{(max(ids) + 1) if ids else 1:03d}"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "proposal"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write(folder: Path, proposal: Proposal, title: str, evidence_lines: list[str], slug: str) -> Proposal:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{proposal.id}-{slug}.md"
    proposal.path = path
    path.write_text(_render(proposal, title, evidence_lines), encoding="utf-8")
    return proposal


def _rewrite(proposal: Proposal) -> None:
    assert proposal.path is not None
    text = proposal.path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(text)
    assert match is not None
    front = match.group(1)
    for name, value in (
        ("status", proposal.status),
        ("decided_utc", proposal.decided_utc),
        ("reason", proposal.reason),
        ("until", str(int(proposal.until))),
    ):
        front = re.sub(rf"^{name}:.*$", f"{name}: {value}", front, count=1, flags=re.M)
    proposal.path.write_text("---\n" + front + "\n---\n" + text[match.end():], encoding="utf-8")


# --- source one: the failure loop ------------------------------------------


_AGAIN_RE = re.compile(r"^(?P<tool>\S+) again with (?P<rest>.*)$")
_CHANGE_RE = re.compile(r"^(\w+): (.*) -> (.*)$", re.S)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{3,}")


def fix_key(row: RunFailure) -> str:
    """The dedupe key of a recorded fix: pattern, fix tool, and for a retry the argument names that changed.

    Values differ from run to run (paths, stamps); the names do not, so
    "the same fix" means the same tool and the same arguments touched.
    """
    match = _AGAIN_RE.match(row.fix)
    if match and match.group("tool") == row.tool:
        rest = match.group("rest")
        if rest == "the same arguments":
            names = "retry"
        else:
            found: list[str] = []
            for part in rest.split("; "):
                m = re.match(r"^(\w+)(?::| dropped$|=)", part)
                if m:
                    found.append(m.group(1))
            names = ",".join(sorted(set(found))) or "args"
        return f"{row.pattern}|{row.tool}|{names}"
    return f"{row.pattern}|{row.fix_tool}|instead"


def _skill_for_tool(tool: str) -> str | None:
    try:
        from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

        skill = _DETERMINISTIC_BINDINGS.get(tool)
    except Exception:  # pragma: no cover - the router is always importable in-tree
        return None
    return None if skill in PRIVATE_SKILLS else skill


def _is_fresh(group: list[RunFailure], now: datetime) -> bool:
    stamps = [stamp for row in group if (stamp := _parse_stamp(row.recorded_at)) is not None]
    return bool(stamps) and max(stamps) >= now.timestamp() - FRESH_DAYS * 86400


def clears_gate(group: list[RunFailure], *, now: datetime | None = None) -> bool:
    """Whether a group of rows sharing a fix key is evidence enough for a proposal."""
    moment = now or datetime.now(timezone.utc)
    if not group or not _is_fresh(group, moment):
        return False
    runs = {row.run_id for row in group}
    if all(row.case for row in group):
        return len(runs) >= MIN_RUNS and len({row.case for row in group}) >= MIN_CASES
    days = {str(row.recorded_at or "")[:10] for row in group if row.recorded_at}
    return len(runs) >= MIN_RUNS_WITHOUT_CASES and len(days) >= MIN_DAYS_WITHOUT_CASES


def distinctive_tokens(fix: str) -> list[str]:
    """The tokens a fix introduced: in the new values, absent from the old ones."""
    match = _AGAIN_RE.match(fix or "")
    if not match:
        return []
    out: list[str] = []
    for part in match.group("rest").split("; "):
        change = _CHANGE_RE.match(part)
        if not change:
            continue
        before = set(_TOKEN_RE.findall(change.group(2)))
        for token in _TOKEN_RE.findall(change.group(3)):
            if token not in before and not token.replace(".", "").isdigit() and token not in out:
                out.append(token)
    return out


def dedupe_finding(root: Path | None, target: str, tokens: list[str]) -> dict[str, Any]:
    """Where the tracked files already carry every token, and whether the target is among them."""
    finding: dict[str, Any] = {"tokens": tokens, "found": [], "in_target": False}
    if not tokens or root is None:
        return finding
    for pattern in _DEDUPE_GLOBS:
        for path in sorted(Path(root).glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(f"skills/{skill}/") for skill in PRIVATE_SKILLS):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not all(token in text for token in tokens):
                continue
            line = next((i + 1 for i, row in enumerate(text.splitlines()) if tokens[0] in row), 1)
            finding["found"].append({"file": rel, "line": line})
            if rel == target:
                finding["in_target"] = True
    finding["found"].sort(key=lambda hit: hit["file"])
    return finding


def _verify_prompts(store_dir: Path, run_ids: list[str]) -> list[str]:
    """The first prompt of each evidence session, from the sessions table."""
    from agentic_swmm.memory import session_db
    from agentic_swmm.memory.store import db_path_for

    db_path = db_path_for(Path(store_dir))
    if not db_path.is_file():
        return []
    prompts: list[str] = []
    try:
        with session_db.connect(db_path) as conn:
            for run_id in run_ids:
                escaped = run_id.replace("_", "\\_")
                row = conn.execute(
                    "SELECT goal FROM sessions WHERE (session_id = ? OR session_id LIKE ? ESCAPE '\\') "
                    "AND goal IS NOT NULL AND goal != '' ORDER BY session_id DESC LIMIT 1",
                    (run_id, f"%\\_{escaped}"),
                ).fetchone()
                goal = str(row[0]).strip() if row else ""
                if goal and goal not in prompts:
                    prompts.append(goal)
                if len(prompts) >= MAX_VERIFY_PROMPTS:
                    break
    except Exception:  # noqa: BLE001 - the prompts are a convenience, never a blocker
        return prompts
    return prompts


def _rests(group: list[RunFailure]) -> list[str]:
    """What each same-tool fix changed ("key: old -> new; ...")."""
    out: list[str] = []
    for row in group:
        match = _AGAIN_RE.match(row.fix)
        if match:
            out.append(match.group("rest"))
    return out


def _describe_changes(group: list[RunFailure], limit: int = 3) -> str:
    """The distinct changes the fix made, most frequent first, at most ``limit``."""
    latest = group[-1]
    match = _AGAIN_RE.match(latest.fix)
    if match and match.group("tool") == latest.tool:
        if match.group("rest") == "the same arguments":
            return "calling it again unchanged worked (a transient fault)"
        names = fix_key(latest).rsplit("|", 1)[-1].replace(",", "`, `")
        counts = Counter(rest for rest in _rests(group) if rest != "the same arguments")
        changes = "; ".join(f"`{rest}` ({n} run{'s' if n != 1 else ''})" for rest, n in counts.most_common(limit))
        return f"calling it again with a different `{names}` worked: {changes}"
    return f"using `{latest.fix_tool}` instead worked"


def _tool_note(group: list[RunFailure], where: str) -> str:
    """A tool-note bullet within the note cap: the full sentence, else without
    the evidence tail, else with one change only, else cut at a word."""
    from agentic_swmm.memory.tool_notes import MAX_NOTE_CHARS, fit_note

    latest = group[-1]
    summary = latest.pattern.split(":", 2)[-1]
    candidates = [
        f"- Fails with \"{summary}\": {_describe_changes(group)} ({where}).",
        f"- Fails with \"{summary}\": {_describe_changes(group)}.",
        f"- Fails with \"{summary}\": {_describe_changes(group, limit=1)}.",
    ]
    for text in candidates:
        if len(text) - 2 <= MAX_NOTE_CHARS:
            return text
    return "- " + fit_note(candidates[-1][2:])


def _blocked_keys(folder: Path, groups: dict[str, list[RunFailure]]) -> set[str]:
    """Keys with a proposal already: open, promoted, documented, or rejected without a reachable ``until``."""
    by_key: dict[str, list[Proposal]] = {}
    for proposal in load_all(folder):
        by_key.setdefault(proposal.key, []).append(proposal)
    blocked: set[str] = set()
    for key, items in by_key.items():
        latest = items[-1]
        runs = len({row.run_id for row in groups.get(key, [])})
        if latest.status == "rejected" and latest.until > 0 and runs >= latest.until:
            continue
        blocked.add(key)
    return blocked


def propose_from_failures(store_dir: Path, directory: Path | None = None, *, now: datetime | None = None) -> list[Proposal]:
    """Create a proposal for every fix that clears the evidence gate and has no proposal yet."""
    store_dir = Path(store_dir)
    all_rows = read_run_failures(store_dir / "run_failures.jsonl")
    rows = [row for row in all_rows if row.fix and row.failure_class != "policy_refusal"]
    if not rows:
        return []
    folder = proposals_dir(directory)
    groups: dict[str, list[RunFailure]] = {}
    for row in rows:
        groups.setdefault(fix_key(row), []).append(row)
    blocked = _blocked_keys(folder, groups)
    moment = now or datetime.now(timezone.utc)
    cutoff = moment.timestamp() - FRESH_DAYS * 86400
    root = _tracked_root()
    created: list[Proposal] = []
    for key, group in groups.items():
        if key in blocked or not clears_gate(group, now=moment):
            continue
        runs = sorted({row.run_id for row in group})
        cases = sorted({row.case for row in group if row.case})
        latest = group[-1]
        skill = _skill_for_tool(latest.tool)
        summary = latest.pattern.split(":", 2)[-1]
        where = f"{len(runs)} runs across {len(cases)} cases: {', '.join(cases)}" if cases else f"{len(runs)} runs"
        if skill:
            kind, target, section = "skill", f"skills/{skill}/SKILL.md", SECTION
            addition = f"- When `{latest.tool}` fails with \"{summary}\", {_describe_changes(group)} ({where})."
        else:
            # An agent-internal tool has no SKILL.md: the experience rides the
            # tool description through the tool notes (ADR-0014).
            kind, target, section = "tool", TOOL_TARGET, f"## {latest.tool}"
            addition = _tool_note(group, where)
        finding = dedupe_finding(root, target, distinctive_tokens(latest.fix))
        baseline = sum(
            1 for row in all_rows if row.pattern == latest.pattern and (stamp := _parse_stamp(row.recorded_at)) is not None and stamp >= cutoff
        )
        proposal = Proposal(
            id=_next_id(folder),
            kind=kind,
            status="documented" if finding["in_target"] else "open",
            target=target,
            section=section,
            key=key,
            created_utc=_utcnow(),
            addition=addition,
            diff="",
            evidence={
                "runs": runs,
                "cases": cases,
                "count": len(group),
                "pattern": latest.pattern,
                "fix": latest.fix,
                "changes": [[rest, n] for rest, n in Counter(_rests(group)).most_common(3)],
            },
            baseline=baseline,
            verify=_verify_prompts(store_dir, runs),
            dedupe=finding,
        )
        if proposal.status == "open":
            before = _read_target(target)
            proposal.diff = _unified_diff(target, before, apply_addition(before, addition, section=section))
        else:
            proposal.decided_utc = proposal.created_utc
            proposal.reason = f"already written in {target}"
        title = f"{skill} learns a fix for `{latest.tool}`" if skill else f"`{latest.tool}` learns from runs"
        evidence_lines = [
            f"- pattern: `{latest.pattern}`",
            f"- fix: {latest.fix}",
            f"- seen {len(group)} time(s) in {len(runs)} run(s)" + (f" across {len(cases)} case(s): {', '.join(cases)}" if cases else " (no case could be read; the stricter gate applied)"),
            f"- runs: {', '.join(runs)}",
            f"- occurrences of the pattern in the last {FRESH_DAYS} days: {baseline}",
        ]
        created.append(_write(folder, proposal, title, evidence_lines, _slugify(f"{latest.tool}-{summary}")))
        blocked.add(key)
    return created


def propose_all(store_dir: Path, directory: Path | None = None) -> list[Proposal]:
    """Run every proposal source over the store: the session end, the audit hook and ``--refresh`` call this."""
    return propose_from_failures(Path(store_dir), directory)


# --- source two: record_fact -------------------------------------------------


def propose_fact(text: str, *, source_session_id: str | None = None, directory: Path | None = None) -> Proposal:
    """Turn a candidate project fact into a kind ``fact`` proposal for ``memory/facts.md``."""
    text = (text or "").strip()
    if not text:
        raise ValueError("propose_fact: text must not be empty")
    folder = proposals_dir(directory)
    stamp = _utcnow()
    addition = (
        f"{_FACT_DELIMITER}\n"
        f"text: {text}\n"
        f"source_session: {source_session_id or 'unknown'}\n"
        f"proposed_utc: {stamp}\n"
        f"{_FACT_DELIMITER}\n"
    )
    before = _read_target(FACTS_TARGET)
    after = apply_addition(before, addition, section=None)
    proposal = Proposal(
        id=_next_id(folder),
        kind="fact",
        status="open",
        target=FACTS_TARGET,
        key=f"fact|{text}",
        created_utc=stamp,
        addition=addition,
        diff=_unified_diff(FACTS_TARGET, before, after),
        evidence={"source_session": source_session_id or "unknown"},
    )
    evidence_lines = [f"- proposed by the agent in session {source_session_id or 'unknown'}"]
    return _write(folder, proposal, "a project fact", evidence_lines, _slugify(text))


# --- decisions ---------------------------------------------------------------


def _find(proposal_id: str, directory: Path | None) -> Proposal:
    wanted = proposal_id.strip().zfill(3)
    for proposal in load_all(directory):
        if proposal.id == wanted:
            return proposal
    raise KeyError(f"no proposal {wanted} under {proposals_dir(directory)}")


def promote(proposal_id: str, directory: Path | None = None) -> dict[str, Any]:
    """Apply an open proposal to its target file and mark it promoted.

    The addition is applied by the same operation the proposal was made
    with; the resulting diff must equal the stored one, so a target that
    changed since the proposal is refused rather than patched blindly.
    """
    proposal = _find(proposal_id, directory)
    if proposal.status != "open":
        return {"ok": False, "id": proposal.id, "reason": f"proposal {proposal.id} is {proposal.status}"}
    try:
        path = _target_path_for_write(proposal)
    except RuntimeError as exc:
        return {"ok": False, "id": proposal.id, "reason": str(exc)}
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    if proposal.kind == "tool":
        from agentic_swmm.memory.tool_notes import MAX_NOTE_CHARS, MAX_NOTES_PER_TOOL, parse_tool_notes

        tool = proposal.section[3:] if proposal.section.startswith("## ") else proposal.section
        existing = parse_tool_notes(before).get(tool, [])
        if len(existing) >= MAX_NOTES_PER_TOOL:
            return {
                "ok": False,
                "id": proposal.id,
                "reason": f"{proposal.target} already holds {len(existing)} notes for {tool}; the cap is {MAX_NOTES_PER_TOOL}, retire one first",
            }
        if len(proposal.addition.strip()) - 2 > MAX_NOTE_CHARS:
            return {"ok": False, "id": proposal.id, "reason": f"the note is {len(proposal.addition.strip()) - 2} characters; the cap is {MAX_NOTE_CHARS}"}
    after = apply_addition(before, proposal.addition, section=_section_for(proposal))
    if _unified_diff(proposal.target, before, after) != proposal.diff:
        return {
            "ok": False,
            "id": proposal.id,
            "reason": f"{proposal.target} changed since the proposal was made; reject it and let it be proposed again",
        }
    path.write_text(after, encoding="utf-8")
    proposal.status = "promoted"
    proposal.decided_utc = _utcnow()
    _rewrite(proposal)
    return {"ok": True, "id": proposal.id, "target": str(path), "kind": proposal.kind}


def reject(proposal_id: str, *, reason: str = "", until: int = 0, directory: Path | None = None) -> dict[str, Any]:
    """Mark an open proposal rejected; its key is never proposed again.

    ``until`` names a number of distinct runs after which the key may be
    proposed once more (the evidence grew enough to look again).
    """
    proposal = _find(proposal_id, directory)
    if proposal.status != "open":
        return {"ok": False, "id": proposal.id, "reason": f"proposal {proposal.id} is {proposal.status}"}
    proposal.status = "rejected"
    proposal.decided_utc = _utcnow()
    proposal.reason = reason.strip()
    proposal.until = max(0, int(until or 0))
    _rewrite(proposal)
    return {"ok": True, "id": proposal.id, "reason": proposal.reason, "until": proposal.until}
