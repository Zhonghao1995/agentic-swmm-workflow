#!/usr/bin/env python3
"""Candidate-handover artefacts for calibration runs (issue #54).

After any calibration strategy (SCE-UA, DREAM-ZS, random, lhs,
adaptive) finishes, the scaffold must *never* patch the canonical INP
on disk. Instead it writes three artefacts to ``<run_dir>/09_audit/``:

* ``candidate_calibration.json`` — best params + metrics + KGE
  decomposition + secondary metrics + ``evidence_boundary ==
  "candidate_not_accepted_yet"`` + the SHA256 of the patch file (used
  by ``aiswmm calibration accept`` for tamper detection) + (DREAM
  only) a reference to ``posterior_samples.csv``.
* ``candidate_inp_patch.json`` — list of one-line INP edits. Each row
  records ``param``, ``section``, ``object``, ``field_index``,
  ``old_value`` (what is in the canonical INP right now) and
  ``new_value`` (the calibrated value). This is enough for
  ``aiswmm calibration accept`` to re-apply the patch using the
  existing :mod:`inp_patch` machinery.
* ``calibration_report.md`` — human-readable summary with the KGE
  decomposition table, secondary-metrics table, strategy + iteration
  count, and references to the convergence trace / posterior plots
  when applicable.

The agent calls :func:`write_candidate_artefacts` at the end of every
strategy branch; ``aiswmm calibration accept`` is the only path that
turns the candidate into an actual on-disk change to the canonical
INP.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


EVIDENCE_BOUNDARY = "candidate_not_accepted_yet"
CANDIDATE_PATCH_SCHEMA = "1.0"
CANDIDATE_SCHEMA = "1.0"

CANDIDATE_FILENAME = "candidate_calibration.json"
PATCH_FILENAME = "candidate_inp_patch.json"
REPORT_FILENAME = "calibration_report.md"


# ---------------------------------------------------------------------------
# INP patch extraction
# ---------------------------------------------------------------------------


def _strip_inline_comment(line: str) -> str:
    """Return the code portion of an INP line (drop ``;...`` comment tail)."""
    code, *_ = line.split(";", 1)
    return code


def _find_old_value(
    text: str, section: str, obj_name: str, field_index: int
) -> str | None:
    """Walk an INP file and pull the token at ``[section] <object> ... field_index``.

    Mirrors :func:`inp_patch.patch_inp_text` so the diff we emit can be
    applied by the same logic. Returns ``None`` if the section/object
    row is not found or the field index is out of range.
    """
    section_norm = section.upper()
    current_section: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped.upper()
            continue
        if stripped.startswith(";"):
            continue
        if current_section != section_norm:
            continue
        code = _strip_inline_comment(raw)
        tokens = code.split()
        if not tokens or tokens[0] != obj_name:
            continue
        if field_index >= len(tokens):
            return None
        return tokens[field_index]
    return None


def build_inp_patch(
    inp_text: str,
    patch_map: Mapping[str, Mapping[str, Any]],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the ``candidate_inp_patch.json`` payload.

    For each calibrated parameter we look up its row in ``inp_text``
    using the same selector contract as :func:`inp_patch.patch_inp_text`
    (section + object + zero-based field_index). The resulting JSON is
    line-oriented so a human auditor can read it and ``aiswmm
    calibration accept`` can reconstitute a ``{name: value}`` map for
    :func:`inp_patch.patch_inp_text`.

    Raises ``KeyError`` if any parameter is missing from ``patch_map``
    (matches the loud failure mode in :func:`inp_patch.patch_inp_text`).
    """
    missing_keys = sorted(set(params) - set(patch_map))
    if missing_keys:
        raise KeyError(
            "Parameters missing from patch_map: " + ", ".join(missing_keys)
        )
    edits: list[dict[str, Any]] = []
    for name in params:
        spec = patch_map[name]
        section = str(spec["section"])
        obj_name = str(spec["object"])
        field_index = int(spec["field_index"])
        old_value = _find_old_value(inp_text, section, obj_name, field_index)
        edits.append(
            {
                "param": name,
                "section": section,
                "object": obj_name,
                "field_index": field_index,
                "old_value": old_value,
                "new_value": str(params[name]),
            }
        )
    return {"schema_version": CANDIDATE_PATCH_SCHEMA, "edits": edits}


