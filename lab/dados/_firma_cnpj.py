"""Situação cadastral e CNAE dos CNPJs devedores de ICMS × origem e recuperação (L.7, 27/10/2026).

As mesmas análises da seção 10 ("Debtor firm characteristics") do artigo AVD-01, refeitas aqui com código próprio
do laboratório (o artigo não lê o lab, e este módulo não lê o artigo). Só agregados saem daqui.

Fontes (só leitura):
- caches do painel do EDA do paper (attributes_by_year, year_end, gare_year), partes de 2016 a 2025 (sem 2026);
- build local do enriquecimento RFB 2026-03 (pipelines/cnpj_enrich), `cnpj_enriquecido`, conferido pelo SHA-256 do
  manifest versionado (commit 78f88cd). Só as colunas cnpj, match_nivel, cnae_divisao, cnae_secao e situacao_cadastral.
CPF_DEVEDOR e NOME_DEVEDOR não são lidos (a população já é PJ: CNPJ de 14 dígitos no extrato do paper). O CNPJ fica
dentro do DuckDB em memória; nenhuma tabela por CNPJ ou CDA é devolvida nem gravada no cache.
Supressão: célula com 1 a 4 CNPJs distintos vai para "outros (suprimido)"; no CNAE, a divisão inteira; um agrupado que
continue com menos de 5 CNPJs sai em branco.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import _lab_base as B

MIN_N = 5
ANOS = list(range(2016, B.LAST_FULL_YEAR + 1))
CENSURA = f"{B.LAST_FULL_YEAR}-12-31"
REF_RFB = "2026-03"
MANIFEST_COMMIT = "78f88cd"
SUP = "outros (suprimido)"
SITUACAO = {"02": "ativa", "04": "inapta", "03": "suspensa", "08": "baixada", "01": "nula"}
ORDEM_SIT = ["ativa", "inapta", "suspensa", "baixada", "nula", "outro código", "só a raiz (situação desconhecida)",
             "não encontrado no cadastro", SUP]
ORIGEM = {"assessed": "autuado", "declared": "declarado"}
SECAO = {"A": "Agropecuária", "B": "Indústrias extrativas", "C": "Indústrias de transformação", "D": "Eletricidade e gás",
         "E": "Água, esgoto e resíduos", "F": "Construção", "G": "Comércio e reparação de veículos",
         "H": "Transporte e armazenagem", "I": "Alojamento e alimentação", "J": "Informação e comunicação",
         "K": "Financeiras e seguros", "L": "Imobiliárias", "M": "Profissionais, científicas e técnicas",
         "N": "Administrativas e serviços complementares", "O": "Administração pública", "P": "Educação",
         "Q": "Saúde e serviços sociais", "R": "Artes, cultura e esporte", "S": "Outros serviços",
         "T": "Serviços domésticos", "U": "Organismos internacionais"}


def _workdir() -> Path:
    from dotenv import dotenv_values
    return Path(dotenv_values(B.REPO / ".env")["CNPJ_PIPELINE_WORKDIR"])  # só esta chave do .env


def _manifest() -> dict:
    out = subprocess.run(["git", "-C", str(B.REPO), "show",
                          f"{MANIFEST_COMMIT}:data_manifests/cnpj_enrich/cnpj_enriquecido_{REF_RFB}.json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _partes(step: str, parts: dict, anos=ANOS) -> list[str]:
    g = B.parts_glob(step, parts, B.EXTRACT_PARAMS)
    return [g.replace("part-*", f"part-{y}") for y in anos]


def _sql_lista(paths: list[str]) -> str:
    return "[" + ", ".join(f"'{p}'" for p in paths) + "]"


def _suprime_pool(df: pd.DataFrame, chave: str, ncols: list[str]) -> pd.DataFrame:
    m = (df[chave] == SUP) & (df[ncols].replace(0, np.nan).min(axis=1) < MIN_N)
    df.loc[m, df.select_dtypes("number").columns] = np.nan
    return df


def _calcular() -> dict:
    import duckdb
    man = _manifest()
    edir = _workdir() / "build" / REF_RFB / "cnpj_enriquecido"
    for f in man["arquivos"]:
        h = hashlib.sha256((edir / f["arquivo"]).read_bytes()).hexdigest()
        if h != f["sha256"]:
            raise RuntimeError(f"cnpj_enriquecido mudou: {f['arquivo']}")
    con = duckdb.connect(config={"memory_limit": "2500MB", "threads": 4, "temp_directory": str(B.TMP),
                                 "preserve_insertion_order": False})
    q = lambda s: con.execute(s).df()
    at = _sql_lista(_partes("1.4:attributes_by_year", B.ATTR_YEAR))
    ye = _sql_lista(_partes("1.4:year_end", B.YEAR_END))
    gy = B.parts_glob("1.4:gare_year", B.GARE_YEAR, B.EXTRACT_PARAMS)
    n_enr = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM read_parquet('{edir}/*.parquet')").fetchone()
    if not (n_enr[0] == n_enr[1] == man["linhas"]):
        raise RuntimeError(f"cnpj_enriquecido: {n_enr} linhas, manifest {man['linhas']}")
    con.execute(f"""CREATE TEMP TABLE firm AS SELECT cnpj, match_nivel, cnae_divisao, cnae_secao, situacao_cadastral
                    FROM read_parquet('{edir}/*.parquet')""")
    sit = ("CASE WHEN f.cnpj IS NULL OR f.match_nivel = 'nao_encontrado' THEN 'não encontrado no cadastro' "
           "WHEN f.match_nivel = 'raiz' THEN 'só a raiz (situação desconhecida)' ELSE COALESCE(MAP "
           + "{" + ", ".join(f"'{k}': '{v}'" for k, v in SITUACAO.items()) + "}[f.situacao_cadastral], 'outro código') END")
    # atributos da CDA na última foto até dez/2025 (sem 2026)
    con.execute(f"""CREATE TEMP TABLE cda AS
      SELECT a.ID_DEBITO, a.cnpj, a.dt_insc, a.origin, COALESCE(f.match_nivel, 'nao_encontrado') AS match_level,
             {sit} AS status,
             CASE WHEN f.match_nivel = 'estabelecimento' THEN f.cnae_divisao END AS division,
             CASE WHEN f.match_nivel = 'estabelecimento' THEN f.cnae_secao END AS section
      FROM (SELECT ID_DEBITO, MIN(DATA_INSCRICAO) AS dt_insc, arg_max(ICMS_ORIGIN, last_snapshot) AS origin,
                   arg_max(CNPJ, last_snapshot) AS cnpj
            FROM read_parquet({at}) GROUP BY 1) a
      LEFT JOIN firm f ON f.cnpj = a.cnpj
      WHERE a.origin IN ('assessed', 'declared') AND LENGTH(a.cnpj) = 14""")
    con.execute(f"""CREATE TEMP TABLE pf AS
      SELECT y.year, y.ID_DEBITO, y.stock, COALESCE(g.paid, 0) AS paid, c.cnpj, c.origin, c.status, c.division,
             c.section, c.match_level, (y.year = {B.LAST_FULL_YEAR} AND y.present_in_december AND y.stock > 0) AS in_dez
      FROM read_parquet({ye}) y JOIN cda c USING (ID_DEBITO)
      LEFT JOIN (SELECT year, ID_DEBITO, paid FROM read_parquet('{gy}') WHERE year <= {B.LAST_FULL_YEAR}) g
        USING (year, ID_DEBITO)""")
    # cobertura
    cov = q("""SELECT origin, match_level, COUNT(*) FILTER (WHERE in_dez) AS cdas_dez,
                      SUM(stock) FILTER (WHERE in_dez) AS estoque_dez,
                      COUNT(DISTINCT cnpj) FILTER (WHERE in_dez) AS cnpjs_dez, COUNT(DISTINCT ID_DEBITO) AS cdas_painel
               FROM pf GROUP BY ALL ORDER BY 1, 2""")
    cov_n = q("""SELECT COUNT(DISTINCT cnpj) FILTER (WHERE in_dez) AS cnpjs_dez,
                        COUNT(DISTINCT cnpj) FILTER (WHERE in_dez AND match_level = 'estabelecimento') AS cnpjs_dez_estab
                 FROM pf""").iloc[0].to_dict()
    # 1. situação cadastral
    st = q("""SELECT origin, status, COUNT(DISTINCT cnpj) FILTER (WHERE in_dez) AS cnpjs_dez,
                     COUNT(*) FILTER (WHERE in_dez) AS cdas_dez, COALESCE(SUM(stock) FILTER (WHERE in_dez), 0) AS estoque_dez,
                     COUNT(DISTINCT cnpj) AS cnpjs_painel, COUNT(DISTINCT ID_DEBITO) AS cdas_painel,
                     COUNT(DISTINCT ID_DEBITO) FILTER (WHERE paid > 0) AS cdas_pagas, SUM(paid) AS pago, SUM(stock) AS estoque_painel
              FROM pf GROUP BY ALL""")
    nmin = st[["cnpjs_dez", "cnpjs_painel"]].replace(0, np.nan).min(axis=1).fillna(0)
    n_sup_st = int(((nmin > 0) & (nmin < MIN_N)).sum())
    st.loc[(nmin > 0) & (nmin < MIN_N), "status"] = SUP
    st = st.groupby(["origin", "status"], as_index=False).sum(numeric_only=True)
    st = _suprime_pool(st, "status", ["cnpjs_dez", "cnpjs_painel"])
    # 1b. Kaplan-Meier até o 1º pagamento (coorte de inscrição 2016-2025)
    km = q(f"""WITH fp AS (SELECT ID_DEBITO, MIN(first_payment_in_year) AS fp FROM read_parquet('{gy}')
                           WHERE year <= {B.LAST_FULL_YEAR} GROUP BY 1),
               c AS (SELECT c.origin, c.status, c.cnpj,
                            CASE WHEN fp.fp IS NOT NULL THEN GREATEST(DATE_DIFF('month', c.dt_insc, fp.fp), 0)
                                 ELSE DATE_DIFF('month', c.dt_insc, DATE '{CENSURA}') END AS t,
                            (fp.fp IS NOT NULL)::INTEGER AS d
                     FROM cda c LEFT JOIN fp USING (ID_DEBITO)
                     WHERE c.dt_insc BETWEEN DATE '{ANOS[0]}-01-01' AND DATE '{CENSURA}')
               SELECT origin, status, t, COUNT(*) AS n, SUM(d) AS d FROM c GROUP BY ALL""")
    kn = q(f"""SELECT origin, status, COUNT(DISTINCT cnpj) AS cnpjs FROM cda
               WHERE dt_insc BETWEEN DATE '{ANOS[0]}-01-01' AND DATE '{CENSURA}' GROUP BY ALL""")
    km = km.merge(kn, on=["origin", "status"])
    km.loc[km.cnpjs < MIN_N, "status"] = SUP
    pool = kn[kn.cnpjs < MIN_N].groupby("origin").cnpjs.sum()
    km = km[~((km.status == SUP) & km.origin.map(pool).fillna(0).lt(MIN_N))]
    km = km.groupby(["origin", "status", "t"], as_index=False)[["n", "d"]].sum()
    # 2. CNAE por divisão
    dv = q("""SELECT COALESCE(division, 'none') AS division, ANY_VALUE(section) AS section, origin,
                     COUNT(DISTINCT cnpj) FILTER (WHERE in_dez) AS cnpjs_dez, COALESCE(SUM(stock) FILTER (WHERE in_dez), 0) AS estoque_dez,
                     COUNT(DISTINCT cnpj) AS cnpjs_painel, SUM(paid) AS pago, SUM(stock) AS estoque_painel
              FROM pf GROUP BY ALL""")
    dn = q("""SELECT COALESCE(division, 'none') AS division, COUNT(DISTINCT cnpj) FILTER (WHERE in_dez) AS cnpjs_dez,
                     COUNT(DISTINCT cnpj) AS cnpjs_painel FROM pf GROUP BY ALL""")
    con.close()
    small = dv[((dv.cnpjs_dez > 0) & (dv.cnpjs_dez < MIN_N)) | ((dv.cnpjs_painel > 0) & (dv.cnpjs_painel < MIN_N))]
    small = sorted(d for d in small.division.unique() if d != "none")
    lab = lambda d, s: ("sem CNAE (não casou no estabelecimento)" if d == "none" else SUP if d in small
                        else f"{d} ({s or '?'})")
    dv["divisao"] = [lab(d, s) for d, s in zip(dv.division, dv.section)]
    mapa = dv.drop_duplicates("division").set_index("division").divisao
    dn["divisao"] = dn.division.map(mapa)
    w = dv.pivot_table(index="divisao", columns="origin", values=["estoque_dez", "pago", "estoque_painel"],
                       aggfunc="sum", fill_value=0)
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    for o in ORIGEM:
        for c in ("estoque_dez", "pago", "estoque_painel"):
            w[f"{c}_{o}"] = w.get(f"{c}_{o}", 0.0)
    w = w.join(dn.groupby("divisao")[["cnpjs_dez", "cnpjs_painel"]].sum())
    div = pd.DataFrame({
        "divisao": w.index, "cnpjs_dez": w.cnpjs_dez.values, "cnpjs_painel": w.cnpjs_painel.values,
        "estoque_dez": (w.estoque_dez_assessed + w.estoque_dez_declared).values,
        "peso_autuado": (100 * w.estoque_dez_assessed / (w.estoque_dez_assessed + w.estoque_dez_declared)).values,
        "rec_autuado": (100 * w.pago_assessed / w.estoque_painel_assessed.replace(0, np.nan)).values,
        "rec_declarado": (100 * w.pago_declared / w.estoque_painel_declared.replace(0, np.nan)).values,
        "rec_total": (100 * (w.pago_assessed + w.pago_declared) / (w.estoque_painel_assessed + w.estoque_painel_declared)).values})
    div = _suprime_pool(div, "divisao", ["cnpjs_dez", "cnpjs_painel"])
    return {"versao_rfb": man["versao"], "linhas_rfb": int(n_enr[0]), "arquivos_rfb": len(man["arquivos"]),
            "cobertura": cov.to_dict("records"), "cobertura_cnpjs": {k: int(v) for k, v in cov_n.items()},
            "situacao": st.to_dict("records"), "n_celulas_suprimidas_situacao": n_sup_st,
            "km": km.to_dict("records"), "divisao": div.to_dict("records"), "n_divisoes_suprimidas": len(small)}


def agregados() -> dict:
    """Tudo o que a página mostra, já agregado e suprimido. Cache local (só agregados)."""
    return B.cached_json("lab.firma:agregados", _calcular,
                         params={**B.EXTRACT_PARAMS, "anos": ANOS, "ref_rfb": REF_RFB, "min_n": MIN_N,
                                 "manifest": MANIFEST_COMMIT}, version="1")


def kaplan_meier(g: pd.DataFrame) -> pd.DataFrame:
    """1 - S(t) de Kaplan-Meier a partir de (t, n, d) agregados: n = CDAs com T = t, d = 1ºs pagamentos em t."""
    g = g.sort_values("t")
    risco = g.n[::-1].cumsum()[::-1].to_numpy()
    s = np.cumprod(1 - g.d.to_numpy() / risco)
    return pd.DataFrame({"t": g.t.to_numpy(), "pago": 1 - s, "em_risco": risco})
