"""Rebuild arrecadação forecast figures from the freeze. Do not refit models.

Reads ranked test CSVs and ``preds_*_test.json``. Writes PNG and SVG with
Kaleido, then copies the manuscript PNGs and SVGs into the paper folder.

    python projects/monitoramento/scripts/rebuild_arrecadacao_forecast_figures.py

Official rank is raw test sMAPE.
Skill scores are the ``SS_SMAPE`` column already stored in the ranked CSV.

MECE layout (NeurIPS body vs appendix):
  Main preds: Actual + leader only (2nd if sMAPE within 5% of leader).
  Main grains: monthly, daily B, weekly. Daily A pred stays appendix.
  Main residual: one three-panel residual (ŷ−y) for those leaders.
  Ranking bars / ablation bars: generated for the appendix (tables stay in body).
  Appendix small multiples: one panel per model, shared y, actual in light gray.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from arrecadacao_figure_theme import (
    ABLATION_COLOR,
    ABLATION_GRAIN_ORDER,
    ABLATION_HEIGHT,
    ABLATION_PATTERN,
    ABLATION_WIDTH,
    ACTUAL_COLOR,
    ACTUAL_DASH,
    ACTUAL_LIGHT,
    ACTUAL_LINE_WIDTH,
    AXIS_TITLE_SIZE,
    COMPARE_HEIGHT,
    COMPARE_WIDTH,
    EXPORT_SCALE,
    FACET_HEIGHT,
    FACET_WIDTH,
    FAM_ORDER,
    FONT,
    FORECAST_STYLES,
    GRAIN_COLOR,
    GRAIN_LEGEND_LABEL,
    GRAIN_ORDER,
    GRAIN_PATTERN,
    GRID,
    INK,
    LEGEND_SYMBOL_SCALE,
    LEGEND_SYMBOL_SCALE_BARS,
    LINE_MARGIN,
    MANUSCRIPT_STEMS,
    MARKER_SIZE_SHORT,
    PRED_HEIGHT,
    PRED_WIDTH,
    RESIDUAL_HEIGHT,
    RESIDUAL_WIDTH,
    SMALL_MULT_PANEL_H,
    SMALL_MULT_WIDTH,
    TICK_SIZE,
    ZERO_LINE,
    apply_grouped_bar_layout,
    bottom_legend,
    container_title,
    short_model_name,
    title_with_subtitle,
    write_publication_figure,
)
from plotly.subplots import make_subplots

_MONITOR = Path(__file__).resolve().parents[1]
_FREEZE_FILES = (
    "metrics_monthly_test_ranked.csv",
    "metrics_weekly_test_ranked.csv",
    "metrics_daily_test_ranked.csv",
    "metrics_business_daily_test_ranked.csv",
    "preds_monthly_test.json",
    "preds_weekly_test.json",
    "preds_daily_test.json",
    "preds_business_daily_test.json",
)
_PRED_META = {"dates", "y_test", "split", "construction"}
# Include a 2nd forecast on the main pred panel only when this close to the leader.
_CLOSE_REL = 0.05


@dataclass(frozen=True)
class GrainSpec:
    key: str
    label: str
    panel_title: str
    ranked_name: str
    preds_name: str
    zero_floor: bool
    # Max forecasts on the single pred-vs-actual chart (Actual + these).
    max_forecasts: int
    # Whether this grain's clean pred figure is a main-body claim.
    main_pred: bool


GRAINS: tuple[GrainSpec, ...] = (
    GrainSpec(
        "monthly",
        "Monthly",
        "Monthly",
        "metrics_monthly_test_ranked.csv",
        "preds_monthly_test.json",
        False,
        2,
        True,
    ),
    GrainSpec(
        "daily",
        "Daily A (cal)",
        "Daily A (calendar)",
        "metrics_daily_test_ranked.csv",
        "preds_daily_test.json",
        True,
        2,
        False,  # appendix
    ),
    GrainSpec(
        "business_daily",
        "Daily B (biz days)",
        "Daily B (business)",
        "metrics_business_daily_test_ranked.csv",
        "preds_business_daily_test.json",
        True,
        2,
        True,
    ),
    GrainSpec(
        "weekly",
        "Weekly",
        "Weekly",
        "metrics_weekly_test_ranked.csv",
        "preds_weekly_test.json",
        True,
        2,
        True,
    ),
)


@dataclass(frozen=True)
class SeriesPanel:
    spec: GrainSpec
    x: pd.Series
    actual: np.ndarray
    # (short_label, modelo_id, yhat)
    forecasts: tuple[tuple[str, str, np.ndarray], ...]


def family_stem(name: str) -> str:
    """Map a freeze model id onto M0–M9 or E1–E4. Same rule as the notebook cell."""
    text = str(name)
    for prefix in ("D_", "W_", "BD_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    for family in FAM_ORDER:
        if text.startswith(family):
            return family
    raise SystemExit(f"Model id has no family stem: {name}")


def pretty_variant(name: str) -> str:
    """Backward-compatible alias used by bar hover text."""
    return short_model_name(name)


def require_freeze(directory: Path) -> None:
    missing = [name for name in _FREEZE_FILES if not (directory / name).is_file()]
    if not missing:
        return
    lines = "\n".join(f"  {name}" for name in missing)
    raise SystemExit(
        f"Freeze files are missing from {directory}:\n{lines}\n"
        "Place the ranked CSVs and preds_*_test.json in that directory and rerun. "
        "Do not refit models to recreate them."
    )


def load_ranked(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "modelo" not in frame.columns or "SMAPE" not in frame.columns:
        raise SystemExit(f"{path.name} must contain modelo and SMAPE.")
    if "SS_SMAPE" not in frame.columns:
        raise SystemExit(
            f"{path.name} has no SS_SMAPE column. Skill scores are not recomputed."
        )
    if "stage" in frame.columns:
        frame = frame.loc[frame["stage"].eq("test")].copy()
    frame = frame.drop_duplicates(subset=["modelo"], keep="last")
    frame["SMAPE"] = pd.to_numeric(frame["SMAPE"], errors="coerce")
    frame["SS_SMAPE"] = pd.to_numeric(frame["SS_SMAPE"], errors="coerce")
    frame = frame.dropna(subset=["SMAPE", "SS_SMAPE"])
    frame["family"] = frame["modelo"].map(family_stem)
    frame["variant"] = frame["modelo"].map(short_model_name)
    return frame


def best_variant_per_family(frame: pd.DataFrame) -> pd.DataFrame:
    """Lowest test sMAPE inside each family. Ties break on the model id."""
    ordered = frame.sort_values(["SMAPE", "modelo"], kind="mergesort")
    best = ordered.groupby("family", as_index=False).first()
    missing = [family for family in FAM_ORDER if family not in set(best["family"])]
    if missing:
        raise SystemExit(f"Ranked CSV is missing families: {', '.join(missing)}")
    best["family"] = pd.Categorical(best["family"], categories=FAM_ORDER, ordered=True)
    return best.sort_values("family")


def assert_best_matches_min(full: pd.DataFrame, best: pd.DataFrame, grain: str) -> None:
    """The bar height must be the family's minimum freeze sMAPE, not a new score."""
    for family, group in full.groupby("family"):
        row = best.loc[best["family"].eq(family)]
        if row.empty:
            raise SystemExit(f"{grain}: no bar for family {family}")
        got = float(row.iloc[0]["SMAPE"])
        expected = float(group["SMAPE"].min())
        if not math.isclose(got, expected, rel_tol=0, abs_tol=1e-9):
            raise SystemExit(
                f"{grain} {family}: plotted sMAPE {got} != freeze minimum {expected}"
            )
        skill = float(row.iloc[0]["SS_SMAPE"])
        source = group.loc[group["modelo"].eq(row.iloc[0]["modelo"]), "SS_SMAPE"]
        if not math.isclose(skill, float(source.iloc[0]), rel_tol=0, abs_tol=1e-9):
            raise SystemExit(
                f"{grain} {family}: SS_SMAPE does not match the freeze row"
            )


