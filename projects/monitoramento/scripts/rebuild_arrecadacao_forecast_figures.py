"""Rebuild arrecadação forecast figures from the freeze. Do not refit models.

Reads ranked test CSVs and ``preds_*_test.json``. Writes PNG and SVG with
Kaleido, then copies the five manuscript PNGs and SVGs into the paper folder.

    python projects/monitoramento/scripts/rebuild_arrecadacao_forecast_figures.py

Official rank is raw test sMAPE.
Skill scores are the ``SS_SMAPE`` column already stored in the ranked CSV.
"""

from __future__ import annotations

import argparse
import json
import math
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
    ABLATION_WIDTH,
    ACTUAL_COLOR,
    COMPARE_HEIGHT,
    COMPARE_WIDTH,
    EXPORT_SCALE,
    FACET_HEIGHT,
    FACET_WIDTH,
    FAM_ORDER,
    FONT,
    FORECAST_STYLES,
    GRAIN_COLOR,
    GRAIN_ORDER,
    INK,
    MANUSCRIPT_STEMS,
    PRED_HEIGHT,
    PRED_WIDTH,
    ZERO_LINE,
    apply_grouped_bar_layout,
    container_title,
    place_panel_legends,
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


@dataclass(frozen=True)
class GrainSpec:
    key: str
    label: str
    panel_title: str
    ranked_name: str
    preds_name: str
    zero_floor: bool
    top_k: int


GRAINS: tuple[GrainSpec, ...] = (
    GrainSpec(
        "monthly",
        "Monthly",
        "Monthly",
        "metrics_monthly_test_ranked.csv",
        "preds_monthly_test.json",
        False,
        3,
    ),
    GrainSpec(
        "daily",
        "Daily A (cal)",
        "Daily A (calendar)",
        "metrics_daily_test_ranked.csv",
        "preds_daily_test.json",
        True,
        2,
    ),
    GrainSpec(
        "business_daily",
        "Daily B (biz days)",
        "Daily B (business)",
        "metrics_business_daily_test_ranked.csv",
        "preds_business_daily_test.json",
        True,
        2,
    ),
    GrainSpec(
        "weekly",
        "Weekly",
        "Weekly",
        "metrics_weekly_test_ranked.csv",
        "preds_weekly_test.json",
        True,
        3,
    ),
)


@dataclass(frozen=True)
class SeriesPanel:
    spec: GrainSpec
    x: pd.Series
    actual: np.ndarray
    forecasts: tuple[tuple[str, np.ndarray], ...]


