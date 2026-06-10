#!/usr/bin/env python3
"""Generate a Word (.docx) deliverable from an audited SWMM run directory.

Usage
-----
    python generate_report.py --run-dir <path> [--out <path.docx>] [--template <yaml>]

Portability rule: this script imports ONLY stdlib + python-docx + PyYAML.
It must NEVER import agentic_swmm (skill-script portability requirement).
"""

import argparse
import glob
import json
import os
import sys

# ---------------------------------------------------------------------------
# Guard optional dependency up-front so the error surfaces immediately.
# ---------------------------------------------------------------------------
try:
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    print("python-docx is required: pip install 'aiswmm[report]'", file=sys.stderr)
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install PyYAML", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    """Load JSON; return empty dict if file absent."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_template(template_path: str) -> dict:
    """Load a YAML (or JSON) template file by extension."""
    ext = os.path.splitext(template_path)[1].lower()
    with open(template_path, encoding="utf-8") as f:
        if ext in (".yaml", ".yml"):
            return yaml.safe_load(f)
        elif ext == ".json":
            return json.load(f)
        else:
            raise ValueError(f"Unsupported template extension: {ext!r}. Use .yaml or .json.")


def _default_template_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "templates", "default.yaml")


def _na(value) -> str:
    """Return 'n/a' for None/missing values."""
    if value is None:
        return "n/a"
    return str(value)


# ---------------------------------------------------------------------------
# Section renderers
# Each renderer has signature: (doc, section_cfg, artifacts) -> None
# `artifacts` is a dict carrying the loaded JSON data and discovered PNG paths.
# ---------------------------------------------------------------------------

def _render_cover(doc: Document, cfg: dict, artifacts: dict) -> None:
    prov = artifacts.get("provenance", {})
    manifest = artifacts.get("manifest", {})

    title = cfg.get("title", "Run Audit Deliverable")
    subtitle = cfg.get("subtitle", "")

    heading = doc.add_heading(title, level=1)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if subtitle:
        p = doc.add_paragraph(subtitle)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run_id = prov.get("run_id") or manifest.get("run_dir", "")
    generated_at = prov.get("generated_at_utc", "")
    swmm_version = (prov.get("tools") or {}).get("swmm5_version", "")

    info_table = doc.add_table(rows=3, cols=2)
    info_table.style = "Table Grid"
    rows_data = [
        ("Run ID", _na(run_id)),
        ("Generated at (UTC)", _na(generated_at)),
        ("SWMM version", _na(swmm_version)),
    ]
    for row, (label, value) in zip(info_table.rows, rows_data):
        row.cells[0].text = label
        row.cells[1].text = value

    doc.add_paragraph()


def _render_run_summary(doc: Document, cfg: dict, artifacts: dict) -> None:
    prov = artifacts.get("provenance", {})
    manifest = artifacts.get("manifest", {})

    doc.add_heading(cfg.get("title", "Run Summary"), level=2)

    metrics = prov.get("metrics", {})
    peak_flow_obj = metrics.get("peak_flow") or {}
    peak_flow_val = peak_flow_obj.get("value") if isinstance(peak_flow_obj, dict) else None
    time_of_peak = peak_flow_obj.get("time_hhmm") if isinstance(peak_flow_obj, dict) else None
    continuity_error = metrics.get("continuity_error")
    return_code = metrics.get("swmm_return_code")

    # Fall back to manifest qoi when provenance metrics absent
    if peak_flow_val is None:
        qoi = manifest.get("qoi", {})
        peak_flow_val = qoi.get("peak_flow_cms_at_O1")
        time_of_peak = qoi.get("time_of_peak_hhmm")

    columns = cfg.get("columns", ["Metric", "Value", "Unit"])
    rows_cfg = cfg.get("rows", [])

    table = doc.add_table(rows=1 + len(rows_cfg), cols=len(columns))
    table.style = "Table Grid"
    header_row = table.rows[0]
    for i, col_name in enumerate(columns):
        header_row.cells[i].text = col_name

    value_map = {
        "peak_flow": (_na(peak_flow_val), "CMS"),
        "time_of_peak": (_na(time_of_peak), "hh:mm"),
        "continuity_error": (_na(continuity_error), "%"),
        "return_code": (_na(return_code), ""),
    }

    for i, row_cfg in enumerate(rows_cfg):
        key = row_cfg.get("key", "")
        label = row_cfg.get("label", key)
        unit = row_cfg.get("unit", "")
        val, default_unit = value_map.get(key, ("n/a", unit))
        data_row = table.rows[i + 1]
        data_row.cells[0].text = label
        data_row.cells[1].text = val
        if len(columns) > 2:
            data_row.cells[2].text = unit or default_unit

    doc.add_paragraph()


