"""Render declared-vs-assessed payment and recovery levels by firm size.

Source: committed lab aggregate
lab/eda/2026-10-31_cnae_declared_assessed/tables/porte.csv
(aggregates match manuscript Table profile when summed over Micro/Small/Other).

Unknown size is omitted: assessed unknown has n_paid_cda = 1 (cell suppression).
Does not estimate new models. Artifact only; Table 17 stays in the manuscript.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

FIGS = Path(__file__).resolve().parent
# figures/ -> manuscript dir -> macro -> projects -> repo root
REPO = FIGS.parents[4]
PORTE_CSV = (
    REPO / "lab" / "eda" / "2026-10-31_cnae_declared_assessed" / "tables" / "porte.csv"
)

# Match fig_annual_series_origin palette.
DECLARED = "#BD9869"
ASSESSED = "#154C56"
INK = "#242424"
GRID = "#eeeeee"

SIZE_ORDER = [("01", "Micro"), ("03", "Small"), ("05", "Other sizes")]


def _set_sans_font() -> None:
    for name in ("Arial", "DejaVu Sans", "Helvetica"):
        try:
            path = font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.family"] = font_manager.FontProperties(
                fname=path
            ).get_name()
            return
        except (ValueError, RuntimeError):
            continue
    plt.rcParams["font.family"] = "sans-serif"


def load_porte_levels() -> list[dict[str, float | str]]:
    rows = list(csv.DictReader(PORTE_CSV.open(newline="")))
    by_key = {(r["segment"], r["code"]): r for r in rows}
    out: list[dict[str, float | str]] = []
    for code, name in SIZE_ORDER:
        dec = by_key[("declared", code)]
        ass = by_key[("assessed", code)]
        # Suppress if either side has 1-4 paying certificates.
        for side, row in (("declared", dec), ("assessed", ass)):
            n_paid = int(float(row["n_paid_cda"]))
            if 1 <= n_paid <= 4:
                raise RuntimeError(
                    f"Cell suppression required for {side} {name}: n_paid={n_paid}"
                )
        out.append(
            {
                "code": code,
                "size": name,
                "declared_payment_pct": 100.0 * float(dec["payment_rate"]),
                "assessed_payment_pct": 100.0 * float(ass["payment_rate"]),
                "declared_recovery_pct": 100.0 * float(dec["recovery"]),
                "assessed_recovery_pct": 100.0 * float(ass["recovery"]),
                "declared_n_cda": int(float(dec["n_cda"])),
                "assessed_n_cda": int(float(ass["n_cda"])),
                "declared_n_firms": int(float(dec["n_firms"])),
                "assessed_n_firms": int(float(ass["n_firms"])),
            }
        )
    return out


def write_export_csv(levels: list[dict[str, float | str]]) -> Path:
    path = FIGS / "fig_origin_levels.csv"
    fields = [
        "size",
        "code",
        "declared_payment_pct",
        "assessed_payment_pct",
        "declared_recovery_pct",
        "assessed_recovery_pct",
        "declared_n_cda",
        "assessed_n_cda",
        "declared_n_firms",
        "assessed_n_firms",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in levels:
            writer.writerow({k: row[k] for k in fields})
    return path


def _dumbbell(
    axis: plt.Axes,
    levels: list[dict[str, float | str]],
    declared_key: str,
    assessed_key: str,
    xlabel: str,
    label_fmt: str,
) -> None:
    ys = list(range(len(levels) - 1, -1, -1))
    for y, row in zip(ys, levels, strict=True):
        x_d = float(row[declared_key])
        x_a = float(row[assessed_key])
        axis.plot(
            [x_d, x_a],
            [y, y],
            color="#cccccc",
            lw=2.0,
            zorder=1,
            solid_capstyle="round",
        )
        axis.plot(x_d, y, "o", color=DECLARED, ms=8, zorder=3, label=None)
        axis.plot(x_a, y, "o", color=ASSESSED, ms=8, zorder=3, label=None)
        # Direct value labels beside each marker.
        axis.annotate(
            label_fmt.format(x_d),
            xy=(x_d, y),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color=DECLARED,
        )
        axis.annotate(
            label_fmt.format(x_a),
            xy=(x_a, y),
            xytext=(0, -9),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=8,
            color=ASSESSED,
        )

    axis.set_yticks(ys)
    axis.set_yticklabels([str(row["size"]) for row in levels], fontsize=9, color=INK)
    axis.set_xlabel(xlabel, fontsize=9, color=INK)
    axis.tick_params(axis="x", labelsize=8, colors=INK, length=3)
    axis.tick_params(axis="y", length=0)
    axis.grid(axis="x", color=GRID, lw=0.7)
    axis.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(INK)
    axis.spines["bottom"].set_linewidth(0.7)
    axis.set_ylim(-0.55, len(levels) - 0.45)


def main() -> None:
    _set_sans_font()
    plt.rcParams.update({"font.size": 9, "axes.labelsize": 9})

    levels = load_porte_levels()
    export_path = write_export_csv(levels)
    print(f"wrote {export_path}")

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.6), sharey=True)
    _dumbbell(
        axes[0],
        levels,
        "declared_payment_pct",
        "assessed_payment_pct",
        "Certificates with any payment (%)",
        "{:.1f}",
    )
    _dumbbell(
        axes[1],
        levels,
        "declared_recovery_pct",
        "assessed_recovery_pct",
        "Recovery, payments / stock (%)",
        "{:.2f}",
    )

    # Color key as direct text, not a legend box.
    fig.text(
        0.5,
        0.98,
        "Declared",
        color=DECLARED,
        ha="right",
        va="top",
        fontsize=9,
        transform=fig.transFigure,
    )
    fig.text(
        0.515,
        0.98,
        "Assessed",
        color=ASSESSED,
        ha="left",
        va="top",
        fontsize=9,
        transform=fig.transFigure,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.92], w_pad=1.2)
    out_pdf = FIGS / "fig_origin_levels.pdf"
    out_png = FIGS / "fig_origin_levels.png"
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    fig.savefig(out_png, dpi=300, bbox_inches="tight", facecolor="white")
    print(f"wrote {out_pdf}")
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
