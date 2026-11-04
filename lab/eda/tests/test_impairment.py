"""Synthetic checks for the IMP-23 recoverability helpers. No extract is read."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

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
    coef, fit = I.fit_nested(frame, PARAMS)
    I.assert_public(coef)
    I.assert_public(fit)
    assert set(fit["model"]) == {f"M{i}" for i in range(1, 8)}
    assert fit["pseudo_r2"].notna().all()
    assert (fit["n"] > 0).all()
    assert "odds_ratio" in coef.columns
    assert "se_cluster" in coef.columns
    assert "stars" in coef.columns
    assert coef["term"].str.startswith("C(year)").sum() == 0


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
