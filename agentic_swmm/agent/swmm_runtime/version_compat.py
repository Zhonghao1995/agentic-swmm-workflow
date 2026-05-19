"""SWMM solver-version compatibility for run comparison (Round 3).

This module is **distinct** from
:mod:`agentic_swmm.memory.version_compat`. The memory module handles
*schema* migrations for our JSONL memory stores; this module checks
whether two SWMM **solver** versions are safe to compare.

Why the policy starts conservative
----------------------------------
SWMM 5.1.x and 5.2.x ship with different infiltration solvers, different
numerical conventions for time-stepping near continuity transitions,
and different default routing options. A modeler who diffs a 5.1 .rpt
against a 5.2 .rpt without realising it will mis-attribute solver-
behaviour deltas to their parameter change. The agent does not have
the depth of SWMM-internals knowledge to whitelist specific patch
deltas, so the policy is:

- Same exact version string → safe.
- Same major.minor (different patch, e.g. ``5.2.3`` vs ``5.2.4``) →
  safe with an advisory note. Patch releases historically carry bug
  fixes that change continuity-magnitude floors but not directional
  behaviour.
- Different major.minor (``5.1.013`` vs ``5.2.4``) → unsafe by
  default. The caller can force through with the override flag.
- Either version unparseable / missing → unsafe by default. The same
  override flag lets a modeler proceed when they know the runs are
  comparable.

The verdict is a small immutable dataclass so callers can render it
both as text (CLI) and as JSON (audit trace) without re-deriving
state. ``allow_with_override=True`` exists because the gate is a
**guard rail**, not a hard refusal — the modeler is the source of
truth, the agent's job is to make the cross-version decision explicit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# A version string we accept: ``<major>.<minor>`` plus an optional
# ``.<patch>`` (digits or dotted-digits). The patch is captured loosely
# (digits and dots only) so the SWMM-EPA convention ``5.1.013`` matches
# alongside the more compact ``5.2.4``.
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)(?:\.([\d\.]+))?\s*$")


@dataclass(frozen=True)
class SwmmVersionCompatVerdict:
    """Outcome of a version-compatibility check.

    Frozen so callers cannot scribble on a verdict between the policy
    producing it and the comparison surface consuming it.

    Attributes:
        ok: True when the two versions are byte-compatible per policy.
            False otherwise.
        reason: One-line human-readable explanation. Always populated
            so a CLI / trace consumer can render without knowing the
            policy internals.
        version_a: The first input string, unmodified (whitespace
            stripped).
        version_b: The second input string, unmodified.
        allow_with_override: When ``ok=False``, whether a caller may
            force through with an explicit override. Today this is
            ``True`` for every ``ok=False`` outcome — the gate is a
            guard rail, not a hard refusal — but the field is present
            so a future policy can distinguish "force-able" from
            "never force-able" without an API break.
    """

    ok: bool
    reason: str
    version_a: str
    version_b: str
    allow_with_override: bool


def _parse_version(version: str | None) -> tuple[int, int, str] | None:
    """Return ``(major, minor, patch)`` or ``None`` on unparseable input.

    The patch is returned as the original string (or empty) so callers
    can echo it in advisory messages without re-formatting. Returning
    ``None`` is the explicit "unparseable" signal — callers handle that
    branch separately from the "parseable but different minor" branch.
    """
    if not version or not isinstance(version, str):
        return None
    m = _VERSION_RE.match(version)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = m.group(3) or ""
    return major, minor, patch


def check_swmm_versions_for_compare(
    version_a: str | None, version_b: str | None
) -> SwmmVersionCompatVerdict:
    """Return whether two SWMM solver versions are safe to compare.

    Policy (start simple, refine when a maintainer can cite a specific
    patch delta that needs special-case handling):

    - Both versions parse and ``major.minor.patch`` match exactly →
      ``ok=True``.
    - Both versions parse and ``major.minor`` match (different patch)
      → ``ok=True`` with an advisory ``reason``.
    - Both versions parse and ``major.minor`` differ → ``ok=False``,
      ``allow_with_override=True``.
    - Either version is unparseable / missing → ``ok=False``,
      ``allow_with_override=True``.

    Arguments:
        version_a: The first version string (e.g. ``"5.2.4"``). ``None``
            and empty strings are treated as unparseable.
        version_b: The second version string.

    Returns:
        A :class:`SwmmVersionCompatVerdict`. The ``version_a`` /
        ``version_b`` fields echo the inputs (None becomes the literal
        string ``"unknown"`` for trace legibility).
    """
    label_a = (version_a or "").strip() or "unknown"
    label_b = (version_b or "").strip() or "unknown"

    parsed_a = _parse_version(version_a)
    parsed_b = _parse_version(version_b)

    if parsed_a is None or parsed_b is None:
        which = "both" if parsed_a is None and parsed_b is None else (
            "version_a" if parsed_a is None else "version_b"
        )
        return SwmmVersionCompatVerdict(
            ok=False,
            reason=(
                f"unparseable SWMM version string ({which}); cannot "
                f"determine compatibility — got ({label_a!r}, {label_b!r})"
            ),
            version_a=label_a,
            version_b=label_b,
            allow_with_override=True,
        )

    major_a, minor_a, patch_a = parsed_a
    major_b, minor_b, patch_b = parsed_b

    if (major_a, minor_a, patch_a) == (major_b, minor_b, patch_b):
        return SwmmVersionCompatVerdict(
            ok=True,
            reason=f"identical SWMM versions ({label_a})",
            version_a=label_a,
            version_b=label_b,
            allow_with_override=False,
        )

    if (major_a, minor_a) == (major_b, minor_b):
        return SwmmVersionCompatVerdict(
            ok=True,
            reason=(
                f"same SWMM minor version ({major_a}.{minor_a}); patch "
                f"levels differ ({patch_a or '-'} vs {patch_b or '-'}) — "
                "treated as compatible with advisory"
            ),
            version_a=label_a,
            version_b=label_b,
            allow_with_override=False,
        )

    return SwmmVersionCompatVerdict(
        ok=False,
        reason=(
            f"different SWMM minor versions: {label_a} vs {label_b}. "
            "Numerical differences between minor releases can change "
            "continuity behaviour. Pass override to proceed."
        ),
        version_a=label_a,
        version_b=label_b,
        allow_with_override=True,
    )


__all__ = [
    "SwmmVersionCompatVerdict",
    "check_swmm_versions_for_compare",
]
