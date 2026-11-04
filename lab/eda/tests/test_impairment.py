"""Synthetic checks for the IMP-23 recoverability helpers. No extract is read."""

from __future__ import annotations

import gc
import os
import sys
import threading
from datetime import date
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

EDA = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EDA))
sys.path.insert(0, str(ROOT / "scripts"))

import _impairment as I
from pii_check import cnpj_valido

REF = date(2020, 12, 31)
PARAMS = I.PgeParams()


def _row(**kw) -> pl.DataFrame:
    base = {
        "id_debito": 1,
        "cnpj": I.fake_cnpj("01030000", "0001"),
        "cnpj_root": "01030000",
        "origin": "declared",
        "enrol_date": date(2018, 6, 1),
        "situacao": "Inscrito",
        "stock": 1000.0,
        "enrolled_value": 1000.0,
        "registry_code": "02",
        "match_nivel": "estabelecimento",
        "paid_5y": 0.0,
        "paid_3y": 0.0,
        "instalment_open": False,
        "guaranteed": False,
        "in_stock": True,
    }
    base.update(kw)
    return pl.DataFrame([base])


def _cls(**kw) -> str:
    return I.classify_pge(_row(**kw), REF, PARAMS)["specific"][0]


def test_age_band_edges_follow_left_closed_false():
    ages = [0.0, 2.0, 2.01, 5.0, 5.01, 10.0, 10.01, 15.0, -1.0]
    df = pl.DataFrame({"age": ages}).with_columns(I.age_band_expr())
    bands = df["age_band"].to_list()
    assert bands == [
        "(0, 2]",
        "(0, 2]",
        "(2, 5]",
        "(2, 5]",
        "(5, 10]",
        "(5, 10]",
        ">10",
        ">10",
        None,
    ]
    assert I.IMP_AGE_BREAKS == (2.0, 5.0, 10.0)
    assert I.IMP_AGE_BREAKS != (1, 3, 5, 10)


def test_each_pge_branch_and_precedence():
    assert _cls(situacao="Suspenso", instalment_open=True) == "uncollectable_suspended"
    assert _cls(instalment_open=True) == "collectable_instalment"
    assert _cls(paid_5y=200) == "collectable_active_history"
    assert _cls(registry_code="08", paid_5y=200) == "collectable_inactive_history"
    assert _cls(paid_5y=50) == "collectable_active_recent"
    assert _cls(registry_code="04", paid_5y=0) == "uncollectable_inactive_no_history"
    assert _cls(enrol_date=date(2010, 6, 1), paid_5y=0) == "uncollectable_active_old"
    assert _cls(match_nivel="nao_encontrado", registry_code=None) == "unknown_registry"
    assert _cls(match_nivel="raiz", registry_code="02") == "unknown_registry"
    assert _cls(paid_5y=100) == "collectable_active_history"  # exactly 10 percent
    assert _cls(paid_5y=99) == "collectable_active_recent"
    assert _cls(enrol_date=date(2015, 12, 31), paid_5y=0) == "collectable_active_recent"
    assert _cls(enrol_date=date(2015, 12, 30), paid_5y=0) == "uncollectable_active_old"
    guaranteed = _cls(guaranteed=True, situacao="Inscrito")
    assert guaranteed == "guaranteed"
    # Suspended wins over a guarantee flag and over an open instalment.
    assert _cls(guaranteed=True, situacao="Suspenso") == "uncollectable_suspended"


def test_history_is_summed_on_the_root_and_windows_are_calendar_years():
    root = "10000000"
    rows = []
    for slot, paid5, paid3 in ((0, 300.0, 0.0), (1, 0.0, 0.0)):
        rows.append(
            _row(
                id_debito=slot + 1,
                cnpj=I.fake_cnpj(root, f"{slot:04d}"),
                cnpj_root=root,
                paid_5y=paid5,
                paid_3y=paid3,
            ).row(0, named=True)
        )
    out = I.classify_pge(pl.DataFrame(rows), REF, PARAMS)
    # 300 + 0 over 2000 is 15 percent, so both CDAs inherit the root's history.
    assert set(out["specific"].to_list()) == {"collectable_active_history"}
    alone = _cls(paid_5y=100)
    assert alone == "collectable_active_history"

    cda_unit = I.PgeParams(history_unit="cda")
    by_cda = I.classify_pge(pl.DataFrame(rows), REF, cda_unit)
    # On the CDA unit the unpaid certificate has no history and the debt is recent.
    assert set(by_cda["specific"].to_list()) == {
        "collectable_active_history",
        "collectable_active_recent",
    }


def test_natural_person_rows_are_excluded():
    frame = _row(person_type="PF")
    out = I.classify_pge(frame, REF, PARAMS)
    assert out.height == 0
    kept = I.classify_pge(_row(person_type="PJ"), REF, PARAMS)
    assert kept.height == 1


def test_guaranteed_line_is_empty_and_carries_the_warning():
    classified = I.classify_pge(_row(paid_5y=200), REF, PARAMS)
    # Five copies so the real class is not pooled.
    clones = pl.concat(
        [
            classified.with_columns(
                pl.lit(i).alias("id_debito"),
                pl.lit(I.fake_cnpj(f"{i:02d}000000", "0001")).alias("cnpj"),
            )
            for i in range(5)
        ]
    )
    summary, _n = I.summarise_pge(clones, 2020)
    guaranteed = summary.filter(pl.col("class_label") == "guaranteed")
    assert guaranteed.height == 1
    assert guaranteed["n_cda"][0] == 0
    assert guaranteed["note"][0] == I.GUARANTEE_WARNING
    assert "collectable share is understated" in I.GUARANTEE_WARNING


