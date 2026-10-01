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

TITLE_SIZE = 22
SUBTITLE_SIZE = 16
AXIS_TITLE_SIZE = 18
TICK_SIZE = 15
LEGEND_SIZE = 16
PANEL_TITLE_SIZE = 18

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

EXPORT_SCALE = 2
COMPARE_WIDTH = 1600
COMPARE_HEIGHT = 940
ABLATION_WIDTH = 1600
ABLATION_HEIGHT = 880
PRED_WIDTH = 1600
PRED_HEIGHT = 1340
FACET_WIDTH = 1700
FACET_HEIGHT = 1560

MANUSCRIPT_STEMS = (
    "paper_v1_discussion_compare_smape_by_grain",
    "paper_v1_discussion_compare_skill_by_grain",
    "paper_v1_discussion_compare_ablation_smape",
    "paper_v1_discussion_compare_ablation_skill",
    "paper_v1_discussion_pred_vs_actual_top",
)


def title_with_subtitle(title: str, subtitle: str) -> str:
    """Two-line title. The span keeps the subtitle smaller in Kaleido SVG."""
    return (
        f"{title}<br>"
        f"<span style='font-size:{SUBTITLE_SIZE}px;color:{MUTED}'>"
        f"{subtitle}</span>"
    )


def container_title(text: str, *, height: int) -> dict:
    """Pin the title block below the top edge so Kaleido does not clip the caps."""
    return {
        "text": text,
        "font": {"family": FONT, "size": TITLE_SIZE, "color": INK},
        "x": 0.5,
        "xanchor": "center",
        "y": 1 - (26 / max(height, 1)),
        "yanchor": "top",
        "yref": "container",
    }


def _axis_title(text: str) -> dict:
    return {
        "text": text,
        "font": {"family": FONT, "size": AXIS_TITLE_SIZE, "color": INK},
        "standoff": 10,
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
        legend={
            "orientation": "h",
            "yref": "container",
            "xref": "container",
            "x": 0.5,
            "xanchor": "center",
            "y": 0.012,
            "yanchor": "bottom",
            "font": {"family": FONT, "size": LEGEND_SIZE, "color": INK},
            "title_text": "",
            "itemsizing": "constant",
            "bgcolor": "rgba(255,255,255,0)",
            "tracegroupgap": 16,
        },
        margin={"t": 118, "b": 108, "l": 100, "r": 36},
        bargap=0.28,
        bargroupgap=0.1,
    )
    fig.update_xaxes(
        title=_axis_title("Family"),
        tickfont={"family": FONT, "size": TICK_SIZE, "color": INK},
        showline=True,
        linewidth=1,
        linecolor=INK,
        ticks="outside",
        ticklen=4,
        tickcolor=INK,
        automargin=False,
        showgrid=False,
    )
    fig.update_yaxes(
        title=_axis_title(y_title),
        tickfont={"family": FONT, "size": TICK_SIZE, "color": INK},
        showline=True,
        linewidth=1,
        linecolor=INK,
        ticks="outside",
        ticklen=4,
        tickcolor=INK,
        automargin=False,
        showgrid=True,
        gridcolor=GRID,
        gridwidth=1,
        zeroline=False,
    )
    fig.update_traces(
        marker_line_width=0.6,
        marker_line_color="white",
        selector={"type": "bar"},
    )


def place_panel_legends(
    fig: go.Figure,
    *,
    n_panels: int,
    height: int,
    margin_t: int,
    margin_b: int,
) -> None:
    """Reserve a band above each panel for that panel's legend.

    Subplot titles stay above the band. The legend is not shared across panels.
    """
    paper_h = max(height - margin_t - margin_b, 1)
    # Band holds the one-line legend plus air between that box and the traces.
    band = 96 / paper_h
    title_lift = 10 / paper_h
    for i in range(n_panels):
        y_name = "yaxis" if i == 0 else f"yaxis{i + 1}"
        x_name = "xaxis" if i == 0 else f"xaxis{i + 1}"
        y0, y1 = fig.layout[y_name].domain
        x0, _x1 = fig.layout[x_name].domain
        fig.layout[y_name].domain = (y0, y1 - band)
        if i < len(fig.layout.annotations):
            fig.layout.annotations[i].update(
                y=y1 + title_lift,
                yanchor="bottom",
                font={"family": FONT, "size": PANEL_TITLE_SIZE, "color": INK},
            )
        key = "legend" if i == 0 else f"legend{i + 1}"
        fig.layout[key] = {
            "xref": "paper",
            "yref": "paper",
            "x": x0,
            "y": y1 - (4 / paper_h),
            "xanchor": "left",
            "yanchor": "top",
            "orientation": "h",
            "bgcolor": "rgba(255,255,255,0.96)",
            "bordercolor": "#D0D0D0",
            "borderwidth": 1,
            "font": {"family": FONT, "size": 15, "color": INK},
            "itemsizing": "constant",
            "tracegroupgap": 8,
        }


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
