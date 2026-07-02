"""Wrap ImperialCollegeLondon/SWMManywhere as the engine for `swmm-anywhere`.

This is the aiswmm-side integration layer that hides three concrete gotchas
discovered during the D1 spike:

1. **pyswmm SIGKILL on macOS arm64.** The bundled
   ``swmm.toolkit._solver.abi3.so`` ships its own ``libomp.dylib`` which
   collides with any other OpenMP runtime already in the process.
   We install a ``pyswmm`` stub in ``sys.modules`` *before* the first
   ``swmmanywhere`` import so the module-level ``import pyswmm`` never
   reaches the real package. Safe because we force ``run_model=False`` and
   run the resulting INP through aiswmm's own ``swmm5`` binary.

2. **``base_dir`` must be ``pathlib.Path``, not ``str``.** SWMManywhere
   v0.2.2 ``filepaths.py`` does ``self.base_dir / self.project_name`` and
   raises ``TypeError`` if given a string.

3. **``storm.dat`` path with spaces crashes ``swmm5``.** SWMManywhere
   embeds the absolute path of the bundled storm forcing into
   ``[RAINGAGES]``; SWMM 5.2.4 doesn't accept paths containing spaces.
   We post-process the synthesised INP: copy referenced external files
   next to the INP, rewrite the path to be relative.

The default `outfall_derivation` parameters are the tuned values from
spike 04 (``method='withtopo'``, ``river_buffer_distance=300``,
``outfall_length=200``) — they cut outfall count by ~34 % vs SWMManywhere
defaults on a 1×1 km test bbox.

Public surface:

    run_synth_from_bbox(bbox, *, run_dir, project_name="swmm_anywhere",
                        refresh_raw=False, config_overrides=None)
        -> SynthRunResult

    SynthRunResult — frozen dataclass with inp_path, run_dir,
                     provenance dict, stage timings.

    SynthRunError(stage, original_exc) — wraps upstream exceptions with a
                                          tagged ``stage``.

This module deliberately does **lazy** SWMManywhere imports — importing
``swmmanywhere_runner`` itself never triggers the heavy geo stack, so
``aiswmm doctor`` and other read-only entrypoints stay light when the
``[anywhere]`` extra is not installed.
"""
from __future__ import annotations

import re
import shutil
import sys
import time
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.integrations.inp_source import InpSourceError, InpSourceResult
from typing import Any, Mapping

from agentic_swmm.integrations.raw_snapshot import (
    RawSource,
    snapshot_to,
    summarize_snapshot_verification,
)


# Default outfall_derivation overrides — from spike 04 A/B testing.
DEFAULT_OUTFALL_DERIVATION = {
    "method": "withtopo",
    "river_buffer_distance": 300.0,
    "outfall_length": 200.0,
}


@dataclass(frozen=True)
class SynthRunResult(InpSourceResult):
    """swmm-anywhere adapter result at the INP-source seam.

    Inherits the shared surface (``inp_path``, ``run_dir``,
    ``warnings``) and adds the synth path's typed extras.
    """

    raw_manifest_path: Path
    provenance: dict
    stage_durations: dict


class SynthRunError(InpSourceError):
    def __init__(self, stage: str, original_exc: BaseException) -> None:
        super().__init__(f"swmm-anywhere stage '{stage}' failed: {original_exc!r}")
        self.stage = stage
        self.original_exc = original_exc


def _check_anywhere_extra_installed() -> None:
    """Pre-check that the optional ``[anywhere]`` extra is importable.

    Raises ``SynthRunError(stage='extra_missing', ...)`` if not. We raise
    *before* ``_install_pyswmm_stub`` runs so the user gets a clean,
    actionable message instead of a misleading downstream traceback.
    """
    import importlib.util

    if importlib.util.find_spec("swmmanywhere") is None:
        raise SynthRunError(
            "extra_missing",
            ModuleNotFoundError(
                "The aiswmm[anywhere] optional extra is not installed. "
                "This extra wraps SWMManywhere by Imperial College London "
                "(BSD-3-Clause, https://github.com/ImperialCollegeLondon/SWMManywhere). "
                "Install with: pip install aiswmm[anywhere]"
            ),
        )


