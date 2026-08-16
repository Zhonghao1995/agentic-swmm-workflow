"""Shared figure style for the swmm-plot skill: the Nature figure specification.

Every renderer in this directory (hydrograph, network map, study area) goes
through the same three calls, so the figures of one run read as one set and
none of them hand-sets rcParams:

    from plot_style import apply_style, figsize, save_figure

    apply_style()                                   # vendored nature.mplstyle
    fig, ax = plt.subplots(figsize=figsize("single"), layout="constrained")
    ...
    save_figure(fig, out_png)                       # out.pdf (vector) + out.png (preview)

Spec: https://research-figure-guide.nature.com/figures/preparing-figures-our-specifications/
Mirrors the nature-figures skill's ``nature_export.py`` (same numbers, same
guards) so a figure produced here passes its ``check_figure.py`` unchanged.

Why two files per figure: journals take vector PDF/EPS/AI for main figures and
reject PNG outright, so the PDF is the submission file and the PNG is the
preview that reports, chat and slides embed. They share a stem, which is how
the checker knows the PNG is a preview and not an orphan raster.

Never save with ``bbox_inches='tight'``: it silently changes the physical
width and breaks the 89/183 mm rule. Use ``layout='constrained'`` on the
figure and let ``save_figure`` write with the default bbox.
"""

from __future__ import annotations

from pathlib import Path

MM_PER_IN = 25.4
SINGLE_MM, DOUBLE_MM, MAX_H_MM = 89.0, 183.0, 170.0
SINGLE_IN = SINGLE_MM / MM_PER_IN   # 3.5039
DOUBLE_IN = DOUBLE_MM / MM_PER_IN   # 7.2047
MAX_H_IN = MAX_H_MM / MM_PER_IN     # 6.6929

# "For images, minimum 450 dpi" -- the preview PNG is exported at this by default.
PREVIEW_DPI = 450

STYLE_PATH = Path(__file__).resolve().parent.parent / "assets" / "nature.mplstyle"

# Wong colour-blind-safe palette, in the order the spec says to use it
# (black first). Reach for these by name instead of ad-hoc hex codes.
WONG = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddish_purple": "#CC79A7",
}

# Categorical cycle for "one colour per group" layers (map sub-networks).
# Black is kept out because it is the colour of the outfall markers, and
# yellow goes last because thin yellow lines vanish on white paper.
CATEGORY_CYCLE = (
    WONG["orange"],
    WONG["sky_blue"],
    WONG["bluish_green"],
    WONG["blue"],
    WONG["vermillion"],
    WONG["reddish_purple"],
    WONG["yellow"],
)


class SpecError(ValueError):
    """The figure cannot be exported without violating the specification."""


def apply_style() -> None:
    """Load the vendored Nature stylesheet into matplotlib's rcParams."""
    import matplotlib.pyplot as plt

    plt.style.use(str(STYLE_PATH))


def figsize(width: str | float = "single", height_mm: float | None = None,
            aspect: float = 0.618) -> tuple[float, float]:
    """Return a spec-legal ``(w, h)`` in inches.

    width      -- ``"single"`` (89 mm), ``"double"`` (183 mm), or a number in mm.
    height_mm  -- explicit height; otherwise ``width * aspect``, capped at 170 mm.
    """
    if width == "single":
        w_in = SINGLE_IN
    elif width == "double":
        w_in = DOUBLE_IN
    else:
        w_in = float(width) / MM_PER_IN
        if w_in > DOUBLE_IN + 1e-6:
            raise SpecError(f"{width} mm exceeds the 183 mm maximum width.")

    h_in = (height_mm / MM_PER_IN) if height_mm is not None else w_in * aspect
    if h_in > MAX_H_IN + 1e-6:
        raise SpecError(
            f"height {h_in * MM_PER_IN:.1f} mm exceeds the 170 mm maximum height."
        )
    return (w_in, h_in)


def map_figsize(bounds: tuple[float, float, float, float],
                width: str | float = "single") -> tuple[float, float]:
    """Figure size for an equal-aspect map: spec width, height from the data.

    ``bounds`` is ``(minx, miny, maxx, maxy)`` in data units. A fixed square
    canvas would leave bands of white space around a wide or tall network,
    so the height follows the extent's own aspect ratio, clamped to
    [half the width, the 170 mm maximum].
    """
    w_in, _ = figsize(width, height_mm=1.0)
    w_mm = w_in * MM_PER_IN
    minx, miny, maxx, maxy = bounds
    dx, dy = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    height_mm = min(max(w_mm * dy / dx, w_mm * 0.5), MAX_H_MM)
    return figsize(width, height_mm=height_mm)


def _check_size(fig) -> tuple[float, float]:
    w_in, h_in = fig.get_size_inches()
    w_mm, h_mm = w_in * MM_PER_IN, h_in * MM_PER_IN
    if w_mm > DOUBLE_MM + 0.5:
        raise SpecError(
            f"figure is {w_mm:.1f} mm wide; the maximum is {DOUBLE_MM:.0f} mm. "
            f"Build the size with figsize('single'|'double')."
        )
    if h_mm > MAX_H_MM + 0.5:
        raise SpecError(
            f"figure is {h_mm:.1f} mm tall; the maximum is {MAX_H_MM:.0f} mm "
            "(leaves room for the legend)."
        )
    return w_mm, h_mm


def save_figure(fig, out_png: str | Path, *, dpi: int = PREVIEW_DPI,
                pdf: bool = True) -> dict[str, Path]:
    """Write ``out_png`` and, by default, its vector twin ``<stem>.pdf``.

    Returns ``{"png": Path, "pdf": Path}`` (``"pdf"`` absent when ``pdf=False``).
    Creates parent directories. Never uses a tight bbox. Raises ``SpecError``
    when the figure is wider than 183 mm or taller than 170 mm.
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _check_size(fig)

    written: dict[str, Path] = {}
    if pdf:
        out_pdf = out_png.with_suffix(".pdf")
        fig.savefig(out_pdf, format="pdf", facecolor="white")
        written["pdf"] = out_pdf
    fig.savefig(out_png, format="png", dpi=dpi, facecolor="white")
    written["png"] = out_png
    return written


def legend_outside(fig, handles, labels, *, ncol: int | None = None):
    """Figure-level key above the axes, right-aligned, so it never sits on data.

    Text over data is a spec violation (contrast, overlap); an unframed key
    inside the panel cannot promise that on an arbitrary hydrograph. Uses the
    ``outside`` locations of matplotlib >= 3.7 and falls back to an anchored
    corner on older releases.
    """
    kwargs = {"ncol": ncol or max(len(labels), 1), "frameon": False}
    try:
        return fig.legend(handles, labels, loc="outside upper right", **kwargs)
    except ValueError:  # matplotlib < 3.7: no "outside ..." locations
        return fig.legend(handles, labels, loc="upper right",
                          bbox_to_anchor=(1.0, 1.0), **kwargs)


__all__ = [
    "CATEGORY_CYCLE",
    "DOUBLE_IN",
    "DOUBLE_MM",
    "MAX_H_IN",
    "MAX_H_MM",
    "PREVIEW_DPI",
    "SINGLE_IN",
    "SINGLE_MM",
    "STYLE_PATH",
    "WONG",
    "SpecError",
    "apply_style",
    "figsize",
    "legend_outside",
    "map_figsize",
    "save_figure",
]
