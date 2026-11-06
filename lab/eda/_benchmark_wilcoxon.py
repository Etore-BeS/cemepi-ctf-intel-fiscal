"""Display helpers for the paired Wilcoxon lab page.

``show`` follows the declared-versus-assessed page: the white template, with
WebGL trace defaults removed, then ``fig.show()``. The single-page assembler
rejects any remaining ``scattergl`` text, including template defaults.
"""

from __future__ import annotations

import copy

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

SERIES = {"total": "Total", "declarado": "Declared", "autuado": "Assessed"}
GRAIN = {
    "monthly": "Month",
    "weekly": "Week",
    "business_daily": "Mon to Fri",
    "daily": "Calendar day",
}


def show(fig: go.Figure) -> None:
    """Use the white template without WebGL trace defaults, so the page stays on SVG."""
    template = copy.deepcopy(pio.templates["plotly_white"])
    for name in list(template.data):
        if str(name).endswith("gl"):
            setattr(template.data, name, None)
    fig.layout.template = template
    fig.show()


def comparison_table(comparison: pd.DataFrame) -> pd.DataFrame:
    """Twelve cells, with the series and the grain on every row."""
    order = pd.DataFrame(
        [(series, grain) for series in SERIES for grain in GRAIN],
        columns=["series", "grain"],
    )
    frame = order.merge(comparison, on=["series", "grain"], how="left")
    frame["Series"] = frame["series"].map(SERIES)
    frame["Grain"] = frame["grain"].map(GRAIN)
    columns = {
        "Series": "Series",
        "Grain": "Grain",
        "best": "Best",
        "smape": "sMAPE",
        "seasonal_smape": "Seasonal naive sMAPE",
        "skill": "Skill",
        "dm_p": "HLN p",
    }
    if "wsr_p" in frame.columns:
        frame["Agree at 5%"] = (frame["dm_p"] < 0.05) == (frame["wsr_p"] < 0.05)
        columns["wsr_p"] = "Wilcoxon p"
        columns["Agree at 5%"] = "Agree at 5%"
    else:
        frame["Wilcoxon p"] = "not in the committed comparison.csv"
        columns["Wilcoxon p"] = "Wilcoxon p"
    return frame[list(columns)].rename(columns=columns)


def pair_panels(pairs: pd.DataFrame) -> tuple[pd.DataFrame, list[go.Figure]]:
    """One summary row and one heatmap per series × grain cell."""
    rows: list[dict] = []
    figures: list[go.Figure] = []
    for series, grain in ((s, g) for s in SERIES for g in GRAIN):
        block = pairs[(pairs["series"] == series) & (pairs["grain"] == grain)]
        rows.append(
            {
                "Series": SERIES.get(series, series),
                "Grain": GRAIN.get(grain, grain),
                "Pairs": len(block),
                "Significant at 5%": int((block["p_bh"] < 0.05).sum()),
            }
        )
        names = sorted(set(block["model_a"]).union(block["model_b"]))
        matrix = pd.DataFrame(1.0, index=names, columns=names)
        for row in block.itertuples(index=False):
            matrix.loc[row.model_a, row.model_b] = row.p_bh
            matrix.loc[row.model_b, row.model_a] = row.p_bh
        figure = go.Figure(
            data=[
                go.Heatmap(
                    z=matrix.to_numpy(),
                    x=list(matrix.columns),
                    y=list(matrix.index),
                    coloraxis="coloraxis",
                    hovertemplate="%{y} vs %{x}<br>p_bh=%{z:.4g}<extra></extra>",
                )
            ]
        )
        figure.update_layout(
            title=f"{SERIES.get(series, series)}, {GRAIN.get(grain, grain)}",
            coloraxis={
                "colorscale": "Teal",
                "reversescale": True,
                "cmin": 0,
                "cmax": 1,
            },
            xaxis={"tickangle": -45},
            height=520,
            margin={"l": 120, "b": 120},
        )
        figures.append(figure)
    return pd.DataFrame(rows), figures
