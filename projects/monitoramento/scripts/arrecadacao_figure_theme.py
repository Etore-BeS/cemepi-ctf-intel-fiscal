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
LEGEND_SIZE = 56
# Legend symbol width (px). Plotly default is 30; drives line length in the legend.
LEGEND_ITEMWIDTH = 120
# Plotly hardcodes bar legend squares at 12x12 and caps constant marker size at 12.
# After Kaleido, scale those symbols so color swatches match the large legend face.
LEGEND_SYMBOL_SCALE = 3.5
# Kaleido puts the first title line's baseline at this y, not the cap top.
# A 58px face needs about 48px above the baseline, plus padding.
TITLE_BASELINE = 96

ACTUAL_LINE_WIDTH = 10
FORECAST_LINE_WIDTH = 7
MARKER_SIZE_SHORT = 22

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
# Hatch so grain legends stay identifiable in B&W print (keep Okabe–Ito fill).
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

# Rank 1, 2, 3 on the pred-vs-actual panels. Color + dash + width for B&W.
FORECAST_STYLES = (
    {"color": "#D55E00", "dash": "solid", "width": 8},
    {"color": "#0072B2", "dash": "dash", "width": 7},
    {"color": "#009E73", "dash": "dashdot", "width": 7},
)
ACTUAL_COLOR = "#111111"
ACTUAL_DASH = "solid"

EXPORT_SCALE = 3
COMPARE_WIDTH = 2100
COMPARE_HEIGHT = 1580
ABLATION_WIDTH = 2100
ABLATION_HEIGHT = 1520
PRED_WIDTH = 2100
PRED_HEIGHT = 1580
FACET_WIDTH = 2200
FACET_HEIGHT = 2860

# Top margin clears the two-line title. Bottom margin holds ticks, the
# axis title, and the legend as three separate bands.
# Extra bottom room for the larger legend face and itemwidth.
BAR_MARGIN = {"t": 270, "b": 400, "l": 270, "r": 72}
LINE_MARGIN = {"t": 270, "b": 410, "l": 280, "r": 72}
# Legend top, measured down from the plot edge (pixels).
LEGEND_TOP_BELOW_PLOT = 240

MANUSCRIPT_STEMS = (
    "paper_v1_discussion_compare_smape_by_grain",
    "paper_v1_discussion_compare_skill_by_grain",
    "paper_v1_discussion_compare_ablation_smape",
    "paper_v1_discussion_compare_ablation_skill",
    "paper_v1_discussion_pred_vs_actual_monthly",
    "paper_v1_discussion_pred_vs_actual_daily_a",
    "paper_v1_discussion_pred_vs_actual_daily_b",
    "paper_v1_discussion_pred_vs_actual_weekly",
)


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


def bottom_legend(*, height: int, margin_bottom: int) -> dict:
    """Horizontal legend below the axis title, inside the bottom margin."""
    legend_top = height - margin_bottom + LEGEND_TOP_BELOW_PLOT
    legend_top = min(legend_top, height - 110)
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
        "tracegroupgap": 48,
        "entrywidthmode": "fraction",
        "entrywidth": 0.22,
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
    fig.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": TICK_SIZE, "color": INK},
        title=container_title(fig.layout.title.text or "", height=height),
        legend=bottom_legend(height=height, margin_bottom=BAR_MARGIN["b"]),
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


def enlarge_legend_symbols(svg: str, *, scale: float = LEGEND_SYMBOL_SCALE) -> str:
    """Grow Plotly legend color markers after export.

    Plotly.js draws bar swatches as a fixed ``M6,6H-6V-6H6Z`` (12x12) and, with
    ``itemsizing='constant'``, caps scatter markers at 12px and legend line
    strokes at 5px. Layout ``itemwidth`` only lengthens line segments. Scale the
    legend symbol transforms (and line stroke widths) so print swatches read at
    roughly 2-3x the stock size beside the large legend face.
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
) -> tuple[Path, Path]:
    """Write SVG, enlarge legend color markers, then rasterize PNG to match."""
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    png_path = stem_path.with_suffix(".png")
    svg_path = stem_path.with_suffix(".svg")
    fig.write_image(svg_path, width=width, height=height)
    svg_path.write_text(enlarge_legend_symbols(svg_path.read_text()))
    _rasterize_svg(
        svg_path,
        png_path,
        width=width * scale,
        height=height * scale,
    )
    return png_path, svg_path
