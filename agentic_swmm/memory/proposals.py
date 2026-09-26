"""Proposals: changes to a skill, the shipped memory or the facts, awaiting a human.

Memory simplification PR 4. A proposal is one Markdown file under
``memory/proposals/`` (``NNN-<slug>.md``): a front matter with its id,
kind, status, target and dedupe key, then the evidence, the proposed
addition and a unified diff of the target. Nothing in it takes effect
until a human runs ``aiswmm memory promote <id>``; ``reject`` records
the decision so the same key is never proposed again. The program never
edits ``memory/initial/``, ``memory/facts.md`` or a ``SKILL.md`` on its
own.

Two sources:

- the failure loop (#554): the same fix for the same failure pattern
  seen in at least :data:`MIN_RUNS` runs across :data:`MIN_CASES`
  cases becomes a bullet for the skill that owns the failed tool
  (kind ``skill``) or, for an agent-internal tool, for
  ``memory/initial/operational_memory.md`` (kind ``rule``);
- the planner's ``record_fact`` tool: a candidate project fact becomes
  a kind ``fact`` proposal for ``memory/facts.md``.

The folder is the truth (status lives in the file); the two queries this
module needs, "is this key open or decided" and "list what is open",
scan it.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory.run_failures import RunFailure, read_run_failures

KINDS = ("skill", "rule", "fact")
STATUSES = ("open", "promoted", "rejected")

#: Evidence gate for a failure-derived proposal.
MIN_RUNS = 3
MIN_CASES = 2

#: The section a skill or rule addition is appended to (created when absent).
SECTION = "## Learned from runs"
RULE_TARGET = "memory/initial/operational_memory.md"
FACTS_TARGET = "memory/facts.md"

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


def _read_target(target: str) -> str:
    """Current text of ``target`` (a repo-relative path), read where it lives."""
    if target == FACTS_TARGET:
        from agentic_swmm.memory.facts import ensure_facts_md_exists, resolve_paths

        paths = resolve_paths()
        ensure_facts_md_exists(paths)
        return paths.facts_md.read_text(encoding="utf-8")
    root = _checkout_root()
    if root is None:
        from agentic_swmm.utils.paths import resource_root

        root = resource_root()
    path = root / target
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


def _section_for(kind: str) -> str | None:
    return None if kind == "fact" else SECTION


# --- files ------------------------------------------------------------------


def _render(proposal: Proposal, title: str, evidence_lines: list[str]) -> str:
    front = "\n".join(
        [
            "---",
            f"id: {proposal.id}",
            f"kind: {proposal.kind}",
            f"status: {proposal.status}",
            f"target: {proposal.target}",
            f"key: {proposal.key}",
            f"created_utc: {proposal.created_utc}",
            f"decided_utc: {proposal.decided_utc}",
            f"reason: {proposal.reason}",
            f"addition_json: {json.dumps(proposal.addition, ensure_ascii=False)}",
            f"evidence_json: {json.dumps(proposal.evidence, ensure_ascii=False, sort_keys=True)}",
            "---",
        ]
    )
    where = f'appended under "{SECTION}" of `{proposal.target}`' if proposal.kind != "fact" else f"appended to `{proposal.target}`"
    body = [
        "",
        f"# Proposal {proposal.id}: {title}",
        "",
        "## Evidence",
        "",
        *evidence_lines,
        "",
        f"## Proposed addition ({where})",
        "",
        proposal.addition.rstrip("\n"),
        "",
        "## Diff",
        "",
        "```diff",
        proposal.diff.rstrip("\n"),
        "```",
        "",
        "Decide with `aiswmm memory promote {id}` or `aiswmm memory reject {id} --reason ...`.".replace("{id}", proposal.id),
        "",
    ]
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
        path=path,
    )


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
    for name, value in (("status", proposal.status), ("decided_utc", proposal.decided_utc), ("reason", proposal.reason)):
        front = re.sub(rf"^{name}:.*$", f"{name}: {value}", front, count=1, flags=re.M)
    proposal.path.write_text("---\n" + front + "\n---\n" + text[match.end():], encoding="utf-8")


# --- source one: the failure loop ------------------------------------------


_AGAIN_RE = re.compile(r"^(?P<tool>\S+) again with (?P<rest>.*)$")


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


def case_slug(run_id: str) -> str:
    """The case part of a session id ``<stamp>_<slug>_<kind>``; the id itself otherwise."""
    name = run_id.rsplit("/", 1)[-1]
    parts = name.split("_")
    if len(parts) >= 3 and parts[0].isdigit():
        return "_".join(parts[1:-1])
    return name


def _skill_for_tool(tool: str) -> str | None:
    try:
        from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

        return _DETERMINISTIC_BINDINGS.get(tool)
    except Exception:  # pragma: no cover - the router is always importable in-tree
        return None


def _describe_fix(row: RunFailure) -> str:
    match = _AGAIN_RE.match(row.fix)
    if match and match.group("tool") == row.tool:
        rest = match.group("rest")
        if rest == "the same arguments":
            return "calling it again unchanged worked (a transient fault)"
        names = fix_key(row).rsplit("|", 1)[-1].replace(",", "`, `")
        return f"calling it again with a different `{names}` worked"
    return f"using `{row.fix_tool}` instead worked"


def propose_from_failures(store_dir: Path, directory: Path | None = None) -> list[Proposal]:
    """Create a proposal for every fix that clears the evidence gate and has no proposal yet."""
    rows = [row for row in read_run_failures(Path(store_dir) / "run_failures.jsonl") if row.fix]
    if not rows:
        return []
    folder = proposals_dir(directory)
    known = {proposal.key for proposal in load_all(folder)}
    groups: dict[str, list[RunFailure]] = {}
    for row in rows:
        groups.setdefault(fix_key(row), []).append(row)
    created: list[Proposal] = []
    for key, group in groups.items():
        runs = sorted({row.run_id for row in group})
        cases = sorted({case_slug(row.run_id) for row in group})
        if len(runs) < MIN_RUNS or len(cases) < MIN_CASES or key in known:
            continue
        latest = group[-1]
        skill = _skill_for_tool(latest.tool)
        kind = "skill" if skill else "rule"
        target = f"skills/{skill}/SKILL.md" if skill else RULE_TARGET
        summary = latest.pattern.split(":", 2)[-1]
        addition = (
            f"- When `{latest.tool}` fails with \"{summary}\", {_describe_fix(latest)} "
            f"(seen in {len(runs)} runs across {len(cases)} cases, last {latest.run_id}; "
            f"last fix: {latest.fix})."
        )
        before = _read_target(target)
        after = apply_addition(before, addition, section=SECTION)
        proposal = Proposal(
            id=_next_id(folder),
            kind=kind,
            status="open",
            target=target,
            key=key,
            created_utc=_utcnow(),
            addition=addition,
            diff=_unified_diff(target, before, after),
            evidence={"runs": runs, "cases": cases, "count": len(group), "pattern": latest.pattern, "fix": latest.fix},
        )
        title = f"{skill or 'operational memory'} learns a fix for `{latest.tool}`"
        evidence_lines = [
            f"- pattern: `{latest.pattern}`",
            f"- fix: {latest.fix}",
            f"- seen {len(group)} time(s) in {len(runs)} run(s) across {len(cases)} case(s): {', '.join(cases)}",
            f"- runs: {', '.join(runs)}",
        ]
        created.append(_write(folder, proposal, title, evidence_lines, _slugify(f"{latest.tool}-{summary}")))
        known.add(key)
    return created


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
    after = apply_addition(before, proposal.addition, section=_section_for(proposal.kind))
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


def reject(proposal_id: str, *, reason: str = "", directory: Path | None = None) -> dict[str, Any]:
    """Mark an open proposal rejected; its key is never proposed again."""
    proposal = _find(proposal_id, directory)
    if proposal.status != "open":
        return {"ok": False, "id": proposal.id, "reason": f"proposal {proposal.id} is {proposal.status}"}
    proposal.status = "rejected"
    proposal.decided_utc = _utcnow()
    proposal.reason = reason.strip()
    _rewrite(proposal)
    return {"ok": True, "id": proposal.id, "reason": proposal.reason}