def _render_model_description(doc: Document, cfg: dict, artifacts: dict) -> None:
    manifest = artifacts.get("manifest", {})

    doc.add_heading(cfg.get("title", "Model Description"), level=2)

    basin_area = manifest.get("basin_area_ha")
    sim_start = manifest.get("sim_start")
    sim_end = manifest.get("sim_end")
    landuse_params = manifest.get("landuse_params", {}) or {}
    green_ampt = manifest.get("green_ampt_params", {}) or {}

    value_map = {
        "basin_area_ha": _na(basin_area),
        "sim_start": _na(sim_start),
        "sim_end": _na(sim_end),
        "imperv_pct": _na(landuse_params.get("imperv_pct")),
        "ksat_mm_per_hr": _na(green_ampt.get("ksat_mm_per_hr")),
        "suction_mm": _na(green_ampt.get("suction_mm")),
    }

    columns = cfg.get("columns", ["Parameter", "Value"])
    rows_cfg = cfg.get("rows", [])

    table = doc.add_table(rows=1 + len(rows_cfg), cols=len(columns))
    table.style = "Table Grid"
    header_row = table.rows[0]
    for i, col_name in enumerate(columns):
        header_row.cells[i].text = col_name

    for i, row_cfg in enumerate(rows_cfg):
        key = row_cfg.get("key", "")
        label = row_cfg.get("label", key)
        unit = row_cfg.get("unit", "")
        val = value_map.get(key, "n/a")
        data_row = table.rows[i + 1]
        data_row.cells[0].text = f"{label} ({unit})" if unit else label
        data_row.cells[1].text = val

    doc.add_paragraph()


def _render_qa_gates(doc: Document, cfg: dict, artifacts: dict) -> None:
    prov = artifacts.get("provenance", {})

    doc.add_heading(cfg.get("title", "QA Gates"), level=2)

    qa = prov.get("qa", {})
    checks = qa.get("checks", [])
    pass_label = cfg.get("pass_label", "PASS")
    fail_label = cfg.get("fail_label", "FAIL")

    columns = cfg.get("columns", ["Gate ID", "Status", "Detail"])

    table = doc.add_table(rows=1 + len(checks), cols=len(columns))
    table.style = "Table Grid"
    header_row = table.rows[0]
    for i, col_name in enumerate(columns):
        header_row.cells[i].text = col_name

    for i, check in enumerate(checks):
        data_row = table.rows[i + 1]
        data_row.cells[0].text = _na(check.get("id"))
        status = pass_label if check.get("ok") else fail_label
        data_row.cells[1].text = status
        if len(columns) > 2:
            data_row.cells[2].text = _na(check.get("detail"))

    if not checks:
        p = doc.add_paragraph("No QA checks recorded.")
        p.italic = True

    doc.add_paragraph()


def _render_figures(doc: Document, cfg: dict, artifacts: dict) -> None:
    run_dir = artifacts.get("run_dir", "")

    doc.add_heading(cfg.get("title", "Figures"), level=2)

    caption_prefix = cfg.get("caption_prefix", "Figure")
    plot_dirs = cfg.get("plot_dirs", ["07_plot", "08_plot"])
    include_root_png = cfg.get("include_root_png", "network_layout.png")
    no_figures_note = cfg.get("no_figures_note", "No figures available for this run.")

    png_paths = []
    for plot_dir in plot_dirs:
        pattern = os.path.join(run_dir, plot_dir, "*.png")
        png_paths.extend(sorted(glob.glob(pattern)))

    # Include optional root-level PNG (e.g. network_layout.png)
    if include_root_png:
        root_png = os.path.join(run_dir, include_root_png)
        if os.path.exists(root_png) and root_png not in png_paths:
            png_paths.append(root_png)

    if not png_paths:
        p = doc.add_paragraph(no_figures_note)
        p.italic = True
    else:
        for idx, png_path in enumerate(png_paths, start=1):
            stem = os.path.splitext(os.path.basename(png_path))[0]
            try:
                doc.add_picture(png_path, width=Inches(5.5))
            except Exception:
                doc.add_paragraph(f"[Figure {idx}: {stem} — image could not be embedded]")
            caption = f"{caption_prefix} {idx}: {stem}"
            cap_para = doc.add_paragraph(caption)
            cap_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            doc.add_paragraph()

    doc.add_paragraph()


