"""The one question that decides whether SWMMCanada is reachable.

`fetch_swmm_from_canada` is off for every fresh install, because it reads
``AISWMM_SWMMCANADA_URL`` and nothing sets it. The README advertises the
upstream on its front page, the tool is registered and callable, and a user
who asks for a Canadian network gets a configuration error instead. Nothing in
the product ever offered to turn it on.

It stays opt-in rather than defaulted. Enabling it means the area a user draws
travels to a service over the network, and that is a choice to put in front of
them once, in words, rather than a default they discover afterwards.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from agentic_swmm.agent.tool_handlers.swmm_canada import HOSTED_SERVICE_URL
from agentic_swmm.config import config_dir

ENV_VAR = "AISWMM_SWMMCANADA_URL"


def is_configured(env: dict[str, str] | None = None) -> bool:
    """True when a URL is set anywhere the runtime will find one.

    ``env`` is a test seam: an explicit mapping is consulted alone. The
    default asks the resolver the fetch tool and ``doctor`` use
    (environment, then the env file this module writes), so setup's own
    "already enabled" answer cannot disagree with them (finding F-01).
    """
    if env is not None:
        return bool(str(env.get(ENV_VAR, "")).strip())
    from agentic_swmm.integrations.swmmcanada_runner import resolve_base_url

    return bool(resolve_base_url())


def write_url(url: str) -> Path:
    """Persist the endpoint to ``~/.aiswmm/env``, next to the API keys."""
    from agentic_swmm.commands.login import _write_key_to_env

    return _write_key_to_env(ENV_VAR, url.rstrip("/"))


def offer(
    *,
    ask: Callable[[str], str],
    print_fn: Callable[[str], None] = print,
    env: dict[str, str] | None = None,
    write: Callable[[str], Path] | None = None,
) -> str | None:
    """Ask once. Returns the URL written, or ``None`` when left off.

    Never asks again once the variable is set, wherever it was set from.
    """
    if is_configured(env):
        return None
    print_fn("")
    print_fn("SWMMCanada (optional): real municipal storm networks for 35 Canadian")
    print_fn("cities, fetched straight into a run. aiswmm talks to it over HTTP, so")
    print_fn(f"the area you ask for is sent to {HOSTED_SERVICE_URL}.")
    answer = ask("Enable it? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print_fn(f"Left off. Enable later with: {ENV_VAR}={HOSTED_SERVICE_URL}")
        return None
    writer = write or write_url
    try:
        path = writer(HOSTED_SERVICE_URL)
    except OSError as exc:
        print_fn(f"Could not write the setting ({exc}); export {ENV_VAR} yourself.")
        return None
    os.environ[ENV_VAR] = HOSTED_SERVICE_URL
    print_fn(f"Enabled. Saved to {path}; `aiswmm doctor` reports the service's health.")
    return HOSTED_SERVICE_URL


__all__ = ["ENV_VAR", "is_configured", "offer", "write_url"]
