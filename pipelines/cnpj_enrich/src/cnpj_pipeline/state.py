"""Checkpoint store (SQLite, WAL) and the atomic unit-of-work protocol shared by all stages.

Protocol for one unit (e.g. 'enrich', 'Estabelecimentos3.zip#g0'):
  1. if the unit is marked done and its final output exists -> skip (idempotent);
  2. delete any leftover temporary output and any stale final output (crash between rename and commit);
  3. the worker writes ONLY to a temporary path; then os.replace/rename -> final path; then commit 'done'.
A kill at any moment leaves either no final output, or a final output that is recomputed on the next run, so outputs
are never appended to and rows are never duplicated.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

log = logging.getLogger("cnpj_pipeline")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    stage TEXT NOT NULL, unit TEXT NOT NULL, status TEXT NOT NULL, rows INTEGER, meta TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, started_at REAL, finished_at REAL, PRIMARY KEY (stage, unit));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _fault(point: str) -> None:
    """Test hook: CNPJ_PIPELINE_FAULT='<stage>:<point>:<n>' kills the process (os._exit) at the n-th hit."""
    spec = os.environ.get("CNPJ_PIPELINE_FAULT")
    if not spec:
        return
    target, n = spec.rsplit(":", 1)
    if target != point:
        return
    counter = f"_CNPJ_FAULT_{point}"
    hits = int(os.environ.get(counter, "0")) + 1
    os.environ[counter] = str(hits)
    if hits >= int(n):
        log.error("FAULT INJECTION at %s (hit %d): killing the process", point, hits)
        logging.shutdown()
        os._exit(137)


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), timeout=60, isolation_level=None)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=FULL")
        self.con.executescript(_SCHEMA)

    # settings that must not change inside one workdir (e.g. key_buckets)
    def pin(self, key: str, value) -> None:
        v = json.dumps(value)
        row = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            self.con.execute("INSERT INTO settings VALUES (?, ?)", (key, v))
        elif row[0] != v:
            raise RuntimeError(f"Setting {key}={value} differs from the value pinned in this workdir ({row[0]}). "
                               "Use the original value or a new --workdir.")

    def get(self, stage: str, unit: str):
        return self.con.execute("SELECT status, rows, meta FROM units WHERE stage=? AND unit=?", (stage, unit)).fetchone()

    def is_done(self, stage: str, unit: str) -> bool:
        r = self.get(stage, unit)
        return bool(r and r[0] == "done")

    def start(self, stage: str, unit: str) -> None:
        self.con.execute("""INSERT INTO units(stage, unit, status, attempts, started_at) VALUES (?, ?, 'running', 1, ?)
                            ON CONFLICT(stage, unit) DO UPDATE SET status='running', attempts=attempts+1,
                            started_at=excluded.started_at, finished_at=NULL""", (stage, unit, time.time()))

    def done(self, stage: str, unit: str, rows: int | None, meta: dict | None = None) -> None:
        self.con.execute("UPDATE units SET status='done', rows=?, meta=?, finished_at=? WHERE stage=? AND unit=?",
                         (rows, json.dumps(meta or {}, default=str), time.time(), stage, unit))

    def failed(self, stage: str, unit: str, err: str) -> None:
        self.con.execute("UPDATE units SET status='failed', meta=?, finished_at=? WHERE stage=? AND unit=?",
                         (json.dumps({"error": err[:2000]}), time.time(), stage, unit))

    def invalidate(self, stage: str, unit: str | None = None) -> None:
        if unit is None:
            self.con.execute("DELETE FROM units WHERE stage=?", (stage,))
        else:
            self.con.execute("DELETE FROM units WHERE stage=? AND unit=?", (stage, unit))

    def summary(self) -> list[tuple]:
        return self.con.execute("""SELECT stage, status, COUNT(*), SUM(rows) FROM units
                                   GROUP BY stage, status ORDER BY stage, status""").fetchall()

    def units(self, stage: str) -> list[tuple]:
        return self.con.execute("SELECT unit, status, rows, meta FROM units WHERE stage=? ORDER BY unit",
                                (stage,)).fetchall()


def _rm(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


def run_unit(state: State, stage: str, unit: str, final: Path,
             work: Callable[[Path], tuple[int, dict]], force: bool = False) -> bool:
    """Run one unit atomically. `work(tmp)` must write only under/at `tmp` and return (rows, meta).

    Returns True if the unit ran, False if it was skipped (already done)."""
    if not force and state.is_done(stage, unit) and final.exists():
        log.info("[%s] %s: done, skip", stage, unit)
        return False
    tmp = final.with_name(f"_tmp_{final.name}")
    _rm(tmp)
    _rm(final)                          # stale output of a run killed after rename and before commit
    final.parent.mkdir(parents=True, exist_ok=True)
    state.start(stage, unit)
    t0 = time.time()
    try:
        rows, meta = work(tmp)
        _fault(f"{stage}:before_rename")
        os.replace(tmp, final)
        _fault(f"{stage}:after_rename")
    except BaseException as e:          # includes KeyboardInterrupt; tmp is left for cleanup on the next run
        state.failed(stage, unit, repr(e))
        raise
    meta = {**meta, "seconds": round(time.time() - t0, 2)}
    state.done(stage, unit, rows, meta)
    log.info("[%s] %s: done, rows=%s, %.1fs", stage, unit, rows, meta["seconds"])
    return True


@contextmanager
def timer(label: str):
    t0 = time.time()
    yield
    log.info("%s in %.1fs", label, time.time() - t0)
