"""Structured error remediation (PRD-08 Phase A.3).

Every CLI surface that fails today emits a single ``error: <summary>``
line. The summary is correct but rarely enough to act on — the user
must read the source code or guess what to fix. This module formalises
the contract used by Phase A.3:

* ``error: <summary>`` — what went wrong (kept verbatim from the
  pre-A.3 surface so callers that have not yet migrated still print
  the same first line)
* ``cause: <cause>`` — the underlying reason, when knowable. Optional;
  omitted when the surface genuinely cannot tell.
* ``hint:  <hint>`` — the concrete next step. Optional; omitted when
  no actionable next step exists. Two leading spaces in front of
  ``hint:`` to align under the colon in ``cause:``.

The format intentionally lives on three short lines so a terminal user
can scan it as a stanza and a CI log can grep for any of the keywords.
We do *not* return rich JSON here — the CLI surfaces that opt into
``--json`` build a separate machine-readable payload from the same
fields.

The module also exposes typed builders for the common patterns the UX
audit flagged. Each builder takes only what's needed for that pain
point and returns a populated ``RemediationError``. The callers in
``agentic_swmm/commands/*.py`` then call ``format_for_stderr()`` and
``sys.stderr.write`` the result — no template strings spread across
files.

Karpathy guidelines: every existing error path is preserved when a
caller does not opt into a builder. The summary line keeps the legacy
``error: <text>`` shape so logs that grep ``^error:`` still match.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemediationError:
    """One structured error with summary + optional cause + optional hint.

    Frozen because callers pass it through a render pipeline and the
    rendered string must be stable; the dataclass exists primarily to
    keep the three fields named at the call site rather than relying on
    positional arguments.
    """

    summary: str
    cause: str | None = None
    hint: str | None = None

    def format_for_stderr(self) -> str:
        """Render the multi-line stderr stanza.

        The summary line is mandatory. Cause and hint are emitted only
        when present so a builder that genuinely cannot identify a
        cause does not pad the output with empty placeholders.
        """
        lines = [f"error: {self.summary}"]
        if self.cause is not None and self.cause.strip():
            lines.append(f"  cause: {self.cause}")
        if self.hint is not None and self.hint.strip():
            lines.append(f"  hint:  {self.hint}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, str | None]:
        """JSON-friendly payload for ``--json`` callers.

        Keeps the same three keys so a downstream JSON consumer can
        format the same stanza without duplicating logic.
        """
        return {
            "summary": self.summary,
            "cause": self.cause,
            "hint": self.hint,
        }


def fuzzy_match_suggestions(
    *,
    query: str,
    choices: list[str],
    max_suggestions: int = 3,
    min_similarity: float = 0.5,
) -> list[str]:
    """Return up to ``max_suggestions`` close matches from ``choices``.

    Wrapper over ``difflib.get_close_matches`` so the call site does
    not have to remember the cutoff/argument names. Returns an empty
    list when the query is blank or no candidate clears the cutoff so
    the calling builder can branch on ``if not suggestions: ...``.
    """
    if not query or not str(query).strip():
        return []
    pool = [c for c in (choices or []) if isinstance(c, str) and c]
    if not pool:
        return []
    return list(
        difflib.get_close_matches(
            str(query).strip(),
            pool,
            n=max(1, int(max_suggestions)),
            cutoff=float(min_similarity),
        )
    )


def _format_did_you_mean(suggestions: list[str]) -> str | None:
    """Render a "did you mean: a, b, c?" tail or ``None``."""
    if not suggestions:
        return None
    if len(suggestions) == 1:
        return f"did you mean: {suggestions[0]}?"
    return "did you mean: " + ", ".join(suggestions) + "?"


# ---------------------------------------------------------------------------
# Typed builders
# Each addresses one or more numbered pain points from the UX audit
# (.claude/ux-audit-findings.md). Keep the docstrings tight: every
# builder is one concept.


def parameter_lookup_error(
    *,
    parameter_name: str,
    benchmarks_path: Path,
    citations_path: Path | None = None,
    similar_names: list[str] | None = None,
    failure_mode: str = "unknown_parameter",
    citation_key: str | None = None,
) -> RemediationError:
    """Differentiate the four failure modes of ``aiswmm cite-param``.

    ``failure_mode`` is the discriminator:

    * ``"unknown_parameter"`` — the dotted name doesn't resolve to a
      leaf in ``reference_benchmarks.yaml``. Hint suggests similar
      names when ``similar_names`` is supplied.
    * ``"leaf_uncurated"`` — the leaf exists but ``min`` / ``max`` are
      ``None`` because the literature range has not been curated yet.
    * ``"missing_citation_key"`` — the leaf has a numeric range but no
      ``citation:`` token is set on it.
    * ``"citation_unregistered"`` — the leaf names a citation key that
      is not present in ``citations.yaml``. Pass ``citation_key`` to
      surface the offending token.
    """
    name = str(parameter_name)
    benchmarks_str = str(benchmarks_path)
    if failure_mode == "unknown_parameter":
        summary = (
            f"parameter {name!r} has no resolvable range in {benchmarks_str}"
        )
        cause = (
            f"unknown parameter — {name!r} is not a dotted leaf in the "
            f"reference benchmarks YAML"
        )
        suggestion_tail = _format_did_you_mean(similar_names or [])
        if suggestion_tail:
            hint = f"{suggestion_tail} run 'aiswmm cite-param --name <name>' with one of the suggestions"
        else:
            hint = (
                f"open {benchmarks_str} to confirm the dotted key, "
                "or run 'aiswmm bootstrap memory' if the file is missing"
            )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if failure_mode == "leaf_uncurated":
        summary = (
            f"parameter {name!r} has no resolvable range in {benchmarks_str}"
        )
        cause = (
            f"leaf is null because un-curated — the literature range for "
            f"{name!r} has not been entered yet"
        )
        hint = (
            f"edit {benchmarks_str} to populate min/max/citation for "
            f"{name!r}; see contributing notes for the curation policy"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if failure_mode == "missing_citation_key":
        summary = (
            f"parameter {name!r} has a range but no citation in "
            f"{benchmarks_str}"
        )
        cause = (
            f"benchmark leaf carries numeric min/max but the 'citation' "
            f"field is empty; no literature anchor available"
        )
        hint = (
            f"add a 'citation:' key on the leaf in {benchmarks_str} and "
            f"register the entry in citations.yaml"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if failure_mode == "citation_unregistered":
        cite_path = str(citations_path) if citations_path else "citations.yaml"
        key_str = str(citation_key) if citation_key else "(unknown)"
        summary = (
            f"parameter {name!r} references citation {key_str!r} which is "
            f"not registered"
        )
        cause = (
            f"citation key {key_str!r} is not registered in {cite_path}; "
            "the benchmark leaf points at a token with no library entry"
        )
        hint = (
            f"add an entry for {key_str!r} to {cite_path} with authors, "
            "year, title, work, and locator fields"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    # Defensive default — should never run, but never raise.
    return RemediationError(
        summary=f"parameter {name!r} could not be resolved",
        cause=None,
        hint=None,
    )


def case_not_found(*, slug: str, candidates: list[str]) -> RemediationError:
    """``aiswmm case show <slug>`` could not locate ``slug``.

    Runs a fuzzy match against ``candidates`` (the existing case
    slugs) so a typo like ``tod-creek`` vs ``todcreek`` surfaces a
    "did you mean: todcreek?" line instead of a bare not-found.
    """
    slug_str = str(slug)
    summary = f"no case_meta.yaml for {slug_str!r}"
    suggestions = fuzzy_match_suggestions(
        query=slug_str, choices=list(candidates or []), max_suggestions=3
    )
    if suggestions:
        cause = "case slug does not match any case under cases/"
        tail = _format_did_you_mean(suggestions)
        hint = (
            f"{tail} run 'aiswmm list cases' to see every registered slug"
        )
    else:
        cause = (
            "case slug does not match any case under cases/; the directory "
            "exists but case_meta.yaml is missing or the slug itself is new"
        )
        hint = (
            "run 'aiswmm list cases' to inventory cases, or "
            f"'aiswmm case init {slug_str}' to scaffold a new one"
        )
    return RemediationError(summary=summary, cause=cause, hint=hint)


def transfer_empty_result(
    *,
    calibration_store_exists: bool,
    similar_cases_found: int,
    store_path: Path | None = None,
) -> RemediationError:
    """Split the three "transfer returned nothing" modes.

    * Store missing on disk → ``aiswmm bootstrap memory`` first.
    * Store exists but empty → calibrate at least one case.
    * Store has rows but the new case is too dissimilar → adjust the
      similarity threshold or accept that no warm-start applies.
    """
    summary = (
        "no cross-watershed transfer candidates found"
    )
    if not calibration_store_exists:
        path_str = str(store_path) if store_path else "memory/modeling-memory/calibration_memory.jsonl"
        cause = f"calibration store does not exist at {path_str}"
        hint = (
            "run 'aiswmm bootstrap memory' to scaffold the memory "
            "directory, then calibrate at least one case"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if similar_cases_found == 0:
        cause = (
            "calibration store exists but contains no calibrated cases; "
            "transfer has nothing to draw from"
        )
        hint = (
            "calibrate at least one case to populate "
            "calibration_memory.jsonl (run 'aiswmm calibrate ...')"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    cause = (
        "calibration store has rows but no prior case scored above the "
        "similarity threshold; the new case may be too different from "
        "any calibrated case"
    )
    hint = (
        "lower the similarity threshold, calibrate a more similar case, "
        "or proceed without a warm start"
    )
    return RemediationError(summary=summary, cause=cause, hint=hint)


def storm_library_not_found(
    *,
    entry_key: str,
    library_path: Path,
    available_keys: list[str] | None = None,
    failure_mode: str = "library_missing",
) -> RemediationError:
    """``aiswmm storm --library-entry`` could not honour the lookup.

    Three modes:

    * ``"library_missing"`` — the YAML file itself is absent.
    * ``"entry_missing"`` — the file exists but does not carry an
      entry for ``entry_key``; lists ``available_keys`` in the hint.
    * ``"entry_placeholder"`` — the entry exists but every leaf is
      ``None``, i.e. a schema placeholder waiting to be populated.
    """
    key = str(entry_key)
    lib_str = str(library_path)
    if failure_mode == "library_missing":
        summary = f"storm_library entry {key!r} could not be resolved"
        cause = f"the library file does not exist at {lib_str}"
        hint = (
            "run 'aiswmm bootstrap memory' to scaffold the memory "
            "directory; the library is created with empty placeholders"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if failure_mode == "entry_missing":
        summary = f"storm_library entry {key!r} could not be resolved"
        cause = f"the library file at {lib_str} has no entry named {key!r}"
        avail = ", ".join(sorted(set(available_keys or []))[:10]) if available_keys else "(none)"
        suffix = "" if len(available_keys or []) <= 10 else " (top 10 shown)"
        hint = f"available keys: {avail}{suffix}"
        return RemediationError(summary=summary, cause=cause, hint=hint)
    if failure_mode == "entry_placeholder":
        summary = f"storm_library entry {key!r} could not be resolved"
        cause = (
            f"the entry {key!r} exists in {lib_str} but all of its "
            "required leaves (idf_params/peak_position/duration_min) "
            "are still null — this is a schema placeholder"
        )
        hint = (
            f"edit {lib_str} to populate idf_params, peak_position and "
            f"duration_min for {key!r}"
        )
        return RemediationError(summary=summary, cause=cause, hint=hint)
    return RemediationError(
        summary=f"storm_library entry {key!r} could not be resolved",
        cause=None,
        hint=None,
    )


def staged_facts_empty(*, staging_md: Path | None = None) -> RemediationError:
    """``aiswmm memory promote-facts`` found nothing to promote.

    The user almost always reaches this command after the agent
    proposed a fact but they forgot the staging step, so the hint
    points at both the right tool (``record_fact``) and the right
    file path so they can also paste a candidate by hand.
    """
    staging_str = str(staging_md) if staging_md else "agent/memory/curated/facts_staging.md"
    summary = "no staged facts to promote"
    cause = (
        f"the staging file at {staging_str} is empty; nothing has been "
        "queued by the 'record_fact' tool yet"
    )
    hint = (
        "ask the agent to record a fact (it calls 'record_fact' which "
        f"appends to {staging_str}), or edit the file directly with one "
        "fact block per entry"
    )
    return RemediationError(summary=summary, cause=cause, hint=hint)


__all__ = [
    "RemediationError",
    "fuzzy_match_suggestions",
    "parameter_lookup_error",
    "case_not_found",
    "transfer_empty_result",
    "storm_library_not_found",
    "staged_facts_empty",
]
