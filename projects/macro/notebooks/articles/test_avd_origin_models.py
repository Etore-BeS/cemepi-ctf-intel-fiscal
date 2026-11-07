"""Synthetic checks for the origin models. They do not open the extract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

MODULE_PATH = Path(__file__).with_name("avd_origin_models.py")


def load_module():
    spec = importlib.util.spec_from_file_location("avd_origin_models", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def models(monkeypatch, tmp_path):
    module = load_module()
    cache = tmp_path / "cache"
    figures = tmp_path / "figures"
    tables = tmp_path / "tables"
    figures.mkdir()
    tables.mkdir()
    monkeypatch.setattr(module, "cache_dir", lambda: cache)
    monkeypatch.setattr(module, "manuscript_dirs", lambda repo=None: (figures, tables))
    cache.mkdir()
    module.clear_frame()
    return module


def population(seed: int = 2026) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for firm in range(40):
        root = f"{firm:08d}"
        for origin, probability in (("declared", 0.2), ("assessed", 0.6)):
            for _copy in range(4):
                stock = float(rng.uniform(1_000, 5_000))
                paid_flag = int(rng.random() < probability)
                paid = stock * (0.2 if origin == "assessed" else 0.05) * paid_flag
                rows.append(
                    {
                        "origin": origin,
                        "stock": stock,
                        "paid": paid,
                        "paid_flag": paid_flag,
                        "debt_age": float(rng.uniform(1, 10)),
                        "cohort": str(2015 + (firm % 5)),
                        "section": "G" if firm % 2 == 0 else "C",
                        "porte": "01" if firm % 3 else "03",
                        "firm_age": 10.0 + (firm % 4),
                        "firm_age_missing": 0,
                        "root": root,
                    }
                )
    return pd.DataFrame(rows)


def test_usable_connection_rejects_a_dead_object(models):
    assert models.usable_connection(None) is None
    assert models.usable_connection(object()) is None


def test_sql_never_reads_person_columns(models):
    snapshot = models._snapshot_sql()
    firm = models.firm_sql("register.parquet")
    for text in (snapshot, firm):
        models.assert_safe_sql(text)
        folded = text.upper()
        assert "NOME_DEVEDOR" not in folded
        assert "CPF_DEVEDOR" not in folded
        assert "SELECT *" not in folded
    assert "READ_ONLY" in MODULE_PATH.read_text(encoding="utf-8")


def test_small_counts_are_blank(models):
    assert models.blank_count(0) == "0"
    assert models.blank_count(1) == ""
    assert models.blank_count(4) == ""
    assert models.blank_count(5) == "5"
    assert models.blank_count(11928) == "11,928"


def test_percent_difference_transformation(models):
    point, low, high = models.percent_difference(0.0, 0.1)
    assert point == pytest.approx(0.0)
    assert low < 0 < high
    point, _low, _high = models.percent_difference(np.log(1.5), 0.0)
    assert point == pytest.approx(50.0)


def test_origin_logit_ame_is_positive_and_public(models):
    frame = population()
    table, formula = models.fit_origin_logit(frame)
    models.assert_public_frame(table)
    assert "assessed" in formula
    assert "paid_flag" in formula
    ame = float(table.loc[0, "AME (pp)"])
    assert 15 < ame < 60
    assert table.loc[0, "Certificates"] == "320"
    assert table.loc[0, "Firms"] == "40"
    assert "99999999" not in table.to_csv(index=False)
    cached, _formula = models.fit_origin_logit(frame)
    pd.testing.assert_frame_equal(table, cached)


def test_within_firm_difference_is_the_probability_gap(models):
    rows = []
    for firm in range(30):
        root = f"{firm:08d}"
        for origin, paid_flag in (("declared", 0), ("assessed", 1)):
            for _copy in range(2):
                rows.append(
                    {
                        "origin": origin,
                        "stock": 1_000.0,
                        "paid": float(paid_flag),
                        "paid_flag": paid_flag,
                        "debt_age": 5.0,
                        "cohort": "2018",
                        "section": "G",
                        "porte": "01",
                        "firm_age": 12.0,
                        "firm_age_missing": 0,
                        "root": root,
                    }
                )
    table = models.fit_within_firm(pd.DataFrame(rows))
    models.assert_public_frame(table)
    assert float(table.loc[0, "Difference (pp)"]) == pytest.approx(100.0)
    assert table.loc[0, "Firms"] == "30"
    text = table.to_csv(index=False)
    assert "00000000" not in text


def test_recovery_percentage_uses_cluster_and_hc3(models):
    table, formula = models.fit_recovery_percent(population())
    models.assert_public_frame(table)
    assert formula.startswith("paid_scaled ~ assessed")
    point = float(str(table.loc[0, "Percent difference"]).replace(",", ""))
    low = float(str(table.loc[0, "Cluster 95% low"]).replace(",", ""))
    high = float(str(table.loc[0, "Cluster 95% high"]).replace(",", ""))
    assert low < point < high
    assert table.loc[0, "HC3 95% low"] != ""


def test_cache_refuses_identifier_columns(models):
    frame = pd.DataFrame({"root": ["12345678"], "AME (pp)": ["1.00"]})
    with pytest.raises(RuntimeError):
        models._write_cached("secret", frame)


def test_exports_have_no_firm_key_and_use_scatter(models, tmp_path):
    frame = population()
    logit, _formula = models.fit_origin_logit(frame)
    path = models.export_origin_logit(logit)
    tex = path.read_text(encoding="utf-8")
    assert "tab:logit-ame" in tex
    assert "12345678" not in tex
    assert "NOME_DEVEDOR" not in tex
    figure = models._interval_figure(
        logit,
        "AME (pp)",
        "Average marginal effect of assessed origin (percentage points)",
    )
    assert [trace.type for trace in figure.data] == ["scatter"]
    within = models.fit_within_firm(frame)
    models.export_within(within)
    recovery, _formula = models.fit_recovery_percent(frame)
    models.export_recovery(recovery)
    written = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert "tab_logit_origin_ame.tex" in written
    assert "tab_within_firm_logit.tex" in written
    assert "tab_recovery_pct.tex" in written
    assert "fig_logit_origin_ame.csv" in written
    assert "fig_within_firm.csv" in written
    assert "fig_recovery_pct.csv" in written
