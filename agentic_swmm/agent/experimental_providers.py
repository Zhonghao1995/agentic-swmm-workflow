"""Provider choice + help-text helpers for the ``--provider`` argparse flag.

Historically this module hid the ``claude_sdk`` provider behind the
``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` env gate. The gate has been
removed: ``claude_sdk`` is now the first-class, default provider (the
subscription path via the local ``claude`` CLI). The module is kept as
the single seam the four ``--provider`` argparse sites (``setup`` /
``chat`` / ``model`` / ``agent``) consult so the choice list and help
text stay derived from one source of truth
(:data:`agentic_swmm.providers.factory.SUPPORTED_PROVIDERS`).
"""
from __future__ import annotations

from agentic_swmm.providers import factory


def available_provider_choices() -> list[str]:
    """Return the argparse ``--provider`` choices.

    Derived from :data:`agentic_swmm.providers.factory.SUPPORTED_PROVIDERS`
    so adding a third provider lands in ``factory.SUPPORTED_PROVIDERS``
    only. We read the tuple off the module rather than importing the
    name directly so tests can patch the attribute and observe the
    change here.
    """
    return list(factory.SUPPORTED_PROVIDERS)


def provider_help_text(base: str) -> str:
    """Return the ``--provider`` argparse help string.

    Each command keeps its own role-specific base sentence
    (provider-for-planner, default-provider, etc.); this helper appends
    a stable ``claude_sdk`` hint so a single helper unifies the four
    argparse sites. The hint is always present now that ``claude_sdk``
    is the default subscription path.
    """
    return (
        f"{base} 'claude_sdk' (default) routes through a Claude Pro/Max "
        "subscription via the local `claude` CLI; 'openai' is opt-in."
    )


__all__ = [
    "available_provider_choices",
    "provider_help_text",
]
