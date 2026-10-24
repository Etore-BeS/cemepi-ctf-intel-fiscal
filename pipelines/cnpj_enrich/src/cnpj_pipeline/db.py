"""DuckDB connection with bounded memory, spill directory on the external drive and the CNPJ macros."""
from __future__ import annotations

from pathlib import Path

import duckdb

from .cnpj import SQL_MACROS
from .config import Settings


def connect(s: Settings, attach: Path | None = None) -> duckdb.DuckDBPyConnection:
    s.temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{s.memory_limit}'")
    con.execute(f"SET threads={int(s.threads)}")
    con.execute(f"SET temp_directory='{s.temp_dir.as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute(SQL_MACROS)
    if attach is not None:
        con.execute(f"ATTACH '{Path(attach).as_posix()}' AS src (READ_ONLY)")
    return con


def q(path: Path | str) -> str:
    """Quote a path for SQL."""
    return "'" + str(path).replace("'", "''") + "'"
