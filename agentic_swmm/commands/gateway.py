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
``login``    install if needed, run the vendor OAuth flow (opens a browser),
             then leave the gateway serving. One command, start to finish.
``start``    serve on :8317, detached by default (``--foreground`` to attach).
``stop``     stop a gateway started in the background.
``status``   where the binary is, whether it is listening.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
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


def is_healthy(timeout: float = 3.0) -> bool:
    """True when the gateway answers ``/v1/models`` with a 2xx.

    Listening is not the same as serving. With the example config's
    placeholder api-keys the gateway binds the port and then 403s every
    request, and a port probe calls that "ready" forever: the repair was
    gated behind "nothing is listening", so a running-but-refusing gateway
    was the one state that could never heal itself.
    """
    try:
        with urlopen(  # noqa: S310 - fixed loopback URL
            f"http://127.0.0.1:{GATEWAY_PORT}/v1/models", timeout=timeout
        ) as response:
            return 200 <= response.status < 300
    except Exception:
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


# The shipped example is not a runnable config. Its api-keys are three
# placeholders, and the gateway refuses every proxy request while they are
# there ("unsafe_example_api_key", HTTP 403). Its host is "", which binds
# every interface. Seeding it verbatim produced a gateway that was both
# refusing to work and, had it worked, reachable from the LAN.
_TEMPLATE_KEY_MARKER = "your-api-key-1"


