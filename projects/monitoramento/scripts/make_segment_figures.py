"""Large v1-style charts plus forecast-versus-actual scatters.

Reads saved test forecasts. Does not refit models.
The time chart shows the recent history and a zoom on validation plus test.
The scatter is the test block, with a 45-degree line.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from arrecadacao_figure_theme import (
    AXIS_TITLE_SIZE,
    FONT,
    INK,
    TICK_SIZE,
    TITLE_SIZE,
    write_publication_figure,
)
from plotly.subplots import make_subplots

SERIES_LABEL = {
    "total": "Total ICMS",
    "declarado": "ICMS Declarado",
    "autuado": "ICMS Autuado",
}
GRAIN_LABEL = {
    "monthly": "Monthly",
    "weekly": "Weekly",
    "daily": "Daily A (calendar)",
    "business_daily": "Daily B (Mon-Fri)",
}
VALUE_COL = {
    "monthly": "valor_total",
    "weekly": "valor_sum",
    "daily": "valor_sum",
    "business_daily": "valor_sum",
}
HISTORY = {"monthly": None, "weekly": 156, "daily": 420, "business_daily": 420}


def article_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "notebooks"
        / "articles"
        / "arrecadacao_forecast_v1_segmentado"
    )


def _layout(fig: go.Figure, title: str) -> None:
    fig.update_layout(
        template="plotly_white",
        font={"family": FONT, "size": TICK_SIZE, "color": INK},
        title={"text": title, "font": {"size": TITLE_SIZE}},
        legend={"font": {"size": TICK_SIZE}, "orientation": "h", "y": -0.18},
        margin={"l": 110, "r": 80, "t": 140, "b": 100},
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    fig.update_xaxes(title_font={"size": AXIS_TITLE_SIZE}, tickfont={"size": TICK_SIZE})
    fig.update_yaxes(title_font={"size": AXIS_TITLE_SIZE}, tickfont={"size": TICK_SIZE})


def _best_name(ranked_path: Path) -> str:
    ranked = pd.read_csv(ranked_path)
    return str(ranked.sort_values("SMAPE").iloc[0]["modelo"])


def plot_grain(series: str, grain: str, fig_dir: Path) -> list[Path]:
    root = article_dir() / "results" / series / grain
    state = json.loads((root / "state.json").read_text())
    best = _best_name(root / "metrics_test_ranked.csv")
    meta = state["meta"]
    frame = pd.read_csv(
        article_dir() / "data" / f"{series}_{grain}.csv", parse_dates=["data"]
    )
    frame = frame.sort_values("data").reset_index(drop=True)
    y_col = VALUE_COL[grain]
    actual = frame[y_col].to_numpy(float)
    dates = frame["data"]
    forecast = np.asarray(state["preds"][best], float)
    test_start = int(meta["val_end"])
    test_end = int(meta["test_end"])
    zoom_start = int(meta["train_end"])
    hist_n = HISTORY[grain]
    hist_start = 0 if hist_n is None else max(0, len(frame) - hist_n)
    label = f"{SERIES_LABEL[series]}, {GRAIN_LABEL[grain]}"
    unit = "BRL"

    time_fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        subplot_titles=("Recent history", "Validation and test"),
        vertical_spacing=0.14,
    )
    time_fig.add_trace(
        go.Scatter(
            x=dates.iloc[hist_start:],
            y=actual[hist_start:],
            name="Actual",
            mode="lines",
            line={"color": "black", "width": 2.5},
        ),
        row=1,
        col=1,
    )
    time_fig.add_trace(
        go.Scatter(
            x=dates.iloc[test_start:test_end],
            y=forecast,
            name=best,
            mode="lines",
            line={"color": "#0072B2", "width": 2.5, "dash": "dash"},
        ),
        row=1,
        col=1,
    )
    time_fig.add_trace(
        go.Scatter(
            x=dates.iloc[zoom_start:test_end],
            y=actual[zoom_start:test_end],
            name="Actual, holdout",
            mode="lines+markers",
            line={"color": "black", "width": 2.5},
            marker={"size": 7},
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    time_fig.add_trace(
        go.Scatter(
            x=dates.iloc[test_start:test_end],
            y=forecast,
            name="Forecast, test",
            mode="lines+markers",
            line={"color": "#0072B2", "width": 2.5, "dash": "dash"},
            marker={"size": 7, "symbol": "diamond"},
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    time_fig.update_yaxes(title_text=unit, row=1, col=1)
    time_fig.update_yaxes(title_text=unit, row=2, col=1)
    _layout(time_fig, f"{label}<br>Actual and best test forecast")
    time_stem = fig_dir / f"{series}_{grain}_forecast"
    write_publication_figure(time_fig, time_stem, width=1600, height=1100, scale=2)

    y_test = actual[test_start:test_end]
    low = float(min(np.min(y_test), np.min(forecast)))
    high = float(max(np.max(y_test), np.max(forecast)))
    pad = 0.04 * (high - low if high > low else 1.0)
    low -= pad
    high += pad
    scatter = go.Figure()
    scatter.add_trace(
        go.Scatter(
            x=[low, high],
            y=[low, high],
            mode="lines",
            name="45-degree line",
            line={"color": "black", "width": 2, "dash": "dash"},
        )
    )
    scatter.add_trace(
        go.Scatter(
            x=y_test,
            y=forecast,
            mode="markers",
            name="Test",
            marker={"size": 12, "color": "#0072B2"},
        )
    )
    scatter.update_xaxes(title_text=f"Actual ({unit})", range=[low, high])
    scatter.update_yaxes(
        title_text=f"Forecast ({unit})",
        range=[low, high],
        scaleanchor="x",
        scaleratio=1,
    )
    _layout(scatter, f"{label}<br>Test forecast versus actual")
    scatter_stem = fig_dir / f"{series}_{grain}_scatter"
    write_publication_figure(scatter, scatter_stem, width=1200, height=1200, scale=2)
    return [time_stem.with_suffix(".png"), scatter_stem.with_suffix(".png")]


def main() -> None:
    fig_dir = article_dir() / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for ranked in sorted(
        (article_dir() / "results").glob("*/*/metrics_test_ranked.csv")
    ):
        series = ranked.parents[1].name
        grain = ranked.parent.name
        written.extend(plot_grain(series, grain, fig_dir))
        print("wrote", series, grain, flush=True)
    print(f"{len(written)} png files", flush=True)


if __name__ == "__main__":
    main()
