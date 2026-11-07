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


def collinear_population(seed: int = 7) -> pd.DataFrame:
    """Debt age is constant inside each enrolment year.

    With a fixed extraction date, that age is an exact function of the year,
    so the column lies in the span of the cohort indicators.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for firm in range(24):
        year = 2016 + (firm % 4)
        if firm < 2:
            section = "U"
        elif firm % 2 == 0:
            section = "G"
        else:
            section = "C"
        # Size copies section, so the size dummy duplicates a CNAE dummy.
        porte = "01" if section in {"G", "U"} else "03"
        root = f"9{firm:07d}"
        for origin, probability in (("declared", 0.2), ("assessed", 0.75)):
            for _copy in range(3):
                stock = float(1_000 + 50 * firm)
                paid_flag = int(rng.random() < probability)
                rows.append(
                    {
                        "origin": origin,
                        "stock": stock,
                        "paid": stock * 0.1 * paid_flag,
                        "paid_flag": paid_flag,
                        "debt_age": float(10 - (year - 2016)),
                        "cohort": str(year),
                        "section": section,
                        "porte": porte,
                        "firm_age": 8.0 + (firm % 6),
                        "firm_age_missing": 0,
                        "root": root,
                    }
                )
    return pd.DataFrame(rows)


def test_collinear_age_and_cohort_keeps_cohort(models, caplog):
    frame = collinear_population()
    with caplog.at_level("INFO", logger="avd_origin_models"):
        table, formula = models.fit_origin_logit(frame)
        recovery, recovery_formula = models.fit_recovery_percent(frame)
        within = models.fit_within_firm(frame)
    models.assert_public_frame(table)
    ame = float(str(table.loc[0, "AME (pp)"]).replace(",", ""))
    assert ame > 0
    assert "cohort" in formula
    assert "debt_age" not in formula
    assert "debt_age" not in recovery_formula
    assert "paid_scaled ~ assessed" in recovery_formula
    models.assert_public_frame(within)
    models.assert_public_frame(recovery)
    text = caplog.text
    assert "rank" in text
    assert "columns" in text
    assert "debt_age" in text
    assert "enrolment year is kept" in text
    assert "90000000" not in text
    assert "U" not in text
    prepared, _account = models.prepare_population(frame)
    assert "U" not in set(prepared["section"])
    assert "other" in set(prepared["section"])


def test_full_rank_logit_keeps_debt_age(models):
    _table, formula = models.fit_origin_logit(population())
    assert "debt_age" in formula
    assert "cohort" in formula


def test_cnae_below_five_firms_collapses_to_other(models):
    assert models.CNAE_MIN_FIRMS == 5
    assert models.CNAE_OTHER == "other"
    base = population()
    rare = []
    for firm in range(4):
        root = f"8{firm:07d}"
        for origin in ("declared", "assessed"):
            rare.append(
                {
                    "origin": origin,
                    "stock": 1_500.0,
                    "paid": 10.0,
                    "paid_flag": 1 if origin == "assessed" else 0,
                    "debt_age": 3.0,
                    "cohort": "2019",
                    "section": "Q",
                    "porte": "01",
                    "firm_age": 4.0,
                    "firm_age_missing": 0,
                    "root": root,
                }
            )
    prepared, _account = models.prepare_population(
        pd.concat([base, pd.DataFrame(rare)])
    )
    assert "Q" not in set(prepared["section"])
    assert "other" in set(prepared["section"])
    assert "G" in set(prepared["section"])
    kept = []
    for firm in range(5):
        root = f"7{firm:07d}"
        for origin in ("declared", "assessed"):
            kept.append(
                {
                    "origin": origin,
                    "stock": 1_500.0,
                    "paid": 10.0,
                    "paid_flag": 1,
                    "debt_age": 3.0,
                    "cohort": "2019",
                    "section": "Q",
                    "porte": "01",
                    "firm_age": 4.0,
                    "firm_age_missing": 0,
                    "root": root,
                }
            )
    prepared, _account = models.prepare_population(
        pd.concat([base, pd.DataFrame(kept)])
    )
    assert "Q" in set(prepared["section"])


def test_preflight_logs_sparse_or_separated_dummies(models, caplog):
    design = pd.DataFrame(
        {
            "Intercept": np.ones(10),
            "assessed": np.array([0, 1] * 5, dtype=float),
            "section_U": np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0], dtype=float),
            "section_Z": np.array([0, 0, 1, 1, 1, 1, 1, 1, 0, 0], dtype=float),
        }
    )
    outcome = pd.Series([0, 1, 0, 0, 0, 0, 0, 0, 1, 0], dtype=float)
    with caplog.at_level("INFO", logger="avd_origin_models"):
        models.preflight_design(outcome, design, "origin logit")
    text = caplog.text
    assert "origin logit: rank" in text
    assert "of 4 columns" in text
    assert "section_U" in text
    assert "observations=2" in text
    assert "section_Z" in text
    assert "outcome_distinct=1" in text
    assert "dummy assessed" not in text
    assert "0, 1, 0" not in text


def test_models_fail_independently(models, monkeypatch, tmp_path):
    frame = population()

    def fail(_frame):
        raise np.linalg.LinAlgError("Singular matrix")

    monkeypatch.setattr(models, "fit_origin_logit", fail)
    with pytest.raises(RuntimeError, match="logit"):
        models.run_all(frame=frame)
    written = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert "tab_within_firm_logit.tex" in written
    assert "tab_recovery_pct.tex" in written
    assert "tab_logit_origin_ame.tex" not in written


def test_from_cache_skips_extraction(models, monkeypatch, tmp_path):
    frame = population()
    path = models.save_analysis_frame(frame, repo=tmp_path)
    assert path.parts[-3:] == (".local", "cache", "avd_origin_frame.parquet")
    assert path.exists()
    models.clear_frame()

    def fail_extract(*_args, **_kwargs):
        raise AssertionError("extraction ran")

    monkeypatch.setattr(models, "_connect", fail_extract)
    monkeypatch.setattr(models, "_database_path", fail_extract)
    monkeypatch.setattr(models, "find_repo", lambda start=None: tmp_path)
    loaded = models.load_analysis_frame(from_cache=True)
    pd.testing.assert_frame_equal(loaded, frame.loc[:, list(models.FRAME_COLUMNS)])
    text = path.read_bytes()
    assert b"NOME_DEVEDOR" not in text


def test_local_cache_directory_is_gitignored():
    root = MODULE_PATH
    for path in [root, *root.parents]:
        gitignore = path / ".gitignore"
        if gitignore.exists() and (path / "AGENTS.md").exists():
            text = gitignore.read_text(encoding="utf-8")
            assert ".local/" in text.splitlines()
            return
    raise AssertionError("repository gitignore was not found")


def test_from_cache_flag(models, monkeypatch):
    seen = {}

    def fake_run_all(connection=None, *, from_cache=False, frame=None):
        seen["from_cache"] = from_cache
        return {}

    monkeypatch.setattr(models, "run_all", fake_run_all)
    models.main(["--from-cache"])
    assert seen["from_cache"] is True
    models.main([])
    assert seen["from_cache"] is False


def test_notebook_records_the_design_rules():
    notebook = MODULE_PATH.with_name("icms_declarado_autuado.ipynb").read_text(
        encoding="utf-8"
    )
    assert (
        "A CNAE section with fewer than five firms is collapsed into `other`."
        in notebook
    )
    assert "enrolment year is kept and debt age is removed" in notebook
    assert "--from-cache" in notebook
    assert ".local/cache/avd_origin_frame.parquet" in notebook


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
