#!/usr/bin/env python3
"""Integrated uncertainty source decomposition (issue #55).

This module is the FINAL impl deliverable for the PRD
"Uncertainty and Calibration Strengthening" track. It reads whichever
raw uncertainty outputs are present in ``<run_dir>/09_audit/`` and
emits two files alongside them:

* ``uncertainty_source_summary.md`` — paper-reviewer-facing report with
  five fixed sections + an Evidence Boundary header that lists every
  potential method with ✓/✗ so no method is silently absent.
* ``uncertainty_source_decomposition.json`` — machine-readable mirror
  of the markdown ( ``schema_version == "1.0"`` ).

The function is **pure over the filesystem state**: same inputs always
produce the same outputs, no SWMM execution, no network, no global
state mutation outside the audit dir. Re-invoking the function
overwrites the two output files in-place so the latest state of a run
is always represented.

Inputs consumed (each is optional, all detected by filename):

* ``sensitivity_indices.json`` — Sobol' or Morris (Slice 4 / #49).
* ``posterior_samples.csv`` + ``chain_convergence.json`` — DREAM-ZS
  (Slice 2 / #53).
* ``candidate_calibration.json`` — SCE-UA / DREAM-ZS candidate handover
  (Slice 6 / #54). ``strategy`` distinguishes the method that produced
  it.
* ``rainfall_ensemble_summary.json`` — Rainfall ensemble (Slice 5 /
  #51). ``method`` ∈ {``perturbation``, ``idf``} tells us which sub-
  method (A or B) actually ran.
* ``uncertainty_summary.json`` — MC propagation summary (legacy).

Schema (JSON file)::

    {
      "schema_version": "1.0",
      "generated_at_utc": "...",
      "run_id": "...",
      "evidence_boundary": {
        "sobol":             {"ran": bool, "source": "<rel path or null>"},
        "morris":            {"ran": bool, "source": "<rel path or null>"},
        "dream_zs":          {"ran": bool, "source": "<rel path or null>"},
        "sce_ua":            {"ran": bool, "source": "<rel path or null>"},
        "rainfall_ensemble": {
            "ran": bool,
            "method": "perturbation" | "idf" | null,
            "source": "<rel path or null>"
        },
        "mc_propagation":    {"ran": bool, "source": "<rel path or null>"}
      },
      "output_envelope":         {...} | null,
      "parameter_contribution":  {...} | null,
      "input_contribution":      {...} | null,
      "structural_assumptions":  [<str>, ...],
      "cross_references":        {"<artefact id>": "<rel path>", ...}
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any] | None:
    """Return JSON contents at ``path`` or ``None`` if missing/unreadable.

    We swallow malformed JSON because the caller's responsibility is to
    report "method present" or "method absent"; if a file exists but is
    corrupt, treating it as absent yields a more useful audit signal
    than crashing the whole report.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` (posix-style); fallback to str."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class DecompositionResult:
    """Return value from :func:`decompose`.

    Keeping the two output paths in a small dataclass means callers
    (CLI, tests, audit-pipeline hook) can introspect what was written
    without re-deriving the names.
    """

    markdown_path: Path
    json_path: Path
    payload: dict[str, Any]
    methods_present: list[str]
    methods_absent: list[str]


# ---------------------------------------------------------------------------
# Evidence-boundary detection
# ---------------------------------------------------------------------------


def _detect_evidence(audit_dir: Path, run_dir: Path) -> dict[str, dict[str, Any]]:
    """Return the structured ``evidence_boundary`` dict.

    Each row reports ``ran`` (bool), and a relative-to-run-dir
    ``source`` path when present. The sensitivity-indices file is
    one-of {Sobol', Morris}; the Morris vs Sobol detection key is the
    ``method`` field inside it. Candidate calibration covers both
    SCE-UA and DREAM-ZS strategies; the ``strategy`` field discriminates.
    """
    out: dict[str, dict[str, Any]] = {
        "sobol": {"ran": False, "source": None},
        "morris": {"ran": False, "source": None},
        "dream_zs": {"ran": False, "source": None},
        "sce_ua": {"ran": False, "source": None},
        "rainfall_ensemble": {"ran": False, "method": None, "source": None},
        "mc_propagation": {"ran": False, "source": None},
    }

    sens = _read_json(audit_dir / "sensitivity_indices.json")
    if sens:
        method = str(sens.get("method", "")).lower()
        if method == "sobol":
            out["sobol"] = {
                "ran": True,
                "source": _rel(audit_dir / "sensitivity_indices.json", run_dir),
            }
        elif method == "morris":
            out["morris"] = {
                "ran": True,
                "source": _rel(audit_dir / "sensitivity_indices.json", run_dir),
            }
        # method=="oat" is screening, not a variance-decomposition
        # method; the integrated report does not surface it as a
        # parameter-contribution evidence slot, but we still flag the
        # file in cross-references.

    posterior_csv = audit_dir / "posterior_samples.csv"
    convergence = audit_dir / "chain_convergence.json"
    if posterior_csv.is_file():
        # DREAM-ZS writes both files together; require the CSV (the
        # primary evidence) and let the convergence JSON be optional.
        out["dream_zs"] = {
            "ran": True,
            "source": _rel(posterior_csv, run_dir),
            "convergence_source": _rel(convergence, run_dir) if convergence.is_file() else None,
        }

    candidate = _read_json(audit_dir / "candidate_calibration.json")
    if candidate:
        strategy = str(candidate.get("strategy", "")).lower()
        rel = _rel(audit_dir / "candidate_calibration.json", run_dir)
        if strategy == "sce-ua":
            out["sce_ua"] = {"ran": True, "source": rel}
        elif strategy == "dream-zs":
            # DREAM-ZS already detected via the CSV; record the candidate
            # ref for cross-references but don't overwrite the dream_zs
            # slot's "source".
            pass
        else:
            # Unknown strategy — still flag SCE-UA as the closest mode so
            # the agent doesn't silently lose the candidate evidence.
            out["sce_ua"] = {"ran": True, "source": rel, "strategy_label": strategy or "unknown"}

    rainfall = _read_json(audit_dir / "rainfall_ensemble_summary.json")
    if rainfall:
        rmethod = str(rainfall.get("method", "")).lower()
        out["rainfall_ensemble"] = {
            "ran": True,
            "method": rmethod or None,
            "source": _rel(audit_dir / "rainfall_ensemble_summary.json", run_dir),
        }

    mc = _read_json(audit_dir / "uncertainty_summary.json")
    if mc:
        out["mc_propagation"] = {
            "ran": True,
            "source": _rel(audit_dir / "uncertainty_summary.json", run_dir),
        }

    return out


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_output_envelope(mc: dict[str, Any] | None, rainfall: dict[str, Any] | None) -> dict[str, Any] | None:
    """Combine the MC envelope + rainfall ensemble peak flow envelope.

    Returns ``None`` when neither input is present. When both are
    present we keep them as two separate sub-blocks (they represent
    different perturbation sources, so collapsing them would mislead
    readers).
    """
    if mc is None and rainfall is None:
        return None
    block: dict[str, Any] = {}
    if mc is not None:
        block["mc_propagation"] = {
            "samples": mc.get("samples"),
            "node": mc.get("node"),
            "peak_cms_envelope": mc.get("peak_cms_envelope"),
            "peak_percent_change_envelope": mc.get("peak_percent_change_envelope"),
        }
    if rainfall is not None:
        swmm_stats = rainfall.get("swmm_ensemble_stats") or {}
        block["rainfall_ensemble"] = {
            "method": rainfall.get("method"),
            "n_realisations": rainfall.get("n_realisations"),
            "peak_flow": swmm_stats.get("peak_flow"),
            "total_volume_m3": swmm_stats.get("total_volume_m3"),
        }
    return block


def _sorted_sobol_indices(sens: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return ``indices`` rows sorted by ``S_T_i`` descending.

    Returns an empty list when ``sens`` is None or method != "sobol".
    """
    if not sens or str(sens.get("method", "")).lower() != "sobol":
        return []
    rows: list[dict[str, Any]] = []
    indices = sens.get("indices") or {}
    if not isinstance(indices, Mapping):
        return []
    for name, row in indices.items():
        if not isinstance(row, Mapping):
            continue
        rows.append(
            {
                "parameter": name,
                "S_i": row.get("S_i"),
                "S_i_conf": row.get("S_i_conf"),
                "S_T_i": row.get("S_T_i"),
                "S_T_i_conf": row.get("S_T_i_conf"),
            }
        )
    rows.sort(key=lambda r: float(r.get("S_T_i") or 0.0), reverse=True)
    return rows


def _sorted_morris_indices(sens: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return ``indices`` rows sorted by ``mu_star`` descending."""
    if not sens or str(sens.get("method", "")).lower() != "morris":
        return []
    rows: list[dict[str, Any]] = []
    indices = sens.get("indices") or {}
    if not isinstance(indices, Mapping):
        return []
    for name, row in indices.items():
        if not isinstance(row, Mapping):
            continue
        rows.append(
            {
                "parameter": name,
                "mu": row.get("mu"),
                "mu_star": row.get("mu_star"),
                "sigma": row.get("sigma"),
                "mu_star_conf": row.get("mu_star_conf"),
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("mu_star") or 0.0)), reverse=True)
    return rows


def _build_parameter_contribution(sens: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the "Parameter contribution" block.

    When Sobol' indices are present we surface the sorted ``S_T_i``
    ranking. When only Morris is present we surface the ``mu_star``
    ranking and flag this fact so a paper-reviewer reading the report
    knows the values are screening-quality, not full variance
    decomposition.
    """
    if sens is None:
        return None
    method = str(sens.get("method", "")).lower()
    if method == "sobol":
        return {
            "method": "sobol",
            "sample_budget": sens.get("sample_budget"),
            "sobol_total_effect_sorted": _sorted_sobol_indices(sens),
        }
    if method == "morris":
        return {
            "method": "morris",
            "sample_budget": sens.get("sample_budget"),
            "morris_mu_star_sorted": _sorted_morris_indices(sens),
            "note": (
                "Morris elementary-effects is a screening method; "
                "rankings are reliable but the magnitudes are not "
                "variance-decomposition quality."
            ),
        }
    if method == "oat":
        return {
            "method": "oat",
            "note": "OAT is one-at-a-time screening; not a variance decomposition.",
        }
    return None


def _build_input_contribution(
    rainfall: dict[str, Any] | None,
    parameter_contribution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare rainfall-induced and parameter-induced uncertainty.

    The integrated narrative is "how big is the rainfall envelope
    relative to the variance attributable to parameters?". We surface
    both sides so the reader can eyeball the split; we do **not**
    compute a single % attribution number because that requires an
    apples-to-apples reduction (variance of peak flow under rainfall
    perturbation vs Sobol' total-effect variance), and the underlying
    artefacts are not always emitted on the same target node.
    """
    block: dict[str, Any] = {
        "rainfall_ensemble": None,
        "parameter": None,
        "comparison_note": None,
    }
    if rainfall is not None:
        swmm_stats = rainfall.get("swmm_ensemble_stats") or {}
        rainfall_block: dict[str, Any] = {
            "method": rainfall.get("method"),
            "n_realisations": rainfall.get("n_realisations"),
            "rainfall_stats": rainfall.get("rainfall_ensemble_stats"),
        }
        peak_flow = swmm_stats.get("peak_flow") or {}
        if any(peak_flow.get(k) is not None for k in ("p05", "p50", "p95")):
            rainfall_block["peak_flow_envelope"] = peak_flow
        block["rainfall_ensemble"] = rainfall_block
    if parameter_contribution is not None:
        block["parameter"] = {
            "method": parameter_contribution.get("method"),
            "top": (
                parameter_contribution.get("sobol_total_effect_sorted")
                or parameter_contribution.get("morris_mu_star_sorted")
                or []
            )[:3],
        }
    if block["rainfall_ensemble"] and block["parameter"]:
        block["comparison_note"] = (
            "Rainfall-input uncertainty and parameter uncertainty are both "
            "quantified; compare the rainfall peak-flow envelope width "
            "against the top-ranked parameter's variance contribution to "
            "judge which source dominates for this case."
        )
    elif block["rainfall_ensemble"]:
        block["comparison_note"] = (
            "Only rainfall-input uncertainty was quantified; "
            "parameter contribution was not run."
        )
    elif block["parameter"]:
        block["comparison_note"] = (
            "Only parameter sensitivity was quantified; "
            "rainfall ensemble was not run."
        )
    else:
        block["comparison_note"] = (
            "Neither rainfall-input uncertainty nor a variance-based "
            "parameter sensitivity were run for this case."
        )
    return block


def _structural_assumptions(
    evidence: dict[str, dict[str, Any]],
    candidate: dict[str, Any] | None,
) -> list[str]:
    """Document the assumptions that are NOT quantified by this run.

    The reviewer-facing point is that uncertainty quantification is
    bounded: model-structural uncertainty (which conceptual model is
    correct), boundary-condition uncertainty (downstream BCs, time-
    series gaps), and observation-noise uncertainty are not propagated
    by any of the methods listed in the Evidence Boundary table.
    """
    items: list[str] = [
        "Model-structural uncertainty (choice of conceptual model, e.g. "
        "kinematic vs dynamic wave) is not quantified.",
        "Boundary-condition uncertainty (downstream stage, lateral "
        "inflows) is not quantified.",
        "Observation-noise uncertainty is not propagated through the "
        "likelihood — DREAM-ZS treats the observed series as exact.",
    ]
    if not evidence["rainfall_ensemble"]["ran"]:
        items.append(
            "Rainfall-input uncertainty was not quantified (no rainfall "
            "ensemble run); the report is parameter-uncertainty-only."
        )
    if not evidence["sobol"]["ran"] and not evidence["morris"]["ran"]:
        items.append(
            "No global sensitivity / variance decomposition was run; "
            "parameter contribution rankings are unavailable."
        )
    if not evidence["dream_zs"]["ran"] and not evidence["sce_ua"]["ran"]:
        items.append(
            "No calibration (DREAM-ZS or SCE-UA) was run; posterior or "
            "best-fit parameter sets are unavailable."
        )
    if candidate and candidate.get("evidence_boundary") == "candidate_not_accepted_yet":
        items.append(
            "The calibration candidate is recorded but the canonical INP "
            "has not been patched yet; run `aiswmm calibration accept "
            "<run_dir>` to make the candidate effective."
        )
    return items


