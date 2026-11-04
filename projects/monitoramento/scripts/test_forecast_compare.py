"""Checks for the DM-HLN statistic, the Wilcoxon signed-rank test, and the MCS."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from forecast_compare import (
    benjamini_hochberg,
    diebold_mariano_hln,
    model_confidence_set,
    pairwise_wilcoxon,
    smape_loss,
    summarize_pairwise,
    wilcoxon_paired,
)
from statsmodels.stats.multitest import multipletests


def test_hln_factor_matches_one_step_formula() -> None:
    rng = np.random.default_rng(0)
    noise = rng.normal(scale=0.2, size=40)
    loss_model = rng.normal(size=40)
    loss_ref = loss_model + 1.0 + noise
    result = diebold_mariano_hln(loss_model, loss_ref, h=1)
    assert result.n == 40
    assert result.better == "model"
    assert result.p_value < 0.01
    assert abs(result.dm_hln - result.dm * np.sqrt(39 / 40)) < 1e-9


def test_hln_rejects_degenerate_horizon() -> None:
    loss = np.ones(12)
    try:
        diebold_mariano_hln(loss, loss, h=12)
    except ValueError:
        return
    raise AssertionError("h=n must be rejected")


def test_mcs_drops_a_worse_model() -> None:
    rng = np.random.default_rng(1)
    n = 80
    base = rng.normal(size=n)
    losses = {
        "a": np.abs(base),
        "b": np.abs(base + rng.normal(scale=0.05, size=n)),
        "bad": np.abs(base) + 3.0,
    }
    result = model_confidence_set(losses, alpha=0.10, bootstrap=199, seed=1)
    assert "bad" not in result.included
    assert result.p_values["bad"] < 0.10
    assert set(result.included) <= {"a", "b"}


def test_mcs_keeps_equal_models() -> None:
    rng = np.random.default_rng(2)
    base = np.abs(rng.normal(size=60))
    losses = {"a": base, "b": base.copy(), "c": base + rng.normal(scale=0.01, size=60)}
    result = model_confidence_set(losses, alpha=0.10, bootstrap=199, seed=2)
    assert set(result.included) == {"a", "b", "c"}


def test_smape_loss_is_zero_when_forecast_matches() -> None:
    actual = np.array([10.0, 0.0, 5.0])
    loss = smape_loss(actual, actual)
    assert loss[0] == 0.0
    assert loss[1] == 0.0
    assert loss[2] == 0.0


def test_wilcoxon_symmetric_differential_has_high_p() -> None:
    diff = np.array([-1.0, -2.0, -3.0, 1.0, 2.0, 3.0])
    result = wilcoxon_paired(np.zeros(6), diff)
    assert result.n == 6
    assert result.median_differential == 0.0
    assert result.better == "tie"
    assert result.p_value > 0.5


def test_wilcoxon_exact_when_model_always_better() -> None:
    result = wilcoxon_paired(np.zeros(12), np.ones(12))
    assert result.n == 12
    assert result.n_nonzero == 12
    assert result.statistic == 0.0
    assert result.p_value == 2 / 2**12
    assert result.median_differential < 0
    assert result.better == "model"


def test_wilcoxon_identical_losses_are_a_tie() -> None:
    loss = np.arange(12, dtype=float)
    result = wilcoxon_paired(loss, loss.copy())
    assert result.n == 12
    assert result.n_nonzero == 0
    assert result.p_value == 1.0
    assert result.better == "tie"
    assert result.median_differential == 0.0


def test_wilcoxon_drops_non_finite_pairs() -> None:
    loss_model = np.array([1.0, np.nan, 1.0, 1.0])
    loss_ref = np.array([2.0, 2.0, np.nan, 3.0])
    result = wilcoxon_paired(loss_model, loss_ref)
    assert result.n == 2
    assert result.n_nonzero == 2
    assert result.better == "model"


def test_bh_is_monotone_and_at_most_one() -> None:
    raw = np.array([0.01, 0.04, 0.20, 0.50, 0.03])
    adjusted = benjamini_hochberg(raw)
    expected = multipletests(raw, method="fdr_bh")[1]
    assert adjusted.shape == raw.shape
    assert np.all(adjusted <= 1.0)
    assert np.all(adjusted + 1e-12 >= raw)
    order = np.argsort(raw, kind="mergesort")
    assert np.all(np.diff(adjusted[order]) >= -1e-12)
    assert np.allclose(adjusted, expected)
    assert benjamini_hochberg(np.array([])).size == 0


def test_pairwise_adjusts_inside_the_cell() -> None:
    losses = {
        "good": np.zeros(12),
        "mid": np.ones(12),
        "bad": np.full(12, 3.0),
    }
    pairs = pairwise_wilcoxon(losses)
    assert len(pairs) == 3
    assert all(pair.p_bh <= 1.0 for pair in pairs)
    assert all(pair.p_bh + 1e-12 >= pair.p_raw for pair in pairs)
    good_bad = next(
        pair for pair in pairs if {pair.model_a, pair.model_b} == {"good", "bad"}
    )
    assert good_bad.better == "good"
    assert good_bad.p_raw == 2 / 2**12
    rows = [pair.as_dict() for pair in pairs]
    assert summarize_pairwise("good", rows)["leader_beats_all"] is True
    assert summarize_pairwise("mid", rows)["leader_beats_all"] is False
    assert summarize_pairwise("absent", rows)["leader_beats_all"] is False


def test_leader_beats_all_requires_every_rival() -> None:
    rows = [
        {"model_a": "best", "model_b": "a", "p_bh": 0.01, "better": "best"},
        {"model_a": "best", "model_b": "b", "p_bh": 0.20, "better": "best"},
    ]
    summary = summarize_pairwise("best", rows)
    assert summary["n_pairs"] == 2
    assert summary["n_significant"] == 1
    assert summary["leader_beats_all"] is False
    rows[1]["p_bh"] = 0.04
    assert summarize_pairwise("best", rows)["leader_beats_all"] is True
    rows[1]["better"] = "b"
    assert summarize_pairwise("best", rows)["leader_beats_all"] is False


def test_score_state_writes_wilcoxon_on_synthetic_losses() -> None:
    from score_benchmark_tests import score_state

    n = 16
    actual = np.linspace(10.0, 20.0, n)
    state = {
        "meta": {
            "series": "total",
            "grain": "monthly",
            "y_test": actual.tolist(),
            "seasonal_model": "naive",
            "dates_test": ["2024-01-01"] * n,
        },
        "preds": {
            "naive": (actual + 5.0).tolist(),
            "good": (actual + 0.1).tolist(),
            "bad": (actual + 8.0).tolist(),
        },
    }
    ranked = pd.DataFrame(
        {
            "modelo": ["good", "naive", "bad"],
            "SMAPE": [1.0, 10.0, 20.0],
            "SS_SMAPE": [0.9, 0.0, -1.0],
        }
    )
    scored = score_state(state, ranked)
    assert scored["wsr_better"] == "model"
    assert scored["wsr_p"] < 0.05
    assert isinstance(scored["wsr_stat"], float)
    assert len(scored["pairwise"]) == 3
    assert {row["p_bh"] <= 1.0 for row in scored["pairwise"]} == {True}


def test_best_row_has_wilcoxon_column() -> None:
    from make_segment_figures import Cell, _best_header, _best_row, _wilcoxon_pairs_row

    cell = Cell(
        series="total",
        grain="monthly",
        rows=[
            {"modelo": "boost", "short": "M3 boosting", "smape": 12.94, "skill": 0.513}
        ],
        preds={},
        y=np.zeros(1),
        dates=pd.to_datetime(["2024-01-01"]),
        seasonal_short="M0 seasonal (12)",
        dm_p=0.022,
        wsr_p=2 / 2**12,
        n_test=12,
        mcs_short=[],
    )
    header = "\n".join(_best_header())
    assert r"Wilcoxon $p$" in header
    assert r"HLN $p$" in header
    row = _best_row(cell)
    assert "0.022" in row
    assert r"$<0.001$" in row
    summary = {"n_pairs": 6, "n_significant": 2, "leader_beats_all": False}
    assert _wilcoxon_pairs_row(cell, summary).endswith("6 & 2 & no \\\\")


def test_manuscript_cites_the_lab_page() -> None:
    root = Path(__file__).resolve().parents[3]
    method = (
        root
        / "projects/monitoramento/manuscript/arrecadacao_forecast_v1/sections/04_method.tex"
    ).read_text()
    lab = root / "lab/eda/2026-11-04_benchmark_wilcoxon.qmd"
    page = lab.read_text()
    assert "lab-ben-wsr" in method
    assert "benjamini1995fdr" in method
    assert "comparison.csv" in page
    assert "state.json" not in page
    assert "fdr_bh" in page
    assert "zero_method" in page and "wilcox" in page
    assert "10.1111/j.2517-6161.1995.tb02031.x" in page
    assert "10.2307/3001968" in page
    assert "DISCUTIR" not in page