def _render_diagnostics(doc: Document, cfg: dict, artifacts: dict) -> None:
    diag_data = artifacts.get("diagnostics", {})

    doc.add_heading(cfg.get("title", "Model Diagnostics"), level=2)

    diagnostics = diag_data.get("diagnostics", [])
    no_diag_note = cfg.get("no_diagnostics_note", "No diagnostics reported.")
    columns = cfg.get("columns", ["Code", "Severity", "Message"])

    if not diagnostics:
        # Also report overall status
        status = diag_data.get("status", "unknown")
        error_count = diag_data.get("error_count", 0)
        warning_count = diag_data.get("warning_count", 0)
        p = doc.add_paragraph(
            f"{no_diag_note} Overall status: {status.upper()} "
            f"(errors: {error_count}, warnings: {warning_count})."
        )
        p.italic = True
    else:
        table = doc.add_table(rows=1 + len(diagnostics), cols=len(columns))
        table.style = "Table Grid"
        header_row = table.rows[0]
        for i, col_name in enumerate(columns):
            header_row.cells[i].text = col_name
        for i, diag in enumerate(diagnostics):
            data_row = table.rows[i + 1]
            data_row.cells[0].text = _na(diag.get("code"))
            if len(columns) > 1:
                data_row.cells[1].text = _na(diag.get("severity"))
            if len(columns) > 2:
                data_row.cells[2].text = _na(diag.get("message") or diag.get("msg"))

    doc.add_paragraph()


def _render_comparison(doc: Document, cfg: dict, artifacts: dict) -> None:
    """Conditionally rendered — skipped entirely when comparison_available is false."""
    comp = artifacts.get("comparison", {})
    if not comp.get("comparison_available", False):
        return  # Silent skip — no heading added

    doc.add_heading(cfg.get("title", "Comparison with Baseline Run"), level=2)

    columns = cfg.get("columns", ["Metric", "Current", "Baseline", "Delta"])
    comp_metrics = comp.get("metrics", {}) or {}

    rows = []
    for metric_key, metric_obj in comp_metrics.items():
        if isinstance(metric_obj, dict):
            current = metric_obj.get("current", "n/a")
            baseline = metric_obj.get("baseline", "n/a")
            delta = metric_obj.get("delta", "n/a")
            rows.append((metric_key, _na(current), _na(baseline), _na(delta)))
        else:
            rows.append((metric_key, _na(metric_obj), "n/a", "n/a"))

    if rows:
        table = doc.add_table(rows=1 + len(rows), cols=min(len(columns), 4))
        table.style = "Table Grid"
        header_row = table.rows[0]
        for i, col_name in enumerate(columns[:4]):
            header_row.cells[i].text = col_name
        for i, (mk, cur, bas, dlt) in enumerate(rows):
            data_row = table.rows[i + 1]
            data_row.cells[0].text = mk
            if len(columns) > 1:
                data_row.cells[1].text = cur
            if len(columns) > 2:
                data_row.cells[2].text = bas
            if len(columns) > 3:
                data_row.cells[3].text = dlt
    else:
        doc.add_paragraph("Comparison data is available but contains no metric rows.")

    doc.add_paragraph()


def _render_provenance(doc: Document, cfg: dict, artifacts: dict) -> None:
    prov = artifacts.get("provenance", {})

    doc.add_heading(cfg.get("title", "Artifact Provenance"), level=2)

    art_dict = prov.get("artifacts", {})
    sha256_length = cfg.get("sha256_length", 16)
    columns = cfg.get("columns", ["Artifact ID", "Role", f"SHA-256 (first {sha256_length} chars)", "Produced by"])

    existing = [(k, v) for k, v in art_dict.items() if v.get("exists")]

    if not existing:
        doc.add_paragraph("No artifact hashes available.")
    else:
        table = doc.add_table(rows=1 + len(existing), cols=len(columns))
        table.style = "Table Grid"
        header_row = table.rows[0]
        for i, col_name in enumerate(columns):
            header_row.cells[i].text = col_name
        for i, (art_id, art_info) in enumerate(existing):
            sha = art_info.get("sha256") or ""
            sha_short = sha[:sha256_length] if sha else "n/a"
            data_row = table.rows[i + 1]
            data_row.cells[0].text = art_id
            if len(columns) > 1:
                data_row.cells[1].text = _na(art_info.get("role"))
            if len(columns) > 2:
                data_row.cells[2].text = sha_short
            if len(columns) > 3:
                data_row.cells[3].text = _na(art_info.get("produced_by"))

    doc.add_paragraph()