def _build_cross_references(audit_dir: Path, run_dir: Path) -> dict[str, str]:
    """Map artefact-id -> relative path under ``<run_dir>`` for the markdown.

    Every artefact we *might* link from the report is included if it
    exists on disk. This keeps the markdown navigation reliable across
    partial runs without requiring callers to re-derive paths.
    """
    refs: dict[str, str] = {}
    candidates = [
        ("sensitivity_indices", audit_dir / "sensitivity_indices.json"),
        ("posterior_samples", audit_dir / "posterior_samples.csv"),
        ("chain_convergence", audit_dir / "chain_convergence.json"),
        ("posterior_correlation_plot", audit_dir / "posterior_correlation.png"),
        ("rainfall_ensemble_summary", audit_dir / "rainfall_ensemble_summary.json"),
        ("candidate_calibration", audit_dir / "candidate_calibration.json"),
        ("calibration_summary", audit_dir / "calibration_summary.json"),
        ("uncertainty_summary", audit_dir / "uncertainty_summary.json"),
        ("experiment_provenance", audit_dir / "experiment_provenance.json"),
        ("experiment_note", audit_dir / "experiment_note.md"),
    ]
    for key, path in candidates:
        if path.is_file():
            refs[key] = _rel(path, run_dir)
    # Marginal posterior PNGs are auto-discovered (one per parameter)
    for png in sorted(audit_dir.glob("posterior_*.png")):
        if png.name == "posterior_correlation.png":
            continue
        refs[png.stem] = _rel(png, run_dir)
    return refs


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