def load_preds(path: Path) -> dict:
    try:
        blob = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse {path}: {exc}") from exc
    if "y_test" not in blob or "dates" not in blob:
        raise SystemExit(f"{path.name} must contain dates and y_test.")
    return blob


def _select_forecast_ids(
    ranked: pd.DataFrame, blob: dict, *, max_forecasts: int
) -> list[str]:
    """Leader always; 2nd only if within ``_CLOSE_REL`` of the leader sMAPE."""
    ordered = ranked.sort_values(["SMAPE", "modelo"], kind="mergesort")
    chosen: list[str] = []
    leader_smape: float | None = None
    for _, row in ordered.iterrows():
        modelo = row["modelo"]
        if modelo in _PRED_META or modelo not in blob:
            continue
        smape = float(row["SMAPE"])
        if not chosen:
            chosen.append(modelo)
            leader_smape = smape
            continue
        if len(chosen) >= max_forecasts:
            break
        assert leader_smape is not None
        if smape <= leader_smape * (1.0 + _CLOSE_REL):
            chosen.append(modelo)
        break  # only consider immediate runner-up for closeness
    return chosen


def series_panel(
    spec: GrainSpec, ranked: pd.DataFrame, blob: dict
) -> SeriesPanel:
    actual = np.asarray(blob["y_test"], dtype=float)
    dates = pd.to_datetime(blob["dates"])
    if len(dates) != len(actual):
        raise SystemExit(
            f"{spec.preds_name}: {len(dates)} dates and {len(actual)} y_test values."
        )
    forecasts: list[tuple[str, str, np.ndarray]] = []
    for modelo in _select_forecast_ids(
        ranked, blob, max_forecasts=spec.max_forecasts
    ):
        values = np.asarray(blob[modelo], dtype=float)
        if len(values) != len(actual):
            raise SystemExit(
                f"{modelo} has length {len(values)}; y_test has length {len(actual)}."
            )
        forecasts.append((short_model_name(modelo), modelo, values))
    if not forecasts:
        raise SystemExit(f"{spec.label}: no prediction series found for the leader.")
    return SeriesPanel(spec, dates, actual, tuple(forecasts))


def all_model_forecasts(
    ranked: pd.DataFrame, blob: dict
) -> list[tuple[str, str, np.ndarray]]:
    """Every ranked model that has a prediction vector, lowest sMAPE first."""
    actual_len = len(blob["y_test"])
    ordered = ranked.sort_values(["SMAPE", "modelo"], kind="mergesort")
    out: list[tuple[str, str, np.ndarray]] = []
    seen_labels: dict[str, int] = {}
    for modelo in ordered["modelo"]:
        if modelo in _PRED_META or modelo not in blob:
            continue
        values = np.asarray(blob[modelo], dtype=float)
        if len(values) != actual_len:
            raise SystemExit(
                f"{modelo} has length {len(values)}; y_test has length {actual_len}."
            )
        label = short_model_name(modelo)
        if label in seen_labels:
            seen_labels[label] += 1
            label = f"{label}/{seen_labels[label]}"
        else:
            seen_labels[label] = 1
        out.append((label, modelo, values))
    return out