def harden_config(text: str) -> str:
    """Make the shipped example config safe and actually serving.

    ``host`` is pinned to loopback because that is the only address aiswmm
    ever talks to, and ``api-keys`` is emptied so a local, loopback-only
    gateway needs no inbound token. Line-based on purpose: the example is
    37 KB of documented defaults and round-tripping it through a YAML
    parser would throw every comment away.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        top_level = line[:1] not in (" ", "\t")
        stripped = line.strip()
        if top_level and stripped.startswith("host:"):
            out.append('host: "127.0.0.1"\n')
            index += 1
            continue
        if top_level and stripped == "api-keys:":
            out.append("api-keys: []\n")
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith("- "):
                index += 1
            continue
        out.append(line)
        index += 1
    return "".join(out)


def ensure_serving_config() -> bool:
    """Repair a config still carrying the example's placeholder keys.

    Returns True when the file was rewritten. Only touches a config that
    still contains the template marker, so an edited config is never
    clobbered.
    """
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _TEMPLATE_KEY_MARKER not in text:
        return False
    path.write_text(harden_config(text), encoding="utf-8")
    return True


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
        config_path().write_text(
            harden_config(example.read_text(encoding="utf-8")), encoding="utf-8"
        )
    else:
        ensure_serving_config()

    return InstallResult(path=binary, version=GATEWAY_VERSION, verified=verified, skipped=False)


def pid_path() -> Path:
    return gateway_dir() / "gateway.pid"


def log_path() -> Path:
    return gateway_dir() / "gateway.log"


def _command(extra_args: list[str]) -> list[str]:
    binary = binary_path()
    if not binary.exists():
        raise GatewayError("Gateway is not installed. Run: aiswmm gateway install")
    return [str(binary), "-config", str(config_path()), *extra_args]


def _run_binary(extra_args: list[str]) -> int:
    return subprocess.call(_command(extra_args))


def _repair_config_and_note() -> None:
    if ensure_serving_config():
        print("Repaired the gateway config (placeholder api-keys removed, bound to loopback).")


def start_background(*, wait_s: float = 20.0) -> bool:
    """Serve detached and return True once the gateway actually answers.

    Foreground-only was the wrong default for the one command a user is
    told to run: it pins a terminal window open for the rest of the
    session, and closing that window silently takes the provider down.

    The config repair runs first, unconditionally. A gateway that is up but
    refusing (safe mode) is stopped and relaunched so the repaired config is
    the one in memory; one we did not start has no pidfile, and that is
    reported rather than guessed at.
    """
    _repair_config_and_note()
    if is_listening():
        if is_healthy():
            return True
        print("Gateway is listening but refusing requests; restarting it with the repaired config.")
        if not stop_background():
            print(
                "Could not stop it: no pidfile, so this gateway was started outside aiswmm. "
                "Stop it yourself, then run `aiswmm gateway start`.",
                file=sys.stderr,
            )
            return False
    cmd = _command([])
    gateway_dir().mkdir(parents=True, exist_ok=True)
    log = open(log_path(), "ab")
    kwargs: dict = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: survives the shell
        # that launched it, and Ctrl-C in that shell does not kill it.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - path we unpacked ourselves
    pid_path().write_text(str(process.pid), encoding="utf-8")

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_listening() and is_healthy():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.4)
    return is_listening() and is_healthy()


def stop_background() -> bool:
    """Stop a gateway started by :func:`start_background`."""
    try:
        pid = int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    pid_path().unlink(missing_ok=True)
    return True


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
    sub.add_parser("login", help="Sign in to ChatGPT (opens a browser) and leave the gateway serving.")
    start = sub.add_parser("start", help=f"Serve on :{GATEWAY_PORT} (detached by default).")
    start.add_argument("--foreground", action="store_true", help="Attach to this terminal instead.")
    sub.add_parser("stop", help="Stop a gateway started in the background.")
    sub.add_parser("restart", help="Stop and start again (use after a config repair).")
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
            return _cmd_start(foreground=getattr(args, "foreground", False))
        if action == "stop":
            return _cmd_stop()
        if action == "restart":
            stop_background()
            return _cmd_start(foreground=False)
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
    """Install if needed, sign in, and leave the gateway serving.

    This is the command the setup wizard and the installer both point at,
    so it has to finish the job rather than hand back another instruction.
    """
    if not is_installed():
        install_gateway(force=False)
    print("Opening your browser to sign in to ChatGPT. Finish it there, then come back.")
    status = _run_binary(["-codex-login"])
    if status != 0:
        print("Sign-in did not complete. Retry with: aiswmm gateway login", file=sys.stderr)
        return status
    print("Signed in. Starting the gateway...")
    if not start_background():
        print(f"Gateway did not come up; see {log_path()}", file=sys.stderr)
        return 1
    print(f"Gateway is serving on http://127.0.0.1:{GATEWAY_PORT}. Run: aiswmm")
    return 0


def _cmd_start(*, foreground: bool = False) -> int:
    if foreground:
        print(f"Serving on http://127.0.0.1:{GATEWAY_PORT}. Ctrl-C stops it.")
        return _run_binary([])
    if start_background():
        print(f"Gateway is serving on http://127.0.0.1:{GATEWAY_PORT} (detached).")
        print(f"Stop it with: aiswmm gateway stop    Log: {log_path()}")
        return 0
    print(f"Gateway did not come up; see {log_path()}", file=sys.stderr)
    return 1


def _cmd_stop() -> int:
    if stop_background():
        print("Gateway stopped.")
        return 0
    print("No background gateway to stop (or it was already gone).")
    return 0


def _cmd_status() -> int:
    if not is_installed():
        print("Gateway:   not installed (aiswmm gateway install)")
        return 0
    print(f"Gateway:   {binary_path()} (CLIProxyAPI {GATEWAY_VERSION})")
    print(f"Config:    {config_path()}")
    if not is_listening():
        print(f"Port {GATEWAY_PORT}: not listening (aiswmm gateway login)")
    elif is_healthy():
        print(f"Port {GATEWAY_PORT}: serving")
    else:
        print(f"Port {GATEWAY_PORT}: listening but refusing requests (aiswmm gateway restart)")
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
    "ensure_serving_config",
    "harden_config",
    "install_gateway",
    "is_installed",
    "is_healthy",
    "is_listening",
    "start_background",
    "stop_background",
    "main",
    "register",
]