_BOUNDARY_LABELS = [
    ("sobol", "Sobol' SA       "),
    ("morris", "Morris SA       "),
    ("dream_zs", "DREAM-ZS        "),
    ("sce_ua", "SCE-UA          "),
    ("rainfall_ensemble", "Rainfall ensemble"),
    ("mc_propagation", "MC propagation  "),
]


def _render_evidence_boundary(evidence: dict[str, dict[str, Any]]) -> str:
    """Render the ``Evidence boundary:`` code block.

    The label column is pre-padded so the ``:`` aligns vertically; the
    "Rainfall ensemble" row is intentionally one column wider than the
    others because the label itself is longer than the rest. Tests
    assert against the literal aligned text, so do not shorten any of
    the padded labels.
    """
    lines = ["```", "Evidence boundary:"]
    for key, label in _BOUNDARY_LABELS:
        row = evidence[key]
        mark = "✓" if row.get("ran") else "✗"
        suffix = ""
        if key == "rainfall_ensemble" and row.get("ran"):
            method = row.get("method")
            if method == "perturbation":
                suffix = " method A only (method B not run)"
            elif method == "idf":
                suffix = " method B only (method A not run)"
            else:
                suffix = " ran"
        elif row.get("ran"):
            source = row.get("source")
            suffix = f" ran ({Path(source).name})" if source else " ran"
        else:
            suffix = " not run"
        lines.append(f"  {label}: {mark}{suffix}")
    lines.append("```")
    return "\n".join(lines)