def _install_pyswmm_stub() -> None:
    """Install a minimal pyswmm stub so SWMManywhere's module-level
    ``import pyswmm`` doesn't trigger the libomp SIGKILL on macOS arm64."""
    if "pyswmm" in sys.modules:
        return
    stub = types.ModuleType("pyswmm")
    # SWMManywhere references these at module level; they're not actually
    # called because we set ``run_model=False`` upstream.
    stub.Simulation = None  # type: ignore[attr-defined]
    stub.Nodes = None  # type: ignore[attr-defined]
    stub.Links = None  # type: ignore[attr-defined]
    stub.Output = None  # type: ignore[attr-defined]
    sys.modules["pyswmm"] = stub


def _coerce_base_dir(config: dict) -> dict:
    """SWMManywhere ``filepaths.py`` requires ``Path``, not ``str``."""
    if isinstance(config.get("base_dir"), str):
        config["base_dir"] = Path(config["base_dir"])
    return config


_RAINGAGE_FILE_RE = re.compile(
    r"^(\s*\S+\s+\S+\s+\S+\s+\S+\s+FILE\s+)(.+?)(\s+\S+\s+\S+)\s*$",
    re.IGNORECASE,
)


def normalize_external_paths(inp_path: Path) -> tuple[Path, ...]:
    """Copy external files referenced by absolute path in the INP next to
    the INP, then rewrite the references to bare filenames.

    Handles the SWMM 5.2 ``ERROR 205`` failure mode when an absolute path
    contains spaces (e.g. ``/Users/.../Codex Project/...``). Currently
    targets ``[RAINGAGES]`` FILE entries (the only external-file reference
    SWMManywhere emits); extensible to ``[TIMESERIES]`` FILE entries if
    needed.

    Returns the tuple of source paths that were copied next to the INP.
    """
    inp_path = Path(inp_path)
    inp_dir = inp_path.parent
    text = inp_path.read_text()

    copied: list[Path] = []
    new_lines: list[str] = []
    in_raingages = False
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("[raingages]"):
            in_raingages = True
            new_lines.append(line)
            continue
        if stripped.startswith("[") and stripped != "[raingages]":
            in_raingages = False
            new_lines.append(line)
            continue
        if not in_raingages:
            new_lines.append(line)
            continue

        m = _RAINGAGE_FILE_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        prefix, file_path, suffix = m.group(1), m.group(2).strip(), m.group(3)
        src = Path(file_path)
        if not src.is_absolute() or " " not in str(src):
            new_lines.append(line)
            continue
        if not src.exists():
            new_lines.append(line)
            continue
        dest = inp_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        copied.append(src)
        new_lines.append(f"{prefix}{src.name}{suffix}")

    inp_path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
    return tuple(copied)


def override_rain_file(inp_path: Path, rain_file: Path) -> Path:
    """Copy a user-supplied rainfall file next to the INP and rewrite every
    ``[RAINGAGES]`` FILE entry to point at it.

    SWMManywhere bundles a 15-min demo ``storm.dat`` that ships with the
    upstream package; for any real analysis the user needs to swap in their
    own rain forcing. This helper does the swap as a post-process step so
    callers don't have to hand-edit the synthesised INP.

    The destination filename is kept relative (bare ``rain_file.name``) so
    SWMM 5.2's path-with-spaces parsing bug never bites — same defensive
    pattern as ``normalize_external_paths``.

    Args:
        inp_path: the synthesised INP whose ``[RAINGAGES]`` lines to rewrite.
        rain_file: absolute path to the user's rainfall data file.

    Returns:
        The destination path the rain file was copied to (inside the INP's
        parent directory).
    """
    inp_path = Path(inp_path)
    rain_file = Path(rain_file)
    inp_dir = inp_path.parent

    dest = inp_dir / rain_file.name
    if rain_file.resolve() != dest.resolve():
        shutil.copyfile(rain_file, dest)

    text = inp_path.read_text()
    new_lines: list[str] = []
    in_raingages = False
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("[raingages]"):
            in_raingages = True
            new_lines.append(line)
            continue
        if stripped.startswith("[") and stripped != "[raingages]":
            in_raingages = False
            new_lines.append(line)
            continue
        if not in_raingages:
            new_lines.append(line)
            continue
        m = _RAINGAGE_FILE_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        prefix, _old_path, suffix = m.group(1), m.group(2), m.group(3)
        new_lines.append(f"{prefix}{rain_file.name}{suffix}")

    inp_path.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
    return dest


