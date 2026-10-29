"""Color figures and result tables for the segmented VAT forecast.

Reads the saved v1 forecasts. Does not refit any model.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

# Print size: these figures are included at the NeurIPS text width (5.5 in).
TEXT_WIDTH = 5.5
FONT = "DejaVu Sans"
INK = "#1A1A1A"
MUTED = "#5C6570"
GRID = "#E6E6E6"
TEAL = "#0E7C7B"
OTHER_BAR = "#4C78A8"
NAIVE_COLOR = "#88CCEE"

SERIES_ORDER = ("total", "declarado", "autuado")
SERIES_LABEL = {
    "total": "Total",
    "declarado": "Declarado",
    "autuado": "Autuado",
}
SERIES_COLOR = {
    "total": TEAL,
    "declarado": "#6A3D9A",
    "autuado": "#E66101",
}
GRAIN_ORDER = ("monthly", "weekly", "business_daily", "daily")
GRAIN_LABEL = {
    "monthly": "Month",
    "weekly": "Week",
    "business_daily": "Monday to Friday",
    "daily": "Calendar day",
}
TABLE_GRAIN = {
    "monthly": "Month",
    "weekly": "Week",
    "business_daily": "Mon to Fri",
    "daily": "Calendar day",
}
MAIN_GRAINS = ("monthly", "weekly", "business_daily")

# Hue identifies the model. The same hue is used in every figure.
MODEL_COLOR = {
    "M1 least squares": "#332288",
    "M1 ridge": "#117733",
    "M2 SARIMAX": "#44AA99",
    "M3 boosting": "#E69F00",
    "M4 Prophet": "#CC79A7",
    "M5 MLP": "#882255",
    "M6 forest": "#AA4499",
    "M7 Gaussian process": "#999933",
    "M8 kernel ridge": "#661100",
    "M9 TimesFM": "#0072B2",
    "E2 proxy": "#D55E00",
    "E3 TimeGrad": "#009E73",
    "E4 ScoreGrad": "#56B4E9",
    "E1 TEM": "#222222",
}
FAMILY_RANK = {
    "M1 least squares": 20,
    "M1 ridge": 21,
    "M2 SARIMAX": 30,
    "M3 boosting": 40,
    "M4 Prophet": 50,
    "M5 MLP": 60,
    "M6 forest": 70,
    "M7 Gaussian process": 80,
    "M8 kernel ridge": 90,
    "M9 TimesFM": 100,
    "E1 TEM": 110,
    "E2 proxy": 120,
    "E3 TimeGrad": 130,
    "E4 ScoreGrad": 140,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def article_dir() -> Path:
    return (
        _repo_root()
        / "projects/monitoramento/notebooks/articles"
        / "arrecadacao_forecast_v1_segmentado"
    )


def manuscript_dir() -> Path:
    return _repo_root() / "projects/monitoramento/manuscript/arrecadacao_forecast_v1"


def _font_setup() -> None:
    plt.rcParams.update(
        {
            "font.family": FONT,
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "text.color": INK,
            "axes.linewidth": 0.6,
            "xtick.major.width": 0.5,
            "ytick.major.width": 0.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def short_name(raw: str) -> str:
    """Stable display name. The same model keeps this name on every grain."""
    text = str(raw)
    for prefix in ("BD_", "D_", "W_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if "naive_sazonal_lag" in text:
        lag = text.split("naive_sazonal_lag", 1)[1]
        return f"M0 seasonal ({lag})"
    if "naive_lag" in text:
        lag = text.split("naive_lag", 1)[1]
        return f"M0 lag-{lag}"
    if "OLS" in text:
        return "M1 least squares"
    if "Ridge" in text:
        return "M1 ridge"
    if "SARIMAX" in text:
        return "M2 SARIMAX"
    if "HGB" in text:
        return "M3 boosting"
    if "Prophet" in text:
        return "M4 Prophet"
    if "MLP" in text:
        return "M5 MLP"
    if "ARF" in text:
        return "M6 forest"
    if "GPR" in text:
        return "M7 Gaussian process"
    if "KRR" in text:
        return "M8 kernel ridge"
    if "TimesFM" in text:
        return "M9 TimesFM"
    if text.startswith("E2") or "energy_selector" in text:
        return "E2 proxy"
    if "TimeGrad" in text or text.startswith("E3"):
        return "E3 TimeGrad"
    if "ScoreGrad" in text or text.startswith("E4"):
        return "E4 ScoreGrad"
    if "TEM" in text or text.startswith("E1"):
        return "E1 TEM"
    return text


def order_key(name: str) -> tuple[int, int]:
    if name.startswith("M0 lag"):
        return (0, int(name.split("-", 1)[1]))
    if name.startswith("M0 seasonal"):
        lag = int(name.split("(", 1)[1].rstrip(")"))
        return (1, lag)
    return (FAMILY_RANK.get(name, 200), 0)


def model_color(name: str) -> str:
    if name.startswith("M0 seasonal"):
        return NAIVE_COLOR
    if name.startswith("M0 lag"):
        return "#7F7F7F"
    return MODEL_COLOR.get(name, "#444444")


@dataclass
class Cell:
    series: str
    grain: str
    rows: list[dict]
    preds: dict[str, np.ndarray]
    y: np.ndarray
    dates: pd.DatetimeIndex
    seasonal_short: str
    dm_p: float
    n_test: int
    mcs_short: list[str]

    @property
    def best(self) -> dict:
        return self.rows[0]

    @property
    def companion(self) -> dict | None:
        """Second model, drawn only when its sMAPE is within 5 percent."""
        if len(self.rows) < 2:
            return None
        leader = float(self.rows[0]["smape"])
        second = self.rows[1]
        if float(second["smape"]) <= leader * 1.05:
            return second
        return None


def _load_ranked(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "modelo": raw["modelo"],
                    "short": short_name(raw["modelo"]),
                    "smape": float(raw["SMAPE"]),
                    "skill": float(raw["SS_SMAPE"]),
                }
            )
    rows.sort(key=lambda item: item["smape"])
    return rows


def load_cells() -> dict[tuple[str, str], Cell]:
    root = article_dir() / "results"
    comparison = {
        (row["series"], row["grain"]): row
        for row in csv.DictReader((root / "comparison.csv").open(newline=""))
    }
    cells: dict[tuple[str, str], Cell] = {}
    for ranked_path in sorted(root.glob("*/*/metrics_test_ranked.csv")):
        series = ranked_path.parts[-3]
        grain = ranked_path.parts[-2]
        state = json.loads((ranked_path.parent / "state.json").read_text())
        meta = state["meta"]
        ranked = _load_ranked(ranked_path)
        comp = comparison[(series, grain)]
        mcs = [
            short_name(part) for part in comp["mcs_included"].split(";") if part.strip()
        ]
        preds = {
            key: np.asarray(value, dtype=float) for key, value in state["preds"].items()
        }
        cells[(series, grain)] = Cell(
            series=series,
            grain=grain,
            rows=ranked,
            preds=preds,
            y=np.asarray(meta["y_test"], dtype=float),
            dates=pd.to_datetime(meta["dates_test"]),
            seasonal_short=short_name(str(meta["seasonal_model"])),
            dm_p=float(comp["dm_p"]),
            n_test=int(comp["n_test"]),
            mcs_short=mcs,
        )
    return cells


def _save(fig: Figure, stem: str) -> Path:
    folder = manuscript_dir() / "figures"
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / f"{stem}.pdf"
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.04)
    fig.savefig(folder / f"{stem}.png", dpi=160, bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)
    return pdf


def _style_ax(ax: Axes) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.4, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=2.5, labelsize=7.5)


def _names_in_order(rows: list[dict]) -> list[str]:
    names = [row["short"] for row in rows]
    return sorted(set(names), key=order_key)


def fig_shares() -> None:
    """Two panels. Bar length is the share. Color repeats the series hue."""
    data = article_dir() / "data"
    stock = pd.read_csv(data / "share_stock_2025_12.csv")
    coll = pd.read_csv(data / "share_collection_through_2025.csv")
    groups = [
        ("Autuado", "ICMS Autuação", SERIES_COLOR["autuado"]),
        ("Declarado", "ICMS Declarado", SERIES_COLOR["declarado"]),
        ("Other ICMS", None, "#8A8F98"),
    ]

    def _share(frame: pd.DataFrame, column: str, tipo: str | None) -> float:
        if tipo is None:
            mask = ~frame["tipo"].isin(["ICMS Autuação", "ICMS Declarado"])
            return float(frame.loc[mask, column].sum() * 100)
        return float(frame.loc[frame["tipo"] == tipo, column].sum() * 100)

    stock_vals = [_share(stock, "share", tipo) for _, tipo, _ in groups]
    coll_vals = [_share(coll, "share_valor_total_gare", tipo) for _, tipo, _ in groups]
    labels = [name for name, _, _ in groups]
    colors = [color for _, _, color in groups]

    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.7), constrained_layout=True)
    panels = (
        (axes[0], stock_vals, "Stock, December snapshot"),
        (axes[1], coll_vals, "Collection, full sample"),
    )
    for ax, values, title in panels:
        ypos = np.arange(len(labels))
        ax.barh(ypos, values, color=colors, height=0.62, zorder=2)
        ax.set_yticks(ypos, labels)
        ax.set_xlim(0, 100)
        ax.set_xlabel("Share of legal-entity VAT (%)")
        ax.set_title(title, loc="left", fontsize=9, color=INK)
        for y, value in zip(ypos, values, strict=True):
            ax.text(value + 1.2, y, f"{value:.2f}", va="center", fontsize=8, color=INK)
        _style_ax(ax)
        ax.invert_yaxis()
    fig.suptitle(
        "Autuado is most of the stock. Declarado is most of the collection.",
        fontsize=9,
        color=INK,
        x=0.0,
        ha="left",
    )
    _save(fig, "fig_shares")


def _bar_colors(names: list[str], best: str) -> list[str]:
    colors = []
    for name in names:
        if name == best:
            colors.append(TEAL)
        else:
            colors.append(OTHER_BAR)
    return colors


def _draw_bars(
    ax: Axes,
    names: list[str],
    values: dict[str, float],
    best: str,
    *,
    skill: bool,
) -> None:
    ypos = np.arange(len(names))
    present = [
        (y, name, values[name])
        for y, name in zip(ypos, names, strict=True)
        if name in values
    ]
    vals = [item[2] for item in present]
    ax.barh(
        [item[0] for item in present],
        vals,
        color=_bar_colors([item[1] for item in present], best),
        height=0.72,
        zorder=2,
        edgecolor="none",
    )
    if skill:
        ax.axvline(0, color=INK, linewidth=0.6, zorder=3)
    span = max(vals) - min(0, min(vals))
    pad = 0.08 * span if span else 1
    lo = min(0, min(vals)) - pad
    hi = max(0, max(vals)) + pad * 2.4
    ax.set_xlim(lo, hi)
    ax.set_yticks(ypos, names)
    ax.set_ylim(len(names) - 0.5, -0.5)
    ax.tick_params(axis="y", labelsize=6)
    for y, name, value in present:
        label = f"{value:.2f}" if skill else f"{value:.1f}"
        if value >= 0:
            ax.text(
                value + pad * 0.15,
                y,
                label,
                va="center",
                ha="left",
                fontsize=6,
                color=INK,
                clip_on=False,
            )
        else:
            ax.text(
                value - pad * 0.15,
                y,
                label,
                va="center",
                ha="right",
                fontsize=6,
                color=INK,
                clip_on=False,
            )
    _style_ax(ax)


def _catalog(
    cells: dict[tuple[str, str], Cell], series: str, grains: tuple[str, ...]
) -> list[str]:
    names: set[str] = set()
    for grain in grains:
        names.update(row["short"] for row in cells[(series, grain)].rows)
    return sorted(names, key=order_key)


def fig_metric_grid(cells: dict[tuple[str, str], Cell], *, skill: bool) -> None:
    """One figure per series. The four grains share one model order."""
    metric = "Skill score" if skill else "Test sMAPE"
    prefix = "fig_skill" if skill else "fig_smape"
    for series in SERIES_ORDER:
        names = _catalog(cells, series, GRAIN_ORDER)
        fig, axes = plt.subplots(
            1,
            len(GRAIN_ORDER),
            figsize=(TEXT_WIDTH, 4.15),
            constrained_layout=True,
        )
        for col, grain in enumerate(GRAIN_ORDER):
            ax = axes[col]
            cell = cells[(series, grain)]
            values = {
                item["short"]: item["skill"] if skill else item["smape"]
                for item in cell.rows
            }
            _draw_bars(ax, names, values, cell.best["short"], skill=skill)
            ax.set_title(GRAIN_LABEL[grain], fontsize=8, color=INK)
            if col != 0:
                ax.tick_params(axis="y", labelleft=False)
        fig.suptitle(
            f"{SERIES_LABEL[series]}. {metric}. Teal is the lowest sMAPE.",
            fontsize=9,
            color=SERIES_COLOR[series],
            x=0.0,
            ha="left",
        )
        _save(fig, f"{prefix}_{series}")


def fig_ablation(cells: dict[tuple[str, str], Cell], *, skill: bool) -> None:
    grains = ("daily", "business_daily")
    fig, axes = plt.subplots(
        len(SERIES_ORDER),
        2,
        figsize=(TEXT_WIDTH, 7.6),
        constrained_layout=True,
    )
    for row, series in enumerate(SERIES_ORDER):
        names = _catalog(cells, series, grains)
        for col, grain in enumerate(grains):
            ax = axes[row, col]
            cell = cells[(series, grain)]
            values = {
                item["short"]: item["skill"] if skill else item["smape"]
                for item in cell.rows
            }
            _draw_bars(ax, names, values, cell.best["short"], skill=skill)
            if row == 0:
                ax.set_title(GRAIN_LABEL[grain], fontsize=8)
            ax.set_ylabel(
                SERIES_LABEL[series] if col == 0 else "",
                fontsize=8,
                color=SERIES_COLOR[series],
            )
            if col != 0:
                ax.tick_params(axis="y", labelleft=False)
    metric = "Skill score" if skill else "Test sMAPE"
    fig.suptitle(
        f"{metric} on the two daily grains. Teal is the lowest sMAPE.",
        fontsize=9,
        x=0.0,
        ha="left",
    )
    stem = "fig_ablation_skill" if skill else "fig_ablation_smape"
    _save(fig, stem)


def _million(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=float) / 1_000_000


def _date_axis(ax: Axes, grain: str, dates: pd.DatetimeIndex, *, compact: bool) -> None:
    """Keep ticks inside the test window so a later year is not drawn."""
    ax.set_xlim(dates[0], dates[-1])
    if grain == "monthly":
        step = 2 if compact else 1
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=step))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    elif grain == "weekly":
        if compact:
            ax.set_xticks([dates[0], dates[len(dates) // 2], dates[-1]])
        else:
            ax.xaxis.set_major_locator(
                mdates.WeekdayLocator(byweekday=mdates.SU, interval=3)
            )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.tick_params(axis="x", labelrotation=0, labelsize=6.5)


def _label_ends(ax: Axes, items: list[tuple[float, str, str]]) -> None:
    """Names to the right of the last point. Spread them when the ends collide."""
    if not items:
        return
    ys = np.array([item[0] for item in items], dtype=float)
    order = list(np.argsort(ys))
    y_lo, y_hi = ax.get_ylim()
    gap = 0.11 * float(y_hi - y_lo)
    placed = ys.copy()
    last = -np.inf
    for idx in order:
        placed[idx] = max(placed[idx], last + gap)
        last = float(placed[idx])
    overflow = float(placed.max() - ys.max())
    if overflow > 0:
        placed -= overflow
    transform = ax.get_yaxis_transform()
    for (_, text, color), y_text in zip(items, placed, strict=True):
        ax.text(
            1.01,
            float(y_text),
            text,
            transform=transform,
            color=color,
            fontsize=7,
            va="center",
            ha="left",
            clip_on=False,
        )


def fig_pred(cells: dict[tuple[str, str], Cell], grain: str) -> None:
    fig, axes = plt.subplots(
        len(SERIES_ORDER),
        1,
        figsize=(TEXT_WIDTH, 6.3),
        sharex=True,
        constrained_layout=True,
    )
    for ax, series in zip(axes, SERIES_ORDER, strict=True):
        cell = cells[(series, grain)]
        y = _million(cell.y)
        ax.plot(
            cell.dates, y, color=INK, linewidth=1.4, solid_capstyle="round", zorder=3
        )
        ends: list[tuple[float, str, str]] = [(float(y[-1]), "Actual", INK)]
        drawn = [cell.best]
        if cell.companion is not None:
            drawn.append(cell.companion)
        for item in drawn:
            forecast = _million(cell.preds[item["modelo"]])
            color = model_color(item["short"])
            ax.plot(
                cell.dates,
                forecast,
                color=color,
                linewidth=1.3,
                linestyle="--",
                zorder=2,
            )
            ends.append((float(forecast[-1]), item["short"], color))
        _label_ends(ax, ends)
        ax.set_ylabel("Million BRL")
        ax.set_title(
            f"{SERIES_LABEL[series]}  ·  {cell.best['short']}  ·  "
            f"sMAPE {cell.best['smape']:.1f}",
            loc="left",
            fontsize=8,
            color=SERIES_COLOR[series],
        )
        _date_axis(ax, grain, cell.dates, compact=False)
        _style_ax(ax)
        ax.grid(axis="x", color=GRID, linewidth=0.4)
    _save(fig, f"fig_pred_{grain}")


def fig_scatter(cells: dict[tuple[str, str], Cell], grain: str) -> None:
    fig, axes = plt.subplots(
        1,
        len(SERIES_ORDER),
        figsize=(TEXT_WIDTH, 2.45),
        constrained_layout=True,
    )
    for ax, series in zip(axes, SERIES_ORDER, strict=True):
        cell = cells[(series, grain)]
        actual = _million(cell.y)
        forecast = _million(cell.preds[cell.best["modelo"]])
        color = model_color(cell.best["short"])
        low = float(min(actual.min(), forecast.min()))
        high = float(max(actual.max(), forecast.max()))
        pad = 0.06 * (high - low if high > low else 1.0)
        low -= pad
        high += pad
        ax.plot(
            [low, high],
            [low, high],
            color=MUTED,
            linewidth=0.8,
            linestyle="--",
            zorder=1,
        )
        ax.scatter(
            actual,
            forecast,
            s=16 if grain in {"monthly", "weekly"} else 10,
            color=color,
            alpha=0.85,
            linewidths=0,
            zorder=2,
        )
        ax.set_xlim(low, high)
        ax.set_ylim(low, high)
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(
            SERIES_LABEL[series],
            loc="left",
            fontsize=8,
            color=SERIES_COLOR[series],
        )
        ax.set_xlabel("Actual (million BRL)")
        ax.set_ylabel("Forecast")
        ax.text(
            0.04,
            0.96,
            f"{cell.best['short']}\nsMAPE {cell.best['smape']:.1f}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=6.5,
            color=color,
        )
        _style_ax(ax)
    _save(fig, f"fig_scatter_{grain}")


def fig_residual(
    cells: dict[tuple[str, str], Cell], grains: tuple[str, ...], stem: str
) -> None:
    fig, axes = plt.subplots(
        len(SERIES_ORDER),
        len(grains),
        figsize=(TEXT_WIDTH, 5.8),
        sharex="col",
        constrained_layout=True,
    )
    if len(grains) == 1:
        axes = np.array([[ax] for ax in axes])
    for row, series in enumerate(SERIES_ORDER):
        for col, grain in enumerate(grains):
            ax = axes[row, col]
            cell = cells[(series, grain)]
            residual = _million(cell.preds[cell.best["modelo"]] - cell.y)
            color = model_color(cell.best["short"])
            ax.axhline(0, color=INK, linewidth=0.6, zorder=1)
            ax.plot(cell.dates, residual, color=color, linewidth=1.1, zorder=2)
            if grain in {"monthly", "weekly"}:
                ax.scatter(
                    cell.dates, residual, s=12, color=color, zorder=3, linewidths=0
                )
            if row == 0:
                ax.set_title(GRAIN_LABEL[grain], fontsize=8, color=INK)
            if col == 0:
                ax.set_ylabel(
                    f"{SERIES_LABEL[series]}\n(million BRL)",
                    fontsize=7,
                    color=SERIES_COLOR[series],
                )
            _date_axis(ax, grain, cell.dates, compact=True)
            _style_ax(ax)
            ax.text(
                0.98,
                0.92,
                cell.best["short"],
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=6.5,
                color=color,
            )
    _save(fig, stem)


def fig_zoo(cells: dict[tuple[str, str], Cell], series: str, grain: str) -> None:
    cell = cells[(series, grain)]
    names = _names_in_order(cell.rows)
    by_name = {row["short"]: row for row in cell.rows}
    ncol = 4
    nrow = int(np.ceil(len(names) / ncol))
    fig, axes = plt.subplots(
        nrow,
        ncol,
        figsize=(TEXT_WIDTH, 1.15 * nrow + 0.35),
        sharex=True,
        sharey=True,
        constrained_layout=True,
    )
    flat = np.atleast_1d(axes).ravel()
    y = _million(cell.y)
    for ax, name in zip(flat, names, strict=False):
        row = by_name[name]
        forecast = _million(cell.preds[row["modelo"]])
        color = model_color(name)
        ax.plot(cell.dates, y, color="#C5C8CE", linewidth=1.0, zorder=1)
        ax.plot(cell.dates, forecast, color=color, linewidth=1.05, zorder=2)
        best = name == cell.best["short"]
        if best:
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_color(TEAL)
                spine.set_linewidth(1.4)
        ax.set_title(
            f"{name}   {row['smape']:.1f}",
            fontsize=6.2,
            color=TEAL if best else INK,
            loc="left",
        )
        ax.tick_params(labelsize=5.5, length=2, labelbottom=False)
        ax.grid(axis="y", color=GRID, linewidth=0.3)
        _date_axis(ax, grain, cell.dates, compact=True)
    for ax in flat[len(names) :]:
        ax.set_visible(False)
    grid = np.atleast_2d(axes)
    for col in range(ncol):
        for row_i in range(nrow - 1, -1, -1):
            ax = grid[row_i, col]
            if ax.get_visible():
                ax.tick_params(labelbottom=True, labelsize=5.5)
                break
    fig.suptitle(
        f"{SERIES_LABEL[series]}, {GRAIN_LABEL[grain]}. "
        "Gray is actual. Teal frame is the lowest sMAPE.",
        fontsize=8,
        x=0.0,
        ha="left",
    )
    _save(fig, f"fig_zoo_{series}_{grain}")


def _fmt_p(value: float) -> str:
    if value < 0.001:
        return r"$<0.001$"
    return f"{value:.3f}"


def _tex(text: str) -> str:
    return (
        text.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
    )


def _write_tables(cells: dict[tuple[str, str], Cell]) -> None:
    folder = manuscript_dir() / "tables"
    folder.mkdir(parents=True, exist_ok=True)
    _write_best(folder, cells)
    _write_top3(folder, cells)
    _write_monthly(folder, cells)
    _write_ablation(folder, cells)
    _write_mcs(folder, cells)


def _iter_cells(cells: dict[tuple[str, str], Cell]):
    for series in SERIES_ORDER:
        for grain in GRAIN_ORDER:
            yield cells[(series, grain)]


def _write_best(folder: Path, cells: dict[tuple[str, str], Cell]) -> None:
    lines = [
        "% Generated by make_segment_figures.py. Do not edit the numbers.",
        r"\begin{tabular}{lllcrrr}",
        r"\toprule",
        r"Series & Grain & Best & $n$ & sMAPE & Skill & HLN $p$ \\",
        r"\midrule",
    ]
    for cell in _iter_cells(cells):
        lines.append(
            f"{SERIES_LABEL[cell.series]} & {TABLE_GRAIN[cell.grain]} & "
            f"{_tex(cell.best['short'])} & {cell.n_test} & "
            f"{cell.best['smape']:.2f} & {cell.best['skill']:.3f} & "
            f"{_fmt_p(cell.dm_p)} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    (folder / "tab_best.tex").write_text("\n".join(lines))


def _write_top3(folder: Path, cells: dict[tuple[str, str], Cell]) -> None:
    lines = [
        "% Generated by make_segment_figures.py. Do not edit the numbers.",
        r"\begin{tabular}{llllrr}",
        r"\toprule",
        r"Series & Grain & Rank & Model & sMAPE & Skill \\",
        r"\midrule",
    ]
    first = True
    for cell in _iter_cells(cells):
        if not first:
            lines.append(r"\addlinespace")
        first = False
        for rank, row in enumerate(cell.rows[:3], start=1):
            series = SERIES_LABEL[cell.series] if rank == 1 else ""
            grain = TABLE_GRAIN[cell.grain] if rank == 1 else ""
            lines.append(
                f"{series} & {grain} & {rank} & {_tex(row['short'])} & "
                f"{row['smape']:.2f} & {row['skill']:.3f} \\\\"
            )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    (folder / "tab_top3_by_grain.tex").write_text("\n".join(lines))


def _write_monthly(folder: Path, cells: dict[tuple[str, str], Cell]) -> None:
    lines = [
        "% Generated by make_segment_figures.py. Do not edit the numbers.",
        r"\begin{tabular}{llrr}",
        r"\toprule",
        r"Series & Model & sMAPE & Skill \\",
        r"\midrule",
    ]
    first = True
    for series in SERIES_ORDER:
        cell = cells[(series, "monthly")]
        if not first:
            lines.append(r"\addlinespace")
        first = False
        for rank, row in enumerate(cell.rows[:8]):
            label = SERIES_LABEL[series] if rank == 0 else ""
            lines.append(
                f"{label} & {_tex(row['short'])} & {row['smape']:.2f} & {row['skill']:.3f} \\\\"
            )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    (folder / "tab_monthly_top8.tex").write_text("\n".join(lines))


def _write_ablation(folder: Path, cells: dict[tuple[str, str], Cell]) -> None:
    lines = [
        "% Generated by make_segment_figures.py. Do not edit the numbers.",
        r"\begin{tabular}{lllrr}",
        r"\toprule",
        r"Series & Grain & Model & sMAPE & Skill \\",
        r"\midrule",
    ]
    first = True
    for series in SERIES_ORDER:
        for grain in ("daily", "business_daily"):
            cell = cells[(series, grain)]
            if not first:
                lines.append(r"\addlinespace")
            first = False
            for rank, row in enumerate(cell.rows[:3]):
                s_lab = SERIES_LABEL[series] if rank == 0 else ""
                g_lab = TABLE_GRAIN[grain] if rank == 0 else ""
                lines.append(
                    f"{s_lab} & {g_lab} & {_tex(row['short'])} & "
                    f"{row['smape']:.2f} & {row['skill']:.3f} \\\\"
                )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    (folder / "tab_ablation_A_vs_B.tex").write_text("\n".join(lines))


def _write_mcs(folder: Path, cells: dict[tuple[str, str], Cell]) -> None:
    lines = [
        "% Generated by make_segment_figures.py. Do not edit the numbers.",
        r"\begin{tabular}{llrlp{52mm}}",
        r"\toprule",
        r"Series & Grain & In set & Naive in set & Left out \\",
        r"\midrule",
    ]
    for cell in _iter_cells(cells):
        present = {row["short"] for row in cell.rows}
        left = [
            name
            for name in _names_in_order(cell.rows)
            if name not in set(cell.mcs_short)
        ]
        # A name can be in the set only if it was ranked.
        left = [name for name in left if name in present]
        naive = "yes" if cell.seasonal_short in cell.mcs_short else "no"
        if not left:
            left_text = "none"
        elif len(left) <= 4:
            left_text = ", ".join(left)
        elif cell.seasonal_short in left:
            left_text = f"seasonal naive and {len(left) - 1} others"
        else:
            left_text = f"{len(left)} models"
        lines.append(
            f"{SERIES_LABEL[cell.series]} & {TABLE_GRAIN[cell.grain]} & "
            f"{len(cell.mcs_short)} & {naive} & {_tex(left_text)} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", ""])
    (folder / "tab_mcs.tex").write_text("\n".join(lines))


def main() -> list[Path]:
    _font_setup()
    cells = load_cells()
    fig_shares()
    fig_metric_grid(cells, skill=False)
    fig_metric_grid(cells, skill=True)
    fig_ablation(cells, skill=False)
    fig_ablation(cells, skill=True)
    for grain in GRAIN_ORDER:
        fig_pred(cells, grain)
        fig_scatter(cells, grain)
    fig_residual(cells, MAIN_GRAINS, "fig_residual_main")
    fig_residual(cells, ("daily",), "fig_residual_daily")
    for series in SERIES_ORDER:
        for grain in GRAIN_ORDER:
            fig_zoo(cells, series, grain)
    _write_tables(cells)
    written = sorted((manuscript_dir() / "figures").glob("fig_*.pdf"))
    print(f"wrote {len(written)} pdf figures and the result tables")
    return written


if __name__ == "__main__":
    main()
