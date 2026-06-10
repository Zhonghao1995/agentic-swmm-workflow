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
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
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


def _set_style_black(doc: Document) -> None:
    """Override font color to pure black for all styles used by this generator."""
    black = RGBColor(0, 0, 0)
    for style_name in ("Normal", "Heading 1", "Heading 2", "Title"):
        try:
            style = doc.styles[style_name]
            style.font.color.rgb = black
        except KeyError:
            pass  # Style absent in this template — skip silently


def _add_table_caption(doc: Document, caption_text: str, table_counter: list) -> None:
    """Insert an engineering-convention table caption ABOVE the next table.

    Format: bold ``Table N`` prefix followed by em-dash and caption text, all black.
    ``table_counter`` is a one-element list used as a mutable integer.
    """
    table_counter[0] += 1
    n = table_counter[0]
    cap_para = doc.add_paragraph()
    # Bold "Table N —" prefix
    bold_run = cap_para.add_run(f"Table {n} — ")
    bold_run.bold = True
    bold_run.font.color.rgb = RGBColor(0, 0, 0)
    # Regular caption text
    text_run = cap_para.add_run(caption_text)
    text_run.bold = False
    text_run.font.color.rgb = RGBColor(0, 0, 0)


def _add_narrative(doc: Document, narrative_text: str) -> None:
    """Insert an explanatory narrative paragraph immediately after a table."""
    p = doc.add_paragraph(narrative_text)
    for run in p.runs:
        run.font.color.rgb = RGBColor(0, 0, 0)


def _add_figure_caption(doc: Document, caption_text: str, figure_counter: list) -> None:
    """Insert an engineering-convention figure caption BELOW the figure.

    Format: ``Figure N — <text>``, centred, all black.
    ``figure_counter`` is a one-element list used as a mutable integer.
    """
    figure_counter[0] += 1
    n = figure_counter[0]
    cap_para = doc.add_paragraph(f"Figure {n} — {caption_text}")
    cap_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in cap_para.runs:
        run.font.color.rgb = RGBColor(0, 0, 0)


def _add_page_number_footer(doc: Document) -> None:
    """Add a right-aligned PAGE field to the default section footer.

    python-docx has no high-level PAGE-field API; we insert the standard OOXML
    field via fldChar + instrText runs so Word/LibreOffice renders a live number.
    """
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False

    # Clear any existing paragraphs and work with the first one
    for para in footer.paragraphs:
        # Remove all runs from existing paragraph
        p_elem = para._p
        for child in list(p_elem):
            if child.tag != qn("w:pPr"):
                p_elem.remove(child)

    footer_para = footer.paragraphs[0]
    footer_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Build the PAGE field via raw OOXML: fldChar(begin) + instrText + fldChar(end)
    run_begin = footer_para.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_begin._r.append(fld_begin)

    run_instr = footer_para.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    run_instr._r.append(instr)

    run_end = footer_para.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_end._r.append(fld_end)


def _numbered_heading(section_number: int, title: str) -> str:
    """Return ``N Title`` for body sections (section_number >= 1)."""
    return f"{section_number} {title}"


# ---------------------------------------------------------------------------
# Section renderers
# Each renderer has signature: (doc, section_cfg, artifacts, ctx) -> None
# `artifacts` is a dict carrying the loaded JSON data and discovered PNG paths.
# `ctx` is a mutable rendering context dict:
#   ctx["table_counter"]: [int]  — auto-incrementing table number
#   ctx["figure_counter"]: [int] — auto-incrementing figure number
#   ctx["section_number"]: int   — increments for each body heading (cover exempt)
# ---------------------------------------------------------------------------