def _nice_step(span: float) -> float:
    if span <= 0:
        return 1.0
    raw = span / 5
    power = 10 ** math.floor(math.log10(raw))
    for multiplier in (1, 2, 2.5, 5, 10):
        step = multiplier * power
        if step >= raw * 0.9:
            return step
    return 10 * power


def _millions_label(value: float) -> str:
    millions = value / 1e6
    if abs(millions - round(millions)) < 1e-6:
        return f"{millions:.0f}"
    return f"{millions:.1f}"


def apply_brl_axis(
    fig: go.Figure,
    values: list[np.ndarray],
    *,
    zero_floor: bool,
    row: int | None = None,
    col: int | None = None,
) -> None:
    finite = np.concatenate(
        [np.asarray(series, dtype=float).ravel() for series in values]
    )
    finite = finite[np.isfinite(finite)]
    lo = float(finite.min())
    hi = float(finite.max())
    if zero_floor:
        lo = min(0.0, lo)
    span = hi - lo
    pad = span * 0.12 if span else abs(hi) * 0.05 + 1.0
    y0 = 0.0 if zero_floor and lo >= 0 else lo - pad
    y1 = hi + pad
    step = _nice_step((y1 - y0) / 1e6) * 1e6
    start = math.floor((y0 + step * 1e-9) / step) * step
    end = math.ceil((y1 - step * 1e-9) / step) * step
    if end <= start:
        end = start + step
    count = round((end - start) / step)
    ticks = [start + index * step for index in range(count + 1)]
    kwargs = {
        "range": [start, end],
        "tickmode": "array",
        "tickvals": ticks,
        "ticktext": [_millions_label(tick) for tick in ticks],
        "title": {
            "text": "BRL (millions)",
            "font": {"family": FONT, "size": AXIS_TITLE_SIZE, "color": INK},
            "standoff": 28,
        },
        "tickfont": {"family": FONT, "size": TICK_SIZE, "color": INK},
        "showline": True,
        "linewidth": 1.6,
        "linecolor": INK,
        "ticks": "outside",
        "tickcolor": INK,
        "showgrid": True,
        "gridcolor": "#E6E6E6",
        "zeroline": False,
        "automargin": False,
    }
    if row is not None and col is not None:
        fig.update_yaxes(**kwargs, row=row, col=col)
    else:
        fig.update_yaxes(**kwargs)


def apply_date_axis(
    fig: go.Figure,
    grain_key: str,
    *,
    row: int | None = None,
    col: int | None = None,
    show_title: bool = True,
    tick_size: int | None = None,
) -> None:
    tickfont_size = TICK_SIZE if tick_size is None else tick_size
    if grain_key == "monthly":
        xkwargs: dict = {"tickformat": "%b %Y", "dtick": "M2", "tickangle": 0}
    else:
        xkwargs = {"tickformat": "%d %b", "dtick": "M1", "tickangle": 0}
    xkwargs.update(
        {
            "title": {
                "text": "Date" if show_title else "",
                "font": {"family": FONT, "size": AXIS_TITLE_SIZE, "color": INK},
                "standoff": 28,
            },
            "tickfont": {"family": FONT, "size": tickfont_size, "color": INK},
            "showline": True,
            "linewidth": 1.6,
            "linecolor": INK,
            "ticks": "outside",
            "tickcolor": INK,
            "showgrid": False,
            "automargin": False,
        }
    )
    if row is not None and col is not None:
        fig.update_xaxes(**xkwargs, row=row, col=col)
    else:
        fig.update_xaxes(**xkwargs)


def build_grouped_bars(
    frame: pd.DataFrame,
    *,
    y_col: str,
    y_title: str,
    title: str,
    subtitle: str,
    height: int,
    grains: list[str],
    colors: dict[str, str],
    patterns: dict[str, str],
    zero_line: bool,
) -> go.Figure:
    plot = frame.loc[frame["grain"].isin(grains)].copy()
    plot["grain"] = pd.Categorical(plot["grain"], categories=grains, ordered=True)
    plot["family"] = pd.Categorical(plot["family"], categories=FAM_ORDER, ordered=True)
    plot["grain_legend"] = plot["grain"].map(
        lambda g: GRAIN_LEGEND_LABEL.get(str(g), str(g))
    )
    legend_order = [GRAIN_LEGEND_LABEL.get(g, g) for g in grains]
    legend_colors = {
        GRAIN_LEGEND_LABEL.get(g, g): colors[g] for g in grains
    }
    legend_patterns = {
        GRAIN_LEGEND_LABEL.get(g, g): patterns[g] for g in grains
    }
    plot = plot.sort_values(["family", "grain"])
    fig = px.bar(
        plot,
        x="family",
        y=y_col,
        color="grain_legend",
        pattern_shape="grain_legend",
        barmode="group",
        category_orders={"family": FAM_ORDER, "grain_legend": legend_order},
        color_discrete_map=legend_colors,
        pattern_shape_map=legend_patterns,
        template="plotly_white",
        hover_data={
            "variant": True,
            "modelo": True,
            "SMAPE": ":.2f",
            "SS_SMAPE": ":.3f",
            "grain": True,
        },
        labels={
            y_col: y_title,
            "family": "Family",
            "grain_legend": "Grain",
        },
        title=title_with_subtitle(title, subtitle),
    )
    if zero_line:
        fig.add_hline(y=0, line_dash="dash", line_color=ZERO_LINE, line_width=3)
        y_min = float(plot[y_col].min())
        y_max = float(plot[y_col].max())
        pad = 0.06 * (y_max - y_min)
        fig.update_yaxes(range=[y_min - pad, y_max + pad])
    else:
        fig.update_yaxes(rangemode="tozero")
    apply_grouped_bar_layout(fig, y_title=y_title, height=height)
    return fig


