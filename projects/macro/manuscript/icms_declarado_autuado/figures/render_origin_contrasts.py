"""Render fig_origin_contrasts from committed aggregate CSVs.

Does not read raw PGE data. Inputs are the three one-row CSVs already
committed next to the previous single-estimate figures.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIGS = Path(__file__).resolve().parent


def read_one(path: Path) -> dict[str, float]:
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    # Second column is the point estimate; Contrast is the first column.
    keys = list(row.keys())
    est_key = keys[1]
    return {
        "estimate": float(row[est_key]),
        "cl_lo": float(row["Cluster 95% low"]),
        "cl_hi": float(row["Cluster 95% high"]),
        "hc_lo": float(row["HC3 95% low"]),
        "hc_hi": float(row["HC3 95% high"]),
    }


def main() -> None:
    panels = [
        (
            "a",
            "Any payment AME (pp)",
            "Average marginal effect (percentage points)",
            read_one(FIGS / "fig_logit_origin_ame.csv"),
        ),
        (
            "b",
            "Within-firm difference (pp)",
            "Within-firm difference (percentage points)",
            read_one(FIGS / "fig_within_firm.csv"),
        ),
        (
            "c",
            "Paid-to-balance difference (%)",
            "Percentage difference in expected payments per real of stock",
            read_one(FIGS / "fig_recovery_pct.csv"),
        ),
    ]

    out_csv = FIGS / "fig_origin_contrasts.csv"
    with out_csv.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "panel",
                "label",
                "estimate",
                "cluster_low",
                "cluster_high",
                "hc3_low",
                "hc3_high",
                "unit",
            ]
        )
        for letter, label, _xlab, data in panels:
            unit = "pp" if "pp" in label else "percent"
            writer.writerow(
                [
                    letter,
                    label,
                    data["estimate"],
                    data["cl_lo"],
                    data["cl_hi"],
                    data["hc_lo"],
                    data["hc_hi"],
                    unit,
                ]
            )

    fig, axes = plt.subplots(3, 1, figsize=(7.2, 5.8), sharex=False)
    color = "#154C56"
    for axis, (letter, label, xlab, data) in zip(axes, panels, strict=True):
        y = 0.0
        axis.plot(
            [data["cl_lo"], data["cl_hi"]],
            [y, y],
            color=color,
            linewidth=3.2,
            solid_capstyle="butt",
            label="Cluster 95% CI",
            zorder=2,
        )
        axis.plot(
            [data["hc_lo"], data["hc_hi"]],
            [y, y],
            color=color,
            linewidth=1.2,
            solid_capstyle="butt",
            label="HC3 95% CI",
            zorder=3,
        )
        axis.plot([data["estimate"]], [y], "o", color=color, markersize=8, zorder=4)
        axis.annotate(
            f"{data['estimate']:.2f}",
            xy=(data["estimate"], y),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            color=color,
        )
        axis.axvline(0, color="#666666", linewidth=0.8, linestyle="--", zorder=1)
        axis.set_yticks([0])
        axis.set_yticklabels([f"({letter}) {label}"])
        axis.set_xlabel(xlab, fontsize=9)
        axis.set_ylim(-0.7, 0.9)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_visible(False)
        axis.tick_params(axis="y", length=0)
        lo = min(data["cl_lo"], data["hc_lo"], 0)
        hi = max(data["cl_hi"], data["hc_hi"], 0)
        pad = 0.08 * (hi - lo if hi > lo else 1.0)
        axis.set_xlim(lo - pad, hi + pad)

    handles, labels = axes[0].get_legend_handles_labels()
    axes[0].legend(handles, labels, loc="upper right", frameon=False, fontsize=8)
    fig.suptitle(
        "Assessed origin relative to declared origin",
        fontsize=11,
        color=color,
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(
        FIGS / "fig_origin_contrasts.png",
        dpi=300,
        bbox_inches="tight",
        facecolor="white",
    )
    fig.savefig(
        FIGS / "fig_origin_contrasts.svg", bbox_inches="tight", facecolor="white"
    )


if __name__ == "__main__":
    main()
