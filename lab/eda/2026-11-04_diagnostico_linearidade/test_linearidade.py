"""Synthetic checks for the CDA linearity diagnostic. No extract is read."""

from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diag = load("diagnose")
analyze = load("analyze")


def test_log_odds_finite_at_zero_and_at_n():
    lo0, se0, low0, high0 = diag.empirical_log_odds(0, 20)
    lon, sen, lown, highn = diag.empirical_log_odds(20, 20)
    assert np.isfinite([lo0, se0, low0, high0, lon, sen, lown, highn]).all()
    assert lo0 < 0 < lon
    assert low0 < lo0 < high0
    assert lown < lon < highn


def test_suppress_blanks_small_cells():
    frame = pd.DataFrame(
        {
            "municipality": ["big", "few_cdas", "few_firms"],
            "uf": ["SP", "SP", "SP"],
            "n_cda": [100, 9, 40],
            "n_firms": [30, 8, 4],
            "rate": [0.2, 0.4, 0.5],
            "log_odds": [-1.4, -0.4, 0.0],
        }
    )
    out = diag.suppress_cells(frame)
    big = out.loc[out.municipality == "big"].iloc[0]
    assert big.rate == pytest.approx(0.2)
    assert np.isfinite(big.log_odds)
    for label in ("few_cdas", "few_firms"):
        row = out.loc[out.municipality == label].iloc[0]
        assert row.municipality == label
        assert np.isnan(row.rate)
        assert np.isnan(row.log_odds)
        assert np.isnan(row.n_cda)
        assert np.isnan(row.n_firms)


def test_reset_rejects_quadratic_and_keeps_linear():
    rng = np.random.default_rng(20261104)
    n = 500
    x = rng.uniform(-2, 2, n)
    linear = pd.DataFrame({"y": 1 + 2 * x + rng.normal(0, 0.8, n), "x": x})
    quadratic = pd.DataFrame(
        {"y": 1 + 0.2 * x + 3 * x**2 + rng.normal(0, 0.8, n), "x": x}
    )
    lin = diag.linearity_checks("y ~ x", linear)
    quad = diag.linearity_checks("y ~ x", quadratic)
    assert lin["reset_p"] > 0.05
    assert quad["reset_p"] < 0.05
    assert quad["delta_r2"] > 0.01
    assert np.isfinite(lin["reset_p_hc3"])
    assert np.isfinite(quad["harvey_p"])


def test_icc_near_zero_when_groups_are_iid():
    rng = np.random.default_rng(1)
    groups = np.repeat(np.arange(30), 40)
    y = rng.normal(size=groups.size)
    paid = rng.binomial(1, 0.2, size=groups.size)
    classic = diag.classical_icc(y, groups)
    latent = diag.latent_icc(paid, groups)
    assert classic["icc"] < 0.05
    assert latent["icc"] < 0.05


def test_icc_positive_when_groups_differ():
    rng = np.random.default_rng(2)
    n_g, g = 40, 30
    groups = np.repeat(np.arange(g), n_g)
    y = rng.normal(size=g)[groups] + rng.normal(scale=0.3, size=groups.size)
    logits = rng.normal(scale=1.5, size=g)
    paid = rng.binomial(1, 1 / (1 + np.exp(-logits[groups])))
    classic = diag.classical_icc(y, groups)
    latent = diag.latent_icc(paid, groups)
    assert classic["icc"] > 0.4
    assert latent["icc"] > 0.05


def test_figures_have_no_scattergl():
    rng = np.random.default_rng(3)
    n = 300
    fitted = rng.normal(size=n)
    resid = 0.2 * fitted + rng.normal(scale=0.5, size=n)
    figs = diag.residual_figures(
        fitted + resid,
        fitted,
        resid,
        {"debt_age": rng.uniform(0, 20, n)},
    )
    assert figs
    for fig in figs.values():
        html = diag.figure_html(fig)
        assert "scattergl" not in html
        assert "Scattergl" not in html
        for trace in fig.data:
            assert trace.type != "scattergl"


def test_mixed_model_is_fit_only_when_icc_justifies_it():
    calls = []

    def fitter():
        calls.append("fit")
        return {"icc": 0.2, "tau2": 0.8}

    assert diag.maybe_fit_mixed(0.01, 0.0, fitter) is None
    assert calls == []
    fitted = diag.maybe_fit_mixed(0.08, 0.0, fitter)
    assert fitted["tau2"] == pytest.approx(0.8)
    assert calls == ["fit"]
    assert diag.maybe_fit_mixed(0.01, 0.02, fitter)["icc"] == pytest.approx(0.2)

    rng = np.random.default_rng(4)
    g = 8
    groups = np.repeat([f"M{i}" for i in range(g)], 40)
    shift = np.linspace(-1.5, 1.5, g)
    paid = rng.binomial(1, 1 / (1 + np.exp(-shift.repeat(40))))
    frame = pd.DataFrame(
        {
            "paid_flag": paid,
            "municipality": groups,
            "debt_age": rng.uniform(1, 15, groups.size),
            "log_stock": rng.normal(8, 1, groups.size),
            "segment": "declared",
            "section": "G",
            "porte": "01",
        }
    )
    out = diag.fit_mixed_logit(frame, "municipality", controls=False)
    assert out["converged"] is True
    assert out["tau2"] > 0
    assert out["icc"] > 0


