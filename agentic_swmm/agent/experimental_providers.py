"""Provider choice + help-text helpers for the ``--provider`` argparse flag.

This module is the single seam the three ``--provider`` argparse sites
(``setup`` / ``agent`` / ``model``; the ``chat`` alias reuses ``agent``'s)
consult so the choice list and help text stay derived from one source of
truth (:data:`agentic_swmm.providers.factory.SUPPORTED_PROVIDERS`, itself
derived from the ADR-0008 route table). Adding a route lands in that
table only; every argparse site updates automatically.
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
    a stable hint naming the two API-key backends so a single helper
    unifies the four argparse sites.
    """
    return (
        f"{base} 'openai' (default) uses the OpenAI Responses API; "
        "'anthropic' uses the Anthropic Messages API; the other routes "
        "(codex, openrouter, deepseek, groq, gemini, ollama, lmstudio, "
        "custom) use OpenAI-compatible chat/completions endpoints. "
        "Keyed routes read their API key from the environment; ollama "
        "and lmstudio are local and keyless. Run `aiswmm setup` for the "
        "interactive picker."
    )


__all__ = [
    "available_provider_choices",
    "provider_help_text",
]