# ---------------------------------------------------------------------------
# SHA helpers (tamper-detection seam for `aiswmm calibration accept`)
# ---------------------------------------------------------------------------


def sha256_of_canonical_json(payload: Any) -> str:
    """SHA256 of ``json.dumps(payload, sort_keys=True, indent=2)``.

    The accept CLI re-computes this against the *on-disk* patch file
    and refuses the operation if it does not match the SHA recorded
    inside ``candidate_calibration.json``. ``sort_keys=True`` lets us
    canonicalise dict ordering across writers; ``indent=2`` matches the
    on-disk format so a human-readable file produces the same SHA as
    the in-memory dict.
    """
    text = json.dumps(payload, sort_keys=True, indent=2)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path: Path) -> str:
    """SHA256 of the raw bytes of ``path`` (used for canonical INP)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# JSON writers
# ---------------------------------------------------------------------------


def _write_json_canonical(path: Path, payload: Any) -> None:
    """Write ``payload`` with the same formatting we hash over.

    Keeping the read-path and write-path text-identical is what makes
    :func:`sha256_of_canonical_json` and a re-read of the file produce
    the same digest.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _format_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{as_float:.{digits}f}"


def _render_kge_decomposition_table(decomp: Mapping[str, Any]) -> str:
    rows = [
        "| Component | Value |",
        "|---|---|",
        f"| r (correlation) | {_format_number(decomp.get('r'))} |",
        f"| alpha (variability ratio) | {_format_number(decomp.get('alpha'))} |",
        f"| beta (bias ratio) | {_format_number(decomp.get('beta'))} |",
    ]
    return "\n".join(rows)


def _render_secondary_table(secondary: Mapping[str, Any]) -> str:
    rows = [
        "| Metric | Value |",
        "|---|---|",
        f"| NSE | {_format_number(secondary.get('nse'))} |",
        f"| PBIAS (%) | {_format_number(secondary.get('pbias_pct'))} |",
        f"| RMSE | {_format_number(secondary.get('rmse'))} |",
        f"| Peak flow error (rel) | {_format_number(secondary.get('peak_error_rel'))} |",
        f"| Peak timing error (min) | {_format_number(secondary.get('peak_timing_min'))} |",
    ]
    return "\n".join(rows)


def _render_best_params_table(params: Mapping[str, Any]) -> str:
    if not params:
        return "_No parameter values reported._"
    rows = ["| Parameter | Value |", "|---|---|"]
    for name in sorted(params):
        rows.append(f"| {name} | {_format_number(params[name])} |")
    return "\n".join(rows)


def _render_posterior_section(
    summary: Mapping[str, Any],
    refs: Mapping[str, str],
) -> str:
    posterior = summary.get("posterior_summary")
    if not isinstance(posterior, Mapping):
        return ""
    lines: list[str] = ["## Posterior (DREAM-ZS)", ""]
    lines.append(
        f"- chains: {posterior.get('n_chains')} (requested: "
        f"{posterior.get('n_chains_requested')})"
    )
    lines.append(
        f"- post-burn-in samples: {posterior.get('n_samples_post_burnin')}"
    )
    lines.append(
        f"- Gelman-Rubin Rhat threshold: {posterior.get('rhat_threshold')}, "
        f"converged: {posterior.get('converged')}"
    )
    rhat = posterior.get("rhat") or {}
    if isinstance(rhat, Mapping) and rhat:
        lines.append("")
        lines.append("| Parameter | Rhat |")
        lines.append("|---|---|")
        for name in sorted(rhat):
            lines.append(f"| {name} | {_format_number(rhat[name])} |")
    posterior_csv = refs.get("posterior_samples_csv")
    if posterior_csv:
        lines.append("")
        lines.append(f"- posterior samples: `{posterior_csv}`")
    correlation_png = refs.get("posterior_correlation_png")
    if correlation_png:
        lines.append(f"- correlation plot: `{correlation_png}`")
    lines.append("")
    return "\n".join(lines)