def build_grain_forecast(
    panel: SeriesPanel,
    *,
    title: str,
    subtitle: str,
) -> go.Figure:
    """Clean pred-vs-actual: Actual (solid) + leader (dashed), optionally 2nd."""
    fig = go.Figure()
    short = len(panel.actual) <= 16
    # Actual first in the legend (solid black).
    fig.add_trace(
        go.Scatter(
            x=panel.x,
            y=panel.actual,
            name="Actual",
            mode="lines+markers" if short else "lines",
            line={"width": ACTUAL_LINE_WIDTH, "color": ACTUAL_COLOR, "dash": ACTUAL_DASH},
            marker={
                "size": MARKER_SIZE_SHORT + 2,
                "color": ACTUAL_COLOR,
                "line": {"width": 0},
            },
            legendrank=0,
            hovertemplate="%{x|%Y-%m-%d}<br>Actual: %{y:,.0f} BRL<extra></extra>",
        )
    )
    for rank, (label, _modelo, yhat) in enumerate(panel.forecasts):
        style = FORECAST_STYLES[rank % len(FORECAST_STYLES)]
        fig.add_trace(
            go.Scatter(
                x=panel.x,
                y=yhat,
                name=label,
                mode="lines+markers" if short else "lines",
                line={
                    "width": style["width"],
                    "color": style["color"],
                    "dash": style["dash"],
                },
                marker={"size": MARKER_SIZE_SHORT, "color": style["color"]},
                legendrank=rank + 1,
                hovertemplate="%{x|%Y-%m-%d}<br>"
                + label
                + ": %{y:,.0f} BRL<extra></extra>",
            )
        )
    series = [panel.actual, *[yhat for _l, _m, yhat in panel.forecasts]]
    apply_brl_axis(fig, series, zero_floor=panel.spec.zero_floor)
    apply_date_axis(fig, panel.spec.key)
    n_items = 1 + len(panel.forecasts)
    fig.update_layout(
        template="plotly_white",
        height=PRED_HEIGHT,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": TICK_SIZE, "color": INK},
        title=container_title(title_with_subtitle(title, subtitle), height=PRED_HEIGHT),
        legend=bottom_legend(
            height=PRED_HEIGHT,
            margin_bottom=LINE_MARGIN["b"],
            n_items=n_items,
            for_bars=False,
        ),
        margin=LINE_MARGIN,
    )
    return fig


def build_residual_leaders(
    panels: list[SeriesPanel],
) -> go.Figure:
    """One residual figure: ŷ−y for the main-claim grain leaders."""
    main = [p for p in panels if p.spec.main_pred]
    if len(main) != 3:
        raise SystemExit(f"Expected 3 main residual panels, got {len(main)}")
    fig = make_subplots(
        rows=1,
        cols=3,
        subplot_titles=tuple(
            f"{p.spec.panel_title}: {p.forecasts[0][0]}" for p in main
        ),
        horizontal_spacing=0.08,
        shared_yaxes=False,
    )
    for index, panel in enumerate(main):
        row, col = 1, index + 1
        label, _modelo, yhat = panel.forecasts[0]
        residual = np.asarray(yhat, dtype=float) - np.asarray(panel.actual, dtype=float)
        fig.add_hline(
            y=0,
            line_dash="dash",
            line_color=ZERO_LINE,
            line_width=2,
            row=row,
            col=col,
        )
        fig.add_trace(
            go.Scatter(
                x=panel.x,
                y=residual / 1e6,
                name=label if index == 0 else f"{label}_{index}",
                mode="lines+markers" if len(residual) <= 16 else "lines",
                line={"width": 6, "color": FORECAST_STYLES[0]["color"]},
                marker={"size": 14, "color": FORECAST_STYLES[0]["color"]},
                showlegend=False,
                hovertemplate="%{x|%Y-%m-%d}<br>residual %{y:.2f} M BRL<extra></extra>",
            ),
            row=row,
            col=col,
        )
        fig.update_yaxes(
            title={
                "text": "ŷ−y (M BRL)" if index == 0 else "",
                "font": {"family": FONT, "size": 36, "color": INK},
            },
            tickfont={"family": FONT, "size": 32, "color": INK},
            showgrid=True,
            gridcolor=GRID,
            zeroline=False,
            showline=True,
            linecolor=INK,
            row=row,
            col=col,
        )
        apply_date_axis(
            fig,
            panel.spec.key,
            row=row,
            col=col,
            show_title=(index == 1),
            tick_size=30,
        )
    fig.update_layout(
        template="plotly_white",
        height=RESIDUAL_HEIGHT,
        width=RESIDUAL_WIDTH,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": 32, "color": INK},
        title=container_title(
            title_with_subtitle(
                "Holdout residual of each grain leader",
                "Residual is forecast minus actual (millions of BRL). Zero is a perfect hit.",
            ),
            height=RESIDUAL_HEIGHT,
        ),
        margin={"t": 260, "b": 160, "l": 120, "r": 48},
        showlegend=False,
    )
    for ann in fig.layout.annotations:
        ann.font = {"family": FONT, "size": 36, "color": INK}
    return fig


