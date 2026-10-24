"""End-to-end on synthetic data: collect -> download -> enrich -> build -> export, checked against the truth."""
import shutil

import duckdb
import pytest

from cnpj_pipeline.cnpj import check_digits_ok
from cnpj_pipeline.layouts import FORBIDDEN
from helpers import cli
from httpserver import Faults, serve
from synth import SENTINEL


def run_all(world, wd, extra=(), groups="1"):
    f = Faults(); srv, url = serve(world["release"], f)
    try:
        cli(wd, "collect", "--duckdb", str(world["db"]), *extra)
        cli(wd, "download", "--base-url", url, "--no-ibge", "--retries", "1", "--backoff", "0.01", *extra)
    finally:
        srv.shutdown()
    shutil.copy(world["release"] / "2026-03" / "municipios_ibge.csv", wd / "rfb" / "2026-03" / "municipios_ibge.csv")
    cli(wd, "enrich", "--groups", groups, *extra)
    cli(wd, "build", *extra)
    cli(wd, "export", *extra)
    return wd


@pytest.fixture(scope="module")
def done(world, tmp_path_factory):
    return run_all(world, tmp_path_factory.mktemp("wd"))


def table(wd):
    con = duckdb.connect()
    return con, f"read_parquet('{wd}/build/2026-03/cnpj_enriquecido/*.parquet')"


def test_collect_matches_truth(world, done):
    con = duckdb.connect()
    got = {r[0]: r[1] for r in con.execute(
        f"SELECT cnpj, tributos FROM read_parquet('{done}/collect/keys/kb=*/keys.parquet') WHERE cnpj IS NOT NULL").fetchall()}
    truth = world["truth"]
    assert set(got) == set(truth)                       # every PJ debtor, no CPF rows, formatting normalised
    bucket = lambda t: "ICMS" if "ICMS" in t else ("IPVA" if t == "IPVA" else ("ITCMD" if t == "ITCMD" else "OUTROS"))
    for c, tribs in got.items():
        assert sorted(tribs) == sorted({bucket(t) for t, _ in truth[c]})


def test_build_one_row_per_cnpj_and_values(world, done):
    con, t = table(done)
    n, u = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM {t}").fetchone()
    assert n == u == len(world["truth"])
    rows = {r[0]: r[1:] for r in con.execute(
        f"SELECT cnpj, match_nivel, dv_valido, natureza_juridica, pessoa_fisica_vinculada, municipio_ibge, "
        f"cnae_principal_descricao, n_estabelecimentos_rfb, cnae_secao, situacao_cadastral_descricao FROM {t}").fetchall()}
    for r, f in world["firms"].items():
        for c in f["cnpjs"]:
            if c not in rows:
                continue
            level, dv, nat, pf, ibge, desc, n_est, sec, situ = rows[c]
            assert dv == check_digits_ok(c)
            exp = "estabelecimento" if f["in_rfb"] and f["estab_in_rfb"] else ("raiz" if f["in_rfb"] else "nao_encontrado")
            assert level == exp, (c, level, exp)
            if f["in_rfb"]:
                assert nat == f["natureza"] and pf == (f["mei"] or f["natureza"] == "2135")
            if level == "estabelecimento":
                assert ibge in ("3550308", "3509502") and desc and sec in ("C", "G", "J") and n_est == len(f["cnpjs"]) and situ
    assert rows["11111111111111"][0] == "nao_encontrado" and rows["11111111111111"][1] is False
    assert any(k.startswith("12ABC345") for k in rows)          # alphanumeric CNPJ survives end to end


def test_simples_absent_false_in_rfb_null_when_not_found(world, done):
    con, t = table(done)
    # NULL exactly on the rows without any RFB record; false/true everywhere else
    assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE (opcao_simples IS NULL OR opcao_mei IS NULL) "
                       "<> (match_nivel = 'nao_encontrado')").fetchone()[0] == 0
    nf = con.execute(f"SELECT opcao_simples, opcao_mei FROM {t} WHERE match_nivel = 'nao_encontrado'").fetchall()
    assert nf and all(r == (None, None) for r in nf)
    absent = {c for f in world["firms"].values() if f["in_rfb"] and not f.get("in_simples") for c in f["cnpjs"]}
    rows = con.execute(f"SELECT opcao_simples, opcao_mei, data_opcao_simples, data_exclusao_simples, data_opcao_mei, "
                       f"data_exclusao_mei FROM {t} WHERE cnpj IN (SELECT unnest(?::VARCHAR[]))", [sorted(absent)]).fetchall()
    assert rows and all(r == (False, False, None, None, None, None) for r in rows)
    assert any(f["in_rfb"] and not f.get("in_simples") for f in world["firms"].values())   # case exercised
    assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE opcao_simples").fetchone()[0] > 0


def test_no_personal_columns_or_values(done):
    con, t = table(done)
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {t}").fetchall()]
    assert not FORBIDDEN & set(cols)
    assert con.execute(f"SELECT COUNT(*) FROM {t} x WHERE CAST(x AS VARCHAR) LIKE '%SP%'").fetchone()[0] > 0  # scan works
    for p in list(done.rglob("*.parquet")):
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{p}') x WHERE CAST(x AS VARCHAR) LIKE '%{SENTINEL}%'").fetchone()[0]
        assert n == 0, p
    for p in done.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".csv", ".sql", ".json", ".log", ".gz"):
            data = p.read_bytes()
            if p.suffix == ".gz":
                import gzip; data = gzip.decompress(data)
            assert SENTINEL.encode() not in data, p


def test_coverage_report(done):
    md = (done / "build" / "2026-03" / "coverage.md").read_text()
    assert "| TODOS |" in md and "ICMS" in md
    csv = (done / "build" / "2026-03" / "coverage.csv").read_text().splitlines()
    assert csv[0].startswith("tributo,cnpjs_devedores")


def test_groups_give_same_result(world, done, tmp_path):
    """enrich with 3 key groups (less RAM, 3 passes per file) == 1 group."""
    wd = run_all(world, tmp_path / "wd3", groups="3")
    con = duckdb.connect()
    a = con.execute(f"SELECT * FROM read_parquet('{done}/build/2026-03/cnpj_enriquecido/*.parquet') ORDER BY cnpj").fetchall()
    b = con.execute(f"SELECT * FROM read_parquet('{wd}/build/2026-03/cnpj_enriquecido/*.parquet') ORDER BY cnpj").fetchall()
    assert a == b


def test_rerun_is_noop(world, done):
    p = cli(done, "enrich")
    assert "rows=" not in p.stderr and "skip" in p.stderr
    p = cli(done, "build")
    assert "rows=" not in p.stderr