def test_decision_table_follows_the_working_rules():
    quiet = diag.inference_decision(
        {
            "delta_aic_log_stock": 1.0,
            "delta_aic_debt_age": 2.0,
            "curved_log_stock": False,
            "curved_debt_age": False,
            "delta_r2_stock": 0.001,
            "fan_stock": 0.05,
            "delta_r2_stock_trim": 0.001,
            "fan_stock_trim": 0.04,
            "delta_r2_recovery": 0.002,
            "fan_recovery": 0.03,
            "icc_municipality": 0.01,
            "icc_division": 0.02,
            "funnel_municipality": 0.0,
            "funnel_division": 0.0,
            "n_estimation": 1000,
        }
    )
    choices = dict(zip(quiet.inference, quiet.choice, strict=True))
    assert "Linear logit" in choices["Probability of payment"]
    assert "OLS of log stock" in choices["Debt balance"]
    assert "No mixed effects" in choices["Group heterogeneity"]

    loud = diag.inference_decision(
        {
            "delta_aic_log_stock": 25.0,
            "delta_aic_debt_age": 1.0,
            "curved_log_stock": True,
            "curved_debt_age": False,
            "delta_r2_stock": 0.08,
            "fan_stock": 0.4,
            "delta_r2_stock_trim": 0.001,
            "fan_stock_trim": 0.01,
            "delta_r2_recovery": 0.05,
            "fan_recovery": 0.3,
            "icc_municipality": 0.12,
            "icc_division": 0.01,
            "funnel_municipality": 0.0,
            "funnel_division": 0.0,
            "n_estimation": 1000,
        }
    )
    loud_choices = dict(zip(loud.inference, loud.choice, strict=True))
    assert "spline" in loud_choices["Probability of payment"].lower()
    assert "Quantile regression" in loud_choices["Debt balance"]
    assert "top 1 percent" in loud_choices["Debt balance"]
    assert "random intercept" in loud_choices["Group heterogeneity"].lower()
    assert "municipality" in loud_choices["Group heterogeneity"].lower()
    assert set(loud.inference) == {
        "Probability of payment",
        "Debt balance",
        "Recovery",
        "Group heterogeneity",
        "Package for the paper",
    }


def test_top1_sensitivity_drops_the_largest_values():
    frame = pd.DataFrame({"stock": np.arange(1, 101, dtype=float)})
    trimmed = diag.without_top_share(frame)
    assert len(trimmed) == 99
    assert trimmed.stock.max() == 99


def test_queries_do_not_select_person_columns():
    texts = analyze.sql_texts(
        icms="icms.parquet",
        gare_files=["gare.parquet"],
        firm="firm.parquet",
    )
    blob = "\n".join(texts.values()).upper()
    assert "NOME_DEVEDOR" not in blob
    assert "CPF_DEVEDOR" not in blob
    assert "SELECT *" not in blob
    for column in analyze.ICMS_COLUMNS:
        assert column in texts["snapshot"]
    for column in ("ID_DEBITO", "VALOR_RECEITA", "DATA_ARRECADACAO_GARE"):
        assert column in texts["payments"]
    assert analyze.MEMORY_LIMIT == "900MB"


def test_prepare_rejects_person_columns():
    column = "NOME" + "_DEVEDOR"
    frame = pd.DataFrame({column: ["a"], "stock": [1.0]})
    with pytest.raises(RuntimeError, match="NOME" + "_DEVEDOR"):
        diag.prepare(frame)


def test_baseline_matches_the_committed_segment_summary():
    path = (
        REPO
        / "lab"
        / "eda"
        / "2026-10-31_cnae_declared_assessed"
        / "tables"
        / "segment_summary.csv"
    )
    summary = pd.read_csv(path)
    for row in summary.itertuples(index=False):
        assert analyze.BASELINE_N[row.segment] == int(row.n_cda)
        assert analyze.BASELINE_STOCK[row.segment] == pytest.approx(float(row.stock))