def build_small_multiples(
    spec: GrainSpec,
    ranked: pd.DataFrame,
    blob: dict,
) -> go.Figure:
    """One panel per model; shared y; actual in light gray in every panel."""
    actual = np.asarray(blob["y_test"], dtype=float)
    dates = pd.to_datetime(blob["dates"])
    forecasts = all_model_forecasts(ranked, blob)
    if not forecasts:
        raise SystemExit(f"{spec.label}: no models for small multiples")
    n = len(forecasts)
    ncols = 4
    nrows = int(math.ceil(n / ncols))
    height = 220 + nrows * SMALL_MULT_PANEL_H
    fig = make_subplots(
        rows=nrows,
        cols=ncols,
        subplot_titles=tuple(label for label, _m, _y in forecasts)
        + tuple("" for _ in range(nrows * ncols - n)),
        vertical_spacing=min(0.06, 0.5 / max(nrows, 1)),
        horizontal_spacing=0.05,
        shared_yaxes=True,
        shared_xaxes=True,
    )
    y_series = [actual, *[yhat for _l, _m, yhat in forecasts]]
    finite = np.concatenate([np.asarray(s, dtype=float).ravel() for s in y_series])
    finite = finite[np.isfinite(finite)]
    lo = float(finite.min())
    hi = float(finite.max())
    if spec.zero_floor:
        lo = min(0.0, lo)
    pad = (hi - lo) * 0.08 if hi > lo else abs(hi) * 0.05 + 1.0
    y0 = 0.0 if spec.zero_floor and lo >= 0 else lo - pad
    y1 = hi + pad

    for index, (label, _modelo, yhat) in enumerate(forecasts):
        row = index // ncols + 1
        col = index % ncols + 1
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=actual,
                name="Actual",
                mode="lines",
                line={"width": 2.5, "color": ACTUAL_LIGHT},
                showlegend=(index == 0),
                legendrank=0,
                hoverinfo="skip",
            ),
            row=row,
            col=col,
        )
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=yhat,
                name="Forecast",
                mode="lines",
                line={"width": 3.2, "color": FORECAST_STYLES[0]["color"]},
                showlegend=(index == 0),
                legendrank=1,
                hovertemplate="%{x|%Y-%m-%d}<br>"
                + label
                + ": %{y:,.0f}<extra></extra>",
            ),
            row=row,
            col=col,
        )
        fig.update_yaxes(
            range=[y0, y1],
            tickfont={"family": FONT, "size": 22, "color": INK},
            showgrid=True,
            gridcolor=GRID,
            zeroline=False,
            showline=True,
            linecolor=INK,
            title={
                "text": "BRL (M)" if col == 1 else "",
                "font": {"family": FONT, "size": 24, "color": INK},
            },
            tickvals=None,
            row=row,
            col=col,
        )
        # Compact millions ticks
        step = _nice_step((y1 - y0) / 1e6) * 1e6
        start = math.floor((y0 + step * 1e-9) / step) * step
        end = math.ceil((y1 - step * 1e-9) / step) * step
        if end <= start:
            end = start + step
        count = round((end - start) / step)
        ticks = [start + i * step for i in range(count + 1)]
        fig.update_yaxes(
            tickmode="array",
            tickvals=ticks,
            ticktext=[_millions_label(t) for t in ticks],
            range=[start, end],
            row=row,
            col=col,
        )
        show_x_title = row == nrows
        apply_date_axis(
            fig,
            spec.key,
            row=row,
            col=col,
            show_title=show_x_title and col == 2,
            tick_size=20,
        )

    # Hide empty subplot frames
    for index in range(n, nrows * ncols):
        row = index // ncols + 1
        col = index % ncols + 1
        fig.update_xaxes(visible=False, row=row, col=col)
        fig.update_yaxes(visible=False, row=row, col=col)

    fig.update_layout(
        template="plotly_white",
        height=height,
        width=SMALL_MULT_WIDTH,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": 24, "color": INK},
        title=container_title(
            title_with_subtitle(
                f"{spec.panel_title}: every model vs actual",
                "Light gray: realized collection (shared y). Orange: that panel's forecast.",
            ),
            height=height,
        ),
        legend=bottom_legend(
            height=height, margin_bottom=160, n_items=2, for_bars=False
        ),
        margin={"t": 240, "b": 160, "l": 100, "r": 40},
    )
    for ann in fig.layout.annotations:
        # subplot titles + main title
        if ann.text and "<span" not in (ann.text or ""):
            ann.font = {"family": FONT, "size": 28, "color": INK}
    return fig


def _panel_labels(frame: pd.DataFrame) -> list[str]:
    """Short labels, unique inside one grain. Lowest sMAPE is first."""
    ordered = frame.sort_values(["SMAPE", "modelo"], kind="mergesort")
    labels = [short_model_name(modelo) for modelo in ordered["modelo"]]
    if len(set(labels)) == len(labels):
        return labels
    return [str(modelo) for modelo in ordered["modelo"]]