def _apply_parameter_overrides(config: dict, overrides: Mapping[str, Any]) -> dict:
    """Push parameter group overrides into the SWMManywhere config dict."""
    bucket = config.setdefault("parameter_overrides", {})
    for group, values in overrides.items():
        bucket.setdefault(group, {}).update(values)
    return config


def _build_config(
    bbox: list[float],
    run_dir: Path,
    project_name: str,
    config_overrides: Mapping[str, Any] | None,
    use_upstream_defaults: bool = False,
) -> dict:
    # Lazy import — yaml ships with aiswmm so this is light, but read the
    # SWMManywhere demo template only when we actually run.
    import yaml

    from swmmanywhere import swmmanywhere as swmm_anywhere_mod

    defs_dir = Path(swmm_anywhere_mod.__file__).parent / "defs"
    config = yaml.safe_load((defs_dir / "demo_config.yml").read_text())

    config["base_dir"] = run_dir
    config["project"] = project_name
    config["bbox"] = list(bbox)
    config["real"] = {"inp": None, "graph": None, "subcatchments": None, "results": None}
    config["metric_list"] = []
    config["run_model"] = False

    # Apply tuned outfall_derivation defaults from spike 04 — unless the caller
    # explicitly opts out via use_upstream_defaults=True. The opt-out path lets
    # power-users reproduce SWMManywhere's upstream extended_demo behaviour
    # (method=separate, river_buffer_distance=150 m, outfall_length=40) for
    # comparison/validation against ImperialCollegeLondon/SWMManywhere itself.
    if not use_upstream_defaults:
        config = _apply_parameter_overrides(
            config, {"outfall_derivation": DEFAULT_OUTFALL_DERIVATION}
        )

    if config_overrides:
        config = _apply_parameter_overrides(config, config_overrides)
    return config


def _resolve_download_dir(run_dir: Path, project_name: str, bbox: list[float]) -> Path:
    """Return the actual ``bbox_N/download`` path created by SWMManywhere.

    SWMManywhere increments the ``bbox_N`` index when a project directory
    already contains a subdirectory for a *different* bbox. Hardcoding
    ``bbox_1`` silently captures nothing when the upstream engine chose
    ``bbox_2``, ``bbox_3``, etc.

    Resolution strategy:
    1. Glob ``<project>/bbox_*/download`` for directories that exist.
    2. If exactly one exists, return it.
    3. If several exist, prefer the one whose sibling
       ``bounding_box_info.json`` matches *bbox* (coordinate comparison).
    4. Fallback: return ``<project>/bbox_1/download`` (original behaviour)
       so callers still get the warning from ``_snapshot_raw_downloads`` on
       an absent dir rather than a confusing path.

    This function is pure (no I/O side effects) and never imports
    ``swmmanywhere.*`` so it works even when the optional extra is absent.
    """
    import json as _json

    proj_dir = run_dir / project_name
    candidates = sorted(proj_dir.glob("bbox_*/download"))
    candidates = [c for c in candidates if c.is_dir()]

    if not candidates:
        # No bbox_* dir at all — return the legacy default so _snapshot_raw_downloads
        # can emit its "dir absent" warning path.
        return proj_dir / "bbox_1" / "download"

    if len(candidates) == 1:
        return candidates[0]

    # Multiple bbox dirs — match by bounding_box_info.json.
    # SWMManywhere stores coordinates as {"bbox": {"x_min":…, "y_min":…, "x_max":…, "y_max":…}}.
    target_coords = set(round(v, 8) for v in bbox)
    for dl_dir in candidates:
        info_path = dl_dir.parent / "bounding_box_info.json"
        if not info_path.exists():
            continue
        try:
            info = _json.loads(info_path.read_text())
            stored_bbox = info.get("bbox", {})
            stored_coords = set(
                round(float(v), 8)
                for v in (
                    stored_bbox.get("x_min"),
                    stored_bbox.get("y_min"),
                    stored_bbox.get("x_max"),
                    stored_bbox.get("y_max"),
                )
                if v is not None
            )
            if stored_coords == target_coords:
                return dl_dir
        except Exception:
            # Unreadable or malformed info file — skip this candidate.
            continue

    # No bbox info matched — fall back to the first candidate rather than bbox_1
    # (the first candidate is at least a real directory).
    return candidates[0]


