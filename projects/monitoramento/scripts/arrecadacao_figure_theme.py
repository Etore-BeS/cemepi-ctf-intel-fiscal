"""Publication layout for the arrecadação forecast figures.

Plotly template ``plotly_white``. Static export is Kaleido PNG and SVG.
The protocol notebook discussion cell should import these constants so a
later run matches the manuscript exports:

    import sys
    sys.path.insert(0, "projects/monitoramento/scripts")
    from arrecadacao_figure_theme import (
        ABLATION_COLOR,
        AXIS_TITLE_SIZE,
        FONT,
        GRAIN_COLOR,
        GRAIN_ORDER,
        LEGEND_SIZE,
        TICK_SIZE,
        TITLE_SIZE,
        apply_grouped_bar_layout,
        write_publication_figure,
    )
"""

from __future__ import annotations

from pathlib import Path

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
LEGEND_SIZE = 42
# Kaleido puts the first title line's baseline at this y, not the cap top.
# A 58px face needs about 48px above the baseline, plus padding.
TITLE_BASELINE = 96

ACTUAL_LINE_WIDTH = 9
FORECAST_LINE_WIDTH = 6
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
ABLATION_GRAIN_ORDER = ["Daily A (cal)", "Daily B (biz days)"]
ABLATION_COLOR = {
    "Daily A (cal)": "#009E73",
    "Daily B (biz days)": "#CC79A7",
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

# Rank 1, 2, 3 on the pred-vs-actual panels. Color plus dash.
FORECAST_STYLES = (
    {"color": "#D55E00", "dash": "solid"},
    {"color": "#0072B2", "dash": "dash"},
    {"color": "#009E73", "dash": "dot"},
)
ACTUAL_COLOR = "#111111"

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
BAR_MARGIN = {"t": 270, "b": 330, "l": 270, "r": 72}
LINE_MARGIN = {"t": 270, "b": 340, "l": 280, "r": 72}
# Legend top, measured down from the plot edge (pixels).
LEGEND_TOP_BELOW_PLOT = 210

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
        "bgcolor": "rgba(255,255,255,0)",
        "tracegroupgap": 36,
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


def write_publication_figure(
    fig: go.Figure,
    stem_path: Path,
    *,
    width: int,
    height: int,
    scale: int,
) -> tuple[Path, Path]:
    """Write PNG (``width * scale`` pixels) and SVG next to each other."""
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    png_path = stem_path.with_suffix(".png")
    svg_path = stem_path.with_suffix(".svg")
    fig.write_image(png_path, width=width, height=height, scale=scale)
    fig.write_image(svg_path, width=width, height=height)
    return png_path, svg_path
