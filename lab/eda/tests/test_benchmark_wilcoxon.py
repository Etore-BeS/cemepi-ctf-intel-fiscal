"""The Wilcoxon page figures must stay free of WebGL template defaults."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.io as pio

EDA = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EDA))

import _benchmark_wilcoxon as W

RESULTS = (
    ROOT
    / "projects/monitoramento/notebooks/articles"
    / "arrecadacao_forecast_v1_segmentado/results"
)
QMD = EDA / "2026-11-04_benchmark_wilcoxon.qmd"


def test_comparison_table_names_every_row():
    table = W.comparison_table(pd.read_csv(RESULTS / "comparison.csv"))
    assert list(table.columns[:2]) == ["Series", "Grain"]
    assert len(table) == 12
    assert table.groupby(["Series", "Grain"]).ngroups == 12
    assert "Best" in table.columns


def test_two_cells_disagree_at_five_percent():
    table = W.comparison_table(pd.read_csv(RESULTS / "comparison.csv"))
    disagree = table.loc[~table["Agree at 5%"]]
    assert len(disagree) == 2
    got = {
        (row["Series"], row["Grain"]): (
            round(float(row["HLN p"]), 3),
            round(float(row["Wilcoxon p"]), 3),
        )
        for _, row in disagree.iterrows()
    }
    assert got[("Declared", "Month")] == (0.040, 0.064)
    assert got[("Declared", "Calendar day")] == (0.069, 0.026)


def test_pair_summary_counts_437_of_1350():
    summary, figures = W.pair_panels(pd.read_csv(RESULTS / "pairwise_wilcoxon.csv"))
    assert len(figures) == 12
    assert int(summary["Pairs"].sum()) == 1350
    assert int(summary["Significant at 5%"].sum()) == 437


def test_pair_figures_have_no_scattergl():
    pio.renderers.default = "json"
    _summary, figures = W.pair_panels(pd.read_csv(RESULTS / "pairwise_wilcoxon.csv"))
    assert figures
    for fig in figures:
        W.show(fig)
        html = fig.to_html(full_html=False, include_plotlyjs=False)
        assert "scattergl" not in html
        assert "Scattergl" not in html
        blob = fig.to_json()
        assert "scattergl" not in blob
        assert "Scattergl" not in blob
        for trace in fig.data:
            assert trace.type != "scattergl"


def test_page_calls_the_svg_show_helper():
    text = QMD.read_text(encoding="utf-8")
    assert "show(figure)" in text
    assert "figure.show()" not in text
    assert "scattergl" not in text
