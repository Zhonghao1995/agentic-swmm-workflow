"""Environment fingerprint in manifest + provenance (ADR-0003, layer 3).

The runtime writes WHERE a run actually executed into ``manifest.json``
(captured, not prescribed); the audit script copies that block verbatim
into ``experiment_provenance.json`` while staying agentic_swmm-import-free
(pure JSON read). Legacy runs without the block get a minimal audit-time
capture so the ``environment`` key is always present in provenance.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import session_header as sh
from agentic_swmm.agent.swmm_runtime.run_manifests import build_top_manifest, source_type_of

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_audit_module():
    path = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"
    spec = importlib.util.spec_from_file_location("audit_run_env_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FingerprintContainerIdentityTests(unittest.TestCase):
    def test_bare_metal_run_has_null_container_fields(self) -> None:
        import os

        clean = {
            key: value
            for key, value in os.environ.items()
            if key not in (sh.CONTAINER_IMAGE_ENV, sh.CONTAINER_DIGEST_ENV)
        }
        with mock.patch.dict("os.environ", clean, clear=True):
            env = sh.environment_fingerprint()
        self.assertIsNone(env["container_image"])
        self.assertIsNone(env["container_image_digest"])

    def test_container_identity_comes_from_env(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                sh.CONTAINER_IMAGE_ENV: "ghcr.io/x/aiswmm:v9",
                sh.CONTAINER_DIGEST_ENV: "sha256:feed",
            },
        ):
            env = sh.environment_fingerprint()
        self.assertEqual(env["container_image"], "ghcr.io/x/aiswmm:v9")
        self.assertEqual(env["container_image_digest"], "sha256:feed")

    def test_version_matches_checkout_pyproject(self) -> None:
        """The fingerprint reports the version this code IS: the checkout's
        pyproject version, not possibly-stale installed dist metadata."""
        import tomllib

        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            expected = tomllib.load(fh)["project"]["version"]
        self.assertEqual(sh.environment_fingerprint()["aiswmm_version"], expected)


class TopManifestEnvironmentTests(unittest.TestCase):
    def test_top_manifest_carries_environment_block(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "model.inp"
            inp.write_text("[OPTIONS]\n", encoding="utf-8")
            runner_manifest = {
                "swmm5": {"version": "5.2.4"},
                "files": {"rpt": str(tmp / "model.rpt"), "out": str(tmp / "model.out")},
            }
            manifest = build_top_manifest(
                source_inp=inp,
                run_inp=inp,
                builder_inp=inp,
                sidecar_inputs=[],
                source_type=source_type_of(inp),
                runner_manifest=runner_manifest,
                runner_files=runner_manifest["files"],
                runner_dir=tmp / "05_runner",
                qa_dir=tmp / "06_qa",
                run_dir=tmp,
                command_trace={"command": "swmm_runner"},
            )
        env = manifest["environment"]
        self.assertEqual(env["swmm5_version"], "5.2.4")
        self.assertEqual(env["python"], sys.version.split()[0])
        self.assertIn("platform", env)
        self.assertIn("git_commit", env)


class ProvenanceEnvironmentCopyTests(unittest.TestCase):
    def _seed_run_dir(self, tmp: Path, *, manifest_environment: dict | None) -> Path:
        run_dir = tmp / "run-x"
        run_dir.mkdir(parents=True)
        top: dict = {"run_id": "run-x", "tools": {"swmm5_version": "5.2.4"}}
        if manifest_environment is not None:
            top["environment"] = manifest_environment
        (run_dir / "manifest.json").write_text(json.dumps(top), encoding="utf-8")
        return run_dir

    def test_provenance_copies_manifest_environment_verbatim(self) -> None:
        import tempfile

        audit = _load_audit_module()
        block = {"python": "3.11.14", "platform": "test-os", "container_image": "ghcr.io/x:v1"}
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._seed_run_dir(Path(raw), manifest_environment=block)
            provenance, _ = audit.collect_run(run_dir, repo_root=Path(raw))
        self.assertEqual(provenance["environment"], block)

    def test_legacy_run_without_block_gets_audit_fallback(self) -> None:
        import tempfile

        audit = _load_audit_module()
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self._seed_run_dir(Path(raw), manifest_environment=None)
            provenance, _ = audit.collect_run(run_dir, repo_root=Path(raw))
        env = provenance["environment"]
        self.assertEqual(env["captured_by"], "audit-fallback")
        self.assertEqual(env["python"], sys.version.split()[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
