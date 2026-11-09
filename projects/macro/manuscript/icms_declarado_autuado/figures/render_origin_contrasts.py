"""Render the Option B coefficient plot from committed aggregate CSVs.

The manuscript uses a compact booktabs table instead of this figure.
This script keeps a printable coefficient-plot alternative for review.

Option A (declared vs assessed group levels) cannot be drawn: the committed
CSVs store only contrasts, not model group levels.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

FIGS = Path(__file__).resolve().parent


def _set_sans_font() -> None:
    for candidate in ("Arial", "DejaVu Sans", "Helvetica"):
        try:
            path = font_manager.findfont(candidate, fallback_to_default=False)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=path).get_name()
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


def main() -> None:
    _set_sans_font()
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )

    rows = [
        ("Any payment AME (pp)", read_contrast(FIGS / "fig_logit_origin_ame.csv")),
        ("Within-firm difference (pp)", read_contrast(FIGS / "fig_within_firm.csv")),
        ("Paid-to-balance difference (%)", read_contrast(FIGS / "fig_recovery_pct.csv")),
    ]

    with (FIGS / "fig_origin_contrasts.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["outcome", "estimate", "cluster_low", "cluster_high", "hc3_low", "hc3_high"]
        )
        for label, data in rows:
            writer.writerow(
                [
                    label,
                    data["estimate"],
                    data["cl_lo"],
                    data["cl_hi"],
                    data["hc_lo"],
                    data["hc_hi"],
                ]
            )

    color = "#154C56"
    fig, axis = plt.subplots(figsize=(7.2, 2.35))
    ys = list(range(len(rows) - 1, -1, -1))
    for y, (label, data) in zip(ys, rows, strict=True):
        axis.plot(
            [data["cl_lo"], data["cl_hi"]],
            [y, y],
            color=color,
            lw=1.4,
            solid_capstyle="butt",
            zorder=2,
        )
        axis.plot(
            [data["cl_lo"], data["cl_lo"]],
            [y - 0.08, y + 0.08],
            color=color,
            lw=1.2,
            zorder=2,
        )
        axis.plot(
            [data["cl_hi"], data["cl_hi"]],
            [y - 0.08, y + 0.08],
            color=color,
            lw=1.2,
            zorder=2,
        )
        axis.plot(
            [data["hc_lo"], data["hc_hi"]],
            [y, y],
            color=color,
            lw=0.8,
            alpha=0.55,
            solid_capstyle="butt",
            zorder=3,
        )
        axis.plot(data["estimate"], y, "o", color=color, ms=6.5, zorder=4)
        offset = 8 if data["estimate"] >= 0 else -8
        ha = "left" if data["estimate"] >= 0 else "right"
        axis.annotate(
            f"{data['estimate']:.2f}",
            xy=(data["estimate"], y),
            xytext=(offset, 0),
            textcoords="offset points",
            va="center",
            ha=ha,
            fontsize=8,
            color=color,
        )

    axis.axvline(0, color="#888888", lw=0.7, ls="--", zorder=1)
    axis.set_yticks(ys)
    axis.set_yticklabels([label for label, _ in rows])
    axis.set_xlabel("Assessed relative to declared (cluster 95% CI; HC3 lighter)")
    axis.set_ylim(-0.55, len(rows) - 0.45)
    all_lo = min(data["cl_lo"] for _, data in rows)
    all_hi = max(data["cl_hi"] for _, data in rows)
    pad = 0.06 * (all_hi - all_lo)
    axis.set_xlim(all_lo - pad, all_hi + pad)
    axis.grid(axis="x", color="#eeeeee", lw=0.6)
    axis.set_axisbelow(True)
    fig.tight_layout(pad=0.3)
    fig.savefig(FIGS / "fig_origin_contrasts.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(FIGS / "fig_origin_contrasts.svg", bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    main()
