"""Publication layout for the arrecadação forecast figures.

Plotly template ``plotly_white``. Static export is Kaleido PNG and SVG.
The protocol notebook discussion cell should import these constants so a
later run matches the manuscript exports:

    import sys
    sys.path.insert(0, "projects/monitoramento/scripts")
    from arrecadacao_figure_theme import (
        ABLATION_COLOR,
        ABLATION_PATTERN,
        AXIS_TITLE_SIZE,
        FONT,
        GRAIN_COLOR,
        GRAIN_ORDER,
        GRAIN_PATTERN,
        LEGEND_ITEMWIDTH,
        LEGEND_SIZE,
        LEGEND_SYMBOL_SCALE,
        TICK_SIZE,
        TITLE_SIZE,
        apply_grouped_bar_layout,
        write_publication_figure,
    )
"""

from __future__ import annotations

from pathlib import Path
import re

import plotly.graph_objects as go

# Liberation Sans is the Arial-metric face installed in this environment.
# The stack still names Arial for machines that have it.
FONT = "Liberation Sans, Arial, Helvetica, sans-serif"

# Layout pixels on a figure about 2100px wide.
# The preprint text width is 5.5in, so a 32px title would print at about 6pt.
# These sizes print near 11pt (title) and 8pt (ticks and legend) at \linewidth.
TITLE_SIZE = 58
SUBTITLE_SIZE = 42
AXIS_TITLE_SIZE = 48
TICK_SIZE = 42
# Legend must stay readable in a two-column NeurIPS PDF; exceed tick size.
LEGEND_SIZE = 52
# Legend symbol width (px). Plotly default is 30; drives line length in the legend.
LEGEND_ITEMWIDTH = 100
# Plotly hardcodes bar legend squares at 12x12 and caps constant marker size at 12.
# After Kaleido, scale those symbols so color swatches match the large legend face.
# Keep line-plot scale moderate so markers do not sit on the legend text.
LEGEND_SYMBOL_SCALE = 2.4
LEGEND_SYMBOL_SCALE_BARS = 2.8
# Kaleido puts the first title line's baseline at this y, not the cap top.
# A 58px face needs about 48px above the baseline, plus padding.
TITLE_BASELINE = 96

ACTUAL_LINE_WIDTH = 10
FORECAST_LINE_WIDTH = 7
MARKER_SIZE_SHORT = 18

INK = "#222222"
MUTED = "#444444"
GRID = "#E6E6E6"
ZERO_LINE = "#222222"

# Okabe–Ito, one color per grain, shared by every compare chart.
GRAIN_ORDER = ["Monthly", "Weekly", "Daily A (cal)", "Daily B (biz days)"]
GRAIN_COLOR = {
    "Monthly": "#0072B2",
    "Weekly": "#E69F00",
    "Daily A (cal)": "#009E73",
    "Daily B (biz days)": "#CC79A7",
}
# Short legend labels so NeurIPS-width bar legends do not clip.
GRAIN_LEGEND_LABEL = {
    "Monthly": "Monthly",
    "Weekly": "Weekly",
    "Daily A (cal)": "Daily A",
    "Daily B (biz days)": "Daily B",
}
# Hatch so grain legends stay identifiable in B&W print (keep Okabe–Ito fill).
# Hatch is for bar charts only — never on line-plot legends.
GRAIN_PATTERN = {
    "Monthly": "",
    "Weekly": "/",
    "Daily A (cal)": "\\",
    "Daily B (biz days)": "x",
}
ABLATION_GRAIN_ORDER = ["Daily A (cal)", "Daily B (biz days)"]
ABLATION_COLOR = {
    "Daily A (cal)": "#009E73",
    "Daily B (biz days)": "#CC79A7",
}
ABLATION_PATTERN = {
    "Daily A (cal)": "\\",
    "Daily B (biz days)": "x",
}
FAM_ORDER = [
    "M0",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "E1",
    "E2",
    "E3",
    "E4",
]