def _render_appendix(doc: Document, cfg: dict, artifacts: dict) -> None:
    prov = artifacts.get("provenance", {})

    doc.add_heading(cfg.get("title", "Appendix: Generation Environment"), level=2)

    repo = prov.get("repo", {}) or {}
    tools = prov.get("tools", {}) or {}
    schema_version = prov.get("schema_version", "")
    generated_by = prov.get("generated_by", "")

    columns = cfg.get("columns", ["Item", "Value"])
    rows_data = [
        ("Git head", _na(repo.get("git_head"))),
        ("Git branch", _na(repo.get("git_branch"))),
        ("SWMM version", _na(tools.get("swmm5_version"))),
        ("Python version", _na(tools.get("python_version"))),
        ("Provenance schema version", _na(schema_version)),
        ("Generated by", _na(generated_by)),
    ]

    table = doc.add_table(rows=1 + len(rows_data), cols=len(columns))
    table.style = "Table Grid"
    header_row = table.rows[0]
    for i, col_name in enumerate(columns):
        header_row.cells[i].text = col_name
    for i, (label, value) in enumerate(rows_data):
        data_row = table.rows[i + 1]
        data_row.cells[0].text = label
        if len(columns) > 1:
            data_row.cells[1].text = value

    doc.add_paragraph()


# ---------------------------------------------------------------------------
# Section registry — closed vocabulary
# ---------------------------------------------------------------------------

SECTION_RENDERERS = {
    "cover": _render_cover,
    "run_summary": _render_run_summary,
    "model_description": _render_model_description,
    "qa_gates": _render_qa_gates,
    "figures": _render_figures,
    "diagnostics": _render_diagnostics,
    "comparison": _render_comparison,
    "provenance": _render_provenance,
    "appendix": _render_appendix,
}


# ---------------------------------------------------------------------------
# Artifact loader
# ---------------------------------------------------------------------------

def _load_artifacts(run_dir: str) -> dict:
    """Load all JSON artifacts from a run dir. Raises SystemExit on missing audit."""
    audit_dir = os.path.join(run_dir, "09_audit")
    if not os.path.isdir(audit_dir):
        print(
            f"ERROR: {audit_dir!r} not found. "
            "Run `aiswmm audit --run-dir <path>` before generating a report.",
            file=sys.stderr,
        )
        sys.exit(1)

    provenance_path = os.path.join(audit_dir, "experiment_provenance.json")
    if not os.path.exists(provenance_path):
        print(
            f"ERROR: {provenance_path!r} not found. "
            "The run must be fully audited before a report can be generated.",
            file=sys.stderr,
        )
        sys.exit(1)

    return {
        "run_dir": run_dir,
        "manifest": _load_json(os.path.join(run_dir, "manifest.json")),
        "provenance": _load_json(provenance_path),
        "diagnostics": _load_json(os.path.join(run_dir, "model_diagnostics.json")),
        "comparison": _load_json(os.path.join(audit_dir, "comparison.json")),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate(run_dir: str, out_path: str, template_path: str) -> None:
    """Generate the Word deliverable. Pure function — no side-effects beyond writing out_path."""
    artifacts = _load_artifacts(run_dir)

    template = _load_template(template_path)
    sections = template.get("sections", [])

    doc = Document()

    for section_cfg in sections:
        section_id = section_cfg.get("id")
        if section_id not in SECTION_RENDERERS:
            print(
                f"WARNING: Unknown section id {section_id!r} in template — skipping.",
                file=sys.stderr,
            )
            continue
        SECTION_RENDERERS[section_id](doc, section_cfg, artifacts)

    # Ensure output directory exists
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    doc.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Word deliverable from an audited SWMM run directory."
    )
    parser.add_argument("--run-dir", required=True, help="Path to the audited run directory.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output .docx path (default: <run-dir>/report.docx).",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Path to a YAML (or JSON) template file. Defaults to the built-in default template.",
    )
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print(f"ERROR: run-dir {run_dir!r} does not exist.", file=sys.stderr)
        sys.exit(1)

    out_path = args.out if args.out else os.path.join(run_dir, "report.docx")
    template_path = args.template if args.template else _default_template_path()

    generate(run_dir, out_path, template_path)
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