def _fmt_num(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _render_output_envelope(block: dict[str, Any] | None) -> str:
    parts = ["## Output uncertainty envelope", ""]
    if not block:
        parts.append("_No MC propagation or rainfall-driven SWMM ensemble outputs are present._")
        parts.append("")
        return "\n".join(parts)
    mc = block.get("mc_propagation")
    if mc:
        envelope = mc.get("peak_cms_envelope") or {}
        parts.append(f"**Monte Carlo parameter propagation** ({mc.get('samples') or '?'} samples at node `{mc.get('node') or '?'}`).")
        parts.append("")
        parts.append("| Quantile | Peak flow (cms) |")
        parts.append("|---|---|")
        parts.append(f"| p05 | {_fmt_num(envelope.get('p05'))} |")
        parts.append(f"| p50 | {_fmt_num(envelope.get('p50'))} |")
        parts.append(f"| p95 | {_fmt_num(envelope.get('p95'))} |")
        parts.append("")
    rainfall = block.get("rainfall_ensemble")
    if rainfall and rainfall.get("peak_flow"):
        peak = rainfall["peak_flow"]
        parts.append(
            f"**Rainfall ensemble — SWMM-propagated peak flow** "
            f"({rainfall.get('n_realisations') or '?'} realisations, "
            f"method `{rainfall.get('method') or '?'}`)."
        )
        parts.append("")
        parts.append("| Quantile | Peak flow (cms) |")
        parts.append("|---|---|")
        parts.append(f"| p05 | {_fmt_num(peak.get('p05'))} |")
        parts.append(f"| p50 | {_fmt_num(peak.get('p50'))} |")
        parts.append(f"| p95 | {_fmt_num(peak.get('p95'))} |")
        parts.append("")
    return "\n".join(parts)


def _render_parameter_contribution(block: dict[str, Any] | None) -> str:
    parts = ["## Parameter contribution (Sobol' total-effect, sorted)", ""]
    if not block:
        parts.append("_No variance-based sensitivity analysis was run for this case._")
        parts.append("")
        return "\n".join(parts)
    method = block.get("method")
    if method == "sobol":
        parts.append(f"Sample budget: `N*(2k+2)` = {block.get('sample_budget')}.")
        parts.append("")
        parts.append("| Parameter | S_T_i | 95% CI | S_i (first-order) |")
        parts.append("|---|---|---|---|")
        for row in block.get("sobol_total_effect_sorted", []):
            parts.append(
                f"| `{row['parameter']}` | {_fmt_num(row.get('S_T_i'))} | "
                f"±{_fmt_num(row.get('S_T_i_conf'))} | {_fmt_num(row.get('S_i'))} |"
            )
        parts.append("")
    elif method == "morris":
        parts.append(f"Sample budget: `r*(k+1)` = {block.get('sample_budget')}.")
        parts.append("")
        parts.append(
            "_Note: this case ran Morris screening, not Sobol' decomposition. "
            "Rankings are reliable but magnitudes are not variance-quality._"
        )
        parts.append("")
        parts.append("| Parameter | mu_star | sigma | mu_star_conf |")
        parts.append("|---|---|---|---|")
        for row in block.get("morris_mu_star_sorted", []):
            parts.append(
                f"| `{row['parameter']}` | {_fmt_num(row.get('mu_star'))} | "
                f"{_fmt_num(row.get('sigma'))} | ±{_fmt_num(row.get('mu_star_conf'))} |"
            )
        parts.append("")
    else:
        parts.append("_Only OAT screening was run; parameter contribution is unavailable._")
        parts.append("")
    return "\n".join(parts)


def _render_input_contribution(block: dict[str, Any]) -> str:
    parts = ["## Input contribution (rainfall ensemble vs parameter)", ""]
    rainfall = block.get("rainfall_ensemble")
    parameter = block.get("parameter")
    if rainfall:
        peak = rainfall.get("peak_flow_envelope") or {}
        parts.append(
            f"**Rainfall ensemble** — method `{rainfall.get('method')}`, "
            f"{rainfall.get('n_realisations')} realisations."
        )
        if peak:
            parts.append("")
            parts.append(
                f"- peak-flow envelope (cms): "
                f"p05 = {_fmt_num(peak.get('p05'))}, "
                f"p50 = {_fmt_num(peak.get('p50'))}, "
                f"p95 = {_fmt_num(peak.get('p95'))}"
            )
        rainfall_stats = rainfall.get("rainfall_stats") or {}
        intensity = rainfall_stats.get("peak_intensity_mm_per_hr") or {}
        if intensity:
            parts.append(
                f"- rainfall peak-intensity envelope (mm/hr): "
                f"p05 = {_fmt_num(intensity.get('p05'))}, "
                f"p50 = {_fmt_num(intensity.get('p50'))}, "
                f"p95 = {_fmt_num(intensity.get('p95'))}"
            )
        parts.append("")
    if parameter and parameter.get("top"):
        parts.append(f"**Parameter contribution top-3** (method: `{parameter.get('method')}`).")
        parts.append("")
        for row in parameter["top"]:
            label = row.get("parameter")
            if "S_T_i" in row:
                parts.append(f"- `{label}` (S_T_i = {_fmt_num(row.get('S_T_i'))})")
            elif "mu_star" in row:
                parts.append(f"- `{label}` (mu_star = {_fmt_num(row.get('mu_star'))})")
            else:
                parts.append(f"- `{label}`")
        parts.append("")
    note = block.get("comparison_note")
    if note:
        parts.append(f"_{note}_")
        parts.append("")
    return "\n".join(parts)


def _render_structural_assumptions(items: list[str]) -> str:
    parts = ["## Structural assumptions (not quantified)", ""]
    for item in items:
        parts.append(f"- {item}")
    parts.append("")
    return "\n".join(parts)


def _render_cross_references(refs: dict[str, str]) -> str:
    parts = ["## Cross-references", ""]
    if not refs:
        parts.append("_No raw uncertainty artefacts on disk for this run._")
        parts.append("")
        return "\n".join(parts)
    parts.append("| Artefact | Path (relative to run dir) |")
    parts.append("|---|---|")
    for key in sorted(refs):
        parts.append(f"| `{key}` | `{refs[key]}` |")
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Top-level decompose()
# ---------------------------------------------------------------------------


def decompose(run_dir: Path | str) -> DecompositionResult:
    """Build and write the integrated uncertainty source decomposition.

    Always overwrites ``<run_dir>/09_audit/uncertainty_source_summary.md``
    and ``<run_dir>/09_audit/uncertainty_source_decomposition.json``;
    callers that want a single source of truth on disk should run this
    after every relevant artefact is updated.
    """
    run_dir = Path(run_dir)
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)

    sens = _read_json(audit / "sensitivity_indices.json")
    rainfall = _read_json(audit / "rainfall_ensemble_summary.json")
    candidate = _read_json(audit / "candidate_calibration.json")
    mc = _read_json(audit / "uncertainty_summary.json")
    provenance = _read_json(audit / "experiment_provenance.json") or {}

    evidence = _detect_evidence(audit, run_dir)
    output_envelope = _build_output_envelope(mc, rainfall)
    parameter_contribution = _build_parameter_contribution(sens)
    input_contribution = _build_input_contribution(rainfall, parameter_contribution)
    structural = _structural_assumptions(evidence, candidate)
    refs = _build_cross_references(audit, run_dir)

    run_id = provenance.get("run_id") or run_dir.name

    methods_present = [
        label
        for key, label in _BOUNDARY_LABELS
        if evidence[key].get("ran")
    ]
    methods_absent = [
        label
        for key, label in _BOUNDARY_LABELS
        if not evidence[key].get("ran")
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "run_id": run_id,
        "evidence_boundary": evidence,
        "output_envelope": output_envelope,
        "parameter_contribution": parameter_contribution,
        "input_contribution": input_contribution,
        "structural_assumptions": structural,
        "cross_references": refs,
    }

    md_parts = [
        f"# Uncertainty source decomposition — `{run_id}`",
        "",
        "Generated by `skills/swmm-uncertainty/scripts/source_decomposition.py`.",
        "This report integrates the raw uncertainty outputs present in "
        f"`{_rel(audit, run_dir)}` into a single paper-reviewer-facing summary.",
        "",
        _render_evidence_boundary(evidence),
        "",
        _render_output_envelope(output_envelope),
        _render_parameter_contribution(parameter_contribution),
        _render_input_contribution(input_contribution),
        _render_structural_assumptions(structural),
        _render_cross_references(refs),
    ]
    markdown = "\n".join(md_parts).rstrip() + "\n"

    md_path = audit / "uncertainty_source_summary.md"
    json_path = audit / "uncertainty_source_decomposition.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return DecompositionResult(
        markdown_path=md_path,
        json_path=json_path,
        payload=payload,
        methods_present=methods_present,
        methods_absent=methods_absent,
    )


# ---------------------------------------------------------------------------
# Script entry point (for the MCP server and dev invocations)
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integrate uncertainty raw outputs in <run_dir>/09_audit/ into "
        "uncertainty_source_summary.md + uncertainty_source_decomposition.json.",
    )
    parser.add_argument("run_dir", type=Path, help="Path to the run directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.run_dir.is_dir():
        print(f"error: run_dir is not a directory: {args.run_dir}", file=sys.stderr)
        return 1
    result = decompose(args.run_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": SCHEMA_VERSION,
                "markdown_path": str(result.markdown_path),
                "json_path": str(result.json_path),
                "methods_present": result.methods_present,
                "methods_absent": result.methods_absent,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