def test_suppression_pools_small_cells_and_blanks_a_small_pool():
    rows = []
    # One origin with 6 CNPJs split 3+3 across two statuses: the pool is kept.
    for i, status in enumerate(["a", "a", "a", "b", "b", "b"]):
        rows.append(
            {
                "origin": "declared",
                "status": status,
                "cnpj": f"c{i}",
                "id_debito": i,
                "stock": 10.0,
            }
        )
    # A lone status with 2 CNPJs: the pool is blank.
    for i in range(2):
        rows.append(
            {
                "origin": "assessed",
                "status": "c",
                "cnpj": f"d{i}",
                "id_debito": 100 + i,
                "stock": 10.0,
            }
        )
    out, n_sup = I.suppress_aggregate(
        pl.DataFrame(rows),
        ["origin"],
        {"stock": pl.col("stock").sum(), "n_cda": pl.len()},
        pool="status",
    )
    assert n_sup == 3  # two cells of 3 and one cell of 2
    kept = out.filter(
        (pl.col("origin") == "declared") & (pl.col("status") == I.SUPPRESSED)
    )
    assert kept["n_cnpj"][0] == 6
    assert kept["stock"][0] == 60
    blank = out.filter(pl.col("origin") == "assessed")
    assert blank["stock"][0] is None
    assert blank["n_cnpj"][0] is None


def test_whole_division_is_pooled_when_any_cell_is_small():
    rows = []
    for i in range(6):
        rows.append(
            {
                "division": "46",
                "origin": "declared",
                "cnpj": f"a{i}",
                "id_debito": i,
                "stock": 1.0,
            }
        )
    for i in range(2):
        rows.append(
            {
                "division": "99",
                "origin": "declared",
                "cnpj": f"b{i}",
                "id_debito": 50 + i,
                "stock": 1.0,
            }
        )
        rows.append(
            {
                "division": "99",
                "origin": "assessed",
                "cnpj": f"b{i}",
                "id_debito": 70 + i,
                "stock": 1.0,
            }
        )
    out, n_sup = I.suppress_aggregate(
        pl.DataFrame(rows),
        ["origin"],
        {"stock": pl.col("stock").sum()},
        pool="division",
        whole=True,
    )
    assert n_sup == 2
    assert "99" not in out["division"].to_list()
    pooled = out.filter(pl.col("division") == I.SUPPRESSED)
    # Two CNPJs in the pool, still under the floor, so the values are blank.
    assert pooled["stock"].null_count() == pooled.height


def test_no_year_after_the_window_and_impossible_dates_are_counted():
    bundle = I.synthetic_bundle()
    assert bundle["panel"].filter(pl.col("year") > I.LAST_YEAR).height >= 1
    annual, _n = I.annual_recovery(bundle["panel"], bundle["firms"])
    assert annual.filter(pl.col("year") > I.LAST_YEAR).height == 0
    assert annual["year"].max() <= I.LAST_YEAR
    quality = I.date_quality(bundle["instalments"], "data_rompimento")
    assert quality["n_year_before_1000"] >= 1
    assert quality["n_before_window"] >= 1
    assert quality["n_after_window"] >= 1
    assert quality["n_null"] >= 1
    # A year-502 break does not close the request and is not a break.
    bad = bundle["instalments"].filter(pl.col("data_rompimento").dt.year() < 1000)
    assert bad.height >= 1


def test_windows_of_five_and_three_years_on_the_synthetic_panel():
    bundle = I.synthetic_bundle()
    snap = I.cda_at_ref(
        bundle["panel"], bundle["firms"], bundle["instalments"], 2020, PARAMS
    )
    # The 2015 payment is outside 2016–2020. The 2018 payment is inside both windows.
    assessed = snap.filter(pl.col("origin") == "assessed")
    assert assessed.filter(pl.col("paid_5y") > 500).height == 0
    classified = I.classify_pge(snap, REF, PARAMS)
    labels = set(classified["specific"].to_list())
    expected = {
        "uncollectable_suspended",
        "collectable_instalment",
        "collectable_active_history",
        "collectable_inactive_history",
        "collectable_active_recent",
        "uncollectable_inactive_no_history",
        "uncollectable_active_old",
        "unknown_registry",
    }
    assert expected <= labels
    # Payment only in 2017: history, and no collection in 2018–2020.
    hist = classified.filter(pl.col("specific") == "collectable_active_history")
    assert hist.filter(~pl.col("recoverable")).height > 0
    assert hist.filter(pl.col("recoverable")).height > 0


def test_public_tables_have_no_identifiers_and_cnpjs_fail_the_check_digit():
    bundle = I.synthetic_bundle()
    for cnpj in bundle["firms"]["cnpj"].to_list():
        assert not cnpj_valido(cnpj)
    annual, _n = I.annual_recovery(bundle["panel"], bundle["firms"])
    I.assert_public(annual)
    share, _n = I.request_share(bundle["panel"], bundle["instalments"])
    I.assert_public(share)
    for name in ("panel", "firms", "instalments", "protests", "payments"):
        I.reject_person_columns(bundle[name])
        assert "NOME_DEVEDOR" not in bundle[name].columns
        assert "CPF_DEVEDOR" not in bundle[name].columns


