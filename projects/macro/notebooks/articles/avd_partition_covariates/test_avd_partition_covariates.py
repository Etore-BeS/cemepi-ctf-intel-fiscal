"""Synthetic checks for the AVD partition/covariate prep. No extract is read."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

HERE = Path(__file__).resolve().parent
ARTICLES = HERE.parent
LAB_EDA = HERE.parents[4] / "lab" / "eda"
MODULE_PATH = HERE / "run.py"


def load_module():
    if str(LAB_EDA) not in sys.path:
        sys.path.insert(0, str(LAB_EDA))
    if str(ARTICLES) not in sys.path:
        sys.path.insert(0, str(ARTICLES))
    spec = importlib.util.spec_from_file_location(
        "avd_partition_covariates_run", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prep(tmp_path, monkeypatch):
    module = load_module()
    out = tmp_path / "out"
    tables = out / "tables"
    tables.mkdir(parents=True)
    (out / "output").mkdir(parents=True)
    (out / "task3").mkdir(parents=True)
    monkeypatch.setattr(module, "analysis_dirs", lambda repo=None: (out, tables))
    return module, out


def test_no_partition_methods_in_module():
    text = MODULE_PATH.read_text(encoding="utf-8").lower()
    for banned in ("hansen", "bai", "perron", "aic", "bic", "threshold grid", "gam"):
        assert banned not in text


def test_porte_pequena_uses_rfb_codes(prep):
    module, _out = prep
    codes = module.porte_pequena_series(
        pd.Series(["01", "03", "05", "00", "unknown", "01"])
    )
    assert codes.tolist() == [1, 1, 0, 0, 0, 1]
    assert module.PORTE_PEQUENA_CODES == frozenset({"01", "03"})
    assert "individual" not in module.RFB_PORTE_CODES


def test_instalment_flags_reuse_impairment_definitions(prep):
    module, _out = prep
    import _impairment as impairment

    instalments = pl.DataFrame(
        {
            "id_debito": [1, 1, 2, 3],
            "data_solicitacao": [
                date(2018, 1, 10),
                date(2019, 2, 1),
                date(2017, 6, 1),
                date(2018, 3, 1),
            ],
            "data_rompimento": [
                None,
                date(2019, 8, 1),
                date(2017, 9, 1),
                None,
            ],
            "status_parcelamento": ["A", "A", "B", "C"],
            "qtd_parcelas": [12, 24, 6, 3],
        }
    )
    cdas = pl.DataFrame({"id_debito": [1, 2, 3, 4]})
    flags = module.instalment_flags_by_cda(
        cdas, instalments, ref=date(2025, 12, 31)
    ).sort("id_debito")
    open_ids = impairment.instalment_open_ids(instalments, date(2025, 12, 31))
    expected_open = {
        row["id_debito"]: row["instalment_open"] for row in open_ids.to_dicts()
    }
    by_id = {row["id_debito"]: row for row in flags.to_dicts()}
    assert by_id[1]["has_request"] is True
    assert by_id[2]["has_request"] is True
    assert by_id[3]["has_request"] is True
    assert by_id[4]["has_request"] is False
    for debt_id, row in by_id.items():
        assert row["instalment_open"] is bool(expected_open.get(debt_id, False))
    assert by_id[1]["instalment_broken"] is True
    assert by_id[2]["instalment_broken"] is True
    assert by_id[3]["instalment_broken"] is False
    assert by_id[4]["instalment_broken"] is False
    assert by_id[4]["guaranteed"] is False


def test_inventory_reports_parcelamento_present_and_garantia_missing(prep):
    module, out = prep
    tables = out / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    path = module.write_inventory(tables)
    frame = pd.read_csv(path)
    parcel = frame.loc[frame["topic"] == "parcelamento"].iloc[0]
    garantia = frame.loc[frame["topic"] == "garantia"].iloc[0]
    assert parcel["present"] == True or str(parcel["present"]).lower() == "true"
    assert "SOLICITACAOPARCELAMENTO" in parcel["source"]
    assert garantia["present"] == False or str(garantia["present"]).lower() == "false"
    assert "no guarantee" in garantia["note"].lower() or "GUARANTEE" in garantia["note"]


def test_methodology_table_has_current_code_variables(prep):
    module, out = prep
    tables = out / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    csv_path, _tex = module.write_methodology_table(tables)
    frame = pd.read_csv(csv_path)
    names = set(frame["name"])
    for required in (
        "paid_flag",
        "origin",
        "log_stock",
        "porte",
        "porte_pequena",
        "has_request",
        "instalment_open",
        "instalment_broken",
        "guaranteed",
        "municipality",
    ):
        assert required in names
    assert "definition" in frame.columns
    assert "source" in frame.columns


def test_sql_never_reads_person_columns(prep):
    module, _out = prep
    for text in (
        module.schema_sql(),
        module.extended_snapshot_sql(),
        module.firm_enrichment_sql("register/*.parquet"),
    ):
        folded = text.upper()
        assert "NOME_DEVEDOR" not in folded
        assert "CPF_DEVEDOR" not in folded
        assert "SELECT *" not in folded


def test_task3_stub_is_not_runnable_yet(prep):
    module, _out = prep
    with pytest.raises(RuntimeError, match="Task 3"):
        module.run_task3_stub()


def test_docs_only_writes_without_duckdb(prep):
    module, out = prep
    written = module.run_docs_only()
    assert (out / "tables" / "tab_inventory_parcelamento_garantia.csv").exists()
    assert (out / "tables" / "tab_variables_methodology.csv").exists()
    assert (out / "tables" / "tab_porte_codes.csv").exists()
    assert (out / "code_facts_outcome_municipality.md").exists()
    assert "tab_schema_describe.csv" not in {path.name for path in written}