def family_stem(name: str) -> str:
    """Map a freeze model id onto M0–M9 or E1–E4. Same rule as the notebook cell."""
    text = str(name)
    for prefix in ("D_", "W_", "BD_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    for family in FAM_ORDER:
        if text.startswith(family):
            return family
    raise SystemExit(f"Model id has no family stem: {name}")


def pretty_variant(name: str) -> str:
    """Short legend label. Same replacements as the notebook cell."""
    text = str(name)
    for prefix in ("D_", "W_", "BD_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = (
        text.replace("energy_selector_proxy_ablation", "proxy")
        .replace("naive_sazonal_lag", "seas")
        .replace("naive_lag", "lag")
        .replace("_regressor", "")
        .replace("_selective", "")
        .replace("_tuned", "")
        .replace("_torch", "")
    )
    if len(text) > 28:
        return text[:27] + "…"
    return text


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
    frame["variant"] = frame["modelo"].map(pretty_variant)
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


def series_panel(
    spec: GrainSpec, ranked: pd.DataFrame, blob: dict, *, top_k: int
) -> SeriesPanel:
    actual = np.asarray(blob["y_test"], dtype=float)
    dates = pd.to_datetime(blob["dates"])
    if len(dates) != len(actual):
        raise SystemExit(
            f"{spec.preds_name}: {len(dates)} dates and {len(actual)} y_test values."
        )
    ordered = ranked.sort_values(["SMAPE", "modelo"], kind="mergesort")
    forecasts: list[tuple[str, np.ndarray]] = []
    for modelo in ordered["modelo"]:
        if modelo in _PRED_META or modelo not in blob:
            continue
        values = np.asarray(blob[modelo], dtype=float)
        if len(values) != len(actual):
            raise SystemExit(
                f"{modelo} has length {len(values)}; y_test has length {len(actual)}."
            )
        forecasts.append((pretty_variant(modelo), values))
        if len(forecasts) == top_k:
            break
    if len(forecasts) < top_k:
        raise SystemExit(
            f"{spec.label}: found {len(forecasts)} prediction series, need {top_k}."
        )
    return SeriesPanel(spec, dates, actual, tuple(forecasts))


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
    fig: go.Figure, row: int, col: int, values: list[np.ndarray], *, zero_floor: bool
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
    fig.update_yaxes(
        range=[start, end],
        tickmode="array",
        tickvals=ticks,
        ticktext=[_millions_label(tick) for tick in ticks],
        title={
            "text": "BRL (millions)",
            "font": {"family": FONT, "size": 18, "color": INK},
            "standoff": 8,
        },
        tickfont={"family": FONT, "size": 15, "color": INK},
        showline=True,
        linewidth=1,
        linecolor=INK,
        ticks="outside",
        tickcolor=INK,
        showgrid=True,
        gridcolor="#E6E6E6",
        zeroline=False,
        automargin=False,
        row=row,
        col=col,
    )


def apply_date_axis(fig: go.Figure, row: int, col: int, grain_key: str) -> None:
    if grain_key == "monthly":
        fig.update_xaxes(
            tickformat="%b %Y",
            dtick="M2",
            tickangle=0,
            row=row,
            col=col,
        )
    else:
        fig.update_xaxes(
            tickformat="%d %b",
            nticks=6,
            tickangle=-28,
            row=row,
            col=col,
        )
    fig.update_xaxes(
        title={
            "text": "Date",
            "font": {"family": FONT, "size": 18, "color": INK},
            "standoff": 8,
        },
        tickfont={"family": FONT, "size": 14, "color": INK},
        showline=True,
        linewidth=1,
        linecolor=INK,
        ticks="outside",
        tickcolor=INK,
        showgrid=False,
        automargin=False,
        row=row,
        col=col,
    )


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
    zero_line: bool,
) -> go.Figure:
    plot = frame.loc[frame["grain"].isin(grains)].copy()
    plot["grain"] = pd.Categorical(plot["grain"], categories=grains, ordered=True)
    plot["family"] = pd.Categorical(plot["family"], categories=FAM_ORDER, ordered=True)
    plot = plot.sort_values(["family", "grain"])
    fig = px.bar(
        plot,
        x="family",
        y=y_col,
        color="grain",
        barmode="group",
        category_orders={"family": FAM_ORDER, "grain": grains},
        color_discrete_map=colors,
        template="plotly_white",
        hover_data={
            "variant": True,
            "modelo": True,
            "SMAPE": ":.2f",
            "SS_SMAPE": ":.3f",
        },
        labels={y_col: y_title, "family": "Family", "grain": "Grain"},
        title=title_with_subtitle(title, subtitle),
    )
    if zero_line:
        fig.add_hline(y=0, line_dash="dash", line_color=ZERO_LINE, line_width=1.6)
        y_min = float(plot[y_col].min())
        y_max = float(plot[y_col].max())
        pad = 0.06 * (y_max - y_min)
        fig.update_yaxes(range=[y_min - pad, y_max + pad])
    else:
        fig.update_yaxes(rangemode="tozero")
    apply_grouped_bar_layout(fig, y_title=y_title, height=height)
    return fig


def build_pred_figure(
    panels: list[SeriesPanel],
    *,
    title: str,
    subtitle: str,
    width: int,
    height: int,
) -> go.Figure:
    margin_t, margin_b = 156, 108
    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=tuple(panel.spec.panel_title for panel in panels),
        shared_xaxes=False,
        shared_yaxes=False,
        vertical_spacing=0.20,
        horizontal_spacing=0.10,
    )
    for index, panel in enumerate(panels):
        row = index // 2 + 1
        col = index % 2 + 1
        legend_name = "legend" if index == 0 else f"legend{index + 1}"
        short = len(panel.actual) <= 16
        for rank, (label, yhat) in enumerate(panel.forecasts):
            style = FORECAST_STYLES[rank % len(FORECAST_STYLES)]
            fig.add_trace(
                go.Scatter(
                    x=panel.x,
                    y=yhat,
                    name=label,
                    mode="lines+markers" if short else "lines",
                    line={"width": 2.4, "color": style["color"], "dash": style["dash"]},
                    marker={"size": 6, "color": style["color"]},
                    legend=legend_name,
                    legendrank=rank + 1,
                    hovertemplate="%{x|%Y-%m-%d}<br>"
                    + label
                    + ": %{y:,.0f} BRL<extra></extra>",
                ),
                row=row,
                col=col,
            )
        fig.add_trace(
            go.Scatter(
                x=panel.x,
                y=panel.actual,
                name="Actual",
                mode="lines+markers" if short else "lines",
                line={"width": 3.5, "color": ACTUAL_COLOR},
                marker={"size": 8, "color": ACTUAL_COLOR, "line": {"width": 0}},
                legend=legend_name,
                legendrank=0,
                hovertemplate="%{x|%Y-%m-%d}<br>Actual: %{y:,.0f} BRL<extra></extra>",
            ),
            row=row,
            col=col,
        )
        series = [panel.actual, *[yhat for _label, yhat in panel.forecasts]]
        apply_brl_axis(fig, row, col, series, zero_floor=panel.spec.zero_floor)
        apply_date_axis(fig, row, col, panel.spec.key)
    fig.update_layout(
        template="plotly_white",
        width=width,
        height=height,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": FONT, "size": 15, "color": INK},
        title=container_title(title_with_subtitle(title, subtitle), height=height),
        margin={"t": margin_t, "b": margin_b, "l": 108, "r": 36},
    )
    place_panel_legends(
        fig,
        n_panels=len(panels),
        height=height,
        margin_t=margin_t,
        margin_b=margin_b,
    )
    return fig


