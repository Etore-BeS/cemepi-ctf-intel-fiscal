"""Kill in the middle of each stage, resume, and get exactly the clean-run result (no loss, no duplicates)."""
import os
import random
import shutil
import signal
import subprocess
import sys
import time

import duckdb
import pytest

from helpers import PKG, SRC, cli
from httpserver import Faults, serve


def prepare(world, wd):
    """collect + download (verified elsewhere) so that enrich/build can be killed."""
    f = Faults(); srv, url = serve(world["release"], f)
    try:
        cli(wd, "collect", "--duckdb", str(world["db"]))
        cli(wd, "download", "--base-url", url, "--no-ibge", "--retries", "1", "--backoff", "0.01")
    finally:
        srv.shutdown()
    shutil.copy(world["release"] / "2026-03" / "municipios_ibge.csv", wd / "rfb" / "2026-03")


def snapshot(wd):
    con = duckdb.connect()
    t = f"read_parquet('{wd}/build/2026-03/cnpj_enriquecido/*.parquet')"
    n, u = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM {t}").fetchone()
    assert n == u
    enr = {k: con.execute(f"SELECT COUNT(*) FROM read_parquet('{wd}/enrich/2026-03/{k}/*.parquet')").fetchone()[0]
           for k in ("empresas", "estabelecimentos", "simples")}
    return con.execute(f"SELECT * FROM {t} ORDER BY cnpj").fetchall(), enr


@pytest.fixture(scope="module")
def clean(world, tmp_path_factory):
    wd = tmp_path_factory.mktemp("clean")
    prepare(world, wd)
    cli(wd, "enrich", "--groups", "2"); cli(wd, "build")
    return snapshot(wd)


@pytest.mark.parametrize("point", ["enrich:before_rename:3", "enrich:after_rename:5", "enrich:after_rename:1"])
def test_enrich_fault_then_resume(world, clean, tmp_path, point):
    wd = tmp_path / "wd"; prepare(world, wd)
    p = cli(wd, "enrich", "--groups", "2", env={"CNPJ_PIPELINE_FAULT": point}, check=False)
    assert p.returncode == 137, p.stderr[-2000:]
    assert list((wd / "enrich" / "2026-03").rglob("_tmp_*")) or "after_rename" in point
    cli(wd, "enrich", "--groups", "2"); cli(wd, "build")
    assert snapshot(wd) == clean
    assert not list((wd / "enrich").rglob("_tmp_*"))


def test_collect_and_build_fault_then_resume(world, clean, tmp_path):
    wd = tmp_path / "wd"
    f = Faults(); srv, url = serve(world["release"], f)
    try:
        for point in ("collect:after_rename:2", "collect:before_rename:1"):
            p = cli(wd, "collect", "--duckdb", str(world["db"]), env={"CNPJ_PIPELINE_FAULT": point}, check=False)
            assert p.returncode == 137
        cli(wd, "collect", "--duckdb", str(world["db"]))
        cli(wd, "download", "--base-url", url, "--no-ibge", "--retries", "1", "--backoff", "0.01")
    finally:
        srv.shutdown()
    shutil.copy(world["release"] / "2026-03" / "municipios_ibge.csv", wd / "rfb" / "2026-03")
    cli(wd, "enrich", "--groups", "2")
    p = cli(wd, "build", env={"CNPJ_PIPELINE_FAULT": "build:after_rename:4"}, check=False)
    assert p.returncode == 137
    cli(wd, "build")
    assert snapshot(wd) == clean


def test_random_sigkill_loop(world, clean, tmp_path):
    """SIGKILL at random moments (not at hook points) until enrich finishes; result must equal the clean run.

    The kill delays are scaled to the measured duration of a clean enrich on this machine, so the first kill always
    lands before the end (a fixed 0.3-1.2 s window let a fast machine/DuckDB finish before any kill).
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CNPJ_", "CEMEPI_")) and k != "DATA_ROOT"}
    env.update(CNPJ_PIPELINE_NO_DOTENV="1", PYTHONPATH=str(SRC))

    def enrich_cmd(w):
        return [sys.executable, "-m", "cnpj_pipeline", "enrich", "--workdir", str(w), "--groups", "2",
                "--memory-limit", "256MB", "--threads", "2"]

    probe = tmp_path / "probe"; prepare(world, probe)
    t0 = time.monotonic()
    subprocess.run(enrich_cmd(probe), cwd=PKG, env=env, check=True, capture_output=True)
    full = time.monotonic() - t0
    wd = tmp_path / "wd"; prepare(world, wd)
    rng = random.Random(7)
    kills = 0
    for i in range(60):
        proc = subprocess.Popen(enrich_cmd(wd), cwd=PKG, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=full * (rng.uniform(0.25, 0.8) if i < 30 else rng.uniform(0.8, 3.0)))
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGKILL); proc.wait(); kills += 1
            continue
        if proc.returncode == 0:
            break
    else:
        pytest.fail("enrich never completed")
    assert kills >= 1
    cli(wd, "build")
    assert snapshot(wd) == clean