def test_sql_does_not_read_person_columns_or_star():
    for sql in (I.SQL_INSTALMENTS, I.SQL_PROTEST):
        upper = sql.upper()
        assert "NOME_DEVEDOR" not in upper
        assert "CPF_DEVEDOR" not in upper
        assert "SELECT *" not in upper
        assert "SELECT*" not in upper


def test_figures_do_not_use_webgl():
    df = pl.DataFrame(
        {
            "year": [2016, 2017, 2018],
            "rate": [0.1, 0.2, 0.15],
            "origin": ["declared"] * 3,
        }
    )
    fig = I.fig_lines(df, "year", "rate", "origin", x_title="Year", y_title="Rate")
    assert not I.uses_webgl(fig)
    assert {tr.type for tr in fig.data} == {"scatter"}
    bars = I.fig_bars(df, "year", "rate", x_title="Year", y_title="Rate")
    assert not I.uses_webgl(bars)
    heat = I.fig_heatmap(df, "origin", "year", "rate", x_title="Year", y_title="Origin")
    assert not I.uses_webgl(heat)
    assert {tr.type for tr in heat.data} == {"heatmap"}


def test_sources_have_no_webgl_and_no_person_select():
    text = (EDA / "_impairment.py").read_text(encoding="utf-8")
    for token in ("Scattergl", "scattergl", "Heatmapgl", "render_mode"):
        assert token not in text
    for qmd in EDA.glob("2026-11-04_*.qmd"):
        body = qmd.read_text(encoding="utf-8")
        for token in (
            "Scattergl",
            "scattergl",
            "Heatmapgl",
            "render_mode",
            "NOME_DEVEDOR",
            "CPF_DEVEDOR",
        ):
            assert token not in body


def test_kaplan_meier_hand_calculation():
    counts = pl.DataFrame(
        {
            "origin": ["declared", "declared"],
            "t": [10, 20],
            "n": [4, 2],
            "d": [4, 0],
            "n_cnpj": [6, 6],
        }
    )
    curve = I.kaplan_meier(counts, ["origin"]).sort("t")
    # Risk 6 then 2. Four events at the first time: share 4/6.
    # The later point has fewer than 5 certificates at risk, so the share is hidden.
    assert abs(curve["share"][0] - 4 / 6) < 1e-9
    assert curve["share"][1] is None or curve["share"][1] != curve["share"][1]


def test_logit_is_nested_on_the_payment_outcome_and_hides_identifiers():
    bundle = I.synthetic_bundle()
    frame, notes = I.estimation_frame(
        bundle["panel"],
        bundle["firms"],
        bundle["instalments"],
        bundle["protests"],
        PARAMS,
    )
    assert "status_protesto" not in frame.columns
    assert set(frame["cnae_division"].dropna().unique()) <= {
        "46",
        "47",
        "99",
        I.SUPPRESSED,
    }
    assert all(len(v) == 2 for v in frame["cnae_division"].dropna())
    assert notes["formula_m1"] == "paid_flag ~ log_stock + age + rate_l1 + C(year)"
    formulas = dict(I.nested_formulas(frame))
    assert formulas["M1"] == I.M1_FORMULA
    assert formulas["M1"] in formulas["M7"]
    assert "instalment_requested_before" in formulas["M6"]
    assert "protest_before" not in formulas["M6"]
    assert "protest_before" in formulas["M7"]
    coef, fit, solver = I.fit_nested(frame, PARAMS)
    assert solver == "newton"
    I.assert_public(coef)
    I.assert_public(fit)
    assert set(fit["model"]) == {f"M{i}" for i in range(1, 8)}
    assert fit["pseudo_r2"].notna().all()
    assert (fit["n"] > 0).all()
    assert "odds_ratio" in coef.columns
    assert "se_cluster" in coef.columns
    assert "stars" in coef.columns
    assert coef["term"].str.startswith("C(year)").sum() == 0


def _known_slope_frame(n: int, seed: int = 2026) -> pd.DataFrame:
    """Logit data with the published 5.6.1 slopes. No stratified draw."""
    rng = np.random.default_rng(seed)
    log_stock = rng.normal(8.0, 1.5, n)
    age = rng.uniform(0.5, 12.0, n)
    rate_l1 = rng.normal(0.1, 0.25, n)
    eta = 0.55 - 0.123 * log_stock - 0.077 * age + 0.0016 * rate_l1
    paid = (rng.random(n) < 1.0 / (1.0 + np.exp(-eta))).astype(int)
    year = rng.choice(np.arange(2016, 2026), n)
    return pd.DataFrame(
        {
            "id_debito": np.arange(n),
            "year": year.astype(str),
            "cnpj_root": [f"r{i // 20:05d}" for i in range(n)],
            "paid_flag": paid,
            "log_stock": log_stock,
            "age": age,
            "rate_l1": rate_l1,
            "origin": np.where(rng.random(n) < 0.5, "declared", "assessed"),
            "status": rng.choice(["active", "closed (baixada)", "other code"], n),
            "cnae_division": rng.choice(["46", "47", "10"], n),
            "porte_label": rng.choice(["micro", "other sizes", "small"], n),
            "instalment_requested_before": rng.integers(0, 2, n),
            "instalment_broken_before": rng.integers(0, 2, n),
            "protest_before": rng.choice(
                ["not protested", "protested before the year"], n
            ),
        }
    )


