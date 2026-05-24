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

from agentic_swmm.agent import feature_flags
from agentic_swmm.providers import factory


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"


def claude_sdk_enabled() -> bool:
    """Return True iff the experimental-providers env gate is set.

    Delegates the truthy check to :func:`feature_flags.is_truthy` so
    every ``AISWMM_*`` boolean env var honours the same allowlist
    (``"1"``, ``"true"``, ``"yes"``, ``"on"`` — case-insensitive).
    Anything else — including unset, ``""``, ``"0"``, ``"false"``,
    ``"no"``, ``"off"`` — returns False. An explicit allowlist rather
    than a generic non-empty check means a stray export of ``=0``
    does not accidentally flip the gate ON.
    """
    return feature_flags.is_truthy(os.environ.get(_ENV_VAR))


def available_provider_choices() -> list[str]:
    """Return the argparse ``--provider`` choices for the current gate state.

    Derived from :data:`agentic_swmm.providers.factory.SUPPORTED_PROVIDERS`
    so the gate stays a filter over a single source of truth — adding
    a third provider lands in ``factory.SUPPORTED_PROVIDERS`` only.

    Gate OFF filters ``claude_sdk`` out; gate ON returns the full
    tuple. The order matches ``SUPPORTED_PROVIDERS`` so help text
    rendering is deterministic. We read the tuple off the module
    rather than importing the name directly so tests can patch the
    attribute and observe the change here.
    """
    supported = factory.SUPPORTED_PROVIDERS
    if claude_sdk_enabled():
        return list(supported)
    return [name for name in supported if name != "claude_sdk"]


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
