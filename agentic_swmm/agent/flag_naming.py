"""Canonical flag-name registration helpers (PRD-08 Phase A.2).

The historical CLI accreted inconsistent flag names: some verbs used
``--inp`` while others used ``--base-inp``; path flags arrived as
``--calibration-store`` or ``--storm-library`` with no shared suffix.
This module centralises the canonical names and exposes ``register_*``
helpers that wire them onto an :class:`argparse.ArgumentParser` while
keeping the legacy names as deprecated aliases for one release.

A deprecated alias works at the argparse level (so existing scripts
keep running) but emits a ``[deprecated]: ...`` line on stderr the
first time the user invokes it. The prefix is grep-friendly so
log scrapers can filter the noise.

The module is intentionally narrow: only the flags shared across
multiple verbs live here. Verb-specific knobs stay in the command
modules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import IO

# ---------------------------------------------------------------------
# Canonical flag-name constants. Re-exported so tests and command
# modules can grep for the one source of truth.
# ---------------------------------------------------------------------

INP_FLAG = "--inp"
BASE_INP_FLAG = "--base-inp"  # deprecated alias of --inp (single release)
JSON_FLAG = "--json"
QUIET_FLAG = "--quiet"
EXAMPLE_FLAG = "--example"
IGNORE_MEMORY_FLAG = "--ignore-memory"


# ---------------------------------------------------------------------
# Deprecation helpers
# ---------------------------------------------------------------------


_DEPRECATED_PREFIX = "[deprecated]:"


def emit_deprecated_alias_warning(
    stream: IO[str] | None = None, *, old: str, new: str
) -> None:
    """Write a single ``[deprecated]:`` line to stderr.

    Tests can pass a captured ``stream`` to assert the wording. The
    prefix is constant so users can grep ``2>&1 | grep deprecated`` to
    surface every alias the runtime is currently absorbing.
    """
    target = stream if stream is not None else sys.stderr
    target.write(
        f"{_DEPRECATED_PREFIX} {old} is renamed to {new}; alias works "
        "through next release\n"
    )


class _DeprecatedAliasAction(argparse.Action):
    """``argparse.Action`` that emits a deprecation warning on use.

    Stores the value under the canonical destination so the rest of
    the command code reads from a single attribute regardless of
    which spelling the user typed. The warning fires exactly once per
    invocation (argparse calls the action once per occurrence).
    """

    def __init__(self, option_strings, dest, *, canonical: str, **kwargs):
        self._canonical_flag = canonical
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values,
        option_string: str | None = None,
    ) -> None:
        # Surface the deprecation before assigning the value so the
        # warning is visible even if a later validator rejects the run.
        emit_deprecated_alias_warning(
            old=option_string or "",
            new=self._canonical_flag,
        )
        setattr(namespace, self.dest, values)


# ---------------------------------------------------------------------
# register_* helpers
# ---------------------------------------------------------------------


def register_inp_flag(
    parser: argparse.ArgumentParser,
    *,
    required: bool = False,
    help_text: str | None = None,
) -> None:
    """Register the canonical ``--inp`` flag with ``--base-inp`` as an alias.

    Both flags resolve to ``args.inp``. ``--base-inp`` emits the
    deprecation warning the moment it is parsed.

    ``required=True`` is enforced after parse via a post-parse hook —
    argparse's own ``required`` parameter would fire even when the
    user passed the deprecated alias, because argparse sees the two
    options as separate arguments. The post-parse hook checks
    ``namespace.inp is None`` and raises ``parser.error`` so the
    misuse message matches argparse's house style.
    """
    help_message = help_text or "Path to the SWMM .inp file."
    parser.add_argument(
        INP_FLAG,
        dest="inp",
        type=Path,
        default=None,
        help=help_message,
    )
    parser.add_argument(
        BASE_INP_FLAG,
        dest="inp",
        type=Path,
        action=_DeprecatedAliasAction,
        canonical=INP_FLAG,
        help=argparse.SUPPRESS,
    )
    if required:
        # Hook the parser's ``parse_known_args`` so we can fail with
        # the standard argparse error message after both --inp and
        # --base-inp have had a chance to populate ``namespace.inp``.
        _attach_required_inp_check(parser)


def _attach_required_inp_check(parser: argparse.ArgumentParser) -> None:
    """Wrap the parser so a missing ``--inp`` raises ``parser.error``.

    We monkey-patch ``parse_known_args`` rather than relying on
    argparse's built-in ``required=True`` flag — that flag would
    misfire even when the deprecated ``--base-inp`` alias has been
    supplied (argparse tracks "seen" by option name, not by dest).
    """
    original = parser.parse_known_args

    def _checked_parse_known_args(args=None, namespace=None):
        ns, remaining = original(args=args, namespace=namespace)
        if getattr(ns, "inp", None) is None:
            parser.error(
                f"the following arguments are required: {INP_FLAG}"
            )
        return ns, remaining

    parser.parse_known_args = _checked_parse_known_args  # type: ignore[assignment]


def register_path_flag(
    parser: argparse.ArgumentParser,
    *,
    noun: str,
    help_text: str,
    default: Path | None = None,
    legacy_aliases: tuple[str, ...] = (),
    dest: str | None = None,
) -> None:
    """Register ``--<noun>-path`` with optional legacy alias flags.

    Examples:
      * ``register_path_flag(p, noun="calibration-memory", ...)``
        produces ``--calibration-memory-path``.
      * Passing ``legacy_aliases=("--calibration-store",)`` keeps the
        old name working while emitting the deprecation warning.

    ``dest`` defaults to ``<noun>_path`` with hyphens swapped for
    underscores so attribute access stays Pythonic.
    """
    canonical = f"--{noun}-path"
    destination = dest if dest is not None else f"{noun.replace('-', '_')}_path"
    parser.add_argument(
        canonical,
        dest=destination,
        type=Path,
        default=default,
        help=help_text,
    )
    for alias in legacy_aliases:
        parser.add_argument(
            alias,
            dest=destination,
            type=Path,
            action=_DeprecatedAliasAction,
            canonical=canonical,
            help=argparse.SUPPRESS,
        )


def register_library_entry_flag(
    parser: argparse.ArgumentParser,
    *,
    noun: str,
    help_text: str,
    legacy_aliases: tuple[str, ...] = ("--from-library",),
    dest: str | None = None,
) -> None:
    """Register ``--<noun>-entry`` for selecting a key inside a library file.

    ``--from-library`` is the historical name and stays as a deprecated
    alias. Callers can override the alias list if a different verb used
    a different legacy flag.
    """
    canonical = f"--{noun}-entry"
    destination = dest if dest is not None else f"{noun.replace('-', '_')}_entry"
    parser.add_argument(
        canonical,
        dest=destination,
        type=str,
        default=None,
        help=help_text,
    )
    for alias in legacy_aliases:
        parser.add_argument(
            alias,
            dest=destination,
            type=str,
            action=_DeprecatedAliasAction,
            canonical=canonical,
            help=argparse.SUPPRESS,
        )


def register_json_flag(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "Produce machine-readable JSON output.",
) -> None:
    """Register ``--json`` as a boolean store-true flag (``args.json``)."""
    parser.add_argument(
        JSON_FLAG,
        dest="json",
        action="store_true",
        help=help_text,
    )


def register_quiet_flag(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = (
        "Suppress chrome; only errors and structured output emitted."
    ),
) -> None:
    """Register ``--quiet`` (``args.quiet``)."""
    parser.add_argument(
        QUIET_FLAG,
        dest="quiet",
        action="store_true",
        help=help_text,
    )


class _PrintExampleAction(argparse.Action):
    """Argparse action that prints the verb's example and exits 0.

    Used by :func:`register_example_flag` so every verb has a uniform
    ``--example`` flag. The action is ``nargs=0`` (boolean-like) — the
    user just types ``aiswmm <verb> --example`` and the example text
    lands on stdout.
    """

    def __init__(self, option_strings, dest, *, example_text: str, **kwargs):
        self._example_text = example_text
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values,
        option_string: str | None = None,
    ) -> None:
        sys.stdout.write(self._example_text.rstrip() + "\n")
        parser.exit(0)


def register_example_flag(
    parser: argparse.ArgumentParser,
    *,
    example_text: str,
) -> None:
    """Register ``--example``. When set, prints ``example_text`` and exits 0.

    ``example_text`` is the copy-pasteable invocation. The helper
    appends one trailing newline so terminal output has a clean
    boundary.
    """
    parser.add_argument(
        EXAMPLE_FLAG,
        action=_PrintExampleAction,
        example_text=example_text,
        help="Print a copy-pasteable example invocation and exit.",
    )


__all__ = [
    "BASE_INP_FLAG",
    "EXAMPLE_FLAG",
    "IGNORE_MEMORY_FLAG",
    "INP_FLAG",
    "JSON_FLAG",
    "QUIET_FLAG",
    "emit_deprecated_alias_warning",
    "register_example_flag",
    "register_inp_flag",
    "register_json_flag",
    "register_library_entry_flag",
    "register_path_flag",
    "register_quiet_flag",
]