def test_m1_recovers_known_slopes_and_nested_r2_does_not_fall():
    import time

    frame = _known_slope_frame(100_000)
    started = time.perf_counter()
    m0 = I.fit_logit("paid_flag ~ 1", frame, "M0", positive_r2=False)
    coef, fit, solver = I.fit_nested(frame, I.PgeParams())
    elapsed = time.perf_counter() - started
    print(f"5.9 Newton runtime: {elapsed:.1f} s on {len(frame)} rows; solver {solver}.")
    assert solver == "newton"
    assert elapsed < 600
    slopes = coef[coef["model"] == "M1"].set_index("term")["coef"]
    assert abs(float(slopes["log_stock"]) - (-0.123)) < 0.03
    assert abs(float(slopes["age"]) - (-0.077)) < 0.02
    assert abs(float(slopes["rate_l1"]) - 0.0016) < 0.08
    order = [f"M{i}" for i in range(8)]
    pseudo = [float(m0.prsquared)] + [
        float(fit.set_index("model").loc[name, "pseudo_r2"]) for name in order[1:]
    ]
    assert pseudo[0] >= -1e-8
    assert pseudo[1] >= pseudo[0]
    assert all(later + 1e-8 >= earlier for earlier, later in pairwise(pseudo))
    assert all(value > 0 for value in pseudo[1:])
    key = I._fit_cache_key(frame, I.PgeParams())
    I.FIT_VERSION = "stale"
    try:
        assert I._fit_cache_key(frame, I.PgeParams()) != key
    finally:
        I.FIT_VERSION = "3"


def test_a_zero_payer_category_is_merged_and_does_not_separate():
    frame = _known_slope_frame(2_000, seed=11)
    extra = frame.iloc[:40].copy()
    extra["origin"] = "nowhere"
    extra["paid_flag"] = 0
    extra["id_debito"] = np.arange(2_000, 2_040)
    rare = frame.iloc[40:55].copy()
    rare["cnae_division"] = "99"
    rare["paid_flag"] = [1] * 4 + [0] * 11
    rare["id_debito"] = np.arange(2_040, 2_055)
    frame = pd.concat([frame, extra, rare], ignore_index=True)
    merged, merges = I._merge_sparse_payers(frame)
    nowhere = [
        item
        for item in merges
        if item["variable"] == "origin" and item["category"] == "nowhere"
    ]
    cnae = [
        item
        for item in merges
        if item["variable"] == "cnae_division" and item["category"] == "99"
    ]
    assert len(nowhere) == 1
    assert nowhere[0]["reason"] == "<10 payers in sample"
    assert len(cnae) == 1
    assert cnae[0]["reason"] == "<10 payers in sample"
    shown = I.payer_merge_table(merges)
    for column in shown.columns:
        lowered = column.lower()
        assert lowered not in {"n", "rows", "size"}
        assert "count" not in lowered
        assert "payer" not in lowered
        assert not lowered.startswith("n_")
    assert set(shown.columns) == {"variable", "category", "reason"}
    assert set(shown["reason"].to_list()) == {"<10 payers in sample"}
    assert (merged["origin"] == "nowhere").sum() == 0
    assert (merged["cnae_division"] == "99").sum() == 0
    coef, fit, solver = I.fit_nested(merged, I.PgeParams(cluster=False))
    assert solver == "newton"
    assert float(coef["coef"].abs().max()) < 5
    assert (fit["score_norm"] < I.FIT_GRAD_TOL).all()
    bare = I._fit_cache_key(merged, I.PgeParams(), [])
    listed = I._fit_cache_key(merged, I.PgeParams(), merges)
    assert bare != listed


def test_stratified_sample_respects_the_cap():
    bundle = I.synthetic_bundle()
    params = I.PgeParams(sample_n=40)
    _frame, notes = I.estimation_frame(
        bundle["panel"],
        bundle["firms"],
        bundle["instalments"],
        bundle["protests"],
        params,
    )
    assert notes["n_sampled"] <= notes["n_before_sample"]
    if notes["n_before_sample"] > 40:
        assert notes["stratified"] is True
        assert notes["n_sampled"] <= 40
        assert notes["strata"] == "year, origin, paid_flag"


def test_fit_cache_hits_on_the_second_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CEMEPI_CACHE", str(tmp_path))
    bundle = I.synthetic_bundle()
    coef, fit, notes = I.fit_payment_models(
        bundle["panel"],
        bundle["firms"],
        bundle["instalments"],
        bundle["protests"],
        PARAMS,
    )
    assert notes["fit_cache"] == "miss"
    assert notes["solver"] in {"lbfgs", "bfgs", "newton"}
    assert set(fit["model"]) == {f"M{i}" for i in range(1, 8)}
    I.assert_public(coef)
    _, _, again = I.fit_payment_models(
        bundle["panel"],
        bundle["firms"],
        bundle["instalments"],
        bundle["protests"],
        PARAMS,
    )
    assert again["fit_cache"] == "hit"
    assert again["n_sampled"] == notes["n_sampled"]


