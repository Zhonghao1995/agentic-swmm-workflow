"""Provider construction seam (PRD-09).

aiswmm historically hard-coded ``OpenAIProvider(model=...)`` in five
call sites. PRD-09 widens the runtime to a second backend, the Claude
Agent SDK, without bumping the optional-extra surface for users who
stay on OpenAI. This factory is the single seam every caller migrates
onto.

The factory is deliberately import-light: ``claude_agent_sdk`` is only
referenced from inside the ``"claude_sdk"`` branch so that importing
``agentic_swmm.providers.factory`` does not pull the optional extra
into ``sys.modules``. When the extra is missing we raise a
``RuntimeError`` that names the exact install command, instead of
leaking the underlying ``ImportError``.

Adding a new provider here later (Anthropic raw API, Codex CLI, etc.)
is a 5-line addition; the rest of aiswmm only sees the ``ChatProvider``
Protocol from :mod:`agentic_swmm.providers.base`.
"""
from __future__ import annotations

from typing import Any

from agentic_swmm.providers.base import ChatProvider


SUPPORTED_PROVIDERS = ("openai", "claude_sdk")
"""Canonical tuple of provider names this factory accepts.

Single source of truth for the set so the experimental-providers env
gate (and any future caller) can derive its argparse choices from one
tuple instead of restating the literal.
"""


def make_provider(provider_name: str, *, model: str | None = None) -> ChatProvider:
    """Return a :class:`ChatProvider` for the requested backend name.

    Supported names: ``"openai"`` (default, pure-Python urllib path)
    and ``"claude_sdk"`` (requires the ``[claude]`` optional extra).

    Raises:
        ValueError: when ``provider_name`` is not in
            :data:`SUPPORTED_PROVIDERS`.
        RuntimeError: when ``"claude_sdk"`` is requested but the
            optional extra has not been installed. The error message
            includes the exact ``pip install aiswmm[claude]`` command.
    """
    if provider_name == "openai":
        from agentic_swmm.providers.openai_api import OpenAIProvider

        return OpenAIProvider(model=model)  # type: ignore[arg-type]
    if provider_name == "claude_sdk":
        try:
            from agentic_swmm.providers.claude_sdk_api import ClaudeSDKProvider
        except ImportError as exc:  # pragma: no cover - exercised via test patching
            raise RuntimeError(
                "claude_sdk provider requires the optional extra. "
                "Install with: pip install aiswmm[claude]"
            ) from exc
        return ClaudeSDKProvider(model=model)  # type: ignore[arg-type]
    raise ValueError(
        f"unsupported provider: {provider_name!r}. "
        f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
    )


__all__ = ["SUPPORTED_PROVIDERS", "make_provider"]
