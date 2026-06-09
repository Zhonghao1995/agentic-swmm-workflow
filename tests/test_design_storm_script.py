"""Tests for skills/swmm-climate/scripts/design_storm.py — PR1 math slice.

Five test groups:
1. Mass conservation — Σ depth increments == analytic total, rel tol 0.5%.
2. Peak position — Chicago peak at r·duration ± 1 dt; alternating-block peak at center.
3. IDF sanity — longer duration ⇒ lower mean intensity (both forms).
4. Output-contract parity — out-json keys ⊇ format_rainfall keys; builder integration smoke.
5. Determinism — two runs → byte-identical files.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers to locate scripts and fixtures
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent

_DESIGN_STORM_SCRIPT = _REPO_ROOT / "skills" / "swmm-climate" / "scripts" / "design_storm.py"
_BUILD_SWMM_SCRIPT = _REPO_ROOT / "skills" / "swmm-builder" / "scripts" / "build_swmm_inp.py"
_PARAMS_LANDUSE_SCRIPT = _REPO_ROOT / "skills" / "swmm-params" / "scripts" / "landuse_to_swmm_params.py"
_PARAMS_SOIL_SCRIPT = _REPO_ROOT / "skills" / "swmm-params" / "scripts" / "soil_to_greenampt.py"
_PARAMS_MERGE_SCRIPT = _REPO_ROOT / "skills" / "swmm-params" / "scripts" / "merge_swmm_params.py"

_LANDUSE_EXAMPLE = _REPO_ROOT / "skills" / "swmm-params" / "examples" / "landuse_input.csv"
_SOIL_EXAMPLE = _REPO_ROOT / "skills" / "swmm-params" / "examples" / "soil_input.csv"
_SUBCATCHMENTS_EXAMPLE = _REPO_ROOT / "skills" / "swmm-builder" / "examples" / "subcatchments_input.csv"
_NETWORK_EXAMPLE = _REPO_ROOT / "skills" / "swmm-network" / "examples" / "basic-network.json"
_CONFIG_EXAMPLE = _REPO_ROOT / "skills" / "swmm-builder" / "examples" / "options_config.json"


def _run_design_storm(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_DESIGN_STORM_SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# IDF helpers (mirror the script's math for test assertions)
# ---------------------------------------------------------------------------

def _idf_generic_depth(t_min: float, a: float, b: float, c: float) -> float:
    """Total depth (mm) for duration t_min from generic IDF: i = a/(t+b)^c."""
    if t_min <= 0.0:
        return 0.0
    return a / (t_min + b) ** c * t_min / 60.0


def _idf_cn_depth(t_min: float, A1: float, C: float, lgP: float, b: float, n: float) -> float:
    """Total depth (mm) for duration t_min from CN formula: q = 167·A1·(1+C·lgP)/(t+b)^n."""
    if t_min <= 0.0:
        return 0.0
    q_lsha = 167.0 * A1 * (1.0 + C * lgP) / (t_min + b) ** n
    i_mm_hr = q_lsha * 0.36  # mm/hr
    return i_mm_hr * t_min / 60.0


def _read_ts_depths(ts_path: Path, dt_min: int) -> list[float]:
    """Read timeseries file and return depth-per-dt values in mm.

    The timeseries stores intensity (mm/hr); convert back to depth per timestep.
    """
    depths: list[float] = []
    for line in ts_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";;"):
            continue
        parts = stripped.split()
        # Format: name date time value (4 tokens per row)
        if len(parts) == 4:
            intensity_mm_hr = float(parts[3])
            depths.append(intensity_mm_hr * dt_min / 60.0)
    return depths


# ---------------------------------------------------------------------------
# 1. Mass conservation
# ---------------------------------------------------------------------------


class TestMassConservation:
    """Σ depth increments == analytic total depth, rel tol 0.5%."""

    def test_chicago_generic_form(self, tmp_path: Path) -> None:
        """Chicago generic form: mass = IDF(r·T) + IDF((1-r)·T), rel tol < 0.5%."""
        a, b, c = 1000.0, 10.0, 0.7
        duration, dt, r = 120.0, 5.0, 0.4

        out_json = tmp_path / "chicago.json"
        out_ts = tmp_path / "chicago_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", str(a),
            "--b", str(b),
            "--c-exp", str(c),
            "--return-period", "2",
            "--duration", str(duration),
            "--dt", str(dt),
            "--r", str(r),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr

        depths = _read_ts_depths(out_ts, int(dt))
        total_depth = sum(depths)

        # Chicago method: analytic total = IDF(r·T) + IDF((1-r)·T)
        expected = _idf_generic_depth(r * duration, a, b, c) + _idf_generic_depth(
            (1.0 - r) * duration, a, b, c
        )
        rel_err = abs(total_depth - expected) / expected
        assert rel_err < 0.005, (
            f"Chicago generic: total {total_depth:.4f} mm vs expected {expected:.4f} mm "
            f"(rel err {rel_err:.4%})"
        )

    def test_chicago_cn_form(self, tmp_path: Path) -> None:
        """Chicago CN form: mass = IDF(r·T) + IDF((1-r)·T), rel tol < 0.5%."""
        A1, C, b, n = 4.0, 0.7, 8.0, 0.65
        P, duration, dt, r = 5.0, 60.0, 5.0, 0.4
        lgP = math.log10(P)

        out_json = tmp_path / "cn.json"
        out_ts = tmp_path / "cn_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "CN",
            "--a1", str(A1),
            "--C", str(C),
            "--b", str(b),
            "--n", str(n),
            "--return-period", str(P),
            "--duration", str(duration),
            "--dt", str(dt),
            "--r", str(r),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr

        depths = _read_ts_depths(out_ts, int(dt))
        total_depth = sum(depths)

        expected = _idf_cn_depth(r * duration, A1, C, lgP, b, n) + _idf_cn_depth(
            (1.0 - r) * duration, A1, C, lgP, b, n
        )
        rel_err = abs(total_depth - expected) / expected
        assert rel_err < 0.005, (
            f"Chicago CN: total {total_depth:.4f} mm vs expected {expected:.4f} mm "
            f"(rel err {rel_err:.4%})"
        )

    def test_alternating_block(self, tmp_path: Path) -> None:
        """Alternating-block mass = IDF(T) exactly (no branch split)."""
        a, b, c = 1000.0, 10.0, 0.7
        duration, dt = 60.0, 5.0

        idf_table = json.dumps([
            {"duration_min": float(k * dt), "intensity_mm_per_hr": a / (k * dt + b) ** c}
            for k in range(1, int(duration / dt) + 1)
        ])

        out_json = tmp_path / "ab.json"
        out_ts = tmp_path / "ab_ts.txt"
        result = _run_design_storm([
            "--method", "alternating_block",
            "--idf-json", idf_table,
            "--return-period", "2",
            "--duration", str(duration),
            "--dt", str(dt),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr

        depths = _read_ts_depths(out_ts, int(dt))
        total_depth = sum(depths)

        expected = _idf_generic_depth(duration, a, b, c)
        rel_err = abs(total_depth - expected) / expected
        assert rel_err < 0.005, (
            f"Alternating-block: total {total_depth:.4f} mm vs expected {expected:.4f} mm "
            f"(rel err {rel_err:.4%})"
        )

    def test_mass_conservation_numbers(self) -> None:
        """Document the exact mass-conservation numbers (referenced in PR body).

        Chicago generic: a=1000, b=10, c=0.7, T=120min, r=0.4
          pre-peak depth  = IDF(48 min)  ≈ 46.63 mm
          post-peak depth = IDF(72 min)  ≈ 54.89 mm

        Alternating-block: T=60min ≈ 51.10 mm
        """
        a, b, c, T, r = 1000.0, 10.0, 0.7, 120.0, 0.4
        pre = _idf_generic_depth(r * T, a, b, c)
        post = _idf_generic_depth((1.0 - r) * T, a, b, c)
        assert abs(pre - 46.63) < 0.01, f"pre-peak depth {pre}"
        assert abs(post - 54.89) < 0.01, f"post-peak depth {post}"

        ab_expected = _idf_generic_depth(60.0, a, b, c)
        assert abs(ab_expected - 51.10) < 0.01, f"alt-block expected {ab_expected}"


# ---------------------------------------------------------------------------
# 2. Peak position
# ---------------------------------------------------------------------------


class TestPeakPosition:
    """Chicago peak at r·duration ± 1 dt; alternating-block peak at center block."""

    def test_chicago_peak_position(self, tmp_path: Path) -> None:
        a, b, c = 1000.0, 10.0, 0.7
        duration, dt, r = 120.0, 5.0, 0.4

        out_json = tmp_path / "p.json"
        out_ts = tmp_path / "p_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", str(a),
            "--b", str(b),
            "--c-exp", str(c),
            "--return-period", "2",
            "--duration", str(duration),
            "--dt", str(dt),
            "--r", str(r),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr

        depths = _read_ts_depths(out_ts, int(dt))
        n_steps = len(depths)
        peak_idx = depths.index(max(depths))

        # Peak must be within 1 dt of r * n_steps
        target_idx = r * n_steps  # = 9.6
        assert abs(peak_idx - target_idx) <= 1.0 + 1e-9, (
            f"Chicago peak at {peak_idx}, expected near {target_idx:.2f} (±1 step)"
        )

    def test_chicago_peak_with_r_0_5(self, tmp_path: Path) -> None:
        """r=0.5 (symmetric Chicago): peak near center."""
        out_json = tmp_path / "p.json"
        out_ts = tmp_path / "p_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
            "--r", "0.5",
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr
        depths = _read_ts_depths(out_ts, 5)
        peak_idx = depths.index(max(depths))
        n = len(depths)
        target = 0.5 * n  # 6
        assert abs(peak_idx - target) <= 1.0 + 1e-9, (
            f"r=0.5 peak at {peak_idx}, expected near {target}"
        )

    def test_alternating_block_peak_at_center(self, tmp_path: Path) -> None:
        """Alternating-block: peak block at center index (n//2)."""
        a, b, c = 1000.0, 10.0, 0.7
        duration, dt = 60.0, 5.0
        n_steps = int(duration / dt)  # 12

        idf_table = json.dumps([
            {"duration_min": float(k * dt), "intensity_mm_per_hr": a / (k * dt + b) ** c}
            for k in range(1, n_steps + 1)
        ])

        out_json = tmp_path / "p.json"
        out_ts = tmp_path / "p_ts.txt"
        result = _run_design_storm([
            "--method", "alternating_block",
            "--idf-json", idf_table,
            "--return-period", "2",
            "--duration", str(duration),
            "--dt", str(dt),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr

        depths = _read_ts_depths(out_ts, int(dt))
        peak_idx = depths.index(max(depths))
        center = len(depths) // 2

        assert peak_idx == center, (
            f"Alternating-block peak at {peak_idx}, expected center {center}"
        )


# ---------------------------------------------------------------------------
# 3. IDF sanity: longer duration ⇒ lower mean intensity
# ---------------------------------------------------------------------------


class TestIdfSanity:
    """Longer duration must produce lower mean intensity (IDF property)."""

    def _mean_intensity(self, ts_path: Path, dt_min: int) -> float:
        """Mean intensity (mm/hr) over the storm from the timeseries file."""
        depths = _read_ts_depths(ts_path, dt_min)
        if not depths:
            return 0.0
        total_duration_hr = len(depths) * dt_min / 60.0
        return sum(depths) / total_duration_hr

    def test_chicago_generic_longer_duration_lower_mean_intensity(self, tmp_path: Path) -> None:
        """Chicago generic: 120 min storm has lower mean intensity than 60 min."""
        short_ts = tmp_path / "s_ts.txt"
        long_ts = tmp_path / "l_ts.txt"

        for dur, ts in [("60", short_ts), ("120", long_ts)]:
            r = _run_design_storm([
                "--method", "chicago",
                "--form", "generic",
                "--a-coeff", "1000",
                "--b", "10",
                "--c-exp", "0.7",
                "--return-period", "2",
                "--duration", dur,
                "--dt", "5",
                "--r", "0.4",
                "--out-json", str(tmp_path / f"j{dur}.json"),
                "--out-timeseries", str(ts),
            ])
            assert r.returncode == 0, r.stderr

        mean_short = self._mean_intensity(short_ts, 5)
        mean_long = self._mean_intensity(long_ts, 5)
        assert mean_long < mean_short, (
            f"Longer duration should have lower mean intensity: "
            f"60 min={mean_short:.3f} mm/hr, 120 min={mean_long:.3f} mm/hr"
        )

    def test_chicago_cn_longer_duration_lower_mean_intensity(self, tmp_path: Path) -> None:
        """Chicago CN form: 120 min mean intensity < 60 min mean intensity."""
        short_ts = tmp_path / "s_ts.txt"
        long_ts = tmp_path / "l_ts.txt"

        for dur, ts in [("60", short_ts), ("120", long_ts)]:
            r = _run_design_storm([
                "--method", "chicago",
                "--form", "CN",
                "--a1", "4.0",
                "--C", "0.7",
                "--b", "8.0",
                "--n", "0.65",
                "--return-period", "5",
                "--duration", dur,
                "--dt", "5",
                "--r", "0.4",
                "--out-json", str(tmp_path / f"jcn{dur}.json"),
                "--out-timeseries", str(ts),
            ])
            assert r.returncode == 0, r.stderr

        mean_short = self._mean_intensity(short_ts, 5)
        mean_long = self._mean_intensity(long_ts, 5)
        assert mean_long < mean_short, (
            f"CN form longer duration should have lower mean intensity: "
            f"60 min={mean_short:.3f} mm/hr, 120 min={mean_long:.3f} mm/hr"
        )

    def test_alternating_block_longer_duration_lower_mean_intensity(self, tmp_path: Path) -> None:
        """Alternating-block: 120 min mean intensity < 60 min mean intensity."""
        a, b, c = 1000.0, 10.0, 0.7
        dt = 5.0

        short_ts = tmp_path / "s_ts.txt"
        long_ts = tmp_path / "l_ts.txt"

        for dur, ts in [(60.0, short_ts), (120.0, long_ts)]:
            idf = json.dumps([
                {"duration_min": float(k * dt), "intensity_mm_per_hr": a / (k * dt + b) ** c}
                for k in range(1, int(dur / dt) + 1)
            ])
            r = _run_design_storm([
                "--method", "alternating_block",
                "--idf-json", idf,
                "--return-period", "2",
                "--duration", str(dur),
                "--dt", str(dt),
                "--out-json", str(tmp_path / f"jab{int(dur)}.json"),
                "--out-timeseries", str(ts),
            ])
            assert r.returncode == 0, r.stderr

        mean_short = self._mean_intensity(short_ts, int(dt))
        mean_long = self._mean_intensity(long_ts, int(dt))
        assert mean_long < mean_short, (
            f"Alt-block longer duration should have lower mean intensity: "
            f"60 min={mean_short:.3f} mm/hr, 120 min={mean_long:.3f} mm/hr"
        )


# ---------------------------------------------------------------------------
# 4. Output-contract parity + builder integration smoke
# ---------------------------------------------------------------------------


# Mandatory stdout keys from format_rainfall.py
_FORMAT_RAINFALL_STDOUT_KEYS = frozenset(
    ["ok", "out_json", "out_timeseries", "series_name", "series_names", "rows", "stations", "interval_minutes"]
)


class TestOutputContract:
    """out-json keys ⊇ format_rainfall keys; builder integration smoke."""

    def _generate_chicago(self, tmp_path: Path) -> tuple[Path, Path]:
        out_json = tmp_path / "ds.json"
        out_ts = tmp_path / "ds_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
            "--r", "0.4",
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr
        return out_json, out_ts

    def test_stdout_keys_superset_of_format_rainfall(self, tmp_path: Path) -> None:
        """stdout JSON must include all keys that format_rainfall.py emits."""
        out_json = tmp_path / "ds.json"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
            "--r", "0.4",
            "--out-json", str(out_json),
            "--out-timeseries", str(tmp_path / "ds_ts.txt"),
        ])
        assert result.returncode == 0, result.stderr
        stdout_obj = json.loads(result.stdout)
        missing = _FORMAT_RAINFALL_STDOUT_KEYS - set(stdout_obj.keys())
        assert not missing, f"stdout missing keys from format_rainfall contract: {missing}"

    def test_out_json_keys_superset_of_format_rainfall(self, tmp_path: Path) -> None:
        """out-json file must include all keys present in format_rainfall.py's contract."""
        out_json, _ = self._generate_chicago(tmp_path)
        meta = json.loads(out_json.read_text(encoding="utf-8"))
        required = {
            "ok", "series_name", "series_names", "rows", "stations",
            "interval_minutes", "range", "outputs",
        }
        missing = required - set(meta.keys())
        assert not missing, f"out-json missing keys: {missing}"
        # Design-storm-specific provenance keys (new keys added by this script)
        for k in ["method", "return_period_yr", "coefficients", "form"]:
            assert k in meta, f"out-json missing design-storm key '{k}'"

    def test_out_json_outputs_timeseries_text_points_to_file(self, tmp_path: Path) -> None:
        """outputs.timeseries_text in out-json must point to a real file."""
        out_json, out_ts = self._generate_chicago(tmp_path)
        meta = json.loads(out_json.read_text(encoding="utf-8"))
        ts_path = Path(meta["outputs"]["timeseries_text"])
        assert ts_path.exists(), f"outputs.timeseries_text not found: {ts_path}"

    def test_coefficients_provenance_echoed_verbatim(self, tmp_path: Path) -> None:
        """Coefficients in out-json must match exactly what was passed as CLI args."""
        out_json = tmp_path / "ds.json"
        out_ts = tmp_path / "ds_ts.txt"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1234.5",
            "--b", "7.89",
            "--c-exp", "0.654",
            "--return-period", "10",
            "--duration", "90",
            "--dt", "5",
            "--r", "0.4",
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr
        meta = json.loads(out_json.read_text(encoding="utf-8"))
        assert meta["coefficients"]["a"] == pytest.approx(1234.5, rel=1e-9)
        assert meta["coefficients"]["b"] == pytest.approx(7.89, rel=1e-9)
        assert meta["coefficients"]["c"] == pytest.approx(0.654, rel=1e-9)
        assert meta["return_period_yr"] == pytest.approx(10.0, rel=1e-9)
        assert meta["form"] == "generic"

    def test_builder_integration_smoke(self, tmp_path: Path) -> None:
        """Drive build_swmm_inp.py with a design-storm --rainfall-json; assert ok + rows > 0.

        Uses the same bundled fixtures as the builder SKILL.md smoke chain.
        This is the integration smoke that validates the full output contract
        is compatible with what build_swmm_inp.py expects.
        """
        smoke = tmp_path / "smoke"
        smoke.mkdir()

        # Step 1: generate merged params (prerequisite for builder)
        landuse_json = smoke / "landuse.json"
        soil_json = smoke / "soil.json"
        merged_params = smoke / "merged_params.json"

        for script, args in [
            (_PARAMS_LANDUSE_SCRIPT, ["--input", str(_LANDUSE_EXAMPLE), "--output", str(landuse_json)]),
            (_PARAMS_SOIL_SCRIPT, ["--input", str(_SOIL_EXAMPLE), "--output", str(soil_json)]),
        ]:
            r = subprocess.run([sys.executable, str(script)] + args, capture_output=True, text=True)
            assert r.returncode == 0, f"{script.name} failed: {r.stderr}"

        r = subprocess.run(
            [sys.executable, str(_PARAMS_MERGE_SCRIPT),
             "--landuse-json", str(landuse_json),
             "--soil-json", str(soil_json),
             "--output", str(merged_params)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"merge_swmm_params failed: {r.stderr}"

        # Step 2: generate Chicago design storm (P=2yr, 60min, generic IDF)
        ds_json = smoke / "ds.json"
        ds_ts = smoke / "ds_ts.txt"
        r = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
            "--r", "0.4",
            "--out-json", str(ds_json),
            "--out-timeseries", str(ds_ts),
        ])
        assert r.returncode == 0, r.stderr

        # Step 3: build SWMM INP with design-storm rainfall-json
        out_inp = smoke / "model.inp"
        out_manifest = smoke / "manifest.json"
        r = subprocess.run(
            [
                sys.executable, str(_BUILD_SWMM_SCRIPT),
                "--subcatchments-csv", str(_SUBCATCHMENTS_EXAMPLE),
                "--params-json", str(merged_params),
                "--network-json", str(_NETWORK_EXAMPLE),
                "--rainfall-json", str(ds_json),
                "--config-json", str(_CONFIG_EXAMPLE),
                "--out-inp", str(out_inp),
                "--out-manifest", str(out_manifest),
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"build_swmm_inp failed: {r.stderr}"

        manifest = json.loads(out_manifest.read_text(encoding="utf-8"))
        assert manifest["ok"] is True
        ts_rows = manifest["counts"]["timeseries_rows"]
        assert ts_rows > 0, f"Expected timeseries_rows > 0, got {ts_rows}"

    def test_alternating_block_from_csv(self, tmp_path: Path) -> None:
        """IDF table via CSV file round-trips correctly (--idf-csv path)."""
        idf_csv = tmp_path / "idf.csv"
        a, b, c, dt = 1000.0, 10.0, 0.7, 5.0
        rows = [(k * dt, a / (k * dt + b) ** c) for k in range(1, 13)]
        idf_csv.write_text(
            "duration_min,intensity_mm_per_hr\n"
            + "".join(f"{d},{i}\n" for d, i in rows),
            encoding="utf-8",
        )

        out_json = tmp_path / "ab.json"
        out_ts = tmp_path / "ab_ts.txt"
        result = _run_design_storm([
            "--method", "alternating_block",
            "--idf-csv", str(idf_csv),
            "--return-period", "2",
            "--duration", "60",
            "--dt", str(dt),
            "--out-json", str(out_json),
            "--out-timeseries", str(out_ts),
        ])
        assert result.returncode == 0, result.stderr
        stdout_obj = json.loads(result.stdout)
        assert stdout_obj["ok"] is True
        assert stdout_obj["rows"] == 12

    def test_default_series_name_format(self, tmp_path: Path) -> None:
        """Default series name follows TS_DESIGN_P<P>Y_<duration>MIN format."""
        out_json = tmp_path / "ds.json"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "120",
            "--dt", "5",
            "--out-json", str(out_json),
            "--out-timeseries", str(tmp_path / "ds_ts.txt"),
        ])
        assert result.returncode == 0, result.stderr
        stdout_obj = json.loads(result.stdout)
        assert stdout_obj["series_name"] == "TS_DESIGN_P2Y_120MIN"

    def test_custom_series_name(self, tmp_path: Path) -> None:
        """Custom --series-name is respected in outputs."""
        out_json = tmp_path / "ds.json"
        result = _run_design_storm([
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
            "--series-name", "MY_STORM",
            "--out-json", str(out_json),
            "--out-timeseries", str(tmp_path / "ds_ts.txt"),
        ])
        assert result.returncode == 0, result.stderr
        stdout_obj = json.loads(result.stdout)
        assert stdout_obj["series_name"] == "MY_STORM"
        meta = json.loads(out_json.read_text(encoding="utf-8"))
        assert meta["series_name"] == "MY_STORM"


# ---------------------------------------------------------------------------
# 5. Determinism: two runs → byte-identical output files
# ---------------------------------------------------------------------------


def _json_content_sha256(path: Path) -> str:
    """SHA-256 of the JSON content with path fields normalised away.

    The out-json embeds ``out_json`` and ``out_timeseries`` as absolute paths,
    which vary across tmp_path invocations.  We strip those before hashing so
    the test checks the *computational* determinism (coefficients, depths,
    interval), not the output file locations.
    """
    obj = json.loads(path.read_text(encoding="utf-8"))
    # Remove fields that legitimately differ across runs (absolute output paths)
    for key in ("out_json", "out_timeseries"):
        obj.pop(key, None)
    if isinstance(obj.get("outputs"), dict):
        obj["outputs"].pop("timeseries_text", None)
    canonical = json.dumps(obj, sort_keys=True, indent=2)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestDeterminism:
    """Same args must produce byte-identical output content across two runs.

    The timeseries file must be byte-identical.
    The out-json must be content-identical (absolute output paths are
    normalised away before comparison — those legitimately differ).
    """

    def _run_pair(self, tmp_path: Path, method_args: list[str]) -> tuple[Path, Path, Path, Path]:
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        run1.mkdir()
        run2.mkdir()

        for run_dir in (run1, run2):
            out_j = run_dir / "out.json"
            out_t = run_dir / "out_ts.txt"
            r = _run_design_storm(
                method_args + ["--out-json", str(out_j), "--out-timeseries", str(out_t)]
            )
            assert r.returncode == 0, r.stderr

        return (
            run1 / "out.json",
            run1 / "out_ts.txt",
            run2 / "out.json",
            run2 / "out_ts.txt",
        )

    def test_chicago_generic_determinism(self, tmp_path: Path) -> None:
        j1, ts1, j2, ts2 = self._run_pair(tmp_path, [
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "1000",
            "--b", "10",
            "--c-exp", "0.7",
            "--return-period", "2",
            "--duration", "120",
            "--dt", "5",
            "--r", "0.4",
        ])
        assert _json_content_sha256(j1) == _json_content_sha256(j2), (
            "out-json content differed between two runs"
        )
        assert _sha256(ts1) == _sha256(ts2), "timeseries differed between two runs"

    def test_chicago_cn_determinism(self, tmp_path: Path) -> None:
        j1, ts1, j2, ts2 = self._run_pair(tmp_path, [
            "--method", "chicago",
            "--form", "CN",
            "--a1", "4.0",
            "--C", "0.7",
            "--b", "8.0",
            "--n", "0.65",
            "--return-period", "5",
            "--duration", "60",
            "--dt", "5",
            "--r", "0.4",
        ])
        assert _json_content_sha256(j1) == _json_content_sha256(j2), "CN out-json content differed"
        assert _sha256(ts1) == _sha256(ts2), "CN timeseries differed"

    def test_alternating_block_determinism(self, tmp_path: Path) -> None:
        idf = json.dumps([
            {"duration_min": float(d), "intensity_mm_per_hr": 1000.0 / (d + 10) ** 0.7}
            for d in range(5, 65, 5)
        ])
        j1, ts1, j2, ts2 = self._run_pair(tmp_path, [
            "--method", "alternating_block",
            "--idf-json", idf,
            "--return-period", "2",
            "--duration", "60",
            "--dt", "5",
        ])
        assert _json_content_sha256(j1) == _json_content_sha256(j2), "alt-block out-json content differed"
        assert _sha256(ts1) == _sha256(ts2), "alt-block timeseries differed"

    def test_custom_series_name_determinism(self, tmp_path: Path) -> None:
        """Custom series name also produces deterministic output."""
        j1, ts1, j2, ts2 = self._run_pair(tmp_path, [
            "--method", "chicago",
            "--form", "generic",
            "--a-coeff", "500",
            "--b", "5",
            "--c-exp", "0.6",
            "--return-period", "10",
            "--duration", "90",
            "--dt", "10",
            "--r", "0.4",
            "--series-name", "MY_CUSTOM_STORM",
        ])
        assert _json_content_sha256(j1) == _json_content_sha256(j2)
        assert _sha256(ts1) == _sha256(ts2)