def test_synthetic_mode_does_not_open_the_extract(monkeypatch):
    monkeypatch.setenv("IMP23_SYNTHETIC", "1")

    def boom():
        raise AssertionError("the extract was opened")

    monkeypatch.setattr(I, "load_observed_bundle", boom)
    bundle, params = I.page_setup()
    assert bundle["synthetic"] is True
    assert params.history_unit == "cnpj_root"
    assert params.ref_years == (2020, 2021, 2022, 2023, 2024)
    text = I.assumptions_text(params, synthetic=True)
    for label in ("Q1.", "Q2.", "Q3.", "Q4.", "Q5.", "Q6.", "Q7.", "Q8."):
        assert label in text
    assert I.GUARANTEE_WARNING in text
    assert I.CITED_COLLECTION in text


def test_backtest_covers_the_reference_years_and_stops_at_the_window():
    bundle = I.synthetic_bundle()
    classes, recovered, meta = I.backtest_rule(
        bundle["panel"], bundle["firms"], bundle["instalments"], PARAMS
    )
    assert set(classes["ref_year"].unique().to_list()) == set(PARAMS.ref_years)
    assert recovered["ref_year"].max() <= I.LAST_YEAR
    assert "n_cells_suppressed" in meta
    I.assert_public(classes)
    I.assert_public(recovered)
    assert classes.filter(pl.col("class_label") == "guaranteed")["n_cda"].sum() == 0
    coverage = meta["coverage"]
    assert set(coverage["ref_year"].to_list()) == set(PARAMS.ref_years)
    assert coverage.filter(~pl.col("included")).height == 0
    bases = I.stock_bases(bundle["panel"])
    assert set(bases["year"].to_list()) <= set(range(I.FIRST_YEAR, I.LAST_YEAR + 1))
    assert (bases["n_not_in_december"] >= 0).all()
    assert (bases["stock_not_in_december"] >= -1e-6).all()


def test_a_missing_december_snapshot_is_reported_and_left_out():
    bundle = I.synthetic_bundle()
    panel = bundle["panel"].with_columns(
        pl.when(pl.col("year") == 2020)
        .then(pl.lit(False))
        .otherwise(pl.col("present_in_december"))
        .alias("present_in_december")
    )
    classes, _, meta = I.backtest_rule(
        panel, bundle["firms"], bundle["instalments"], PARAMS
    )
    assert 2020 not in classes["ref_year"].to_list()
    row = meta["coverage"].filter(pl.col("ref_year") == 2020).row(0, named=True)
    assert row["included"] is False
    assert row["n_cda"] == 0
    assert "present_in_december" in row["reason"]
    note = meta["coverage"].filter(pl.col("ref_year") == 2024).row(0, named=True)
    assert note["included"] is True
    assert "EQ-05" in note["reason"]


def test_lazy_reference_facts_match_the_in_memory_facts():
    bundle = I.synthetic_bundle()
    params = I.PgeParams()
    eager = I.ref_facts(bundle["panel"], params)
    lazy = I.ref_facts(bundle["panel"].lazy(), params)
    year = 2020
    cols = [
        "id_debito",
        f"n_snap_{year}",
        f"paid5_{year}",
        f"paid3_{year}",
        f"stock_{year}",
        f"cnpj_{year}",
        f"situacao_{year}",
    ]
    left = eager.select(cols).sort("id_debito")
    right = lazy.select(cols).sort("id_debito")
    assert left.height == right.height
    joined = left.join(right, on="id_debito", suffix="_l")
    assert (joined[f"n_snap_{year}"] == joined[f"n_snap_{year}_l"]).all()
    assert (joined[f"paid5_{year}"] - joined[f"paid5_{year}_l"]).abs().max() < 1e-4
    assert (
        joined[f"cnpj_{year}"].fill_null("") == joined[f"cnpj_{year}_l"].fill_null("")
    ).all()


def test_cda_index_is_built_once_per_page():
    bundle = I.synthetic_bundle()
    I.reset_build_counts()
    I.instalment_page(
        bundle["panel"], bundle["instalments"], bundle["protests"], bundle["payments"]
    )
    assert I.cda_index_builds() == 1
    I.reset_build_counts()
    I.recovery_age_page(bundle["panel"], bundle["firms"])
    assert I.cda_index_builds() == 0
    assert I._BUILDS["age_base"] == 1
    I.reset_build_counts()
    I.backtest_rule(
        bundle["panel"], bundle["firms"], bundle["instalments"], I.PgeParams()
    )
    assert I.cda_index_builds() == 0
    assert I._BUILDS["ref_facts"] == 1


def test_variable_table_uses_the_four_statuses_and_names_the_material():
    table = I.variable_table()
    assert set(table["status"].unique().to_list()) <= {
        "available",
        "partial",
        "missing",
        "n/a",
    }
    assert "missing" in table["status"].to_list()
    guarantee = table.filter(
        pl.col("variable") == "Guarantee (flag, type, value, date)"
    )
    assert guarantee["status"][0] == "missing"
    note = I.cited_material_note()
    assert "Proposal of variables for the recoverability model (PGE-SP, 2026)" in note
    assert "PGE-SP seminar on active debt (2026)" in note


