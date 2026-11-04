"""Load the section 4.5 estimation sample and fit the section 4.7 models.

The extract stays on the external disk. Column lists and the DuckDB statements
are the ones in ``2026-11-04_diagnostico_linearidade/analyze.py``. This module
does not add a query. NOME_DEVEDOR, CPF_DEVEDOR and CNPJ are not in the table
that leaves DuckDB. Run it on the machine that has the disk. The tests use
synthetic rows and do not call the loader.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
LIN_DIR = HERE.parent / "2026-11-04_diagnostico_linearidade"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lin = _load("lin_analyze_avd", LIN_DIR / "analyze.py")
infer = _load("avd_infer", HERE / "infer.py")

LIN_LOADER = lin.load_estimation_frame
sql_texts = lin.sql_texts
MEMORY_LIMIT = lin.MEMORY_LIMIT
TABLES = ("coefficients", "quantiles", "curves", "variance", "seconds")


def load_estimation_frame():
    """Same DuckDB sample as section 4.5. CNPJ is a join key and does not leave DuckDB."""
    frame, population = LIN_LOADER()
    infer.reject_forbidden(frame)
    return frame, population


def write_tables(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for key in TABLES:
        frame = result.get(key)
        if isinstance(frame, pd.DataFrame):
            frame.to_csv(out_dir / f"{key}.csv", index=False)


def run(out_dir: Path | None = None) -> dict:
    sample, population = load_estimation_frame()
    frame = infer.prepare(sample)
    del sample
    result = infer.run_inference(frame)
    result["population"] = population
    if out_dir is not None:
        write_tables(result, out_dir)
        pd.DataFrame([population]).to_csv(out_dir / "population.csv", index=False)
    return result


if __name__ == "__main__":
    run(HERE / "tables")