def test_stratified_sample_respects_the_cap():
    rng = np.random.default_rng(5)
    n = 1000
    frame = pd.DataFrame(
        {
            "segment": rng.choice(["declared", "assessed"], size=n, p=[0.8, 0.2]),
            "porte": rng.choice(["01", "03", "05"], size=n),
            "paid_flag": rng.integers(0, 2, size=n),
            "stock": rng.uniform(1, 100, size=n),
        }
    )
    out = diag.stratified_sample(frame, 100, ["segment", "porte", "paid_flag"], seed=5)
    assert len(out) == 100
    assert set(out.segment) == {"declared", "assessed"}


def test_spline_prefers_a_curve_and_keeps_a_line():
    rng = np.random.default_rng(6)
    x = rng.uniform(0, 6, 900)
    linear_p = 1 / (1 + np.exp(-(-1 + 0.4 * x)))
    curved_p = 1 / (1 + np.exp(-(-0.2 + 1.6 * np.sin(x))))
    line = pd.DataFrame(
        {"paid_flag": rng.binomial(1, linear_p), "log_stock": x, "segment": "declared"}
    )
    curve = pd.DataFrame(
        {"paid_flag": rng.binomial(1, curved_p), "log_stock": x, "segment": "declared"}
    )
    straight = diag.spline_compare(line, "log_stock")
    bent = diag.spline_compare(curve, "log_stock")
    assert straight["delta_aic"] < 10
    assert straight["curved"] is False
    assert bent["delta_aic"] >= 10 or bent["curved"] is True


def _population(rng, n=700):
    segment = rng.choice(["declared", "assessed"], size=n, p=[0.75, 0.25])
    porte = rng.choice(["01", "03", "05"], size=n)
    section = rng.choice(["G", "C"], size=n)
    debt_age = rng.uniform(0.5, 20, n)
    stock = rng.lognormal(mean=8, sigma=0.6, size=n)
    log_stock = np.log1p(stock)
    eta = -2.2 + 0.12 * (log_stock - log_stock.mean()) - 0.03 * debt_age
    eta = np.where(segment == "assessed", eta + 0.3, eta)
    paid_flag = rng.binomial(1, 1 / (1 + np.exp(-eta)))
    paid = paid_flag * rng.uniform(0.01, 0.2, n) * stock
    return pd.DataFrame(
        {
            "stock": stock,
            "paid": paid,
            "debt_age": debt_age,
            "segment": segment,
            "porte": porte,
            "section": section,
            "division": np.where(section == "G", "47", "10"),
            "situacao_cadastral": rng.choice(["02", "04"], size=n, p=[0.7, 0.3]),
            "municipality": rng.choice(["Alpha", "Beta", "Gamma", "Delta"], size=n),
            "uf": "SP",
            "root": [f"F{i // 7:04d}" for i in range(n)],
        }
    )


def test_diagnostics_cover_both_outcomes_samples_and_quantiles():
    rng = np.random.default_rng(7)
    out = diag.run_diagnostics(_population(rng), seed=7)
    coef = out["coefficients"]
    assert set(coef.outcome) == {"log_stock", "recovery"}
    assert set(coef["sample"]) == {"full", "trim"}
    assert set(coef.se_type) >= {"HC3", "cluster"}
    assert set(out["quantiles"].tau) == {0.25, 0.5, 0.75, 0.9}
    assert {"Probability of payment", "Package for the paper"} <= set(
        out["decision"].inference
    )
    assert len(out["reset"]) > 0
    assert len(out["bins"]) > 0
    for fig in out["figures"].values():
        html = diag.figure_html(fig)
        assert "scattergl" not in html


def test_publish_sticks_the_header_above_25_rows():
    small = pd.DataFrame({"term": ["a"], "coef": [1.0]})
    large = pd.DataFrame({"term": [f"t{i}" for i in range(26)], "coef": np.arange(26)})
    short = diag.publish(small)
    long = diag.publish(large)
    assert "position:sticky" not in short
    assert "position:sticky" in long
    assert long.count("<tr>") > 25


def test_written_tables_omit_identifiers(tmp_path):
    rng = np.random.default_rng(8)
    # A saturated dummy on this small draw makes one HC3 leverage equal to 1.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = diag.run_diagnostics(_population(rng, n=400), seed=8)
    analyze.write_tables(out, tmp_path)
    paths = list(tmp_path.glob("*.csv"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        header = text.splitlines()[0].lower()
        assert "nome_devedor" not in text.lower()
        assert "cpf_devedor" not in text.lower()
        assert "root" not in header.split(",")
        assert "cnpj" not in header


def test_page_is_registered_as_section_4_5():
    text = (REPO / "lab" / "paper_eda" / "_pagina_unica.py").read_text(encoding="utf-8")
    assert "2026-11-04_diagnostico_linearidade" in text
    assert '"lab-lin"' in text
    assert '"4.5"' in text