def _same_table(left: pl.DataFrame, right: pl.DataFrame, keys: list[str]) -> None:
    assert set(left.columns) == set(right.columns)
    order = [col for col in keys if col in left.columns]
    left = left.sort(order)
    right = right.sort(order)
    assert left.height == right.height
    for col in left.columns:
        if left[col].dtype.is_float() or right[col].dtype.is_float():
            gap = (
                (
                    left[col].cast(pl.Float64).fill_null(0)
                    - right[col].cast(pl.Float64).fill_null(0)
                )
                .abs()
                .max()
            )
            assert gap is None or gap <= 1e-4, col
        else:
            assert (
                left[col].cast(pl.Utf8).fill_null("<NA>")
                == right[col].cast(pl.Utf8).fill_null("<NA>")
            ).all(), col


def test_lazy_steps_match_the_in_memory_frames(tmp_path: Path):
    """A parquet scan, including a renamed GARE scan, matches the eager tables."""
    bundle = I.synthetic_bundle()
    panel = bundle["panel"]
    cohort = I.cda_index(panel)
    panel.write_parquet(tmp_path / "panel.parquet")
    bundle["instalments"].write_parquet(tmp_path / "instalments.parquet")
    bundle["protests"].write_parquet(tmp_path / "protests.parquet")
    bundle["payments"].write_parquet(tmp_path / "payments.parquet")
    gare = tmp_path / "gare" / "year=2020"
    gare.mkdir(parents=True)
    paid = bundle["payments"]
    pl.DataFrame(
        {
            "ID_DEBITO": paid["id_debito"],
            "DATA_ARRECADACAO_GARE": paid["pay_date"],
            "VALOR_RECEITA": paid["amount"],
            "EXTRA_NOTE": ["kept out of the projection"] * paid.height,
        }
    ).write_parquet(gare / "part.parquet")
    raw = tmp_path / "raw_protests.parquet"
    pl.DataFrame(
        {
            "ID_DEBITO": bundle["protests"]["id_debito"],
            "STATUS_PROTESTO": bundle["protests"]["status_protesto"],
            "DATA_PROTESTO": bundle["protests"]["data_protesto"],
            "VALOR_PROTESTADO": bundle["protests"]["valor_protestado"],
        }
    ).write_parquet(raw)
    panel_l = pl.scan_parquet(tmp_path / "panel.parquet")
    instalments = pl.scan_parquet(tmp_path / "instalments.parquet")
    protests = pl.scan_parquet(tmp_path / "protests.parquet")
    payments = pl.scan_parquet(tmp_path / "payments.parquet")
    firms = bundle["firms"]
    before = I.sql_runs()

    def check(eager, lazy, keys):
        _same_table(eager[0], lazy[0], keys)
        assert eager[1] == lazy[1]

    check(
        I.request_share(panel, bundle["instalments"], cdas=cohort),
        I.request_share(panel_l, instalments, cdas=cohort),
        ["enrol_year", "origin"],
    )
    check(
        I.protest_share(panel, bundle["protests"], cdas=cohort),
        I.protest_share(panel_l, protests, cdas=cohort),
        ["enrol_year", "origin"],
    )
    check(
        I.status_distribution(panel, bundle["instalments"], cdas=cohort),
        I.status_distribution(panel_l, instalments, cdas=cohort),
        ["origin", "status"],
    )
    check(
        I.protest_status(panel, bundle["protests"], cdas=cohort),
        I.protest_status(panel_l, protests, cdas=cohort),
        ["origin", "status"],
    )
    check(
        I.recovery_by_flag(panel, bundle["instalments"], flag="request", cdas=cohort),
        I.recovery_by_flag(panel_l, instalments, flag="request", cdas=cohort),
        ["origin", "group"],
    )
    eager_break = I.break_rates(panel, bundle["instalments"], cdas=cohort)
    lazy_break = I.break_rates(panel_l, instalments, cdas=cohort)
    _same_table(eager_break[0], lazy_break[0], ["origin"])
    _same_table(eager_break[1], lazy_break[1], ["origin", "parcel_band"])
    assert eager_break[2] == lazy_break[2]
    eager_km = I.km_enrol_to_request(panel, bundle["instalments"], cdas=cohort)
    lazy_km = I.km_enrol_to_request(panel_l, instalments, cdas=cohort)
    assert eager_km[2] == lazy_km[2]
    if eager_km[0].height:
        _same_table(eager_km[0], lazy_km[0], ["origin", "t"])
    check(
        I.recovery_around_protest(
            panel, bundle["protests"], bundle["payments"], cdas=cohort
        ),
        I.recovery_around_protest(panel_l, protests, payments, cdas=cohort),
        ["origin"],
    )
    check(
        I.annual_recovery(panel, firms),
        I.annual_recovery(panel_l, firms),
        ["year", "age_band", "origin"],
    )
    check(
        I.cumulative_by_band(panel),
        I.cumulative_by_band(panel_l),
        ["enrol_year", "age_band", "origin"],
    )
    eager_bt = I.backtest_rule(panel, firms, bundle["instalments"], PARAMS)
    I.reset_build_counts()
    lazy_bt = I.backtest_rule(panel_l, firms, instalments, PARAMS)
    assert I._BUILDS["ref_facts"] == 1
    _same_table(eager_bt[0], lazy_bt[0], ["ref_year", "origin", "class_label"])
    _same_table(eager_bt[1], lazy_bt[1], ["ref_year", "origin", "binary_class"])
    eager_fit = I.estimation_frame(
        panel, firms, bundle["instalments"], bundle["protests"], PARAMS
    )
    lazy_fit = I.estimation_frame(panel_l, firms, instalments, protests, PARAMS)
    assert eager_fit[1]["n_estimated"] == lazy_fit[1]["n_estimated"]
    _same_table(
        pl.from_pandas(eager_fit[0]),
        pl.from_pandas(lazy_fit[0]),
        ["id_debito", "year"],
    )
    aliased_pay = (
        pl.scan_parquet(tmp_path / "gare" / "**" / "*.parquet", hive_partitioning=True)
        .filter(pl.col("DATA_ARRECADACAO_GARE") <= pl.date(2025, 12, 31))
        .select(
            pl.col("ID_DEBITO").alias("id_debito"),
            pl.col("DATA_ARRECADACAO_GARE").alias("pay_date"),
            pl.col("VALOR_RECEITA").alias("amount"),
        )
    )
    compiled = I._compile_relation(aliased_pay)
    assert compiled is not None
    assert "EXTRA_NOTE" not in compiled
    assert "SELECT *" not in compiled
    aliased_protest = pl.scan_parquet(raw).select(
        pl.col("ID_DEBITO").alias("id_debito"),
        pl.col("STATUS_PROTESTO").alias("status_protesto"),
        pl.col("DATA_PROTESTO").alias("data_protesto"),
        pl.col("VALOR_PROTESTADO").alias("valor_protestado"),
    )
    check(
        I.recovery_around_protest(
            panel, bundle["protests"], bundle["payments"], cdas=cohort
        ),
        I.recovery_around_protest(panel_l, aliased_protest, aliased_pay, cdas=cohort),
        ["origin"],
    )
    assert I.date_quality(aliased_protest, "data_protesto") == I.date_quality(
        bundle["protests"], "data_protesto"
    )
    after = I.sql_runs()
    assert after["recovery"] > before["recovery"]
    assert after["age"] > before["age"]
    assert after["backtest"] > before["backtest"]
    assert after["logit"] > before["logit"]
    assert after["dates"] > before["dates"]
    page = (EDA / "2026-11-04_instalments_protest.qmd").read_text(encoding="utf-8")
    assert "instalment_page(" not in page
    assert "I.recovery_around_protest(" in page
    age_page = (EDA / "2026-11-04_recovery_age_bands.qmd").read_text(encoding="utf-8")
    assert "recovery_age_page(" not in age_page
    assert "I.annual_recovery(" in age_page


