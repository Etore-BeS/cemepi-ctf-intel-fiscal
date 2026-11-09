"""Render a three-panel origin-contrast figure from committed aggregate CSVs.

Artifact only. The manuscript still uses the compact booktabs table.
Does not invent group levels; plots the three committed contrasts.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

FIGS = Path(__file__).resolve().parent
COLOR = "#154C56"


def _set_sans_font() -> None:
    for candidate in ("Arial", "DejaVu Sans", "Helvetica"):
        try:
            path = font_manager.findfont(candidate, fallback_to_default=False)
            plt.rcParams["font.family"] = font_manager.FontProperties(
                fname=path
            ).get_name()
            return
        except (ValueError, RuntimeError):
            continue
    plt.rcParams["font.family"] = "sans-serif"


def read_contrast(path: Path) -> dict[str, float]:
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    keys = list(row.keys())
    return {
        "estimate": float(row[keys[1]]),
        "cl_lo": float(row["Cluster 95% low"]),
        "cl_hi": float(row["Cluster 95% high"]),
        "hc_lo": float(row["HC3 95% low"]),
        "hc_hi": float(row["HC3 95% high"]),
    }


def _xlim(data: dict[str, float], pad_frac: float = 0.35) -> tuple[float, float]:
    lo = min(data["cl_lo"], data["hc_lo"], data["estimate"])
    hi = max(data["cl_hi"], data["hc_hi"], data["estimate"])
    span = hi - lo
    if span <= 0:
        span = max(abs(data["estimate"]) * 0.05, 0.5)
    pad = max(span * pad_frac, 0.15)
    return lo - pad, hi + pad


def _draw_panel(axis: plt.Axes, title: str, unit: str, data: dict[str, float]) -> None:
    x0, x1 = _xlim(data)
    zero_in_view = x0 < 0 < x1

    # Cluster CI: thick bar, slight upward offset.
    y_cl = 0.10
    axis.plot(
        [data["cl_lo"], data["cl_hi"]],
        [y_cl, y_cl],
        color=COLOR,
        lw=3.6,
        solid_capstyle="butt",
        zorder=2,
    )
    axis.plot(
        [data["cl_lo"], data["cl_lo"]],
        [y_cl - 0.055, y_cl + 0.055],
        color=COLOR,
        lw=2.1,
        zorder=2,
    )
    axis.plot(
        [data["cl_hi"], data["cl_hi"]],
        [y_cl - 0.055, y_cl + 0.055],
        color=COLOR,
        lw=2.1,
        zorder=2,
    )

    # HC3 CI: thin bar, slight downward offset so both remain visible.
    y_hc = -0.10
    axis.plot(
        [data["hc_lo"], data["hc_hi"]],
        [y_hc, y_hc],
        color=COLOR,
        lw=1.4,
        solid_capstyle="butt",
        zorder=3,
    )
    axis.plot(
        [data["hc_lo"], data["hc_lo"]],
        [y_hc - 0.04, y_hc + 0.04],
        color=COLOR,
        lw=1.15,
        zorder=3,
    )
    axis.plot(
        [data["hc_hi"], data["hc_hi"]],
        [y_hc - 0.04, y_hc + 0.04],
        color=COLOR,
        lw=1.15,
        zorder=3,
    )

    axis.plot(data["estimate"], 0.0, "o", color=COLOR, ms=7.5, zorder=4)

    # Point label above the marker; keep clear of both CI bars.
    axis.annotate(
        f"{data['estimate']:.2f}",
        xy=(data["estimate"], 0.0),
        xytext=(0, 10),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=8,
        color=COLOR,
        zorder=5,
    )

    if zero_in_view:
        axis.axvline(0, color="#888888", lw=0.8, ls="--", zorder=1)
        xlabel = unit
    else:
        xlabel = f"{unit}  (reference = 0)"

    axis.set_xlim(x0, x1)
    axis.set_ylim(-0.32, 0.42)
    axis.set_yticks([])
    axis.set_title(title, fontsize=9, color=COLOR, pad=4)
    axis.set_xlabel(xlabel, fontsize=8)
    axis.tick_params(axis="x", labelsize=8, length=3)
    axis.grid(axis="x", color="#eeeeee", lw=0.7)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_visible(False)


def main() -> None:
    _set_sans_font()
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "xtick.labelsize": 8,
        }
    )

    panels = [
        (
            "Any payment AME (pp)",
            "percentage points",
            read_contrast(FIGS / "fig_logit_origin_ame.csv"),
        ),
        (
            "Within-firm difference (pp)",
            "percentage points",
            read_contrast(FIGS / "fig_within_firm.csv"),
        ),
        (
            "Paid-to-balance difference (%)",
            "percent",
            read_contrast(FIGS / "fig_recovery_pct.csv"),
        ),
    ]

    # Full text width (~7.0–7.2 in), ~2.2 in tall.
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.2), sharey=True)
    for axis, (title, unit, data) in zip(axes, panels, strict=True):
        _draw_panel(axis, title, unit, data)

    fig.tight_layout(pad=0.35, w_pad=0.9)
    out_png = FIGS / "fig_origin_contrasts_v2.png"
    out_svg = FIGS / "fig_origin_contrasts_v2.svg"
    fig.savefig(out_png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(out_svg, bbox_inches="tight", facecolor="white")
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