def _panel_labels(frame: pd.DataFrame) -> list[str]:
    """Short labels, unique inside one grain. Lowest sMAPE is first."""
    ordered = frame.sort_values(["SMAPE", "modelo"], kind="mergesort")
    labels = [pretty_variant(modelo) for modelo in ordered["modelo"]]
    if len(set(labels)) == len(labels):
        return labels
    return [str(modelo) for modelo in ordered["modelo"]]


def build_metric_facets(frames: dict[str, pd.DataFrame]) -> go.Figure:
    """Every ranked model, not the best-per-family aggregate.

    Each panel has its own category order. The lowest test sMAPE is at the top.
    """
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
            tickfont={"family": FONT, "size": 12, "color": INK},
            automargin=True,
            row=index // 2 + 1,
            col=index % 2 + 1,
        )
        fig.update_xaxes(
            title={
                "text": "sMAPE (%)",
                "font": {"family": FONT, "size": 16, "color": INK},
            },
            tickfont={"family": FONT, "size": 14, "color": INK},
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
        font={"family": FONT, "size": 15, "color": INK},
        title=container_title(
            title_with_subtitle(
                "Test sMAPE by model and grain",
                "Every ranked variant. Lower is better. Official rank is this score.",
            ),
            height=FACET_HEIGHT,
        ),
        margin={"t": 120, "b": 64, "l": 20, "r": 28},
    )
    for ann in fig.layout.annotations:
        ann.font = {"family": FONT, "size": 18, "color": INK}
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


def copy_manuscript(fig_dir: Path, manuscript_dir: Path) -> None:
    manuscript_dir.mkdir(parents=True, exist_ok=True)
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
    panels = [
        series_panel(
            spec,
            frames[spec.label],
            load_preds(freeze_dir / spec.preds_name),
            top_k=spec.top_k,
        )
        for spec in GRAINS
    ]
    best_only = [
        series_panel(
            spec,
            frames[spec.label],
            load_preds(freeze_dir / spec.preds_name),
            top_k=1,
        )
        for spec in GRAINS
    ]

    smape = build_grouped_bars(
        best,
        y_col="SMAPE",
        y_title="sMAPE (%)",
        title="Test sMAPE by family and grain",
        subtitle="Best variant in each family. Lower is better.",
        height=COMPARE_HEIGHT,
        grains=list(GRAIN_ORDER),
        colors=GRAIN_COLOR,
        zero_line=False,
    )
    skill = build_grouped_bars(
        best,
        y_col="SS_SMAPE",
        y_title="SS_sMAPE",
        title="Skill score by family and grain",
        subtitle="Best variant in each family. Higher is better. Dashed line: seasonal naive (SS = 0).",
        height=COMPARE_HEIGHT,
        grains=list(GRAIN_ORDER),
        colors=GRAIN_COLOR,
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
        zero_line=True,
    )
    pred = build_pred_figure(
        panels,
        title="Realized collection and the lowest-sMAPE forecasts",
        subtitle="Independent axes. Black line: realized collection. Rank is test sMAPE.",
        width=PRED_WIDTH,
        height=PRED_HEIGHT,
    )
    best_pred = build_pred_figure(
        best_only,
        title="Realized collection and the best test-sMAPE forecast",
        subtitle="One model per grain. Independent axes. Black line: realized collection.",
        width=PRED_WIDTH,
        height=PRED_HEIGHT,
    )
    facets = build_metric_facets(frames)

    exports = {
        "paper_v1_discussion_compare_smape_by_grain": (
            smape,
            COMPARE_WIDTH,
            COMPARE_HEIGHT,
        ),
        "paper_v1_discussion_compare_skill_by_grain": (
            skill,
            COMPARE_WIDTH,
            COMPARE_HEIGHT,
        ),
        "paper_v1_discussion_compare_ablation_smape": (
            abl_smape,
            ABLATION_WIDTH,
            ABLATION_HEIGHT,
        ),
        "paper_v1_discussion_compare_ablation_skill": (
            abl_skill,
            ABLATION_WIDTH,
            ABLATION_HEIGHT,
        ),
        "paper_v1_discussion_pred_vs_actual_top": (pred, PRED_WIDTH, PRED_HEIGHT),
        "paper_v1_real_vs_pred_best": (best_pred, PRED_WIDTH, PRED_HEIGHT),
        "paper_v1_test_metrics_all": (facets, FACET_WIDTH, FACET_HEIGHT),
    }
    for stem, (fig, width, height) in exports.items():
        png_path, svg_path = write_publication_figure(
            fig,
            fig_dir / stem,
            width=width,
            height=height,
            scale=args.scale,
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
    copy_manuscript(fig_dir, manuscript_dir)
    print(f"copied manuscript figures to {manuscript_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