def build_metric_facets(frames: dict[str, pd.DataFrame]) -> go.Figure:
    """Every ranked model, not the best-per-family aggregate."""
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=tuple(spec.panel_title for spec in GRAINS),
        vertical_spacing=0.12,
        horizontal_spacing=0.20,
    )
    for index, spec in enumerate(GRAINS):
        frame = frames[spec.label].sort_values(["SMAPE", "modelo"], kind="mergesort")
        labels = _panel_labels(frame)
        fig.add_trace(
            go.Bar(
                x=frame["SMAPE"],
                y=labels,
                orientation="h",
                marker={"color": GRAIN_COLOR[spec.label]},
                showlegend=False,
                hovertemplate="%{y}<br>sMAPE %{x:.2f}<extra></extra>",
            ),
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
        fig.update_yaxes(
            categoryorder="array",
            categoryarray=labels,
            autorange="reversed",
            tickfont={"family": FONT, "size": 32, "color": INK},
            automargin=True,
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
        fig.update_xaxes(
            title={
                "text": "sMAPE (%)",
                "font": {"family": FONT, "size": 36, "color": INK},
            },
            tickfont={"family": FONT, "size": 32, "color": INK},
            rangemode="tozero",
            showgrid=True,
            gridcolor="#E6E6E6",
            zeroline=False,
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
    fig.update_layout(
        template="plotly_white",
        height=FACET_HEIGHT,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": 32, "color": INK},
        title=container_title(
            title_with_subtitle(
                "Test sMAPE by model and grain",
                "Every ranked variant. Lower is better. Official rank is this score.",
            ),
            height=FACET_HEIGHT,
        ),
        margin={"t": 280, "b": 120, "l": 36, "r": 48},
    )
    for ann in fig.layout.annotations:
        ann.font = {"family": FONT, "size": 40, "color": INK}
    return fig


def print_official_top3(frames: dict[str, pd.DataFrame]) -> None:
    print("Official top 3 by test sMAPE (ranked CSV, not recomputed)")
    for label, frame in frames.items():
        print(f"[{label}]")
        top = frame.nsmallest(3, "SMAPE")
        for _, row in top.iterrows():
            print(
                f"  {row['modelo']}  sMAPE={float(row['SMAPE']):.4f}"
                f"  SS_sMAPE={float(row['SS_SMAPE']):.4f}"
                f"  short={short_model_name(row['modelo'])}"
            )


def assemble_best(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for spec in GRAINS:
        full = frames[spec.label]
        best = best_variant_per_family(full)
        assert_best_matches_min(full, best, spec.label)
        best = best.copy()
        best["grain"] = spec.label
        parts.append(best)
    return pd.concat(parts, ignore_index=True)


def save_alias(src_stem: Path, alias: str) -> None:
    for suffix in (".png", ".svg"):
        shutil.copy2(src_stem.with_suffix(suffix), src_stem.with_name(alias + suffix))


def remove_stale_multiplots(*directories: Path) -> None:
    """Drop the old 2×2 pred exports so the manuscript cannot include them."""
    stale = (
        "paper_v1_discussion_pred_vs_actual_top",
        "paper_v1_real_vs_pred_best",
    )
    for directory in directories:
        for stem in stale:
            for suffix in (".png", ".svg"):
                path = directory / f"{stem}{suffix}"
                if path.is_file():
                    path.unlink()
                    print(f"removed {path}")


def assert_svg_readable(svg_path: Path, *, expect_labels: list[str] | None = None) -> None:
    """Fail when the title is clipped, legend meets the axis, or a label is truncated."""
    text = svg_path.read_text()
    height_match = re.search(r'\bheight="([0-9.]+)"', text)
    title = re.search(
        r'<text class="gtitle"[^>]*\sy="([0-9.]+)"[^>]*font-size:\s*([0-9.]+)px',
        text,
    )
    axis = re.search(
        r'<text class="xtitle"[^>]*\sy="([0-9.]+)"[^>]*font-size:\s*([0-9.]+)px',
        text,
    )
    axis_shift = re.search(
        r'<g class="g-xtitle" transform="translate\(0,(-?[0-9.]+)\)"',
        text,
    )
    legend = re.search(
        r'<g class="legend"[^>]*transform="translate\([0-9.]+,([0-9.]+)\)"',
        text,
    )
    trace = re.search(
        r'<g class="traces" transform="translate\(0,([0-9.]+)\)"[^>]*>\s*'
        r'<text class="legendtext"[^>]*\sy="([0-9.]+)"[^>]*font-size:\s*([0-9.]+)px',
        text,
    )
    # Residual / small-multiples may omit a bottom legend; skip legend geometry then.
    if expect_labels:
        plain = re.sub(r"<[^>]+>", "", text)
        for label in expect_labels:
            if label not in plain and label not in text:
                raise SystemExit(
                    f"{svg_path.name}: expected legend/panel label missing: {label!r}"
                )
    if title and height_match:
        title_cap = float(title.group(1)) - 0.78 * float(title.group(2))
        if title_cap < 18:
            raise SystemExit(
                f"{svg_path.name}: title cap at {title_cap:.1f}px is clipped"
            )
    # Small multiples / residual panels may omit a shared xtitle or bottom legend.
    if legend is None or trace is None or axis is None or height_match is None:
        return
    height = float(height_match.group(1))
    title_cap = float(title.group(1)) - 0.78 * float(title.group(2))
    if title_cap < 18:
        raise SystemExit(f"{svg_path.name}: title cap at {title_cap:.1f}px is clipped")
    shift = float(axis_shift.group(1)) if axis_shift else 0.0
    axis_bottom = float(axis.group(1)) + shift + 0.28 * float(axis.group(2))
    legend_shift = float(legend.group(1)) + float(trace.group(1))
    legend_baseline = legend_shift + float(trace.group(2))
    legend_size = float(trace.group(3))
    legend_cap = legend_baseline - 0.78 * legend_size
    gap = legend_cap - axis_bottom
    if gap < 18:
        raise SystemExit(f"{svg_path.name}: axis title and legend gap is {gap:.1f}px")
    legend_bottom = legend_baseline + 0.30 * legend_size
    # Two-row legends extend further; allow the last row via a looser edge check
    # by scanning all legendtext y positions.
    legend_ys = [
        float(m.group(1))
        for m in re.finditer(
            r'<g class="traces" transform="translate\([^,]+,([0-9.]+)\)"',
            text,
        )
    ]
    if legend_ys:
        deepest = float(legend.group(1)) + max(legend_ys) + 0.30 * legend_size
        if height - deepest < 8:
            raise SystemExit(
                f"{svg_path.name}: legend ends {height - deepest:.1f}px from the edge"
            )
    elif height - legend_bottom < 12:
        raise SystemExit(
            f"{svg_path.name}: legend ends {height - legend_bottom:.1f}px from the edge"
        )

    symbol = re.search(
        r'<g class="legendpoints">[^/][\s\S]*?scale\(([0-9.]+)\)',
        text,
    )
    has_symbol_paths = bool(
        re.search(r'<g class="legendpoints">\s*<path\b', text)
    )
    line_stroke = re.search(
        r'<g class="legendlines">[\s\S]*?stroke-width:\s*([0-9.]+)px',
        text,
    )
    # Accept either the line or bar symbol scale.
    if has_symbol_paths:
        if not symbol:
            raise SystemExit(f"{svg_path.name}: legendpoints missing scale(...)")
        got = float(symbol.group(1))
        allowed = {LEGEND_SYMBOL_SCALE, LEGEND_SYMBOL_SCALE_BARS}
        if not any(abs(got - a) < 0.01 for a in allowed):
            raise SystemExit(
                f"{svg_path.name}: legend symbol scale {got} not in {allowed}"
            )
    if line_stroke:
        got = float(line_stroke.group(1))
        expected_min = 5.0 * min(LEGEND_SYMBOL_SCALE, LEGEND_SYMBOL_SCALE_BARS) - 0.05
        if got + 0.01 < expected_min:
            raise SystemExit(
                f"{svg_path.name}: legend line stroke {got}px < {expected_min}px"
            )


def copy_manuscript(fig_dir: Path, manuscript_dir: Path) -> None:
    manuscript_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous manuscript figures that are no longer in MANUSCRIPT_STEMS
    # only for our paper_v1_* stems — keep anything else untouched.
    keep = {f"{stem}{suf}" for stem in MANUSCRIPT_STEMS for suf in (".png", ".svg")}
    for path in manuscript_dir.glob("paper_v1_*"):
        if path.name not in keep:
            path.unlink()
            print(f"removed stale manuscript figure {path.name}")
    for stem in MANUSCRIPT_STEMS:
        for suffix in (".png", ".svg"):
            source = fig_dir / f"{stem}{suffix}"
            if not source.is_file():
                raise SystemExit(f"Expected export is missing: {source}")
            shutil.copy2(source, manuscript_dir / source.name)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freeze-dir",
        type=Path,
        default=_MONITOR / "output/articles/arrecadacao_forecast_paper_v1",
    )
    parser.add_argument(
        "--fig-dir",
        type=Path,
        default=_MONITOR / "output/figures/articles/arrecadacao_forecast_paper_v1",
    )
    parser.add_argument(
        "--manuscript-dir",
        type=Path,
        default=_MONITOR / "manuscript/arrecadacao_forecast_v1/figures",
    )
    parser.add_argument("--scale", type=int, default=EXPORT_SCALE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.scale < 1:
        raise SystemExit("--scale must be a positive integer.")
    freeze_dir = args.freeze_dir.resolve()
    fig_dir = args.fig_dir.resolve()
    manuscript_dir = args.manuscript_dir.resolve()
    require_freeze(freeze_dir)

    frames = {spec.label: load_ranked(freeze_dir / spec.ranked_name) for spec in GRAINS}
    print_official_top3(frames)
    best = assemble_best(frames)
    blobs = {spec.key: load_preds(freeze_dir / spec.preds_name) for spec in GRAINS}
    panels = [
        series_panel(spec, frames[spec.label], blobs[spec.key]) for spec in GRAINS
    ]
    for panel in panels:
        names = ", ".join(label for label, _m, _y in panel.forecasts)
        print(
            f"pred panel [{panel.spec.label}] series: Actual + {names} "
            f"(n_forecasts={len(panel.forecasts)})"
        )

    grain_titles = {
        "monthly": (
            "Monthly holdout",
            "Solid black: actual. Dashed: sMAPE leader (E2).",
        ),
        "daily": (
            "Grain A (calendar days)",
            "Solid black: actual. Dashed: sMAPE leader.",
        ),
        "business_daily": (
            "Grain B (business days)",
            "Solid black: actual. Dashed: sMAPE leader (and 2nd if within 5%).",
        ),
        "weekly": (
            "Weekly holdout",
            "Solid black: actual. Dashed: sMAPE leader (TimesFM).",
        ),
    }
    grain_stems = {
        "monthly": "paper_v1_discussion_pred_vs_actual_monthly",
        "daily": "paper_v1_discussion_pred_vs_actual_daily_a",
        "business_daily": "paper_v1_discussion_pred_vs_actual_daily_b",
        "weekly": "paper_v1_discussion_pred_vs_actual_weekly",
    }
    grain_figures = {
        grain_stems[panel.spec.key]: build_grain_forecast(
            panel,
            title=grain_titles[panel.spec.key][0],
            subtitle=grain_titles[panel.spec.key][1],
        )
        for panel in panels
    }
    residual = build_residual_leaders(panels)
    small_stems = {
        "monthly": "paper_v1_appendix_small_multiples_monthly",
        "weekly": "paper_v1_appendix_small_multiples_weekly",
        "daily": "paper_v1_appendix_small_multiples_daily_a",
        "business_daily": "paper_v1_appendix_small_multiples_daily_b",
    }
    small_figs = {
        small_stems[spec.key]: build_small_multiples(
            spec, frames[spec.label], blobs[spec.key]
        )
        for spec in GRAINS
    }

    smape = build_grouped_bars(
        best,
        y_col="SMAPE",
        y_title="sMAPE (%)",
        title="Test sMAPE by family and grain",
        subtitle="Best variant in each family. Lower is better.",
        height=COMPARE_HEIGHT,
        grains=list(GRAIN_ORDER),
        colors=GRAIN_COLOR,
        patterns=GRAIN_PATTERN,
        zero_line=False,
    )
    skill = build_grouped_bars(
        best,
        y_col="SS_SMAPE",
        y_title="SS_sMAPE",
        title="Skill score by family and grain",
        subtitle="Higher is better. Dashed line: seasonal naive (SS = 0).",
        height=COMPARE_HEIGHT,
        grains=list(GRAIN_ORDER),
        colors=GRAIN_COLOR,
        patterns=GRAIN_PATTERN,
        zero_line=True,
    )
    abl_smape = build_grouped_bars(
        best,
        y_col="SMAPE",
        y_title="sMAPE (%)",
        title="Ablation A vs B: test sMAPE",
        subtitle="Calendar days (A) and business days (B). Lower is better.",
        height=ABLATION_HEIGHT,
        grains=list(ABLATION_GRAIN_ORDER),
        colors=ABLATION_COLOR,
        patterns=ABLATION_PATTERN,
        zero_line=False,
    )
    abl_skill = build_grouped_bars(
        best,
        y_col="SS_SMAPE",
        y_title="SS_sMAPE",
        title="Ablation A vs B: skill score",
        subtitle="Reference is lag 7 on A and lag 5 on B. Higher is better.",
        height=ABLATION_HEIGHT,
        grains=list(ABLATION_GRAIN_ORDER),
        colors=ABLATION_COLOR,
        patterns=ABLATION_PATTERN,
        zero_line=True,
    )
    facets = build_metric_facets(frames)

    exports: dict[str, tuple[go.Figure, int, int, float]] = {
        "paper_v1_discussion_compare_smape_by_grain": (
            smape,
            COMPARE_WIDTH,
            COMPARE_HEIGHT,
            LEGEND_SYMBOL_SCALE_BARS,
        ),
        "paper_v1_discussion_compare_skill_by_grain": (
            skill,
            COMPARE_WIDTH,
            COMPARE_HEIGHT,
            LEGEND_SYMBOL_SCALE_BARS,
        ),
        "paper_v1_discussion_compare_ablation_smape": (
            abl_smape,
            ABLATION_WIDTH,
            ABLATION_HEIGHT,
            LEGEND_SYMBOL_SCALE_BARS,
        ),
        "paper_v1_discussion_compare_ablation_skill": (
            abl_skill,
            ABLATION_WIDTH,
            ABLATION_HEIGHT,
            LEGEND_SYMBOL_SCALE_BARS,
        ),
        "paper_v1_test_metrics_all": (
            facets,
            FACET_WIDTH,
            FACET_HEIGHT,
            LEGEND_SYMBOL_SCALE,
        ),
        "paper_v1_discussion_residual_leaders": (
            residual,
            RESIDUAL_WIDTH,
            RESIDUAL_HEIGHT,
            1.0,
        ),
    }
    for stem, fig in grain_figures.items():
        exports[stem] = (fig, PRED_WIDTH, PRED_HEIGHT, LEGEND_SYMBOL_SCALE)
    for stem, fig in small_figs.items():
        exports[stem] = (
            fig,
            SMALL_MULT_WIDTH,
            fig.layout.height or (220 + 4 * SMALL_MULT_PANEL_H),
            LEGEND_SYMBOL_SCALE,
        )

    for stem, (fig, width, height, sym_scale) in exports.items():
        png_path, svg_path = write_publication_figure(
            fig,
            fig_dir / stem,
            width=width,
            height=int(height),
            scale=args.scale,
            legend_symbol_scale=sym_scale,
        )
        print(f"wrote {png_path}")
        print(f"wrote {svg_path}")

    save_alias(
        fig_dir / "paper_v1_discussion_compare_smape_by_grain",
        "paper_v1_discussion_smape_monthly_vs_daily",
    )
    save_alias(
        fig_dir / "paper_v1_discussion_compare_skill_by_grain",
        "paper_v1_discussion_skill_score_by_grain",
    )
    save_alias(
        fig_dir / "paper_v1_discussion_compare_ablation_smape",
        "paper_v1_discussion_ablation_A_vs_B_smape",
    )
    save_alias(
        fig_dir / "paper_v1_discussion_compare_ablation_skill",
        "paper_v1_discussion_ablation_A_vs_B_skill",
    )

    # Legend / label expectations for QA
    expect: dict[str, list[str]] = {
        "paper_v1_discussion_pred_vs_actual_monthly": ["Actual", "E2"],
        "paper_v1_discussion_pred_vs_actual_daily_b": ["Actual", "TimesFM"],
        "paper_v1_discussion_pred_vs_actual_weekly": ["Actual", "TimesFM"],
        "paper_v1_discussion_pred_vs_actual_daily_a": ["Actual", "TimesFM"],
        "paper_v1_discussion_compare_smape_by_grain": [
            "Monthly",
            "Weekly",
            "Daily A",
            "Daily B",
        ],
        "paper_v1_discussion_compare_skill_by_grain": [
            "Monthly",
            "Weekly",
            "Daily A",
            "Daily B",
        ],
        "paper_v1_discussion_compare_ablation_smape": ["Daily A", "Daily B"],
        "paper_v1_discussion_compare_ablation_skill": ["Daily A", "Daily B"],
    }
    # Add HGB on daily B if the panel included it.
    for panel in panels:
        if panel.spec.key == "business_daily" and len(panel.forecasts) >= 2:
            expect["paper_v1_discussion_pred_vs_actual_daily_b"].append(
                panel.forecasts[1][0]
            )

    for stem in MANUSCRIPT_STEMS:
        assert_svg_readable(
            fig_dir / f"{stem}.svg",
            expect_labels=expect.get(stem),
        )
    copy_manuscript(fig_dir, manuscript_dir)
    remove_stale_multiplots(fig_dir, manuscript_dir)
    print(f"copied manuscript figures to {manuscript_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