def _snapshot_raw_downloads(download_dir: Path, snapshot_dir: Path) -> Path:
    """Take every file SWMManywhere wrote under ``<project>/bbox_N/download/``
    into our run-aware ``00_raw/`` snapshot via raw_snapshot."""
    if not download_dir.exists():
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest = snapshot_to(snapshot_dir, [])
        return snapshot_dir / "raw_manifest.json"

    captured = datetime.now(timezone.utc).isoformat()
    sources = [
        RawSource(
            path=p,
            source_url=f"swmmanywhere://download/{p.name}",
            captured_at=captured,
        )
        for p in sorted(download_dir.iterdir())
        if p.is_file()
    ]
    snapshot_to(snapshot_dir, sources)
    return snapshot_dir / "raw_manifest.json"


def run_synth_from_bbox(
    bbox: list[float],
    *,
    run_dir: Path,
    project_name: str = "swmm_anywhere",
    refresh_raw: bool = False,
    config_overrides: Mapping[str, Any] | None = None,
    use_upstream_defaults: bool = False,
    rain_file: Path | None = None,
) -> SynthRunResult:
    """Run SWMManywhere on a bbox and return a SynthRunResult.

    Args:
        bbox: ``[min_lon, min_lat, max_lon, max_lat]`` in WGS84.
        run_dir: target directory. Will contain ``00_raw/`` and
            ``10_swmmanywhere/`` subdirectories after the call.
        project_name: human label embedded in the SWMManywhere config and
            its output filesystem layout.
        refresh_raw: not yet used at this layer (SWMManywhere's own
            prepare_data re-downloads each call). Reserved for the future
            cache-aware path described in the PRD.
        config_overrides: per-call SWMManywhere parameter overrides; merged
            into the tuned defaults from spike 04.
        use_upstream_defaults: when True, skip the spike-04 tuned
            ``outfall_derivation`` overrides and let SWMManywhere fall back to
            its upstream ``parameters.py`` defaults (method=separate,
            river_buffer_distance=150 m, outfall_length=40). ``config_overrides``
            still applies on top, so users can opt into upstream behaviour and
            then nudge individual knobs.
        rain_file: optional path to a user-supplied rainfall data file
            (SWMM ``DAT`` format). When provided, the file is copied next to
            the synth INP and every ``[RAINGAGES]`` FILE entry is rewritten
            to point at it, replacing SWMManywhere's bundled 15-min demo
            ``storm.dat``. Raises ``SynthRunError(stage='rain_file_missing')``
            if the path does not exist.

    Raises:
        SynthRunError(stage=...): wraps upstream exceptions with a tagged
            stage so callers can decide how to recover.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = run_dir / "00_raw"
    synth_dir = run_dir / "10_swmmanywhere"

    stage_durations: dict = {}
    warnings: list[str] = []

    # Validate the user-supplied rain file path up-front. This runs *before*
    # the [anywhere] extra check so a typo in --rain-file fails immediately,
    # without needing the geo stack installed — it's a pure-filesystem check.
    if rain_file is not None:
        rain_file = Path(rain_file)
        if not rain_file.exists():
            raise SynthRunError(
                "rain_file_missing",
                FileNotFoundError(
                    f"--rain-file path does not exist: {rain_file}. "
                    "Hint: pass an absolute path to a SWMM-format DAT file "
                    "(see the [RAINGAGES] FILE format in the SWMM 5.2 manual)."
                ),
            )

    # Fail fast with an actionable message if the user hasn't opted into the
    # 27-package [anywhere] extra. Doing this before the heavy imports avoids
    # the misleading "stage='config_build' / ModuleNotFoundError" the user
    # would otherwise see when the lazy SWMManywhere import inside
    # _build_config fails.
    _check_anywhere_extra_installed()

    try:
        _install_pyswmm_stub()
    except Exception as exc:
        raise SynthRunError("pyswmm_stub", exc) from exc

    try:
        config = _build_config(
            bbox,
            run_dir,
            project_name,
            config_overrides,
            use_upstream_defaults=use_upstream_defaults,
        )
        config = _coerce_base_dir(config)
    except Exception as exc:
        raise SynthRunError("config_build", exc) from exc

    try:
        t0 = time.time()
        from swmmanywhere import swmmanywhere as swmm_anywhere_mod
        inp_path_str, _ = swmm_anywhere_mod.swmmanywhere(config)
        stage_durations["swmmanywhere_pipeline"] = round(time.time() - t0, 2)
    except Exception as exc:
        raise SynthRunError("swmmanywhere_pipeline", exc) from exc

    upstream_inp = Path(inp_path_str)
    if not upstream_inp.exists():
        raise SynthRunError(
            "inp_write",
            FileNotFoundError(f"swmmanywhere() returned non-existent INP: {upstream_inp}"),
        )

    try:
        t0 = time.time()
        synth_dir.mkdir(parents=True, exist_ok=True)
        final_inp = synth_dir / "synth.inp"
        shutil.copyfile(upstream_inp, final_inp)
        # Also copy parquet artefacts for downstream visualisation.
        upstream_model_dir = upstream_inp.parent
        for artefact in ("nodes.geoparquet", "edges.geoparquet", "subcatchments.geoparquet"):
            src = upstream_model_dir / artefact
            if src.exists():
                shutil.copyfile(src, synth_dir / artefact)
        copied_externals = normalize_external_paths(final_inp)
        if rain_file is not None:
            override_rain_file(final_inp, rain_file)
            warnings.append(
                f"replaced bundled SWMManywhere storm.dat with user rain file: {rain_file.name}"
            )
        stage_durations["post_process_inp"] = round(time.time() - t0, 2)
        if copied_externals:
            warnings.append(
                f"rewrote {len(copied_externals)} absolute path(s) in [RAINGAGES] to relative "
                "filenames to avoid SWMM 5.2 path-with-spaces parsing error"
            )
    except Exception as exc:
        raise SynthRunError("inp_postprocess", exc) from exc

    try:
        t0 = time.time()
        download_dir = _resolve_download_dir(run_dir, project_name, bbox)
        raw_manifest_path = _snapshot_raw_downloads(download_dir, raw_dir)
        stage_durations["raw_snapshot"] = round(time.time() - t0, 2)
    except Exception as exc:
        raise SynthRunError("raw_snapshot", exc) from exc

    # Verify the snapshot we just wrote so its integrity is a checked, recorded
    # fact in provenance — not an unverified claim. (verify_snapshot was dead
    # code before this.) A drift here means the captured 00_raw bytes do not
    # match their recorded hashes; surface it rather than silently trusting it.
    raw_snapshot_verified = summarize_snapshot_verification(raw_manifest_path)
    if not raw_snapshot_verified["ok"]:
        warnings.append(
            "00_raw snapshot failed verification "
            f"(missing={raw_snapshot_verified['missing']}, "
            f"mismatched={raw_snapshot_verified['mismatched']})"
        )

    provenance = {
        "tool": "swmmanywhere",
        "tool_version_attr": getattr(swmm_anywhere_mod, "__version__", "unknown"),
        "bbox_wgs84": list(bbox),
        "project_name": project_name,
        "config_overrides_applied": dict(config.get("parameter_overrides") or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_snapshot_verified": raw_snapshot_verified,
    }
    (synth_dir / "synth_provenance.json").write_text(
        __import__("json").dumps(provenance, indent=2, sort_keys=False)
    )

    return SynthRunResult(
        inp_path=final_inp,
        run_dir=run_dir,
        raw_manifest_path=raw_manifest_path,
        provenance=provenance,
        stage_durations=stage_durations,
        warnings=tuple(warnings),
    )