def render_calibration_report(
    *,
    summary: Mapping[str, Any],
    best_params: Mapping[str, Any],
    candidate_inp_patch_sha256: str,
    refs: Mapping[str, str],
) -> str:
    """Build the markdown body for ``calibration_report.md``.

    Returned as a string so callers can write it (the writer also does
    this, but exposing the pure function keeps it cheap to unit-test).
    """
    strategy = str(summary.get("strategy", "unknown"))
    iterations = summary.get("iterations")
    primary = summary.get("primary_value")
    convergence_ref = refs.get("convergence_csv") or summary.get("convergence_trace_ref")

    parts: list[str] = []
    parts.append("# Calibration candidate report\n")
    parts.append(
        "> **Evidence boundary**: Candidate not accepted yet. The canonical "
        "INP on disk has not been modified. Run `aiswmm calibration accept "
        "<run_dir>` to apply the recorded patch and record a `human_decisions` "
        "entry on this run.\n"
    )
    parts.append("## Summary\n")
    parts.append(f"- strategy: `{strategy}`")
    parts.append(f"- primary objective: `{summary.get('primary_objective', 'kge')}`")
    parts.append(f"- primary value: {_format_number(primary)}")
    if iterations is not None:
        parts.append(f"- iterations: {iterations}")
    parts.append(f"- candidate INP patch SHA256: `{candidate_inp_patch_sha256}`")
    if convergence_ref:
        parts.append(f"- convergence trace: `{convergence_ref}`")
    parts.append("")
    parts.append("## KGE decomposition\n")
    parts.append(_render_kge_decomposition_table(summary.get("kge_decomposition") or {}))
    parts.append("")
    parts.append("## Secondary metrics\n")
    parts.append(_render_secondary_table(summary.get("secondary_metrics") or {}))
    parts.append("")
    parts.append("## Best parameters\n")
    parts.append(_render_best_params_table(best_params))
    parts.append("")
    posterior_block = _render_posterior_section(summary, refs)
    if posterior_block:
        parts.append(posterior_block)
    parts.append(
        "_Generated by the swmm-calibration candidate writer; consult "
        "`candidate_calibration.json` for machine-readable evidence._\n"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def _build_candidate_payload(
    *,
    summary: Mapping[str, Any],
    best_params: Mapping[str, Any],
    patch_sha256: str,
    canonical_inp: Path,
    canonical_inp_sha256: str,
    extra_refs: Mapping[str, str],
) -> dict[str, Any]:
    """Assemble the dict that gets written to candidate_calibration.json."""
    payload: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA,
        "evidence_boundary": EVIDENCE_BOUNDARY,
        "generated_at_utc": _utc_now_iso(),
        "strategy": summary.get("strategy"),
        "primary_objective": summary.get("primary_objective", "kge"),
        "primary_value": summary.get("primary_value"),
        "iterations": summary.get("iterations"),
        "kge_decomposition": summary.get("kge_decomposition"),
        "secondary_metrics": summary.get("secondary_metrics"),
        "best_params": dict(best_params),
        "candidate_inp_patch_ref": PATCH_FILENAME,
        "candidate_inp_patch_sha256": patch_sha256,
        "canonical_inp_ref": str(canonical_inp),
        "canonical_inp_sha256_at_candidate_time": canonical_inp_sha256,
        "convergence_trace_ref": (
            extra_refs.get("convergence_csv")
            or summary.get("convergence_trace_ref")
        ),
    }
    # DREAM-only extras — only embed if present in the summary so the
    # candidate file stays minimal for SCE-UA.
    if "posterior_summary" in summary:
        payload["posterior_summary"] = summary["posterior_summary"]
    posterior_samples_ref = extra_refs.get("posterior_samples_csv")
    if posterior_samples_ref:
        payload["posterior_samples_ref"] = posterior_samples_ref
    posterior_correlation_ref = extra_refs.get("posterior_correlation_png")
    if posterior_correlation_ref:
        payload["posterior_correlation_ref"] = posterior_correlation_ref
    return payload