# Pred-vs-actual channel MECE:
#   color = model (Okabe–Ito)
#   solid = actual
#   dashed = forecast (when a forecast is drawn)
# No hatch on line legends. Rank 0 is the leader.
FORECAST_STYLES = (
    {"color": "#D55E00", "dash": "dash", "width": 8},
    {"color": "#0072B2", "dash": "dash", "width": 7},
    {"color": "#009E73", "dash": "dot", "width": 7},
)
ACTUAL_COLOR = "#111111"
ACTUAL_DASH = "solid"
ACTUAL_LIGHT = "#B0B0B0"  # small-multiples background actual

EXPORT_SCALE = 3
COMPARE_WIDTH = 2100
COMPARE_HEIGHT = 1680
ABLATION_WIDTH = 2100
ABLATION_HEIGHT = 1600
PRED_WIDTH = 2100
PRED_HEIGHT = 1480
RESIDUAL_WIDTH = 2200
RESIDUAL_HEIGHT = 1600
FACET_WIDTH = 2200
FACET_HEIGHT = 2860
SMALL_MULT_WIDTH = 2400
SMALL_MULT_PANEL_H = 420

# Top margin clears the two-line title. Bottom margin holds ticks, the
# axis title, and the legend as three separate bands.
BAR_MARGIN = {"t": 270, "b": 460, "l": 270, "r": 72}
LINE_MARGIN = {"t": 250, "b": 360, "l": 280, "r": 72}
# Legend top, measured down from the plot edge (pixels).
LEGEND_TOP_BELOW_PLOT = 220
LEGEND_TOP_BELOW_PLOT_BARS = 250

# Main + appendix stems mirrored into manuscript/figures/.
MANUSCRIPT_STEMS = (
    # Main: clean pred (Actual + leader, optionally 2nd)
    "paper_v1_discussion_pred_vs_actual_monthly",
    "paper_v1_discussion_pred_vs_actual_daily_b",
    "paper_v1_discussion_pred_vs_actual_weekly",
    # Main: one residual / error-shape figure
    "paper_v1_discussion_residual_leaders",
    # Appendix: daily A pred, bar duplicates, small multiples
    "paper_v1_discussion_pred_vs_actual_daily_a",
    "paper_v1_discussion_compare_smape_by_grain",
    "paper_v1_discussion_compare_skill_by_grain",
    "paper_v1_discussion_compare_ablation_smape",
    "paper_v1_discussion_compare_ablation_skill",
    "paper_v1_appendix_small_multiples_monthly",
    "paper_v1_appendix_small_multiples_weekly",
    "paper_v1_appendix_small_multiples_daily_a",
    "paper_v1_appendix_small_multiples_daily_b",
)


