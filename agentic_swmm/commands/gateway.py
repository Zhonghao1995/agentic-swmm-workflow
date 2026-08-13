"""``aiswmm gateway`` — install and drive the local ChatGPT-plan gateway.

The ``codex`` route (ADR-0008) is keyless: it speaks the OpenAI-compatible
protocol to a gateway running on this machine, and the *gateway* owns the
vendor OAuth login and quota. aiswmm stays a plain HTTP client and never sees
a ChatGPT credential.

That split is right, and it used to leave the user stranded. ``aiswmm setup``
would offer the route, detect nothing on :8317, print a ``brew install`` line
that does not exist on Windows, and stop. Anyone who wanted the no-API-key
path had to find a gateway project, pick the right build for their machine,
and wire it up before aiswmm became useful.

This command closes that gap. It fetches a pinned CLIProxyAPI release
(MIT, https://github.com/router-for-me/CLIProxyAPI) for the running
OS/architecture, verifies it against the release's own ``checksums.txt``, and
unpacks the single binary into ``<config-dir>/gateway/``. Nothing is
downloaded unless the user asks for it, so the API-key routes carry no cost.

Subcommands
-----------
``install``  fetch + verify + unpack (idempotent; ``--force`` re-fetches).
``login``    run the gateway's OAuth flow (opens a browser), installing first
             if needed, so the whole path is one command.
``start``    run the gateway in the foreground on :8317.
``status``   where the binary is, whether it is listening.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from agentic_swmm.agent.flag_naming import register_example_flag
from agentic_swmm.config import config_dir

# Pinned like the SWMM solver: a reproducible install beats "whatever is
# latest today". Bumping this is a deliberate, reviewable edit.
GATEWAY_VERSION = "7.2.130"
GATEWAY_REPO = "router-for-me/CLIProxyAPI"
GATEWAY_PORT = 8317

_RELEASE_BASE = f"https://github.com/{GATEWAY_REPO}/releases/download/v{GATEWAY_VERSION}"
_DOWNLOAD_TIMEOUT_S = 120


class GatewayError(RuntimeError):
    """Raised when the gateway cannot be installed or started."""


def asset_name(system: str, machine: str, version: str = GATEWAY_VERSION) -> str:
    """Return the release asset for ``system``/``machine``.

    Pure so the platform matrix is testable without a network. Raises
    :class:`GatewayError` for a platform the project does not publish,
    rather than downloading a 404 body and failing at unpack time.
    """
    os_part = {"windows": "windows", "darwin": "darwin", "linux": "linux"}.get(system.lower())
    arch_part = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine.lower())
    if os_part is None or arch_part is None:
        raise GatewayError(
            f"No published gateway build for {system}/{machine}. "
            f"See https://github.com/{GATEWAY_REPO}/releases"
        )
    ext = "zip" if os_part == "windows" else "tar.gz"
    return f"CLIProxyAPI_{version}_{os_part}_{arch_part}.{ext}"


def detect_machine() -> str:
    """The machine's architecture, not the running process's.

    Windows on ARM runs x64 processes under emulation, and such a process
    reports ``PROCESSOR_ARCHITECTURE=AMD64`` through
    :func:`platform.machine`. The true machine is in
    ``PROCESSOR_ARCHITEW6432``, which Windows sets only in that emulated
    case. Trusting the process value would hand an ARM64 laptop the amd64
    gateway, which then runs through the same emulation layer instead of
    natively. The whole point of this command is that the user never has
    to know which of the two they have.
    """
    if platform.system().lower() == "windows":
        native = os.environ.get("PROCESSOR_ARCHITEW6432", "").strip()
        if native:
            return native
    return platform.machine()


def binary_name(system: str | None = None) -> str:
    """Name of the executable inside the archive."""
    system = system or platform.system()
    return "cli-proxy-api.exe" if system.lower() == "windows" else "cli-proxy-api"


def gateway_dir() -> Path:
    """``<config-dir>/gateway`` — sibling of the pinned ``swmm/`` solver."""
    return config_dir() / "gateway"


def binary_path() -> Path:
    return gateway_dir() / binary_name()


def config_path() -> Path:
    return gateway_dir() / "config.yaml"


def is_installed() -> bool:
    return binary_path().exists()


def is_listening(port: int = GATEWAY_PORT, timeout: float = 0.8) -> bool:
    """True when something answers on the gateway port."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _fetch(url: str) -> bytes:
    with urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310 - pinned https release URL
        return response.read()


def _expected_sha256(asset: str, fetch=_fetch) -> str | None:
    """SHA-256 for ``asset`` from the release's ``checksums.txt``.

    ``None`` when the file is unreachable or does not list the asset: a
    missing checksum downgrades to "unverified", it does not block the
    install, and the caller reports which happened.
    """
    try:
        body = fetch(f"{_RELEASE_BASE}/checksums.txt").decode("utf-8", "replace")
    except Exception:
        return None
    for line in body.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            return parts[0].lower()
    return None


