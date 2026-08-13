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
CONFIG_BODY = b"port: 8317\n"


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
        # config path, so first install seeds one.
        self.assertTrue(gateway.config_path().exists())
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