def _rss_bytes() -> int:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("VmRSS is not available")


def _extra_mib(fn) -> float:
    gc.collect()
    start = _rss_bytes()
    peak = {"rss": start}
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.02):
            peak["rss"] = max(peak["rss"], _rss_bytes())

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        fn()
    finally:
        stop.set()
        thread.join()
        peak["rss"] = max(peak["rss"], _rss_bytes())
    return (peak["rss"] - start) / (1024 * 1024)


def _ensure_scale(root: Path) -> None:
    """Synthetic parquet at the published protest and GARE volumes. Not PGE data."""
    marker = root / "_READY"
    if marker.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='900MB'")
    con.execute("SET temp_directory='/tmp'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")
    specs = {
        "protests.parquet": """
            SELECT (i % 2000000)::BIGINT AS id_debito,
                   CASE WHEN i % 23 = 0 THEN NULL
                        ELSE DATE '2016-01-01' + (i % 3000)::INTEGER
                   END AS data_protesto,
                   CASE WHEN i % 11 = 0 THEN 'cancelled' ELSE 'filed' END
                       AS status_protesto,
                   (i % 100)::DOUBLE AS valor_protestado
            FROM range(18889381) t(i)
        """,
        "gare.parquet": """
            SELECT (i % 2000000)::BIGINT AS id_debito,
                   DATE '2016-01-01' + (i % 3500)::INTEGER AS pay_date,
                   (10 + (i % 50))::DOUBLE AS amount
            FROM range(8673659) t(i)
        """,
        "instalments.parquet": """
            SELECT i::BIGINT AS id_solicitacao,
                   (i % 2000000)::BIGINT AS id_debito,
                   DATE '2017-01-01' + (i % 2500)::INTEGER AS data_solicitacao,
                   CASE WHEN i % 7 = 0 THEN 'broken' ELSE 'open' END
                       AS status_parcelamento,
                   CASE WHEN i % 7 = 0
                        THEN DATE '2018-01-01' + (i % 2000)::INTEGER
                        ELSE NULL END AS data_rompimento,
                   (1 + (i % 80))::INTEGER AS qtd_parcelas
            FROM range(2715674) t(i)
        """,
        "firms.parquet": """
            SELECT printf('%014d', i) AS cnpj,
                   'estabelecimento' AS match_nivel,
                   printf('%02d', i % 87) AS cnae_divisao,
                   '02' AS situacao_cadastral,
                   CASE WHEN i % 3 = 0 THEN '01'
                        WHEN i % 3 = 1 THEN '03'
                        ELSE '05' END AS porte
            FROM range(968610) t(i)
        """,
        "cohort.parquet": """
            SELECT i::BIGINT AS id_debito,
                   printf('%014d', i % 968610) AS cnpj,
                   printf('%08d', i % 968610) AS cnpj_root,
                   CASE WHEN i % 2 = 0 THEN 'declared' ELSE 'assessed' END AS origin,
                   DATE '2016-01-01' + CAST((i % 8) * 365 AS INTEGER) AS enrol_date,
                   2016 + (i % 8) AS enrol_year,
                   1000.0 AS enrolled_value,
                   100.0 AS paid
            FROM range(2000000) t(i)
        """,
        "panel.parquet": """
            SELECT c.id_debito,
                   y.year,
                   printf('%014d', c.id_debito % 968610) AS cnpj,
                   printf('%08d', c.id_debito % 968610) AS cnpj_root,
                   CASE WHEN c.id_debito % 2 = 0 THEN 'declared' ELSE 'assessed' END
                       AS origin,
                   DATE '2016-01-01' + CAST((c.id_debito % 8) * 365 AS INTEGER)
                       AS enrol_date,
                   2016 + (c.id_debito % 8) AS enrol_year,
                   'Inscrito' AS situacao,
                   100.0 AS stock,
                   10.0 AS paid,
                   1 AS paid_flag,
                   0.1 AS rate,
                   0.1 AS rate_l1,
                   (y.year - (2016 + (c.id_debito % 8)))::DOUBLE AS age,
                   TRUE AS present_in_december,
                   1000.0 AS enrolled_value
            FROM range(2000000) c(id_debito)
            CROSS JOIN (SELECT unnest(range(2016, 2026)) AS year) y
        """,
    }
    try:
        for name, query in specs.items():
            path = (root / name).as_posix().replace("'", "''")
            con.execute(
                f"COPY ({query}) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
    finally:
        con.close()
    marker.write_text("protests=18889381\ngare=8673659\n", encoding="utf-8")


def test_real_volume_steps_stay_within_two_gigabytes():
    """Extra RSS at protest volume 18889381 and GARE volume 8673659 stays under 2 GiB."""
    os.environ["POLARS_MAX_THREADS"] = "2"
    root = Path("/tmp/imp23_scale")
    _ensure_scale(root)
    import duckdb

    counted = duckdb.connect()
    try:
        n_protest = counted.execute(
            f"SELECT count(*) FROM read_parquet('{(root / 'protests.parquet').as_posix()}')"
        ).fetchone()[0]
        n_gare = counted.execute(
            f"SELECT count(*) FROM read_parquet('{(root / 'gare.parquet').as_posix()}')"
        ).fetchone()[0]
    finally:
        counted.close()
    assert n_protest == 18_889_381
    assert n_gare == 8_673_659
    cohort = pl.read_parquet(root / "cohort.parquet")
    firms = pl.read_parquet(root / "firms.parquet")
    panel = pl.scan_parquet(root / "panel.parquet")
    instalments = pl.scan_parquet(root / "instalments.parquet")
    protests = pl.scan_parquet(root / "protests.parquet")
    payments = pl.scan_parquet(root / "gare.parquet")
    before = I.sql_runs()
    steps = {
        "3.16 date quality": lambda: I.date_quality(protests, "data_protesto"),
        "3.16 request share": lambda: I.request_share(panel, instalments, cdas=cohort),
        "3.16 status": lambda: I.status_distribution(panel, instalments, cdas=cohort),
        "3.16 km request": lambda: I.km_enrol_to_request(
            panel, instalments, cdas=cohort
        ),
        "3.16 km break": lambda: I.km_request_to_break(instalments, panel, cdas=cohort),
        "3.16 break rates": lambda: I.break_rates(panel, instalments, cdas=cohort),
        "3.16 recovery request": lambda: I.recovery_by_flag(
            panel, instalments, flag="request", cdas=cohort
        ),
        "3.16 protest share": lambda: I.protest_share(panel, protests, cdas=cohort),
        "3.16 km protest": lambda: I.km_enrol_to_protest(panel, protests, cdas=cohort),
        "3.16 protest status": lambda: I.protest_status(panel, protests, cdas=cohort),
        "3.16 recovery around protest": lambda: I.recovery_around_protest(
            panel, protests, payments, cdas=cohort
        ),
        "3.17 annual": lambda: I.annual_recovery(panel, firms),
        "3.17 mean": lambda: I.mean_recovery(panel, firms),
        "3.17 cumulative": lambda: I.cumulative_by_band(panel),
        "3.17 status": lambda: I.recovery_by_status(panel, firms),
        "3.18 backtest": lambda: I.backtest_rule(panel, firms, instalments, PARAMS),
        "5.9 design matrix": lambda: I.estimation_frame(
            panel, firms, instalments, protests, PARAMS
        ),
    }
    peaks: dict[str, float] = {}
    for name, fn in steps.items():
        peaks[name] = round(_extra_mib(fn), 1)
        print(f"{name}: {peaks[name]} MiB extra")
        assert peaks[name] <= 2048, peaks
    after = I.sql_runs()
    assert after["recovery"] > before["recovery"]
    assert after["age"] > before["age"]
    assert after["backtest"] > before["backtest"]
    assert after["logit"] > before["logit"]
