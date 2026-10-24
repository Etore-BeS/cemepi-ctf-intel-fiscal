"""The export package loads twice (idempotent upsert) into DuckDB (CLI) and PostgreSQL (throw-away local cluster)."""
import glob
import os
import shutil
import socket
import subprocess

import duckdb
import pytest

from cnpj_pipeline.export import ddl_postgres, load_postgres_sql
from helpers import duck_cli, sha256_check_cmd
from test_pipeline import run_all

PG_BIN = next(iter(sorted(glob.glob("/usr/lib/postgresql/*/bin"))), None)
DUCK_CLI = shutil.which("duckdb")


@pytest.fixture(scope="module")
def pkg(world, tmp_path_factory):
    wd = run_all(world, tmp_path_factory.mktemp("wdx"))
    return next((wd / "export").glob("cnpj_enriquecido_ref*"))


def test_postgres_sql_parses():
    pglast = pytest.importorskip("pglast")
    pglast.parse_sql(ddl_postgres())
    body = "\n".join(l for l in load_postgres_sql().splitlines() if not l.startswith("\\"))
    pglast.parse_sql(body)


def test_package_contents(pkg):
    import json
    m = json.loads((pkg / "MANIFEST.json").read_text())
    assert m["rows"] > 0 and m["pf_policy"] == "suprimir"
    con = duckdb.connect()
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{pkg}/data/cnpj_enriquecido.parquet') "
                    "WHERE pessoa_fisica_vinculada AND (capital_social IS NOT NULL OR municipio_ibge IS NOT NULL)").fetchone()[0]
    assert n == 0                                                   # suppression applied
    assert subprocess.run(sha256_check_cmd(), cwd=pkg, capture_output=True).returncode == 0


@pytest.mark.skipif(DUCK_CLI is None, reason="duckdb CLI not installed")
def test_duckdb_load_twice(pkg, tmp_path):
    db = tmp_path / "api.duckdb"
    for _ in range(2):
        p = subprocess.run(["./load_duckdb.sh", str(db)], cwd=pkg, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr + p.stdout
    n, u = map(int, duck_cli(db, "SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM cnpj_enriquecido").split(","))
    m = duckdb.connect().execute(f"SELECT COUNT(*) FROM read_parquet('{pkg}/data/cnpj_enriquecido.parquet')").fetchone()[0]
    assert n == u == m
    duck_cli(db, "UPDATE cnpj_enriquecido SET porte = 'XX' WHERE cnpj = (SELECT MIN(cnpj) FROM cnpj_enriquecido)")
    assert subprocess.run(["./load_duckdb.sh", str(db)], cwd=pkg, capture_output=True).returncode == 0
    assert duck_cli(db, "SELECT COUNT(*) FROM cnpj_enriquecido WHERE porte = 'XX'") == "0"


@pytest.mark.skipif(PG_BIN is None or shutil.which("psql") is None, reason="PostgreSQL not installed")
def test_postgres_load_twice(pkg, tmp_path):
    data = tmp_path / "pg"
    subprocess.run([f"{PG_BIN}/initdb", "-D", str(data), "-A", "trust", "-U", "t"], check=True, capture_output=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    subprocess.run([f"{PG_BIN}/pg_ctl", "-D", str(data), "-o", f"-p {port} -k {tmp_path} -c listen_addresses=127.0.0.1",
                    "-l", str(tmp_path / "pg.log"), "-w", "start"], check=True, capture_output=True)
    try:
        uri = f"postgresql://t@127.0.0.1:{port}/postgres"
        env = {**os.environ, "PGURI": uri}
        for _ in range(2):
            p = subprocess.run(["./load_postgres.sh"], cwd=pkg, env=env, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr + p.stdout
        psql = lambda sql: subprocess.run(["psql", uri, "-tAc", sql], capture_output=True, text=True, check=True).stdout.strip()
        m = duckdb.connect().execute(f"SELECT COUNT(*) FROM read_parquet('{pkg}/data/cnpj_enriquecido.parquet')").fetchone()[0]
        assert psql("SELECT COUNT(*) || ',' || COUNT(DISTINCT cnpj) FROM cnpj_enriquecido") == f"{m},{m}"
        psql("UPDATE cnpj_enriquecido SET porte = 'XX' WHERE cnpj = (SELECT MIN(cnpj) FROM cnpj_enriquecido)")
        assert subprocess.run(["./load_postgres.sh"], cwd=pkg, env=env, capture_output=True).returncode == 0
        assert psql("SELECT COUNT(*) FROM cnpj_enriquecido WHERE porte = 'XX'") == "0"
        # same types/values as the Parquet for one full row
        a = psql("SELECT cnpj, data_inicio_atividade, opcao_simples, capital_social FROM cnpj_enriquecido "
                 "WHERE match_nivel = 'estabelecimento' AND NOT pessoa_fisica_vinculada ORDER BY cnpj LIMIT 1")
        r = duckdb.connect().execute(f"SELECT cnpj, data_inicio_atividade, opcao_simples, capital_social FROM "
                                     f"read_parquet('{pkg}/data/cnpj_enriquecido.parquet') WHERE match_nivel = 'estabelecimento' "
                                     "AND NOT pessoa_fisica_vinculada ORDER BY cnpj LIMIT 1").fetchone()
        assert a == f"{r[0]}|{r[1]}|{'t' if r[2] else 'f'}|{r[3]}"
    finally:
        subprocess.run([f"{PG_BIN}/pg_ctl", "-D", str(data), "-m", "fast", "stop"], capture_output=True)
