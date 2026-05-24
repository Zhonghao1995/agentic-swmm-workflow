"""Env gate for experimental LLM providers (issue #182).

Today this gate hides the ``claude_sdk`` provider behind
``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS``. The provider implementation
itself is preserved unchanged — see
``agentic_swmm.providers.claude_sdk_api`` and the corresponding branch
of ``agentic_swmm.providers.factory`` — but every user-facing surface
(argparse choices on ``setup`` / ``chat`` / ``model`` / ``agent``, the
no-provider welcome banner, the interactive preflight) consults the
predicate below before exposing it.

When the gate is OFF (the default for new users):

* ``available_provider_choices()`` returns ``["openai"]`` so argparse
  rejects ``--provider claude_sdk`` at parse time with an ``invalid
  choice`` message.
* The "Quick fix (option 2) — Claude Pro/Max subscription" block must
  be omitted from the no-provider guidance banner.
* A legacy ``provider.default = claude_sdk`` in
  ``~/.aiswmm/config.toml`` is silently downgraded to ``openai`` after
  printing ``gate_notice_for_legacy_config()`` exactly once per
  process. The downgrade path lives in
  ``agent.provider_preflight.check_interactive_provider``.

Re-enabling is a single env export. The gate keeps the implementation
in tree (for developers and the eventual reactivation) while removing
the user trap.
"""
from __future__ import annotations

import os


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def claude_sdk_enabled() -> bool:
    """Return True iff the experimental-providers env gate is set.

    Truthy values (case-insensitive): ``"1"``, ``"true"``, ``"yes"``,
    ``"on"`` — matching ``agentic_swmm.agent.feature_flags._TRUTHY`` so
    every ``AISWMM_*`` boolean env var honours the same set. Anything
    else — including unset, ``""``, ``"0"``, ``"false"``, ``"no"``,
    ``"off"`` — returns False. We use an explicit allowlist rather
    than a generic non-empty check so a stray export of ``=0`` does
    not accidentally flip the gate ON.
    """
    return os.environ.get(_ENV_VAR, "").strip().lower() in _TRUTHY


def available_provider_choices() -> list[str]:
    """Return the argparse ``--provider`` choices for the current gate state.

    Gate OFF → ``["openai"]``; gate ON → ``["openai", "claude_sdk"]``.
    The order is stable so help text rendering is deterministic.
    """
    if claude_sdk_enabled():
        return ["openai", "claude_sdk"]
    return ["openai"]


def gate_notice_for_legacy_config() -> str:
    """Return the notice line printed when a legacy config selects ``claude_sdk``.

    Two actionable remedies, named explicitly so the user can pick
    either path without re-reading the README:

    1. Set the env var to re-enable the provider as-is.
    2. Switch the persisted default to ``openai`` to silence the notice.

    Returned as a single line (no trailing newline) so the caller
    controls how it lands on stderr.
    """
    return (
        "`claude_sdk` is currently gated. Run "
        "`export AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS=1` to re-enable, or "
        "`aiswmm config set provider.default openai` to silence this notice. "
        "Falling back to openai for now."
    )


__all__ = [
    "claude_sdk_enabled",
    "available_provider_choices",
    "gate_notice_for_legacy_config",
]