def write_candidate_artefacts(
    *,
    run_dir: Path,
    canonical_inp: Path,
    patch_map: Mapping[str, Mapping[str, Any]],
    best_params: Mapping[str, Any],
    summary: Mapping[str, Any],
    extra_refs: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Emit the three candidate artefacts into ``<run_dir>/09_audit/``.

    The canonical INP at ``canonical_inp`` is **read** to extract the
    old values for the patch diff; this function never writes to it.
    """
    refs: dict[str, str] = dict(extra_refs or {})
    audit_dir = Path(run_dir) / "09_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    canonical_inp_text = Path(canonical_inp).read_text(errors="ignore")
    canonical_inp_sha = sha256_of_file(Path(canonical_inp))

    patch_payload = build_inp_patch(canonical_inp_text, patch_map, best_params)
    patch_path = audit_dir / PATCH_FILENAME
    _write_json_canonical(patch_path, patch_payload)
    # Hash the on-disk text (without trailing newline) so the candidate
    # and the recompute by ``aiswmm calibration accept`` line up.
    patch_sha = sha256_of_canonical_json(patch_payload)

    candidate_payload = _build_candidate_payload(
        summary=summary,
        best_params=best_params,
        patch_sha256=patch_sha,
        canonical_inp=canonical_inp,
        canonical_inp_sha256=canonical_inp_sha,
        extra_refs=refs,
    )
    candidate_path = audit_dir / CANDIDATE_FILENAME
    _write_json_canonical(candidate_path, candidate_payload)

    report_text = render_calibration_report(
        summary=summary,
        best_params=best_params,
        candidate_inp_patch_sha256=patch_sha,
        refs=refs,
    )
    report_path = audit_dir / REPORT_FILENAME
    report_path.write_text(report_text, encoding="utf-8")

    return {
        "candidate_path": str(candidate_path),
        "patch_path": str(patch_path),
        "report_path": str(report_path),
        "candidate_inp_patch_sha256": patch_sha,
        "canonical_inp_sha256": canonical_inp_sha,
    }


# ---------------------------------------------------------------------------
# Reader helpers (used by ``aiswmm calibration accept``)
# ---------------------------------------------------------------------------


def read_candidate(run_dir: Path) -> dict[str, Any]:
    """Return the parsed ``candidate_calibration.json`` for ``run_dir``.

    Raises ``FileNotFoundError`` if it is missing — callers must catch
    this and turn it into the accept CLI's "no candidate" refusal.
    """
    path = Path(run_dir) / "09_audit" / CANDIDATE_FILENAME
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def read_patch(run_dir: Path) -> dict[str, Any]:
    """Return the parsed ``candidate_inp_patch.json`` for ``run_dir``.

    Raises ``FileNotFoundError`` if it is missing.
    """
    path = Path(run_dir) / "09_audit" / PATCH_FILENAME
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def patch_to_params(patch: Mapping[str, Any]) -> dict[str, Any]:
    """Convert ``candidate_inp_patch.json`` into ``{param: new_value}``.

    The result is the input shape :func:`inp_patch.patch_inp_text`
    expects; the accept CLI feeds it straight in.
    """
    edits: Iterable[Mapping[str, Any]] = patch.get("edits") or []
    return {edit["param"]: edit["new_value"] for edit in edits}
