"""Render a two-facet Cleveland coefficient plot from committed CSVs.

Artifact only. Table 17 remains in the manuscript.

Left facet: any-payment AME and within-firm difference (percentage points).
Right facet: paid-to-balance percentage difference.
Both facets include zero on the x axis.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.gridspec import GridSpec

FIGS = Path(__file__).resolve().parent

# Print-safe grayscale.
INK = "#1a1a1a"
HC3_FILL = "#9a9a9a"
REF = "#b0b0b0"
GRID = "#e6e6e6"


def _set_serif_font() -> None:
    # Prefer STIX (CM-like, TrueType) for clean PDF embedding; then DejaVu Serif.
    for name in ("STIXGeneral", "DejaVu Serif"):
        try:
            path = font_manager.findfont(name, fallback_to_default=False)
            prop = font_manager.FontProperties(fname=path)
            plt.rcParams["font.family"] = prop.get_name()
            return
        except (OSError, RuntimeError, ValueError):
            continue
    plt.rcParams["font.family"] = "serif"


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


def _draw_interval(axis: plt.Axes, y: float, data: dict[str, float]) -> None:
    # HC3: thicker, lighter segment underneath (nested-interval style).
    axis.plot(
        [data["hc_lo"], data["hc_hi"]],
        [y, y],
        color=HC3_FILL,
        lw=6.5,
        solid_capstyle="butt",
        zorder=2,
        clip_on=False,
    )
    # Cluster: thin line with small caps on top.
    axis.plot(
        [data["cl_lo"], data["cl_hi"]],
        [y, y],
        color=INK,
        lw=1.05,
        solid_capstyle="butt",
        zorder=3,
        clip_on=False,
    )
    cap = 0.11
    axis.plot(
        [data["cl_lo"], data["cl_lo"]],
        [y - cap, y + cap],
        color=INK,
        lw=1.05,
        zorder=3,
        clip_on=False,
    )
    axis.plot(
        [data["cl_hi"], data["cl_hi"]],
        [y - cap, y + cap],
        color=INK,
        lw=1.05,
        zorder=3,
        clip_on=False,
    )
    # Point estimate centered on the interval.
    axis.plot(
        data["estimate"],
        y,
        "o",
        color=INK,
        markersize=5.2,
        markerfacecolor=INK,
        markeredgewidth=0,
        zorder=4,
        clip_on=False,
    )


def _label_text(data: dict[str, float]) -> str:
    return f"{data['estimate']:.2f} [{data['cl_lo']:.2f}, {data['cl_hi']:.2f}]"


def _annotate(axis: plt.Axes, y: float, data: dict[str, float]) -> None:
    """Place the direct label just outside the cluster interval, toward zero."""
    text = _label_text(data)
    # Always to the right of the cluster high end (toward / past zero for negatives).
    axis.annotate(
        text,
        xy=(data["cl_hi"], y),
        xytext=(5, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=8,
        color=INK,
        zorder=5,
        clip_on=False,
    )


def _style_axis(axis: plt.Axes, xlabel: str) -> None:
    axis.axvline(0, color=REF, lw=0.7, zorder=1)
    axis.set_xlabel(xlabel, fontsize=8)
    axis.tick_params(axis="x", labelsize=8, length=3, width=0.55, colors=INK)
    axis.tick_params(axis="y", labelsize=8, length=0, colors=INK)
    axis.grid(axis="x", color=GRID, lw=0.55, zorder=0)
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    axis.spines["left"].set_color(INK)
    axis.spines["bottom"].set_color(INK)
    axis.spines["left"].set_linewidth(0.6)
    axis.spines["bottom"].set_linewidth(0.6)


def main() -> None:
    _set_serif_font()
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    ame = read_contrast(FIGS / "fig_logit_origin_ame.csv")
    within = read_contrast(FIGS / "fig_within_firm.csv")
    recovery = read_contrast(FIGS / "fig_recovery_pct.csv")

    left_rows = [
        ("Any payment AME", ame),
        ("Within-firm difference", within),
    ]
    right_rows = [
        ("Paid-to-balance", recovery),
    ]

    fig = plt.figure(figsize=(6.5, 2.2))
    grid = GridSpec(
        1,
        2,
        figure=fig,
        width_ratios=[1.15, 1.0],
        wspace=0.55,
        left=0.22,
        right=0.98,
        top=0.92,
        bottom=0.28,
    )
    ax_left = fig.add_subplot(grid[0, 0])
    ax_right = fig.add_subplot(grid[0, 1])

    # Left facet: two pp outcomes, top to bottom.
    left_ys = [1.0, 0.0]
    for y, (_label, data) in zip(left_ys, left_rows, strict=True):
        _draw_interval(ax_left, y, data)
        _annotate(ax_left, y, data)
    ax_left.set_yticks(left_ys)
    ax_left.set_yticklabels([label for label, _ in left_rows])
    left_hi = max(ame["cl_hi"], within["cl_hi"])
    ax_left.set_xlim(-0.8, left_hi + 5.5)
    ax_left.set_ylim(-0.55, 1.55)
    _style_axis(ax_left, "Percentage points")

    # Right facet: one percent outcome, vertically centered with the left panel.
    y_r = 0.5
    _draw_interval(ax_right, y_r, recovery)
    _annotate(ax_right, y_r, recovery)
    ax_right.set_yticks([y_r])
    ax_right.set_yticklabels([label for label, _ in right_rows])
    ax_right.set_xlim(min(recovery["cl_lo"] - 3.0, -100.0), 2.0)
    ax_right.set_ylim(-0.55, 1.55)
    _style_axis(ax_right, "Percent")

    out_pdf = FIGS / "fig_origin_contrasts_v3.pdf"
    out_png = FIGS / "fig_origin_contrasts_v3.png"
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    fig.savefig(out_png, dpi=300, bbox_inches="tight", facecolor="white")
    print(f"wrote {out_pdf}")
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
