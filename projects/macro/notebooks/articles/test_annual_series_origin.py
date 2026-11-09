"""The annual snapshot rule, without opening the extract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

MODULE_PATH = Path(__file__).with_name("annual_series_origin.py")


def load_module():
    spec = importlib.util.spec_from_file_location("annual_series_origin", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _grid() -> pd.DataFrame:
    rows = []
    for year in range(2016, 2026):
        for month in range(1, 13):
            cdas = 1000.0
            if year == 2018 and month == 12:
                cdas = None
            if year == 2020 and month >= 10:
                cdas = None
            if year == 2024 and month == 12:
                cdas = 100.0
            rows.append({"year": year, "month": month, "cdas": cdas})
    return pd.DataFrame(rows)


def test_last_complete_month_skips_partial_december_and_missing_months():
    module = load_module()
    status = module.snapshot_status(_grid())
    chosen = module.last_complete_months(status)
    assert chosen[2024] == 11
    assert chosen[2018] == 11
    assert chosen[2020] == 9
    assert chosen[2025] == 12
    december = status.loc[(status.year == 2024) & (status.month == 12)].iloc[0]
    assert bool(december.complete) is False


def test_script_attaches_the_database_read_only():
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert "READ_ONLY" in text
    assert "GROUP BY month, ID_DEBITO" in text
    assert "NOME_DEVEDOR" not in text
    assert "CPF_DEVEDOR" not in text