def short_model_name(name: str) -> str:
    """Short NeurIPS legend / panel label (TimesFM, HGB, E2, …)."""
    text = str(name)
    for prefix in ("D_", "W_", "BD_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    replacements = (
        ("energy_selector_proxy_ablation", "E2"),
        ("E2_E2", "E2"),
        ("TEM_selective", "TEM"),
        ("E1_TEM", "TEM"),
        ("naive_sazonal_lag", "seas"),
        ("naive_lag", "lag"),
        ("TimesFM3", "TimesFM"),
        ("M9_TimesFM", "TimesFM"),
        ("HGB_tuned", "HGB"),
        ("M3_HGB", "HGB"),
        ("MLP2_torch", "MLP"),
        ("M5_MLP2", "MLP"),
        ("ARF_regressor", "ARF"),
        ("M6_ARF", "ARF"),
        ("GPR_RBF_cap200", "GPR"),
        ("GPR_RBF", "GPR"),
        ("M7_GPR", "GPR"),
        ("KRR_laplacian", "KRR"),
        ("KRR_rbf", "KRR"),
        ("M8_KRR", "KRR"),
        ("ScoreGrad", "ScoreGrad"),
        ("TimeGrad", "TimeGrad"),
        ("Prophet", "Prophet"),
        ("Ridge_a10.0", "Ridge"),
        ("Ridge_a0.1", "Ridge"),
        ("M1_Ridge", "Ridge"),
        ("M1_OLS", "OLS"),
        ("_regressor", ""),
        ("_selective", ""),
        ("_tuned", ""),
        ("_torch", ""),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    # Collapse family prefixes when the short name already identifies the model.
    if text.startswith("M9_") and "TimesFM" in text:
        return "TimesFM"
    if text.startswith("M3_") and text.endswith("HGB"):
        return "HGB"
    if text.startswith("E2"):
        return "E2"
    if text.startswith("E1"):
        return "TEM"
    if text.startswith("E3") or "TimeGrad" in text:
        return "TimeGrad"
    if text.startswith("E4") or "ScoreGrad" in text:
        return "ScoreGrad"
    if text.startswith("M0_seas"):
        return text[len("M0_"):]  # seas7, seas12, seas52, …
    if text.startswith("M0_lag"):
        return text[len("M0_"):]
    if "Prophet" in text:
        return "Prophet"
    if text.endswith("_MLP") or text == "MLP" or text.startswith("M5_"):
        return "MLP"
    if text.startswith("M4_"):
        return text[len("M4_"):]
    # SARIMAX: keep a short token
    if "SARIMAX" in text:
        return "SARIMAX"
    if len(text) > 16:
        return text[:15] + "…"
    return text


def title_with_subtitle(title: str, subtitle: str) -> str:
    """Two-line title. The span keeps the subtitle smaller in Kaleido SVG."""
    return (
        f"{title}<br>"
        f"<span style='font-size:{SUBTITLE_SIZE}px;color:{MUTED}'>"
        f"{subtitle}</span>"
    )


def container_title(text: str, *, height: int) -> dict:
    """Place the title baseline low enough that Kaleido does not clip the caps.

    Multi-line titles ignore ``yanchor='top'``: the first ``<tspan>`` baseline
    is the y coordinate Kaleido writes.
    """
    return {
        "text": text,
        "font": {"family": FONT, "size": TITLE_SIZE, "color": INK},
        "x": 0.5,
        "xanchor": "center",
        "y": 1 - (TITLE_BASELINE / max(height, 1)),
        "yanchor": "top",
        "yref": "container",
    }


def bottom_legend(
    *,
    height: int,
    margin_bottom: int,
    n_items: int = 4,
    for_bars: bool = False,
) -> dict:
    """Horizontal legend below the axis title, inside the bottom margin.

    ``entrywidth`` is sized so 2–4 short items fit without clipping. Bar charts
    with four grains wrap to two rows when needed.
    """
    top_pad = LEGEND_TOP_BELOW_PLOT_BARS if for_bars else LEGEND_TOP_BELOW_PLOT
    legend_top = height - margin_bottom + top_pad
    legend_top = min(legend_top, height - 100)
    # Fraction of plot width per legend entry. Force 2-per-row when many items.
    if n_items <= 2:
        entry_frac = 0.28
    elif n_items == 3:
        entry_frac = 0.24
    else:
        entry_frac = 0.20  # 4 items → wraps to 2×2 with short labels
    return {
        "orientation": "h",
        "yref": "container",
        "xref": "container",
        "x": 0.5,
        "xanchor": "center",
        "y": 1 - (legend_top / max(height, 1)),
        "yanchor": "top",
        "font": {"family": FONT, "size": LEGEND_SIZE, "color": INK},
        "title_text": "",
        "itemsizing": "constant",
        "itemwidth": LEGEND_ITEMWIDTH,
        "bgcolor": "rgba(255,255,255,0)",
        "tracegroupgap": 56,
        "entrywidthmode": "fraction",
        "entrywidth": entry_frac,
        "valign": "middle",
    }


def _axis_title(text: str) -> dict:
    return {
        "text": text,
        "font": {"family": FONT, "size": AXIS_TITLE_SIZE, "color": INK},
        "standoff": 28,
    }


def apply_grouped_bar_layout(fig: go.Figure, *, y_title: str, height: int) -> None:
    """White theme, bottom legend, and a top margin reserved for the two-line title.

    The legend sits in the bottom margin (``yref=container``) so it cannot
    cover the title. That collision is what the previous exports did.
    """
    n_legend = sum(1 for tr in fig.data if getattr(tr, "showlegend", True) is not False)
    # Grouped bars share a legend entry per grain via color; count unique names.
    names = {tr.name for tr in fig.data if tr.name}
    n_items = max(len(names), 1)
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": TICK_SIZE, "color": INK},
        title=container_title(fig.layout.title.text or "", height=height),
        legend=bottom_legend(
            height=height,
            margin_bottom=BAR_MARGIN["b"],
            n_items=n_items,
            for_bars=True,
        ),
        margin=BAR_MARGIN,
        bargap=0.28,
        bargroupgap=0.1,
    )
    fig.update_xaxes(
        title=_axis_title("Family"),
        tickfont={"family": FONT, "size": TICK_SIZE, "color": INK},
        showline=True,
        linewidth=1.6,
        linecolor=INK,
        ticks="outside",
        ticklen=8,
        tickcolor=INK,
        automargin=False,
        showgrid=False,
    )
    fig.update_yaxes(
        title=_axis_title(y_title),
        tickfont={"family": FONT, "size": TICK_SIZE, "color": INK},
        showline=True,
        linewidth=1.6,
        linecolor=INK,
        ticks="outside",
        ticklen=8,
        tickcolor=INK,
        automargin=False,
        showgrid=True,
        gridcolor=GRID,
        gridwidth=1,
        zeroline=False,
    )
    fig.update_traces(
        marker_line_width=1.2,
        marker_line_color="white",
        selector={"type": "bar"},
    )


