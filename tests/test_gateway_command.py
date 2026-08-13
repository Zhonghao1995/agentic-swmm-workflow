"""``aiswmm gateway`` — the managed local gateway for the keyless codex route.

The codex route was reachable in theory and unreachable in practice: the
wizard offered it, detected nothing on :8317, and printed a Homebrew line
that does not exist on Windows. These tests pin the parts that made it a
dead end.

Nothing here touches the network. ``install_gateway`` takes an injected
fetch, and the archives are built in memory.
"""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.commands import gateway


BINARY_BODY = b"#!/bin/sh\necho gateway\n"
# Shaped like the real config.example.yaml: an all-interfaces host, three
# placeholder api-keys, and comments that must survive the rewrite.
CONFIG_BODY = b"""# Server host/interface to bind to. Default is empty ("") to bind all interfaces.
host: ""

# Server port
port: 8317

# API keys for authentication
api-keys:
  - "your-api-key-1"
  - "your-api-key-2"
  - "your-api-key-3"

# Enable debug logging
debug: false

pprof:
  host: "127.0.0.1:6060"
"""


def _tar_bytes(binary_name: str = "cli-proxy-api") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in ((binary_name, BINARY_BODY), ("config.example.yaml", CONFIG_BODY)):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _zip_bytes(binary_name: str = "cli-proxy-api.exe") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(binary_name, BINARY_BODY)
        zf.writestr("config.example.yaml", CONFIG_BODY)
    return buf.getvalue()


def _fetcher(payload: bytes, *, checksum: bytes | None = None):
    """Serve checksums.txt and the asset from memory."""

    def fetch(url: str) -> bytes:
        if url.endswith("checksums.txt"):
            if checksum is None:
                raise OSError("no checksums published")
            return checksum
        return payload

    return fetch


