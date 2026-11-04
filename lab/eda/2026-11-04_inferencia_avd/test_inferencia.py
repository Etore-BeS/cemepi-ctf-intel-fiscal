"""Synthetic checks for the declared × assessed inference. No extract is read."""

from __future__ import annotations

import importlib.util
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


infer = load("infer")
analyze = load("analyze")
diag = infer.diag


def _frame(n: int = 900, seed: int = 20261104) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    segment = np.array(["declared"] * (2 * n // 3) + ["assessed"] * (n - 2 * n // 3))
    rng.shuffle(segment)
    debt_age = rng.uniform(0.4, 24, n)
    stock = rng.lognormal(mean=8, sigma=0.45, size=n)
    log_stock = np.log1p(stock)
    municipality = rng.choice([f"M{i:02d}" for i in range(18)], size=n)
    shift = pd.Series(
        {
            name: value
            for name, value in zip(
                [f"M{i:02d}" for i in range(18)],
                np.linspace(-0.6, 0.6, 18),
                strict=True,
            )
        }
    )
    eta = (
        -1.3
        + 0.22 * (log_stock - log_stock.mean())
        - 0.035 * debt_age
        + shift.loc[municipality].to_numpy()
        + np.where(segment == "assessed", 0.25, 0.0)
    )
    paid_flag = rng.binomial(1, 1 / (1 + np.exp(-eta)))
    paid = paid_flag * rng.uniform(0.02, 0.25, n) * stock
    return pd.DataFrame(
        {
            "stock": stock,
            "paid": paid,
            "debt_age": debt_age,
            "segment": segment,
            "porte": rng.choice(["01", "03", "05"], size=n, p=[0.5, 0.3, 0.2]),
            "section": rng.choice(["G", "C", "F"], size=n, p=[0.55, 0.25, 0.2]),
            "division": rng.choice(["47", "10", "56"], size=n),
            "situacao_cadastral": rng.choice(["02", "08"], size=n, p=[0.7, 0.3]),
            "municipality": municipality,
            "uf": "SP",
            "root": [f"R{i // 4:04d}" for i in range(n)],
        }
    )


@pytest.fixture(scope="module")
def fitted():
    frame = infer.prepare(_frame())
    return infer.run_inference(frame, seed=20261104)


def test_specification_is_the_section_4_5_decision():
    assert infer.SPEC["declared"]["splines"] == ("log_stock", "debt_age")
    assert infer.SPEC["declared"]["random_intercept"] == "municipality"
    assert infer.SPEC["declared"]["screen_icc"] == pytest.approx(0.061)
    assert infer.SPEC["assessed"]["splines"] == ("debt_age",)
    assert infer.SPEC["assessed"]["linear"] == ("log_stock",)
    assert infer.SPEC["assessed"]["random_intercept"] is None
    assert infer.SPEC["assessed"]["screen_icc"] == pytest.approx(0.034)
    frame = infer.prepare(_frame(420, seed=3))
    declared = infer.payment_formula(frame.loc[frame.segment == "declared"], "declared")
    assessed = infer.payment_formula(frame.loc[frame.segment == "assessed"], "assessed")
    assert "cr(log_stock, df=4)" in declared
    assert "cr(debt_age, df=4)" in declared
    assert "cr(log_stock" not in assessed
    assert "log_stock" in assessed
    assert "cr(debt_age, df=4)" in assessed
    assert "municipality" not in declared
    assert "municipality" not in assessed


def test_loader_and_caps_are_the_section_4_5_module():
    lin = analyze.lin
    assert analyze.LIN_LOADER is lin.load_estimation_frame
    assert analyze.sql_texts is lin.sql_texts
    texts = analyze.sql_texts("icms.parquet", ["gare.parquet"], "firm.parquet")
    blob = "\n".join(texts.values()).upper()
    assert "NOME_DEVEDOR" not in blob
    assert "CPF_DEVEDOR" not in blob
    assert "SELECT *" not in blob
    fetched = texts["sample"].upper()
    assert "CNPJ" not in fetched
    assert "NOME_DEVEDOR" not in fetched
    for cap in (
        "SAMPLE_CAP",
        "SPLINE_CAP",
        "MIXED_CAP",
        "CLUSTER_PER_GROUP",
        "QUANTILE_CAP",
    ):
        assert getattr(infer, cap) == getattr(diag, cap)
    assert infer.QUANTILES == (0.25, 0.5, 0.75, 0.9)
    assert analyze.MEMORY_LIMIT == "900MB"


def test_samples_use_the_cluster_cap_and_the_spline_cap():
    rng = np.random.default_rng(4)
    n = 12_000
    declared = pd.DataFrame(
        {
            "segment": "declared",
            "municipality": np.repeat([f"M{i:03d}" for i in range(300)], 40),
            "paid_flag": rng.integers(0, 2, n),
            "porte": "01",
            "stock": rng.uniform(1, 10, n),
        }
    )
    drawn = infer.payment_rows(declared, "declared", seed=4)
    counts = drawn.groupby("municipality").size()
    assert len(drawn) <= diag.MIXED_CAP
    assert counts.max() <= diag.CLUSTER_PER_GROUP
    assert counts.size < 300
    assessed = pd.DataFrame(
        {
            "segment": "assessed",
            "paid_flag": np.tile([0, 1], 25_000),
            "porte": "01",
            "municipality": "A",
        }
    )
    assessed_rows = infer.payment_rows(assessed, "assessed", seed=4)
    assert len(assessed_rows) == diag.SPLINE_CAP
    quantile_rows = infer.quantile_rows(
        pd.concat(
            [
                declared.assign(section="G"),
                assessed.assign(section="G", segment="assessed"),
            ],
            ignore_index=True,
        ),
        seed=4,
    )
    assert len(quantile_rows) == diag.QUANTILE_CAP


def test_sparse_levels_do_not_become_coefficients():
    frame = infer.prepare(_frame(800, seed=5))
    rare = frame.iloc[:30].copy()
    rare["porte"] = "99"
    rare["paid"] = 0.0
    rare["paid_flag"] = 0
    rare.loc[rare.index[:3], "paid"] = 1.0
    rare.loc[rare.index[:3], "paid_flag"] = 1
    rare["root"] = [f"Z{i // 3:02d}" for i in range(30)]
    pooled = infer.pool_sparse(pd.concat([frame, rare], ignore_index=True))
    assert "99" not in set(pooled["porte"])
    published = pooled.loc[pooled["porte"] == "99"]
    assert published.empty


def test_fit_omits_stock_ols_and_covers_both_segments(fitted):
    coef = fitted["coefficients"]
    assert set(coef.loc[coef.outcome == "paid_flag", "segment"]) >= {
        "declared",
        "assessed",
    }
    stock = coef.loc[coef.outcome == "log_stock"]
    assert stock.empty or set(stock["model"]) == {"quantile"}
    assert "ols" not in set(coef["model"])
    quant = fitted["quantiles"]
    assert set(quant.segment) >= {"declared", "assessed"}
    assert set(quant.tau) == {0.25, 0.5, 0.75, 0.9}
    assert quant["ci_low"].notna().any()
    assert (quant["ci_low"] <= quant["coef"]).all()
    assert (quant["coef"] <= quant["ci_high"]).all()
    recovery = coef.loc[coef.outcome == "recovery"]
    assert set(recovery["model"]) == {"comparison"}
    assert set(recovery.se_type) >= {"HC3", "cluster"}
    declared = coef.loc[(coef.segment == "declared") & (coef.outcome == "paid_flag")]
    assessed = coef.loc[(coef.segment == "assessed") & (coef.outcome == "paid_flag")]
    assert declared["term"].str.contains(r"cr\(log_stock").any()
    assert declared["term"].str.contains(r"cr\(debt_age").any()
    assert not assessed["term"].str.contains(r"cr\(log_stock").any()
    assert assessed["term"].str.contains("log_stock").any()
    assert assessed["term"].str.contains(r"cr\(debt_age").any()
    assert not declared["term"].str.contains("municipality").any()
    variance = fitted["variance"]
    assert set(variance.segment) == {"declared"}
    assert variance["grouping"].iloc[0] == "municipality"
    assert variance["tau2"].iloc[0] > 0
    assert "ci_low" in variance.columns


def test_curves_are_spline_contrasts_without_webgl_or_raw_points(fitted):
    curves = fitted["curves"]
    assert set(curves.variable) == {"log_stock", "debt_age"}
    assert set(curves.segment) >= {"declared", "assessed"}
    counts = curves.groupby(["segment", "variable"]).size()
    assert counts.max() <= diag.N_BINS
    for fig in fitted["figures"].values():
        html = diag.figure_html(fig)
        assert "scattergl" not in html
        assert "Scattergl" not in html
        for trace in fig.data:
            assert trace.type == "scatter"
            assert trace.mode == "lines"
            assert len(trace.x) <= diag.N_BINS
        diag.assert_bin_grid(fig)
    html_bytes = sum(len(diag.figure_html(fig)) for fig in fitted["figures"].values())
    assert html_bytes < 2_000_000


def test_partial_curve_is_zero_at_the_median():
    frame = infer.prepare(_frame(500, seed=9))
    part = infer.pool_sparse(frame.loc[frame.segment == "assessed"].copy())
    fit = infer.fit_logit(part, "assessed")
    value = infer.contrast_at(fit, "debt_age", float(part["debt_age"].median()))
    assert value == pytest.approx(0.0, abs=1e-8)


def test_rejects_person_columns_and_cnpj():
    frame = _frame(30, seed=2)
    person = "NOME" + "_DEVEDOR"
    with pytest.raises(RuntimeError, match=person):
        infer.prepare(frame.assign(**{person: "a"}))
    with pytest.raises(RuntimeError, match="CNPJ"):
        infer.prepare(frame.assign(CNPJ="x"))


def test_cache_and_written_tables_omit_identifiers(tmp_path, monkeypatch, fitted):
    monkeypatch.setenv("CEMEPI_CACHE", str(tmp_path))
    frame = infer.prepare(_frame(480, seed=11))
    first = infer.run_inference(frame, seed=11)
    assert first["cache"] == "miss"
    second = infer.run_inference(frame, seed=11)
    assert second["cache"] == "hit"
    cached = list(tmp_path.rglob("*.parquet"))
    assert cached
    banned = {
        "root",
        "cnpj",
        "stock",
        "paid",
        "municipality",
        "nome_devedor",
        "cpf_devedor",
    }
    for path in cached:
        stored = pd.read_parquet(path)
        assert banned.isdisjoint({column.lower() for column in stored.columns})
        assert len(stored) < 5_000
    analyze.write_tables(fitted, tmp_path / "tables")
    paths = list((tmp_path / "tables").glob("*.csv"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        header = text.splitlines()[0]
        assert "nome_devedor" not in text
        assert "cpf_devedor" not in text
        assert "cnpj" not in header.split(",")
        assert "root" not in header.split(",")


def test_page_is_section_4_7_and_states_the_screen():
    page = (HERE.parent / "2026-11-04_inferencia_avd.qmd").read_text(encoding="utf-8")
    assembler = (REPO / "lab" / "paper_eda" / "_pagina_unica.py").read_text(
        encoding="utf-8"
    )
    assert "section 4.7" in page.lower() or "Section 4.7" in page
    assert "0.061" in page and "0.034" in page
    assert "0.25" in page and "0.90" in page
    assert "NOME_DEVEDOR" not in page
    assert "CPF_DEVEDOR" not in page
    assert "debtor name" in page
    assert "no CNPJ" in page
    assert "2026-11-04_inferencia_avd" in assembler
    assert '"lab-avd-inf"' in assembler
    assert '"4.7"' in assembler
    assert "sem _freeze" in assembler