def enlarge_legend_symbols(
    svg: str, *, scale: float = LEGEND_SYMBOL_SCALE
) -> str:
    """Grow Plotly legend color markers after export.

    Plotly.js draws bar swatches as a fixed ``M6,6H-6V-6H6Z`` (12x12) and, with
    ``itemsizing='constant'``, caps scatter markers at 12px and legend line
    strokes at 5px. Layout ``itemwidth`` only lengthens line segments. Scale the
    legend symbol transforms (and line stroke widths) so print swatches read
    beside the large legend face without covering the legend text.
    """
    if scale == 1:
        return svg

    def scale_points(match: re.Match[str]) -> str:
        inner = match.group(1)

        def fix_transform(tm: re.Match[str]) -> str:
            existing = tm.group(1)
            if "scale(" in existing:
                return tm.group(0)
            return f'transform="{existing} scale({scale:g})"'

        inner = re.sub(r'transform="([^"]+)"', fix_transform, inner)
        return f'<g class="legendpoints">{inner}</g>'

    def scale_lines(match: re.Match[str]) -> str:
        inner = match.group(1)

        def fix_stroke(sm: re.Match[str]) -> str:
            return f"stroke-width: {float(sm.group(1)) * scale:g}px"

        inner = re.sub(r"stroke-width:\s*([0-9.]+)px", fix_stroke, inner)
        return f'<g class="legendlines">{inner}</g>'

    svg = re.sub(r'<g class="legendpoints">([\s\S]*?)</g>', scale_points, svg)
    svg = re.sub(r'<g class="legendlines">([\s\S]*?)</g>', scale_lines, svg)
    return svg


def _rasterize_svg(svg_path: Path, png_path: Path, *, width: int, height: int) -> None:
    """Rasterize the post-processed SVG so PNG legend swatches match SVG."""
    import shutil
    import subprocess

    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise RuntimeError(
            "rsvg-convert is required to bake enlarged legend symbols into PNG"
        )
    subprocess.run(
        [
            converter,
            "-w",
            str(width),
            "-h",
            str(height),
            "-o",
            str(png_path),
            str(svg_path),
        ],
        check=True,
    )


def write_publication_figure(
    fig: go.Figure,
    stem_path: Path,
    *,
    width: int,
    height: int,
    scale: int,
    legend_symbol_scale: float | None = None,
) -> tuple[Path, Path]:
    """Write SVG, enlarge legend color markers, then rasterize PNG to match."""
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    png_path = stem_path.with_suffix(".png")
    svg_path = stem_path.with_suffix(".svg")
    fig.write_image(svg_path, width=width, height=height)
    sym_scale = (
        LEGEND_SYMBOL_SCALE if legend_symbol_scale is None else legend_symbol_scale
    )
    svg_path.write_text(enlarge_legend_symbols(svg_path.read_text(), scale=sym_scale))
    _rasterize_svg(
        svg_path,
        png_path,
        width=width * scale,
        height=height * scale,
    )
    return png_path, svg_path