class AssetNameTests(unittest.TestCase):
    def test_matrix_matches_published_release_names(self) -> None:
        # These four strings are real assets on the pinned release. A typo
        # here downloads a 404 body and fails at unpack time instead.
        self.assertEqual(
            gateway.asset_name("Windows", "ARM64", "7.2.130"),
            "CLIProxyAPI_7.2.130_windows_aarch64.zip",
        )
        self.assertEqual(
            gateway.asset_name("Windows", "AMD64", "7.2.130"),
            "CLIProxyAPI_7.2.130_windows_amd64.zip",
        )
        self.assertEqual(
            gateway.asset_name("Darwin", "arm64", "7.2.130"),
            "CLIProxyAPI_7.2.130_darwin_aarch64.tar.gz",
        )
        self.assertEqual(
            gateway.asset_name("Linux", "x86_64", "7.2.130"),
            "CLIProxyAPI_7.2.130_linux_amd64.tar.gz",
        )

    def test_unpublished_platform_is_a_loud_error(self) -> None:
        with self.assertRaises(gateway.GatewayError):
            gateway.asset_name("Plan9", "riscv64")

    def test_windows_on_arm_is_detected_through_emulation(self) -> None:
        # An x64 Python on an ARM64 Windows box reports AMD64. Windows puts
        # the real machine in PROCESSOR_ARCHITEW6432 in exactly that case,
        # and picking the amd64 build there would run the gateway through
        # the emulation layer instead of natively.
        with mock.patch.object(gateway.platform, "system", return_value="Windows"), \
             mock.patch.object(gateway.platform, "machine", return_value="AMD64"), \
             mock.patch.dict(os.environ, {"PROCESSOR_ARCHITEW6432": "ARM64"}):
            self.assertEqual(gateway.detect_machine(), "ARM64")
            self.assertEqual(
                gateway.asset_name("Windows", gateway.detect_machine(), "7.2.130"),
                "CLIProxyAPI_7.2.130_windows_aarch64.zip",
            )

    def test_native_windows_arch_is_used_when_not_emulated(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PROCESSOR_ARCHITEW6432"}
        with mock.patch.object(gateway.platform, "system", return_value="Windows"), \
             mock.patch.object(gateway.platform, "machine", return_value="ARM64"), \
             mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(gateway.detect_machine(), "ARM64")

    def test_non_windows_ignores_the_wow64_variable(self) -> None:
        with mock.patch.object(gateway.platform, "system", return_value="Darwin"), \
             mock.patch.object(gateway.platform, "machine", return_value="arm64"), \
             mock.patch.dict(os.environ, {"PROCESSOR_ARCHITEW6432": "AMD64"}):
            self.assertEqual(gateway.detect_machine(), "arm64")

    def test_binary_name_is_exe_on_windows_only(self) -> None:
        self.assertEqual(gateway.binary_name("Windows"), "cli-proxy-api.exe")
        self.assertEqual(gateway.binary_name("Linux"), "cli-proxy-api")


class HardenConfigTests(unittest.TestCase):
    """The shipped example is not a runnable config.

    CLIProxyAPI refuses every proxy request while api-keys holds the
    template values ("unsafe_example_api_key", HTTP 403), and its default
    host binds every interface. Seeding it verbatim produced a gateway that
    both refused to work and would have been LAN-reachable if it had.
    """

    def test_placeholder_keys_are_removed(self) -> None:
        out = gateway.harden_config(CONFIG_BODY.decode())
        self.assertIn("api-keys: []", out)
        self.assertNotIn("your-api-key-1", out)

    def test_host_is_pinned_to_loopback(self) -> None:
        out = gateway.harden_config(CONFIG_BODY.decode())
        self.assertIn('host: "127.0.0.1"', out)

    def test_nested_host_keys_are_left_alone(self) -> None:
        # pprof has its own indented host:; only the top-level one is ours.
        out = gateway.harden_config(CONFIG_BODY.decode())
        self.assertIn('  host: "127.0.0.1:6060"', out)

    def test_comments_survive(self) -> None:
        out = gateway.harden_config(CONFIG_BODY.decode())
        self.assertIn("# Server port", out)
        self.assertIn("# Enable debug logging", out)

    def test_idempotent(self) -> None:
        once = gateway.harden_config(CONFIG_BODY.decode())
        self.assertEqual(gateway.harden_config(once), once)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"AISWMM_CONFIG_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _checksum_line(self, payload: bytes, asset: str) -> bytes:
        return f"{hashlib.sha256(payload).hexdigest()}  {asset}\n".encode()

    def test_unpacks_the_binary_and_seeds_a_config(self) -> None:
        payload = _tar_bytes()
        asset = gateway.asset_name("Linux", "x86_64")
        with mock.patch.object(gateway.platform, "system", return_value="Linux"), \
             mock.patch.object(gateway.platform, "machine", return_value="x86_64"):
            result = gateway.install_gateway(
                fetch=_fetcher(payload, checksum=self._checksum_line(payload, asset))
            )
        self.assertTrue(result.verified)
        self.assertFalse(result.skipped)
        self.assertTrue(result.path.exists())
        self.assertEqual(result.path.read_bytes(), BINARY_BODY)
        # The release ships config.example.yaml only; the binary needs a real
        # config path, so first install seeds one, hardened.
        seeded = gateway.config_path().read_text(encoding="utf-8")
        self.assertIn("api-keys: []", seeded)
        self.assertIn('host: "127.0.0.1"', seeded)
        self.assertNotIn("your-api-key-1", seeded)
        self.assertTrue(os.access(result.path, os.X_OK))

    def test_windows_zip_path(self) -> None:
        payload = _zip_bytes()
        asset = gateway.asset_name("Windows", "ARM64")
        with mock.patch.object(gateway.platform, "system", return_value="Windows"), \
             mock.patch.object(gateway.platform, "machine", return_value="ARM64"):
            result = gateway.install_gateway(
                fetch=_fetcher(payload, checksum=self._checksum_line(payload, asset))
            )
        self.assertEqual(result.path.name, "cli-proxy-api.exe")
        self.assertEqual(result.path.read_bytes(), BINARY_BODY)

    def test_checksum_mismatch_writes_nothing(self) -> None:
        payload = _tar_bytes()
        asset = gateway.asset_name("Linux", "x86_64")
        bad = f"{'0' * 64}  {asset}\n".encode()
        with mock.patch.object(gateway.platform, "system", return_value="Linux"), \
             mock.patch.object(gateway.platform, "machine", return_value="x86_64"):
            with self.assertRaises(gateway.GatewayError):
                gateway.install_gateway(fetch=_fetcher(payload, checksum=bad))
        self.assertFalse(gateway.binary_path().exists())

    def test_missing_checksums_installs_but_reports_unverified(self) -> None:
        # A release without checksums.txt should not block the install, but
        # the caller has to be able to say so out loud.
        payload = _tar_bytes()
        with mock.patch.object(gateway.platform, "system", return_value="Linux"), \
             mock.patch.object(gateway.platform, "machine", return_value="x86_64"):
            result = gateway.install_gateway(fetch=_fetcher(payload, checksum=None))
        self.assertFalse(result.verified)
        self.assertTrue(result.path.exists())

    def test_second_install_is_a_no_op(self) -> None:
        payload = _tar_bytes()
        asset = gateway.asset_name("Linux", "x86_64")
        checksum = self._checksum_line(payload, asset)
        with mock.patch.object(gateway.platform, "system", return_value="Linux"), \
             mock.patch.object(gateway.platform, "machine", return_value="x86_64"):
            gateway.install_gateway(fetch=_fetcher(payload, checksum=checksum))
            calls: list[str] = []

            def counting_fetch(url: str) -> bytes:
                calls.append(url)
                return _fetcher(payload, checksum=checksum)(url)

            again = gateway.install_gateway(fetch=counting_fetch)
        self.assertTrue(again.skipped)
        self.assertEqual(calls, [], "an installed gateway must not be re-downloaded")

    def test_an_existing_template_config_is_repaired(self) -> None:
        # Anyone who installed before the hardening landed has a config that
        # makes the gateway answer 403 to everything.
        gateway.gateway_dir().mkdir(parents=True, exist_ok=True)
        gateway.config_path().write_text(CONFIG_BODY.decode(), encoding="utf-8")
        self.assertTrue(gateway.ensure_serving_config())
        self.assertNotIn("your-api-key-1", gateway.config_path().read_text(encoding="utf-8"))

    def test_an_edited_config_is_never_clobbered(self) -> None:
        gateway.gateway_dir().mkdir(parents=True, exist_ok=True)
        mine = 'host: "0.0.0.0"\napi-keys:\n  - "a-real-key"\n'
        gateway.config_path().write_text(mine, encoding="utf-8")
        self.assertFalse(gateway.ensure_serving_config())
        self.assertEqual(gateway.config_path().read_text(encoding="utf-8"), mine)

    def test_status_runs_without_an_install(self) -> None:
        self.assertFalse(gateway.is_installed())
        args = mock.Mock(gateway_action="status")
        self.assertEqual(gateway.main(args), 0)

    def test_login_without_install_reports_instead_of_crashing(self) -> None:
        # Install is attempted first; with no network that surfaces as a
        # GatewayError turned into a non-zero exit, not a traceback.
        with mock.patch.object(gateway, "install_gateway", side_effect=gateway.GatewayError("no net")):
            args = mock.Mock(gateway_action="login")
            self.assertEqual(gateway.main(args), 1)


class HealthTests(unittest.TestCase):
    """Listening is not serving.

    The safe-mode gateway binds :8317 and 403s everything. A port probe
    called that "ready", so the one broken state the tool could produce was
    also the one state it could never repair.
    """

    def test_a_2xx_on_v1_models_is_healthy(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.__enter__ = lambda self_: response
        response.__exit__ = lambda *a: False
        with mock.patch.object(gateway, "urlopen", return_value=response):
            self.assertTrue(gateway.is_healthy())

    def test_a_refusing_gateway_is_not_healthy(self) -> None:
        with mock.patch.object(gateway, "urlopen", side_effect=OSError("403")):
            self.assertFalse(gateway.is_healthy())


class RestartWhenRefusingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"AISWMM_CONFIG_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        gateway.gateway_dir().mkdir(parents=True, exist_ok=True)
        gateway.binary_path().write_bytes(BINARY_BODY)
        gateway.config_path().write_text(CONFIG_BODY.decode(), encoding="utf-8")

    def test_a_listening_but_refusing_gateway_is_stopped_and_relaunched(self) -> None:
        stopped: list[bool] = []
        with mock.patch.object(gateway, "is_listening", return_value=True), \
             mock.patch.object(gateway, "is_healthy", side_effect=[False, True]), \
             mock.patch.object(gateway, "stop_background", lambda: stopped.append(True) or True), \
             mock.patch.object(gateway.subprocess, "Popen") as popen:
            popen.return_value.pid = 4242
            popen.return_value.poll.return_value = None
            self.assertTrue(gateway.start_background(wait_s=1))
        self.assertEqual(stopped, [True], "the broken gateway was left running")
        popen.assert_called_once()
        # The relaunch must use a config that no longer trips safe mode.
        self.assertNotIn("your-api-key-1", gateway.config_path().read_text(encoding="utf-8"))

    def test_a_gateway_we_did_not_start_is_reported_not_guessed_at(self) -> None:
        with mock.patch.object(gateway, "is_listening", return_value=True), \
             mock.patch.object(gateway, "is_healthy", return_value=False), \
             mock.patch.object(gateway, "stop_background", return_value=False), \
             mock.patch.object(gateway.subprocess, "Popen") as popen:
            self.assertFalse(gateway.start_background(wait_s=1))
        popen.assert_not_called()


class SafeModeHintTests(unittest.TestCase):
    """The generic 401/403 advice was wrong for this failure, and looping."""

    def _error(self, body: str):
        import urllib.error

        return urllib.error.HTTPError(
            "http://localhost:8317/v1/chat/completions", 403, "Forbidden", {}, io.BytesIO(body.encode())
        )

    def test_safe_mode_403_points_at_the_gateway_not_a_key(self) -> None:
        from agentic_swmm.providers import _http

        body = '{"error":"unsafe_example_api_key","message":"Proxy API endpoints are disabled"}'
        with self.assertRaises(_http.ProviderHTTPError) as caught:
            _http.post_json_with_retry(
                mock.Mock(),
                timeout=1,
                provider_label="Local gateway",
                auth_hint=" — your AISWMM_CODEX_API_KEY is missing",
                max_attempts=1,
                opener=mock.Mock(side_effect=self._error(body)),
                sleep=lambda _s: None,
            )
        message = str(caught.exception)
        self.assertIn("aiswmm gateway restart", message)
        self.assertNotIn("AISWMM_CODEX_API_KEY is missing", message)

    def test_an_ordinary_403_keeps_the_key_advice(self) -> None:
        from agentic_swmm.providers import _http

        with self.assertRaises(_http.ProviderHTTPError) as caught:
            _http.post_json_with_retry(
                mock.Mock(),
                timeout=1,
                provider_label="OpenAI",
                auth_hint=" — check your OPENAI_API_KEY",
                max_attempts=1,
                opener=mock.Mock(side_effect=self._error('{"error":"invalid_api_key"}')),
                sleep=lambda _s: None,
            )
        self.assertIn("check your OPENAI_API_KEY", str(caught.exception))


class WizardOffersTheGatewayTests(unittest.TestCase):
    """The wizard must offer the install, not print a recipe and stop."""

    def _run(self, answers: list[str], installer, login=None):
        from agentic_swmm.commands.setup_wizard import run_wizard

        printed: list[str] = []
        pending = list(answers)
        return (
            run_wizard(
                ask=lambda _prompt: pending.pop(0),
                ask_secret=lambda _prompt: "",
                print_fn=printed.append,
                probe=lambda *a, **k: None,  # nothing detected anywhere
                install_gateway=installer,
                gateway_login=login or (lambda: 0),
            ),
            printed,
        )

    def test_choosing_codex_offers_the_managed_install(self) -> None:
        installed = []

        def installer():
            installed.append(True)
            return gateway.InstallResult(
                path=Path("/tmp/cli-proxy-api"), version="7.2.130", verified=True, skipped=False
            )

        signed_in = []
        # route=codex, install=yes, sign in=yes, model=default, key=skip
        result, printed = self._run(
            ["codex", "y", "y", "", ""], installer, login=lambda: signed_in.append(True) or 0
        )
        self.assertEqual(installed, [True], "the wizard never called the installer")
        self.assertEqual(signed_in, [True], "the wizard installed but never signed in")
        self.assertIsNotNone(result)
        self.assertEqual(result.route, "codex")

    def test_declining_the_sign_in_leaves_the_one_command_behind(self) -> None:
        def installer():
            return gateway.InstallResult(
                path=Path("/tmp/cli-proxy-api"), version="7.2.130", verified=True, skipped=False
            )

        def login():  # pragma: no cover - must not be called
            raise AssertionError("declined sign-in still ran")

        result, printed = self._run(["codex", "y", "n", "", ""], installer, login=login)
        self.assertIsNotNone(result)
        self.assertIn("aiswmm gateway login", "\n".join(printed))

    def test_a_caller_that_omits_the_callables_gets_the_recipe_not_a_download(self) -> None:
        # run_wizard must never reach for the real installer or the real
        # browser flow by omission: those download 58 MB and write vendor
        # credentials. Omitting them falls back to the printed recipe.
        from agentic_swmm.commands.setup_wizard import run_wizard

        printed: list[str] = []
        pending = ["codex", "", ""]
        result = run_wizard(
            ask=lambda _p: pending.pop(0),
            ask_secret=lambda _p: "",
            print_fn=printed.append,
            probe=lambda *a, **k: None,
        )
        self.assertIsNotNone(result)
        self.assertIn("aiswmm gateway install", "\n".join(printed))

    def test_declining_falls_back_to_the_printed_recipe(self) -> None:
        def installer():  # pragma: no cover - must not be called
            raise AssertionError("declined install still ran")

        result, printed = self._run(["codex", "n", "", ""], installer)
        self.assertIsNotNone(result)
        body = "\n".join(printed)
        self.assertIn("aiswmm gateway install", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class CommandSurfaceTests(unittest.TestCase):
    """The verbs a user actually types, including the failure paths.

    These are the branches a person hits when something is wrong: a gateway
    that was never installed, a stop with no pidfile, a status while the port
    is refusing. Each one has to answer rather than raise.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"AISWMM_CONFIG_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._out = mock.patch("builtins.print")
        self.print_mock = self._out.start()
        self.addCleanup(self._out.stop)

    def _printed(self) -> str:
        return "\n".join(str(call.args[0]) for call in self.print_mock.call_args_list if call.args)

    def _install_fake_binary(self) -> None:
        gateway.gateway_dir().mkdir(parents=True, exist_ok=True)
        gateway.binary_path().write_bytes(BINARY_BODY)
        gateway.config_path().write_text("port: 8317\n", encoding="utf-8")

    def test_register_exposes_every_verb(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        gateway.register(parser.add_subparsers())
        args = parser.parse_args(["gateway", "install", "--force"])
        self.assertEqual(args.gateway_action, "install")
        self.assertTrue(args.force)
        self.assertTrue(parser.parse_args(["gateway", "start", "--foreground"]).foreground)

    def test_a_bare_gateway_call_reports_status(self) -> None:
        self.assertEqual(gateway.main(mock.Mock(gateway_action=None)), 0)
        self.assertIn("not installed", self._printed())

    def test_status_distinguishes_serving_from_refusing(self) -> None:
        self._install_fake_binary()
        with mock.patch.object(gateway, "is_listening", return_value=True), \
             mock.patch.object(gateway, "is_healthy", return_value=False):
            gateway.main(mock.Mock(gateway_action="status"))
        self.assertIn("listening but refusing requests", self._printed())

    def test_status_says_serving_when_it_is(self) -> None:
        self._install_fake_binary()
        with mock.patch.object(gateway, "is_listening", return_value=True), \
             mock.patch.object(gateway, "is_healthy", return_value=True):
            gateway.main(mock.Mock(gateway_action="status"))
        self.assertIn("serving", self._printed())

    def test_installing_twice_says_so_instead_of_re_downloading(self) -> None:
        self._install_fake_binary()
        self.assertEqual(gateway.main(mock.Mock(gateway_action="install", force=False)), 0)
        self.assertIn("already installed", self._printed())

    def test_stop_without_a_pidfile_is_not_an_error(self) -> None:
        # A user who never started one, or already stopped it, should not get
        # a non-zero exit for asking.
        self.assertEqual(gateway.main(mock.Mock(gateway_action="stop")), 0)
        self.assertIn("No background gateway", self._printed())

    def test_stop_with_a_corrupt_pidfile_does_not_raise(self) -> None:
        gateway.gateway_dir().mkdir(parents=True, exist_ok=True)
        gateway.pid_path().write_text("not a pid", encoding="utf-8")
        self.assertFalse(gateway.stop_background())

    def test_restart_stops_then_starts(self) -> None:
        self._install_fake_binary()
        calls = []
        with mock.patch.object(gateway, "stop_background", lambda: calls.append("stop") or True), \
             mock.patch.object(gateway, "start_background", lambda **_k: calls.append("start") or True):
            self.assertEqual(gateway.main(mock.Mock(gateway_action="restart")), 0)
        self.assertEqual(calls, ["stop", "start"])

    def test_start_reports_the_log_when_the_gateway_will_not_come_up(self) -> None:
        self._install_fake_binary()
        with mock.patch.object(gateway, "start_background", return_value=False):
            self.assertEqual(gateway.main(mock.Mock(gateway_action="start", foreground=False)), 1)

    def test_a_gateway_error_becomes_an_exit_code_not_a_traceback(self) -> None:
        with mock.patch.object(gateway, "install_gateway", side_effect=gateway.GatewayError("nope")):
            self.assertEqual(gateway.main(mock.Mock(gateway_action="install", force=False)), 1)