def _render_cover(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    prov = artifacts.get("provenance", {})
    manifest = artifacts.get("manifest", {})

    title = cfg.get("title", "Run Audit Deliverable")
    subtitle = cfg.get("subtitle", "")

    # Cover title is unnumbered (level 1 heading, centred)
    heading = doc.add_heading(title, level=1)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if subtitle:
        p = doc.add_paragraph(subtitle)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run_id = prov.get("run_id") or manifest.get("run_dir", "")
    generated_at = prov.get("generated_at_utc", "")
    swmm_version = (prov.get("tools") or {}).get("swmm5_version", "")

    caption = cfg.get("caption", "Run identification and generation metadata.")
    narrative = cfg.get(
        "narrative",
        "Run ID and timestamps are sourced directly from experiment_provenance.json; "
        "the SWMM version reflects the binary used during the simulation run.",
    )

    rows_data = [
        ("Run ID", _na(run_id)),
        ("Generated at (UTC)", _na(generated_at)),
        ("SWMM version", _na(swmm_version)),
    ]

    _add_table_caption(doc, caption, ctx["table_counter"])
    info_table = doc.add_table(rows=3, cols=2)
    info_table.style = "Table Grid"
    for row, (label, value) in zip(info_table.rows, rows_data):
        row.cells[0].text = label
        row.cells[1].text = value

    _add_narrative(doc, narrative)
    doc.add_paragraph()


def _render_run_summary(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    prov = artifacts.get("provenance", {})
    manifest = artifacts.get("manifest", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Run Summary")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

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

    caption = cfg.get("caption", "Key hydraulic performance metrics for this simulation run.")
    narrative = cfg.get(
        "narrative",
        "Values are read from the run manifest and experiment_provenance.json produced by "
        "the SWMM runner and audit tool; no metrics are recomputed at report generation time.",
    )

    value_map = {
        "peak_flow": (_na(peak_flow_val), "CMS"),
        "time_of_peak": (_na(time_of_peak), "hh:mm"),
        "continuity_error": (_na(continuity_error), "%"),
        "return_code": (_na(return_code), ""),
    }

    _add_table_caption(doc, caption, ctx["table_counter"])
    table = doc.add_table(rows=1 + len(rows_cfg), cols=len(columns))
    table.style = "Table Grid"
    header_row = table.rows[0]
    for i, col_name in enumerate(columns):
        header_row.cells[i].text = col_name

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

    _add_narrative(doc, narrative)
    doc.add_paragraph()


def _render_model_description(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    manifest = artifacts.get("manifest", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Model Description")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

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

    caption = cfg.get("caption", "Catchment geometry and infiltration parameters from the model manifest.")
    narrative = cfg.get(
        "narrative",
        "All parameter values are sourced from manifest.json written by swmm-builder at "
        "model-construction time; they reflect the exact inputs used for this simulation.",
    )

    _add_table_caption(doc, caption, ctx["table_counter"])
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

    _add_narrative(doc, narrative)
    doc.add_paragraph()


def _render_qa_gates(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    prov = artifacts.get("provenance", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "QA Gates")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

    qa = prov.get("qa", {})
    checks = qa.get("checks", [])
    pass_label = cfg.get("pass_label", "PASS")
    fail_label = cfg.get("fail_label", "FAIL")

    columns = cfg.get("columns", ["Gate ID", "Status", "Detail"])

    caption = cfg.get("caption", "Automated QA gate results from the audit pipeline.")
    narrative = cfg.get(
        "narrative",
        "Each gate is evaluated by swmm-experiment-audit against thresholds defined in "
        "the project configuration; PASS/FAIL status is read from experiment_provenance.json "
        "and is not recomputed here.",
    )

    if checks:
        _add_table_caption(doc, caption, ctx["table_counter"])
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

        _add_narrative(doc, narrative)
    else:
        p = doc.add_paragraph("No QA checks recorded.")
        p.italic = True

    doc.add_paragraph()


def _render_figures(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    run_dir = artifacts.get("run_dir", "")

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Figures")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

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
                doc.add_paragraph(f"[image {idx}: {stem} — could not be embedded]")
            # Caption BELOW figure (engineering convention), numbered
            _add_figure_caption(doc, stem, ctx["figure_counter"])
            doc.add_paragraph()

    doc.add_paragraph()


def _render_diagnostics(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    diag_data = artifacts.get("diagnostics", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Model Diagnostics")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

    diagnostics = diag_data.get("diagnostics", [])
    no_diag_note = cfg.get("no_diagnostics_note", "No diagnostics reported.")
    columns = cfg.get("columns", ["Code", "Severity", "Message"])

    caption = cfg.get("caption", "SWMM diagnostic messages emitted during the simulation run.")
    narrative = cfg.get(
        "narrative",
        "Diagnostic entries are parsed from model_diagnostics.json produced by "
        "swmm-experiment-audit; codes and severities follow the SWMM 5 output format.",
    )

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
        _add_table_caption(doc, caption, ctx["table_counter"])
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

        _add_narrative(doc, narrative)

    doc.add_paragraph()


def _render_comparison(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    """Conditionally rendered — skipped entirely when comparison_available is false."""
    comp = artifacts.get("comparison", {})
    if not comp.get("comparison_available", False):
        return  # Silent skip — no heading added

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Comparison with Baseline Run")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

    columns = cfg.get("columns", ["Metric", "Current", "Baseline", "Delta"])
    comp_metrics = comp.get("metrics", {}) or {}

    caption = cfg.get("caption", "Side-by-side metric comparison between the current and baseline runs.")
    narrative = cfg.get(
        "narrative",
        "Current and baseline values are read from 09_audit/comparison.json generated by "
        "swmm-experiment-audit; delta is the arithmetic difference (current minus baseline) "
        "as stored in that file and is not recomputed here.",
    )

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
        _add_table_caption(doc, caption, ctx["table_counter"])
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
        _add_narrative(doc, narrative)
    else:
        doc.add_paragraph("Comparison data is available but contains no metric rows.")

    doc.add_paragraph()


def _render_provenance(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    prov = artifacts.get("provenance", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Artifact Provenance")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

    art_dict = prov.get("artifacts", {})
    sha256_length = cfg.get("sha256_length", 16)
    columns = cfg.get("columns", ["Artifact ID", "Role", f"SHA-256 (first {sha256_length} chars)", "Produced by"])

    caption = cfg.get("caption", "Cryptographic hashes and roles of artifacts involved in this run.")
    narrative = cfg.get(
        "narrative",
        "SHA-256 digests are computed by swmm-experiment-audit at audit time and stored in "
        "experiment_provenance.json; only the first 16 hex characters are shown here for "
        "readability — the full digest is available in the provenance JSON.",
    )

    existing = [(k, v) for k, v in art_dict.items() if v.get("exists")]

    if not existing:
        doc.add_paragraph("No artifact hashes available.")
    else:
        _add_table_caption(doc, caption, ctx["table_counter"])
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

        _add_narrative(doc, narrative)

    doc.add_paragraph()


def _render_appendix(doc: Document, cfg: dict, artifacts: dict, ctx: dict) -> None:
    prov = artifacts.get("provenance", {})

    ctx["section_number"] += 1
    raw_title = cfg.get("title", "Appendix: Generation Environment")
    doc.add_heading(_numbered_heading(ctx["section_number"], raw_title), level=2)

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

    caption = cfg.get("caption", "Software environment at the time of audit generation.")
    narrative = cfg.get(
        "narrative",
        "All environment values are read from experiment_provenance.json; they capture the "
        "exact tool versions used to produce and audit the simulation results.",
    )

    _add_table_caption(doc, caption, ctx["table_counter"])
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

    _add_narrative(doc, narrative)
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

    # Apply pure-black override to all Word built-in styles used by this generator
    _set_style_black(doc)

    # Insert page number field into the section footer
    _add_page_number_footer(doc)

    # Rendering context — mutable counters shared across all section renderers
    ctx = {
        "table_counter": [0],
        "figure_counter": [0],
        "section_number": 0,
    }

    for section_cfg in sections:
        section_id = section_cfg.get("id")
        if section_id not in SECTION_RENDERERS:
            print(
                f"WARNING: Unknown section id {section_id!r} in template — skipping.",
                file=sys.stderr,
            )
            continue
        SECTION_RENDERERS[section_id](doc, section_cfg, artifacts, ctx)

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