def _extract(archive: bytes, asset: str, target: Path, wanted: str) -> None:
    """Unpack ``wanted`` (and config.example.yaml) out of the archive."""
    target.mkdir(parents=True, exist_ok=True)
    extras = {"config.example.yaml"}
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            members = {Path(n).name: n for n in zf.namelist()}
            if wanted not in members:
                raise GatewayError(f"{wanted} not found in {asset}")
            for name in {wanted, *extras} & members.keys():
                with zf.open(members[name]) as src, open(target / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            members = {Path(m.name).name: m for m in tf.getmembers() if m.isfile()}
            if wanted not in members:
                raise GatewayError(f"{wanted} not found in {asset}")
            for name in {wanted, *extras} & members.keys():
                src = tf.extractfile(members[name])
                if src is None:
                    continue
                with src, open(target / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)


@dataclass(frozen=True)
class InstallResult:
    path: Path
    version: str
    verified: bool
    skipped: bool  # already present and not forced


def install_gateway(*, force: bool = False, fetch=_fetch) -> InstallResult:
    """Download, verify, and unpack the pinned gateway. Idempotent."""
    target = gateway_dir()
    wanted = binary_name()
    binary = target / wanted
    if binary.exists() and not force:
        return InstallResult(path=binary, version=GATEWAY_VERSION, verified=True, skipped=True)

    asset = asset_name(platform.system(), detect_machine())
    expected = _expected_sha256(asset, fetch=fetch)
    try:
        payload = fetch(f"{_RELEASE_BASE}/{asset}")
    except Exception as exc:  # pragma: no cover - network shape varies
        raise GatewayError(f"Could not download {asset}: {exc}") from exc

    verified = False
    if expected is not None:
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise GatewayError(
                f"Checksum mismatch for {asset}: expected {expected}, got {actual}. "
                "Nothing was written."
            )
        verified = True

    _extract(payload, asset, target, wanted)
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | 0o111)

    # The release ships config.example.yaml, not config.yaml, and the binary
    # needs a config path. Seed one on first install; never clobber an edited
    # config on --force.
    example = target / "config.example.yaml"
    if example.exists() and not config_path().exists():
        shutil.copyfile(example, config_path())

    return InstallResult(path=binary, version=GATEWAY_VERSION, verified=verified, skipped=False)


def _run_binary(extra_args: list[str]) -> int:
    binary = binary_path()
    if not binary.exists():
        raise GatewayError("Gateway is not installed. Run: aiswmm gateway install")
    cmd = [str(binary), "-config", str(config_path()), *extra_args]
    return subprocess.call(cmd)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "gateway",
        help="Install and run the local gateway for the keyless 'codex' route.",
    )
    sub = parser.add_subparsers(dest="gateway_action")
    install = sub.add_parser("install", help="Download the pinned gateway for this machine.")
    install.add_argument("--force", action="store_true", help="Re-download even if present.")
    sub.add_parser("login", help="Run the gateway's ChatGPT OAuth flow (opens a browser).")
    sub.add_parser("start", help=f"Run the gateway in the foreground on :{GATEWAY_PORT}.")
    sub.add_parser("status", help="Show where the gateway is and whether it is listening.")
    register_example_flag(parser, example_text="aiswmm gateway install")
    parser.set_defaults(func=main)


def main(args: argparse.Namespace) -> int:
    action = getattr(args, "gateway_action", None) or "status"
    try:
        if action == "install":
            return _cmd_install(force=getattr(args, "force", False))
        if action == "login":
            return _cmd_login()
        if action == "start":
            return _cmd_start()
        return _cmd_status()
    except GatewayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_install(*, force: bool) -> int:
    result = install_gateway(force=force)
    if result.skipped:
        print(f"Gateway already installed at {result.path}")
    else:
        checked = "checksum verified" if result.verified else "checksum unavailable, not verified"
        print(f"Installed CLIProxyAPI {result.version} -> {result.path} ({checked})")
    print("Next: aiswmm gateway login    (opens a browser to sign in to ChatGPT)")
    return 0


def _cmd_login() -> int:
    if not is_installed():
        _cmd_install(force=False)
    print("Opening the ChatGPT sign-in flow. Finish it in the browser, then:")
    print("  aiswmm gateway start")
    return _run_binary(["-codex-login"])


def _cmd_start() -> int:
    print(f"Serving on http://127.0.0.1:{GATEWAY_PORT}. Leave this window open; Ctrl-C stops it.")
    return _run_binary([])


def _cmd_status() -> int:
    if not is_installed():
        print("Gateway:   not installed (aiswmm gateway install)")
        return 0
    print(f"Gateway:   {binary_path()} (CLIProxyAPI {GATEWAY_VERSION})")
    print(f"Config:    {config_path()}")
    listening = is_listening()
    print(f"Port {GATEWAY_PORT}: {'listening' if listening else 'not listening (aiswmm gateway start)'}")
    return 0


__all__ = [
    "GATEWAY_PORT",
    "GATEWAY_VERSION",
    "GatewayError",
    "InstallResult",
    "asset_name",
    "binary_name",
    "binary_path",
    "detect_machine",
    "gateway_dir",
    "install_gateway",
    "is_installed",
    "is_listening",
    "main",
    "register",
]
