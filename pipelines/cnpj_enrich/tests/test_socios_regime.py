"""Socios (partners, local layer), Regime Tributário (ECF) and the opt-in downloads, on synthetic data only."""
import gzip
import hashlib
import hmac
import json
import shutil
import subprocess

import duckdb
import pytest

from cnpj_pipeline import download
from cnpj_pipeline.config import Settings
from cnpj_pipeline.export import SOCIOS_COLUMN_NAMES
from cnpj_pipeline.layouts import REGIME_FILES, SOCIOS_FILES, SOCIOS_PERSONAL
from helpers import cli, duck_cli, sha256_check_cmd
from httpserver import Faults, serve
from synth import PF_NAME, REP_NAME

DL = ("--retries", "1", "--backoff", "0.01")
SALT = "sal-do-export-9f8e7d6c5b4a-ÇÃO"


def person_key(nome, doc):
    import re
    import unicodedata
    n = "".join(ch for ch in unicodedata.normalize("NFD", nome or "") if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", n.upper()).strip() + "|" + re.sub(r"[^0-9A-Za-z]", "", doc or "")


def H(m: str, salt: str = SALT) -> str:
    return hmac.new(salt.encode(), m.encode(), hashlib.sha256).hexdigest()


def assert_salt_nowhere(root, salt=SALT):
    for f in root.rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            if f.suffix == ".gz":
                data = gzip.decompress(data)
            assert salt.encode() not in data, f


@pytest.fixture(scope="module")
def servers(world):
    f, fr = Faults(), Faults()
    srv, url = serve(world["release"], f)
    srv2, rurl = serve(world["regime_share"], fr)
    yield {"faults": f, "url": url, "rfaults": fr, "rurl": rurl}
    srv.shutdown(); srv2.shutdown()


def prepare(world, servers, wd):
    cli(wd, "collect", "--duckdb", str(world["db"]))
    cli(wd, "download", "--base-url", servers["url"], "--no-ibge", *DL)
    cli(wd, "download", "--socios", "--regime", "--base-url", servers["url"], "--regime-base-url", servers["rurl"], *DL)
    shutil.copy(world["release"] / "2026-03" / "municipios_ibge.csv", wd / "rfb" / "2026-03")
    cli(wd, "enrich")
    return wd


@pytest.fixture(scope="module")
def base(world, servers, tmp_path_factory):
    return prepare(world, servers, tmp_path_factory.mktemp("soc_base"))


def clone(base, tmp_path):
    wd = tmp_path / "wd"
    shutil.copytree(base, wd)
    return wd


@pytest.fixture(scope="module")
def socios_done(base, tmp_path_factory):
    wd = tmp_path_factory.mktemp("soc_done") / "wd"
    shutil.copytree(base, wd)
    p = cli(wd, "socios")
    return wd, p


def rows(path, order="1"):
    return duckdb.connect().execute(f"SELECT * FROM read_parquet('{path}') ORDER BY ALL").fetchall()


def expected_links(world):
    debtor = {c[:8] for c in world["truth"]}
    exp = set()
    for r in world["socios_rows"]:
        if r[0] in debtor or (r[1] == "1" and r[3][:8] in debtor):
            exp.add((r[0], r[1], r[2], r[3] or None))
    return exp, debtor


# ------------------------------------------------------------------------------------------------ download
def test_download_extras_manifest_and_no_redownload(world, servers, base):
    m = json.loads((base / "rfb" / "2026-03" / "manifest.json").read_text())["files"]
    assert set(SOCIOS_FILES) <= set(m) and set(REGIME_FILES) <= set(m)
    assert all(v["status"] == "ok" and v["sha256"] for v in m.values())
    assert m["entidades-lucro-presumido.zip"]["members"] and len(m["entidades-lucro-presumido.zip"]["members"]) == 2
    assert len(m) == 27 + 10 + 4                        # default release + Socios + regime, nothing lost in merges
    for n in SOCIOS_FILES:
        assert (base / "rfb" / "2026-03" / n).read_bytes() == (world["release"] / "2026-03" / n).read_bytes()
    n_get, n_rget = len(servers["faults"].log), len(servers["rfaults"].log)
    cli(base, "download", "--socios", "--regime", "--base-url", servers["url"], "--regime-base-url", servers["rurl"], *DL)
    cli(base, "download", "--base-url", servers["url"], "--no-ibge", *DL)
    assert len(servers["faults"].log) == n_get and len(servers["rfaults"].log) == n_rget   # no GET at all


def test_socios_only_downloads_socios_and_resumes(world, servers, tmp_path):
    s = Settings(tmp_path / "wd")
    name = "Socios3.zip"
    size = (world["release"] / "2026-03" / name).stat().st_size
    servers["faults"].drop_after[name] = [size // 2]          # one cut connection (Range resume: test_download)
    servers["faults"].status["Socios5.zip"] = [503]
    before = len(servers["faults"].log)
    m = download.run(s, socios=True, base_url=servers["url"], retries=3, backoff=0.01)
    got = {n for _, n, _ in servers["faults"].log[before:]}
    assert got == set(SOCIOS_FILES)                           # no Empresas/Estabelecimentos, no IBGE
    assert set(m["files"]) == set(SOCIOS_FILES)
    assert len([1 for _, n, _ in servers["faults"].log[before:] if n == name]) == 2     # cut, then retried
    assert (s.rfb_dir / name).read_bytes() == (world["release"] / "2026-03" / name).read_bytes()


def test_manifest_merge_between_processes(world, servers, tmp_path):
    """Two downloaders with stale in-memory manifests (e.g. two terminals) must not drop each other's entries."""
    s = Settings(tmp_path / "wd")
    d1 = download.Downloader(s, servers["url"], None, retries=1, backoff=0.01)
    d2 = download.Downloader(s, servers["url"], None, retries=1, backoff=0.01)
    ls = d1.listing()
    d1.fetch("Cnaes.zip", ls["Cnaes.zip"])
    d2.fetch("Paises.zip", ls["Paises.zip"])                 # d2 never saw Cnaes.zip in memory
    d1.fetch("Motivos.zip", ls["Motivos.zip"])
    m = json.loads(s.manifest.read_text())["files"]
    assert {"Cnaes.zip", "Paises.zip", "Motivos.zip"} <= set(m)


# -------------------------------------------------------------------------------------------------- socios
def test_socios_links_second_degree_and_flags(world, socios_done):
    wd, p = socios_done
    exp, debtor = expected_links(world)
    t = f"read_parquet('{wd}/socios/2026-03/socios.parquet')"
    con = duckdb.connect()
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {t}").fetchall()]
    for c in ["cnpj_basico", "identificador_socio", "nome_socio", "cpf_cnpj_socio", "qualificacao_socio",
              "data_entrada_sociedade", "pais", "representante_legal", "nome_representante",
              "qualificacao_representante", "faixa_etaria", "qualificacao_socio_descricao", "pais_descricao",
              "empresa_devedora", "socio_pj_devedor"]:
        assert c in cols, c
    got = con.execute(f"SELECT cnpj_basico, identificador_socio, nome_socio, cpf_cnpj_socio FROM {t}").fetchall()
    assert len(got) == len(set(got))                          # the duplicated RFB line is kept once
    assert set(got) == exp
    second = con.execute(f"SELECT empresa_devedora, socio_pj_devedor FROM {t} WHERE cnpj_basico = '99999999'").fetchall()
    assert second == [(False, True)]                          # only the PJ-debtor link of the non-debtor firm
    assert not con.execute(f"SELECT COUNT(*) FROM {t} WHERE cnpj_basico IN ('99999998', '99999997')").fetchone()[0]
    bad = con.execute(f"""SELECT COUNT(*) FROM {t} WHERE empresa_devedora <> (cnpj_basico IN
                          (SELECT DISTINCT cnpj_raiz FROM read_parquet('{wd}/collect/keys/kb=*/keys.parquet')))""").fetchone()[0]
    assert bad == 0
    r = con.execute(f"SELECT qualificacao_socio_descricao, data_entrada_sociedade::VARCHAR, faixa_etaria_descricao, "
                    f"identificador_socio_descricao FROM {t} WHERE identificador_socio = '2' AND qualificacao_socio = '49' "
                    "LIMIT 1").fetchone()
    assert r[0] == "Sócio-Administrador" and r[1] == "2020-01-15" and r[2] and r[3] == "PESSOA FISICA"
    f = con.execute(f"SELECT pais_descricao, representante_legal, nome_representante, qualificacao_representante_descricao "
                    f"FROM {t} WHERE identificador_socio = '3' LIMIT 1").fetchone()
    assert f[0] == "BAHAMAS, ILHAS" and f[1].startswith("***") and f[2].startswith(REP_NAME) and f[3] == "Procurador"
    assert "JOSÉ ÇÃO" in con.execute(f"SELECT nome_socio FROM {t} WHERE nome_socio LIKE '%JOS%' LIMIT 1").fetchone()[0]
    assert "MASKED" in p.stderr and "APPROXIMATE" in p.stderr


def test_socios_edges(world, socios_done):
    wd, _ = socios_done
    con = duckdb.connect()
    e = f"read_parquet('{wd}/socios/2026-03/socios_arestas.parquet')"
    n_s = con.execute(f"SELECT COUNT(*) FROM read_parquet('{wd}/socios/2026-03/socios.parquet')").fetchone()[0]
    assert con.execute(f"SELECT COUNT(*) FROM {e}").fetchone()[0] == n_s
    pj = con.execute(f"SELECT target, target_raiz, target_chave_aproximada FROM {e} WHERE target_tipo = 'PJ'").fetchall()
    assert pj and all(len(t) == 14 and t[:8] == r and not a for t, r, a in pj)
    hom = con.execute(f"SELECT COUNT(DISTINCT source), COUNT(DISTINCT target), bool_and(target_chave_aproximada) FROM {e} "
                      "WHERE target_nome LIKE '%MARIA HOMONIMA'").fetchone()
    assert hom == (2, 1, True)                               # approximate PF key: homonyms collapse to one node


def test_socios_rerun_is_noop(socios_done):
    wd, _ = socios_done
    p = cli(wd, "socios")
    assert "rows=" not in p.stderr and "skip" in p.stderr


@pytest.mark.parametrize("point", ["socios:before_rename:2", "socios:after_rename:4", "socios_rede:after_rename:1",
                                   "socios_rede:before_rename:2"])
def test_socios_fault_then_resume(base, socios_done, tmp_path, point):
    clean, _ = socios_done
    wd = clone(base, tmp_path)
    p = cli(wd, "socios", env={"CNPJ_PIPELINE_FAULT": point}, check=False)
    assert p.returncode == 137, p.stderr[-2000:]
    cli(wd, "socios")
    for f in ("socios.parquet", "socios_arestas.parquet"):
        assert rows(wd / "socios" / "2026-03" / f) == rows(clean / "socios" / "2026-03" / f)
    assert not list((wd / "socios").rglob("_tmp_*"))


def test_socios_hashed(base, tmp_path):
    wd = clone(base, tmp_path)
    salt = "sal-de-teste-0123456789-ÇÃO"
    p = cli(wd, "socios", "--hash-salt-env", "CNPJ_TEST_SALT", env={"CNPJ_TEST_SALT": salt})
    h = wd / "socios" / "2026-03" / "socios_hashed.parquet"
    con = duckdb.connect()
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{h}')").fetchall()}
    assert not SOCIOS_PERSONAL & cols and {"socio_id", "representante_id", "target_id"} <= cols
    assert con.execute(f"SELECT COUNT(*) FROM read_parquet('{h}') x WHERE CAST(x AS VARCHAR) LIKE '%{PF_NAME}%' "
                       "OR CAST(x AS VARCHAR) LIKE '%***%'").fetchone()[0] == 0
    nome, doc, sid = con.execute(f"""SELECT s.nome_socio, s.cpf_cnpj_socio, h.socio_id
        FROM read_parquet('{wd}/socios/2026-03/socios.parquet') s
        JOIN (SELECT *, row_number() OVER () AS rn FROM read_parquet('{h}')) h
          ON h.cnpj_basico = s.cnpj_basico AND h.identificador_socio = '2' AND s.identificador_socio = '2'
             AND h.qualificacao_socio = s.qualificacao_socio AND h.data_entrada_sociedade = s.data_entrada_sociedade
             AND h.faixa_etaria = s.faixa_etaria
        WHERE s.nome_socio LIKE '%JOSÉ%' LIMIT 1""").fetchone()
    assert sid == hmac.new(salt.encode(), person_key(nome, doc).encode(), hashlib.sha256).hexdigest()
    log = (wd / "logs" / "pipeline.log").read_text()
    assert salt not in log and salt not in p.stderr
    p = cli(wd, "socios", "--hash-salt-env", "CNPJ_UNSET_SALT_VAR", check=False)
    assert p.returncode != 0 and "never hardcoded" in p.stderr


# -------------------------------------------------------------------------------------------------- regime
@pytest.fixture(scope="module")
def regime_done(base, tmp_path_factory):
    wd = tmp_path_factory.mktemp("reg_done") / "wd"
    shutil.copytree(base, wd)
    cli(wd, "build")                                          # build before regime: regime_* NULL
    cli(wd, "regime")
    p = cli(wd, "build")                                      # must rebuild every bucket (regime changed)
    return wd, p


def test_regime_table(world, regime_done):
    wd, _ = regime_done
    con = duckdb.connect()
    t = f"read_parquet('{wd}/regime_tributario/2026-03/regime.parquet')"
    debtor = {c[:8] for c in world["truth"]}
    raizes = {r[0] for r in con.execute(f"SELECT DISTINCT cnpj_raiz FROM {t}").fetchall()}
    assert raizes == set(world["regime_truth"]) and raizes <= debtor        # non-debtor excluded
    B = list(world["regime_truth"])[1]
    b = con.execute(f"SELECT ano, forma_tributacao, arquivos FROM {t} WHERE cnpj_raiz = '{B}'").fetchall()
    assert b == [(2023, "LUCRO PRESUMIDO/REAL", ["entidades-lucro-presumido.zip", "entidades-lucro-real.zip"])]
    scp = con.execute(f"SELECT COUNT(*) FROM {t} WHERE cnpj_da_scp IS NOT NULL").fetchone()[0]
    assert scp == 1
    assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE forma_tributacao LIKE '%\r%' OR length(cnpj) <> 14").fetchone()[0] == 0


def test_build_regime_columns(world, regime_done):
    wd, p = regime_done
    assert p.stderr.count("[build] kb=") >= 1 and "rows=" in p.stderr
    con = duckdb.connect()
    t = f"read_parquet('{wd}/build/2026-03/cnpj_enriquecido/*.parquet')"
    got = {r[0]: r[1:] for r in con.execute(
        f"SELECT cnpj, cnpj_raiz, regime_ultimo, regime_ultimo_ano, regimes_por_ano, regime_derivado, opcao_simples "
        f"FROM {t}").fetchall()}
    assert len(got) == len(world["truth"])
    exp = world["regime_truth"]
    n_derived = 0
    for cnpj, (raiz, ult, ano, por_ano, der, simp) in got.items():
        if raiz in exp:
            assert (ult, ano, por_ano) == exp[raiz] and der is False, cnpj
        elif simp:
            assert ult == "SIMPLES (cadastro)" and der is True and ano is None and por_ano is None
            n_derived += 1
        else:
            assert ult is None and der is None
    assert n_derived > 0
    assert cli(wd, "build").stderr.count("rows=") == 0      # stable afterwards


def test_regime_fault_then_resume(base, regime_done, tmp_path):
    clean, _ = regime_done
    wd = clone(base, tmp_path)
    for point in ("regime:after_rename:2", "regime:before_rename:1", "regime_rede:after_rename:1"):
        p = cli(wd, "regime", env={"CNPJ_PIPELINE_FAULT": point}, check=False)
        assert p.returncode == 137, (point, p.stderr[-2000:])
    cli(wd, "regime")
    f = "regime_tributario/2026-03/regime.parquet"
    assert rows(wd / f) == rows(clean / f)
    assert not list((wd / "regime_tributario").rglob("_tmp_*"))


# -------------------------------------------------------------------------------------------------- export
def _pkg(wd):
    return next(p for p in (wd / "export").glob("cnpj_enriquecido_ref*") if not p.name.endswith("_LOCAL_NAO_SUBIR"))


def _scan_pii(pkg, con):
    for p in pkg.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if p.suffix == ".gz":
                data = gzip.decompress(data)
            if p.suffix == ".parquet":
                data = "\n".join(str(r) for r in con.execute(f"SELECT * FROM read_parquet('{p}')").fetchall()).encode()
            markers = [PF_NAME, REP_NAME, "EMPRESA SOCIA", "FOREIGN HOLDING", "HOMONIMA"]
            if p.suffix != ".md":                    # the docs describe the mask pattern itself
                markers.append("***")
            for marker in markers:
                assert marker.encode() not in data, (p, marker)


@pytest.fixture(scope="module")
def exported(world, socios_done):
    wd, _ = socios_done
    cli(wd, "build")
    cli(wd, "export")
    plain = _pkg(wd)
    assert not list(plain.rglob("socios*"))
    assert json.loads((plain / "MANIFEST.json").read_text())["with_socios"] is False
    p = cli(wd, "export", "--with-socios", env={"CNPJ_HASH_SALT": ""}, check=False)          # no salt -> fails
    assert p.returncode != 0 and "never hardcoded" in p.stderr
    p = cli(wd, "export", "--with-socios", env={"CNPJ_HASH_SALT": SALT})
    return wd, _pkg(wd), p


def test_export_socios_anonymised_edges(world, exported):
    wd, pkg, p = exported
    m = json.loads((pkg / "MANIFEST.json").read_text())
    assert m["with_socios"] is True and m["socios_rows"] > 0 and m["socios_reidentification_risk"]
    con = duckdb.connect()
    spq = pkg / "data" / "socios_vinculos.parquet"
    v = f"read_parquet('{spq}')"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {v}").fetchall()]
    assert cols == SOCIOS_COLUMN_NAMES and not SOCIOS_PERSONAL & set(cols) and "pais" not in cols
    _scan_pii(pkg, con)
    # PJ partner: CNPJ in clear; PF: HMAC(normalised name | CPF middle digits), same id in every firm
    pj = con.execute(f"SELECT socio_id, socio_cnpj_raiz FROM {v} WHERE socio_tipo = 'PJ'").fetchall()
    assert pj and all(len(i) == 14 and i.isdigit() and i[:8] == r for i, r in pj)
    pf_id = H(person_key(f"{PF_NAME} MARIA HOMONIMA", "***555555**"))
    firms = con.execute(f"SELECT COUNT(DISTINCT cnpj_basico) FROM {v} WHERE socio_id = '{pf_id}'").fetchone()[0]
    assert firms == 2                                            # connectable network
    one = next(r for r in world["socios_rows"] if r[1] == "2" and r[2].endswith("JOSÉ ÇÃO"))
    got = con.execute(f"SELECT socio_id, decada_entrada_sociedade, faixa_etaria FROM {v} WHERE cnpj_basico = '{one[0]}' "
                      "AND socio_tipo = 'PF' AND qualificacao_socio = '49'").fetchall()
    assert (H(person_key(one[2], one[3])), int(one[5][:4]) // 10 * 10, one[10]) in got   # api: decade only
    assert "ano_entrada_sociedade" not in cols
    m_api = json.loads((pkg / "MANIFEST.json").read_text())
    assert m_api["socios_detail"] == "api" and m_api["api_upload_allowed"] is True
    assert all(len(i) == 64 for (i,) in con.execute(f"SELECT socio_id FROM {v} WHERE socio_tipo <> 'PJ'").fetchall())
    readme = (pkg / "README.md").read_text()
    assert "Relatório de risco de reidentificação (sócios pessoa física)" in readme and "k<5" in readme
    assert "faixa etária × município × CNAE" in readme
    assert_salt_nowhere(pkg); assert_salt_nowhere(wd)            # package, manifest, state, logs, scratch
    assert SALT not in p.stderr
    assert not (wd / "tmp" / "export_scratch").exists()
    assert subprocess.run(sha256_check_cmd(), cwd=pkg, capture_output=True).returncode == 0
    if shutil.which("duckdb"):
        db = wd / "api.duckdb"
        for _ in range(2):
            r = subprocess.run(["./load_duckdb.sh", str(db)], cwd=pkg, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr + r.stdout
        n = int(duck_cli(db, "SELECT COUNT(*) FROM socios_vinculos"))
        assert n == m["socios_rows"]                             # loading twice does not duplicate


def test_export_socios_detail_local(world, exported):
    wd, api_pkg, _ = exported
    cli(wd, "export", "--with-socios", "--socios-detail", "local", env={"CNPJ_HASH_SALT": SALT})
    pkg = wd / "export" / (api_pkg.name + "_LOCAL_NAO_SUBIR")
    assert pkg.is_dir() and api_pkg.is_dir()                  # the API package is untouched, the local one is apart
    m = json.loads((pkg / "MANIFEST.json").read_text())
    assert m["socios_detail"] == "local" and m["api_upload_allowed"] is False
    assert (pkg / "NAO_SUBIR.txt").exists() and "NÃO SUBIR" in (pkg / "README.md").read_text()
    con = duckdb.connect()
    v = f"read_parquet('{pkg}/data/socios_vinculos.parquet')"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {v}").fetchall()]
    assert "ano_entrada_sociedade" in cols and "decada_entrada_sociedade" not in cols
    a = f"read_parquet('{api_pkg}/data/socios_vinculos.parquet')"
    assert con.execute(f"SELECT COUNT(*) FROM {v}").fetchone() == con.execute(f"SELECT COUNT(*) FROM {a}").fetchone()
    assert con.execute(f"SELECT COUNT(*) FROM {v} l JOIN {a} p USING (cnpj_basico, socio_id, qualificacao_socio) "
                       "WHERE l.ano_entrada_sociedade // 10 * 10 <> p.decada_entrada_sociedade").fetchone()[0] == 0
    assert_salt_nowhere(pkg)
    assert subprocess.run(sha256_check_cmd(), cwd=pkg, capture_output=True).returncode == 0
    cli(wd, "export", "--with-socios", env={"CNPJ_HASH_SALT": SALT})   # leave the API package as the fixture expects


def test_export_pf_policy_anonimizar(world, exported):
    wd, _, _ = exported
    p = cli(wd, "export", "--pf-policy", "anonimizar", env={"CNPJ_HASH_SALT": ""}, check=False)
    assert p.returncode != 0
    cli(wd, "export", "--pf-policy", "anonimizar", "--with-socios", env={"CNPJ_HASH_SALT": SALT})
    pkg = _pkg(wd)
    con = duckdb.connect()
    e = f"read_parquet('{pkg}/data/cnpj_enriquecido.parquet')"
    b = f"read_parquet('{wd}/build/2026-03/cnpj_enriquecido/*.parquet')"
    full = {r[0]: r[1:] for r in con.execute(
        f"SELECT cnpj, cnpj_raiz, pessoa_fisica_vinculada, municipio_rfb, cnae_divisao, data_inicio_atividade, "
        f"capital_social FROM {b}").fetchall()}
    k = dict(((m, d), n) for m, d, n in con.execute(
        f"SELECT municipio_rfb, cnae_divisao, COUNT(DISTINCT cnpj) FROM {b} WHERE pessoa_fisica_vinculada "
        "GROUP BY ALL").fetchall())
    out = {r[0]: r[1:] for r in con.execute(
        f"SELECT cnpj, cnpj_raiz, cnpj_anonimizado, municipio_rfb, municipio_generalizado, data_inicio_atividade, "
        f"capital_social, capital_social_faixa, cnae_principal, uf FROM {e}").fetchall()}
    assert len(out) == len(full)
    n_pf = n_gen = n_kept = 0
    for c, (raiz, pf, mun, div, ini, cap) in full.items():
        key = H(c) if pf else c
        assert key in out, c
        o_raiz, anon, o_mun, gen, o_ini, o_cap, faixa, cnae, uf = out[key]
        assert anon is bool(pf) and o_raiz == (H(raiz) if pf else raiz)
        if pf:
            n_pf += 1
            assert o_cap is None and (faixa is not None) == (cap is not None)
            assert ini is None or (o_ini.month, o_ini.day, o_ini.year) == (1, 1, ini.year)
            if mun is not None and k.get((mun, div), 0) < 5:
                assert o_mun is None and gen is True; n_gen += 1
            elif mun is not None:
                assert o_mun == mun and gen is False; n_kept += 1
        else:
            assert o_mun == mun and o_cap == cap and gen is False
    assert n_pf > 0 and n_gen > 0 and n_kept > 0              # both branches of the k>=5 rule are exercised
    # join test with DEBITO: the API applies the same HMAC to the normalised CNPJ_DEVEDOR
    deb = duckdb.connect(str(world["db"]), read_only=True)
    from cnpj_pipeline.cnpj import normalize
    debtors = {normalize(r[0]) for r in deb.execute("SELECT DISTINCT CNPJ_DEVEDOR FROM DEBITO "
                                                    "WHERE CNPJ_DEVEDOR IS NOT NULL").fetchall()}
    pf_ids = {r[0] for r in con.execute(f"SELECT cnpj FROM {e} WHERE cnpj_anonimizado").fetchall()}
    assert pf_ids <= {H(c) for c in debtors}
    # socios of PF-linked firms carry the same firm id as cnpj_enriquecido
    v = f"read_parquet('{pkg}/data/socios_vinculos.parquet')"
    pfr = {r[0] for r in con.execute(f"SELECT cnpj_raiz FROM {e} WHERE cnpj_anonimizado").fetchall()}
    clear_pf_roots = {r for r, pf, *_ in full.values() if pf}
    got = {r[0] for r in con.execute(f"SELECT DISTINCT cnpj_basico FROM {v}").fetchall()}
    assert not (got & clear_pf_roots) and (got & pfr)
    readme = (pkg / "README.md").read_text()
    assert "CNPJs vinculados a pessoa física" in readme and "sócios pessoa física" in readme
    m = json.loads((pkg / "MANIFEST.json").read_text())
    assert m["pf_policy"] == "anonimizar" and m["pf_reidentification_risk"]
    assert_salt_nowhere(wd)
    if shutil.which("duckdb"):
        db = wd / "api_anon.duckdb"
        for _ in range(2):
            r = subprocess.run(["./load_duckdb.sh", str(db)], cwd=pkg, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr + r.stdout
        assert int(duck_cli(db, "SELECT COUNT(*) FROM cnpj_enriquecido")) == len(full)


def test_postgres_socios_sql_parses():
    pglast = pytest.importorskip("pglast")
    from cnpj_pipeline.export import load_postgres_sql
    body = "\n".join(l for l in load_postgres_sql(True, "2026-03").splitlines() if not l.startswith("\\"))
    pglast.parse_sql(body)


def test_all_with_socios_and_regime(world, servers, socios_done, regime_done, tmp_path):
    wd = tmp_path / "wd"
    cli(wd, "all", "--duckdb", str(world["db"]), "--socios", "--regime", "--with-socios", "--base-url", servers["url"],
        "--regime-base-url", servers["rurl"], "--no-ibge", *DL, env={"CNPJ_HASH_SALT": SALT})
    s_clean, _ = socios_done
    r_clean, _ = regime_done
    assert rows(wd / "socios/2026-03/socios.parquet") == rows(s_clean / "socios/2026-03/socios.parquet")
    assert rows(wd / "regime_tributario/2026-03/regime.parquet") == rows(r_clean / "regime_tributario/2026-03/regime.parquet")
    con = duckdb.connect()
    q = "SELECT cnpj, regime_ultimo, regime_ultimo_ano, regimes_por_ano, regime_derivado FROM read_parquet('{}') ORDER BY 1"
    assert (con.execute(q.format(f"{wd}/build/2026-03/cnpj_enriquecido/*.parquet")).fetchall()
            == con.execute(q.format(f"{r_clean}/build/2026-03/cnpj_enriquecido/*.parquet")).fetchall())
    pkg = next((wd / "export").glob("cnpj_enriquecido_ref*"))
    assert (pkg / "data" / "socios_vinculos.parquet").exists()
