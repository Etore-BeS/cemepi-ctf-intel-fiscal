"""Stage `export`: self-contained package to load `cnpj_enriquecido` on the API server (nothing is sent anywhere).

Package (export/cnpj_enriquecido_ref<REF>/):
  data/cnpj_enriquecido.parquet   for DuckDB (and pandas/polars users)
  data/cnpj_enriquecido.csv.gz    for PostgreSQL \\copy
  sql/ddl_duckdb.sql, sql/ddl_postgres.sql
  sql/load_duckdb.sql, sql/load_postgres.sql   idempotent upsert by cnpj (one transaction)
  load_duckdb.sh, load_postgres.sh             wrappers; run from the package directory
  MANIFEST.json                                 rows, sha256 of every file, policy, versions
  README.md, data_dictionary.md
Policy for natural-person-linked rows (MEI, empresário individual, natureza 4xxx), see README "LGPD":
  suprimir (default): keep the row, blank capital_social and the municipality columns;
  remover: drop those rows; manter: keep everything.
Partners (socios) are NOT exported by default. `--with-socios --hash-salt-env VAR` adds data/socios_vinculos.* (table
socios_vinculos), an ANONYMISED edge list: firm root in clear (it is the API key), PJ partner = its CNPJ in clear,
PF/foreign partner = socio_id = HMAC-SHA256(salt, normalised name | middle digits of the masked CPF), so the same person
has the same id in every firm; type, qualification, entry YEAR and age band. No name, no masked CPF, no legal
representative, no country, no PJ partner name. Without the salt the export with partners fails. The salt is read from
the environment only: never written to the package, the manifest, the state or the log.
The package README gets a re-identification risk report (k-anonymity of PF partners on quasi-identifiers).
Partner detail (`--socios-detail`): `api` (default) is the version that may be loaded on the API server: the entry
year is generalised to the DECADE (`decada_entrada_sociedade`). `local` keeps the exact entry year and writes the
package to `cnpj_enriquecido_ref<REF>_LOCAL_NAO_SUBIR/` (MANIFEST `api_upload_allowed: false`): it stays on the
researcher's disk under the PGE NDA and must never be loaded on the API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

import duckdb

from . import __version__
from .build import COLUMNS, COLUMN_NAMES
from .config import Settings
from .db import connect, q
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")


def _doc(name: str) -> Path:
    """data_dictionary.md / EXPORT_README.md: next to the package (tarball) or in pipelines/cnpj_enrich/docs (monorepo)."""
    here = Path(__file__).resolve().parent
    for d in (here.parent, here.parent.parent / "docs", here.parent / "docs"):
        if (d / name).is_file():
            return d / name
    raise FileNotFoundError(f"{name} not found next to the cnpj_pipeline package")
PF_SUPPRESSED = ["capital_social", "municipio_rfb", "municipio_ibge", "municipio_nome"]
TABLE = "cnpj_enriquecido"
EXPORT_COLUMNS = ([(c, d, "varchar(64)" if c in ("cnpj", "cnpj_raiz") else p) for c, d, p in COLUMNS]
                  + [("capital_social_faixa", "VARCHAR", "text"), ("cnpj_anonimizado", "BOOLEAN", "boolean"),
                     ("municipio_generalizado", "BOOLEAN", "boolean")])
EXPORT_COLUMN_NAMES = [c for c, _, _ in EXPORT_COLUMNS]
PF_POLICIES = ("anonimizar", "suprimir", "remover", "manter")
DATE_COLUMNS = [c for c, d, _ in COLUMNS if d == "DATE"]
MUNICIPIO_COLUMNS = ["municipio_rfb", "municipio_ibge", "municipio_nome"]
CAPITAL_FAIXAS = [(0, "0"), (1_000, "ate 1 mil"), (10_000, "1 mil a 10 mil"), (50_000, "10 mil a 50 mil"),
                  (100_000, "50 mil a 100 mil"), (500_000, "100 mil a 500 mil"), (1_000_000, "500 mil a 1 milhao"),
                  (10_000_000, "1 milhao a 10 milhoes")]
SOCIOS_TABLE = "socios_vinculos"
SOCIOS_DETAILS = ("api", "local")
ENTRY_COLUMN = {"api": "decada_entrada_sociedade", "local": "ano_entrada_sociedade"}
LOCAL_SUFFIX = "_LOCAL_NAO_SUBIR"


def socios_columns(detail: str = "api") -> list[tuple[str, str, str]]:
    """Exported anonymised links: no name, no CPF, no representative (SOCIOS_PERSONAL never appears).
    `api`: entry decade only; `local`: exact entry year (local package, never loaded on the API)."""
    return [
        ("cnpj_basico", "VARCHAR", "char(8)"),
        ("socio_id", "VARCHAR", "text"), ("socio_tipo", "VARCHAR", "text"), ("identificador_socio", "VARCHAR", "char(1)"),
        ("socio_cnpj_raiz", "VARCHAR", "char(8)"),
        ("qualificacao_socio", "VARCHAR", "char(2)"), ("qualificacao_socio_descricao", "VARCHAR", "text"),
        (ENTRY_COLUMN[detail], "INTEGER", "integer"),
        ("faixa_etaria", "VARCHAR", "char(1)"), ("faixa_etaria_descricao", "VARCHAR", "text"),
        ("empresa_devedora", "BOOLEAN", "boolean"), ("socio_pj_devedor", "BOOLEAN", "boolean"),
        ("ref_rfb", "VARCHAR", "char(7)"),
    ]


SOCIOS_COLUMNS = socios_columns("api")
SOCIOS_COLUMN_NAMES = [c for c, _, _ in SOCIOS_COLUMNS]
SOCIOS_COLUMN_NAMES_LOCAL = [c for c, _, _ in socios_columns("local")]


def _ddl_socios(pg: bool, detail: str = "api") -> str:
    cols = ",\n    ".join(f"{c} {p if pg else d}" for c, d, p in socios_columns(detail))
    return (f"CREATE TABLE IF NOT EXISTS {SOCIOS_TABLE} (\n    {cols}\n);\n"
            f"CREATE INDEX IF NOT EXISTS {SOCIOS_TABLE}_basico_idx ON {SOCIOS_TABLE} (cnpj_basico);\n"
            f"CREATE INDEX IF NOT EXISTS {SOCIOS_TABLE}_socio_idx ON {SOCIOS_TABLE} (socio_id);\n")


K_MIN = 5


def _vinculos_sql(soc_src: Path) -> str:
    """Anonymised links. Needs the temp table _hk (socios.load_hmac_key) so the key never appears in SQL text."""
    from .socios import hmac_sql, person_key_sql
    pj = "(identificador_socio = '1' AND socio_cnpj IS NOT NULL)"
    return f"""
        SELECT cnpj_basico,
               CASE WHEN {pj} THEN socio_cnpj ELSE {hmac_sql(person_key_sql('nome_socio', 'cpf_cnpj_socio'))} END AS socio_id,
               CASE identificador_socio WHEN '1' THEN 'PJ' WHEN '2' THEN 'PF' WHEN '3' THEN 'ESTRANGEIRO'
                    ELSE 'DESCONHECIDO' END AS socio_tipo,
               identificador_socio,
               CASE WHEN {pj} THEN socio_cnpj_raiz END AS socio_cnpj_raiz,
               qualificacao_socio, qualificacao_socio_descricao,
               year(data_entrada_sociedade)::INTEGER AS ano_entrada_sociedade,
               (year(data_entrada_sociedade) // 10 * 10)::INTEGER AS decada_entrada_sociedade,
               faixa_etaria, faixa_etaria_descricao, empresa_devedora, socio_pj_devedor, ref_rfb
        FROM read_parquet({q(soc_src)}) CROSS JOIN _hk"""


def risk_sets(detail: str = "api") -> list[tuple[str, list[str]]]:
    """(label, quasi-identifiers) on the PF partner links, with the entry column AS EXPORTED (decade for `api`,
    year for `local`); firm attributes come from the full build."""
    e = "década de entrada" if detail == "api" else "ano de entrada"
    return [
        ("faixa etária × município × CNAE (atributos da empresa via CNPJ)", ["faixa_etaria", "municipio", "cnae"]),
        (f"faixa etária × município × CNAE × {e}", ["faixa_etaria", "municipio", "cnae", "entrada"]),
        ("generalizado: faixa etária × UF × seção CNAE", ["faixa_etaria", "uf", "cnae_secao"]),
        (f"generalizado: faixa etária × UF × seção CNAE × {e}", ["faixa_etaria", "uf", "cnae_secao", "entrada"]),
        (f"dentro da empresa: CNPJ × qualificação × faixa × {e}", ["cnpj_basico", "qualificacao_socio",
                                                                  "faixa_etaria", "entrada"]),
    ]


RISK_SETS = risk_sets("api")


def risk_report(con, vinc: Path, build_glob: str, detail: str = "api") -> tuple[str, list[dict]]:
    """k-anonymity of PF partners: k = distinct socio_id per combination of quasi-identifiers. Firm municipality and
    CNAE are taken from the FULL build (not the PF-suppressed export), because anyone holding the firm CNPJ can look
    them up in the public RFB data. Only aggregate counts are reported, never the rare combinations themselves."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _pf AS
        WITH firm AS (SELECT cnpj_raiz, COALESCE(municipio_ibge, 'TOM' || municipio_rfb) AS municipio, uf,
                             cnae_principal AS cnae, cnae_secao
                      FROM read_parquet({q(build_glob)})
                      QUALIFY row_number() OVER (PARTITION BY cnpj_raiz
                                                 ORDER BY (matriz_filial = 'MATRIZ') DESC NULLS LAST, cnpj) = 1)
        SELECT DISTINCT v.socio_id, v.cnpj_basico, v.qualificacao_socio, COALESCE(v.faixa_etaria, '?') AS faixa_etaria,
               COALESCE(v.{ENTRY_COLUMN[detail]}::VARCHAR, '?') AS entrada,
               COALESCE(f.municipio, '(sem cadastro)') AS municipio, COALESCE(f.uf, '?') AS uf,
               COALESCE(f.cnae, '(sem cadastro)') AS cnae, COALESCE(f.cnae_secao, '?') AS cnae_secao
        FROM read_parquet({q(vinc)}) v LEFT JOIN firm f ON f.cnpj_raiz = v.cnpj_basico
        WHERE v.socio_tipo = 'PF'""")
    n_links, n_pf = con.execute("SELECT COUNT(*), COUNT(DISTINCT socio_id) FROM _pf").fetchone()
    out = []
    for label, qi in risk_sets(detail):
        cols = ", ".join(qi)
        r = con.execute(f"""
            WITH c AS (SELECT {cols}, COUNT(DISTINCT socio_id) AS k, COUNT(*) AS links FROM _pf GROUP BY ALL),
            l AS (SELECT p.socio_id, c.k FROM _pf p JOIN c USING ({cols}))
            SELECT (SELECT COUNT(*) FROM c), (SELECT COUNT(*) FROM c WHERE k < {K_MIN}),
                   (SELECT COUNT(*) FROM c WHERE k = 1),
                   (SELECT COALESCE(SUM(links), 0) FROM c WHERE k < {K_MIN}),
                   (SELECT COUNT(DISTINCT socio_id) FROM l WHERE k < {K_MIN})""").fetchone()
        dist = con.execute(f"""SELECT k, COUNT(*) FROM (SELECT COUNT(DISTINCT socio_id) AS k FROM _pf GROUP BY {cols})
                               WHERE k < {K_MIN} GROUP BY 1 ORDER BY 1""").fetchall()
        out.append({"conjunto": label, "quasi_identificadores": qi, "combinacoes": r[0], "combinacoes_k_lt_5": r[1],
                    "combinacoes_k_1": r[2], "vinculos_em_k_lt_5": int(r[3]), "pf_em_k_lt_5": r[4],
                    "pct_pf_em_k_lt_5": round(100 * r[4] / n_pf, 2) if n_pf else 0.0,
                    "distribuicao_k_lt_5": {int(k): int(c) for k, c in dist}})
    con.execute("DROP TABLE _pf")
    lines = ["", "## Relatório de risco de reidentificação (sócios pessoa física)", "",
             f"Versão de `socios_vinculos` medida: **{detail}** ("
             + ("vai para a API: entrada só por década" if detail == "api" else
                "LOCAL, NÃO SUBIR: ano de entrada exato") + "). "
             f"Gerado por `cnpj_pipeline export --with-socios`. Base: {n_links:,} vínculos PF–empresa, {n_pf:,} "
             "pessoas físicas distintas (pelo `socio_id`). `k` = número de pessoas distintas que compartilham a mesma "
             "combinação de quase-identificadores. Município e CNAE vêm do cadastro completo da empresa (e não da "
             "versão suprimida do pacote), porque quem tem o CNPJ consegue esses dados na RFB. Só contagens são "
             "publicadas aqui; as combinações raras não são listadas.", "",
             "| quase-identificadores | combinações | combinações com k<5 | com k=1 | vínculos em k<5 | PF em k<5 | % das PF |",
             "|---|---|---|---|---|---|---|"]
    for r in out:
        lines.append(f"| {r['conjunto']} | {r['combinacoes']:,} | {r['combinacoes_k_lt_5']:,} | {r['combinacoes_k_1']:,} | "
                     f"{r['vinculos_em_k_lt_5']:,} | {r['pf_em_k_lt_5']:,} | {r['pct_pf_em_k_lt_5']} |")
    lines += ["", "Distribuição das combinações com k<5 (k: combinações): " + "; ".join(
                  f"{r['conjunto']}: " + ", ".join(f"k={k}: {c:,}" for k, c in r["distribuicao_k_lt_5"].items())
                  for r in out), "",
              "**Recomendação.** Onde k<5, generalize antes de abrir a tabela a outros pesquisadores: município → UF "
              "ou região, CNAE (7 dígitos) → divisão (2) ou seção, faixa etária → faixas mais largas, ano de entrada "
              "→ década, ou suprima a célula/linha. A linha \"generalizado\" acima mostra o efeito de uma generalização. "
              "A linha \"dentro da empresa\" mede outro risco: como o CNPJ da empresa está em claro e o quadro de "
              "sócios da RFB é público (com nome), quem cruzar o pacote com a RFB recupera o nome da pessoa sempre que "
              "ela for a única da empresa com aquela qualificação, faixa e ano de entrada. O HMAC impede ligar o id ao "
              "nome sem o sal, mas não impede essa ligação via RFB. Por isso, trate `socios_vinculos` como dado pessoal "
              "pseudonimizado (LGPD art. 13), com acesso autenticado e termo de uso, e não como dado anônimo.", ""]
    return "\n".join(lines), out


def ddl_duckdb() -> str:
    cols = ",\n    ".join(f"{c} {t}{' NOT NULL' if c in ('cnpj', 'cnpj_raiz', 'ref_rfb') else ''}" for c, t, _ in EXPORT_COLUMNS)
    return (f"-- cnpj_enriquecido (DuckDB). Generated by cnpj_pipeline {__version__}.\n"
            f"CREATE TABLE IF NOT EXISTS {TABLE} (\n    {cols},\n    PRIMARY KEY (cnpj)\n);\n"
            f"CREATE INDEX IF NOT EXISTS {TABLE}_raiz_idx ON {TABLE} (cnpj_raiz);\n")


def ddl_postgres() -> str:
    cols = ",\n    ".join(f"{c} {t}{' NOT NULL' if c in ('cnpj', 'cnpj_raiz', 'ref_rfb') else ''}" for c, _, t in EXPORT_COLUMNS)
    return (f"-- cnpj_enriquecido (PostgreSQL >= 11, generic SQL). Generated by cnpj_pipeline {__version__}.\n"
            f"CREATE TABLE IF NOT EXISTS {TABLE} (\n    {cols},\n    CONSTRAINT {TABLE}_pk PRIMARY KEY (cnpj)\n);\n"
            f"CREATE INDEX IF NOT EXISTS {TABLE}_raiz_idx ON {TABLE} (cnpj_raiz);\n"
            f"CREATE INDEX IF NOT EXISTS {TABLE}_cnae_idx ON {TABLE} (cnae_principal);\n"
            f"COMMENT ON TABLE {TABLE} IS 'Debtor CNPJs (DEBITO) enriched with Receita Federal open CNPJ data; "
            f"no names, addresses, phones, e-mails or partners. See data_dictionary.md.';\n")


def load_duckdb_sql(with_socios: bool = False, ref: str = "", detail: str = "api") -> str:
    cols = ", ".join(EXPORT_COLUMN_NAMES)
    sc = ", ".join(c for c, _, _ in socios_columns(detail))
    soc = (f"""{_ddl_socios(False, detail)}DELETE FROM {SOCIOS_TABLE} WHERE ref_rfb = '{ref}';
INSERT INTO {SOCIOS_TABLE} ({sc}) SELECT {sc} FROM read_parquet('data/socios_vinculos.parquet');
""" if with_socios else "")
    return f"""-- Idempotent load into DuckDB. Run from the package directory:
--   duckdb /path/to/api.duckdb -c ".read sql/load_duckdb.sql"
-- Re-running with the same or a newer package never duplicates rows: INSERT OR REPLACE upserts by cnpj.
BEGIN TRANSACTION;
.read sql/ddl_duckdb.sql
INSERT OR REPLACE INTO {TABLE} ({cols})
SELECT {cols} FROM read_parquet('data/cnpj_enriquecido.parquet');
{soc}COMMIT;
SELECT ref_rfb, COUNT(*) AS rows FROM {TABLE} GROUP BY ref_rfb ORDER BY ref_rfb;
"""


def load_postgres_sql(with_socios: bool = False, ref: str = "", detail: str = "api") -> str:
    cols = ", ".join(EXPORT_COLUMN_NAMES)
    upd = ",\n        ".join(f"{c} = EXCLUDED.{c}" for c in EXPORT_COLUMN_NAMES if c != "cnpj")
    sc = ", ".join(c for c, _, _ in socios_columns(detail))
    soc = (f"""{_ddl_socios(True, detail)}DELETE FROM {SOCIOS_TABLE} WHERE ref_rfb = '{ref}';
\\copy {SOCIOS_TABLE} ({sc}) FROM PROGRAM 'gzip -dc data/socios_vinculos.csv.gz' WITH (FORMAT csv, HEADER true)
""" if with_socios else "")
    return f"""-- Idempotent load into PostgreSQL (psql). Run from the package directory:
--   psql "$PGURI" -f sql/load_postgres.sql
-- One transaction: the table is never half-loaded. Upsert by cnpj; unchanged rows are not rewritten.
\\set ON_ERROR_STOP on
BEGIN;
\\i sql/ddl_postgres.sql
CREATE TEMP TABLE {TABLE}_stg (LIKE {TABLE} INCLUDING DEFAULTS) ON COMMIT DROP;
\\copy {TABLE}_stg ({cols}) FROM PROGRAM 'gzip -dc data/cnpj_enriquecido.csv.gz' WITH (FORMAT csv, HEADER true)
INSERT INTO {TABLE} AS t ({cols})
SELECT {cols} FROM {TABLE}_stg
ON CONFLICT (cnpj) DO UPDATE SET
        {upd}
    WHERE (t.*) IS DISTINCT FROM (EXCLUDED.*);
{soc}COMMIT;
ANALYZE {TABLE};
SELECT ref_rfb, COUNT(*) AS rows FROM {TABLE} GROUP BY ref_rfb ORDER BY ref_rfb;
"""


LOCAL_BANNER = """> **PACOTE LOCAL — NÃO SUBIR PARA A API NEM PARA A VPS.**
> Gerado com `export --socios-detail local`: `socios_vinculos` traz o ANO exato de entrada na sociedade (mais
> risco de reidentificação, veja o relatório no fim). Uso só local, sob o NDA da PGE. Para a API use o pacote
> sem o sufixo `_LOCAL_NAO_SUBIR` (gerado com `--socios-detail api`, o padrão). `MANIFEST.json`:
> `api_upload_allowed: false`.

"""
SH_DUCKDB = """#!/usr/bin/env bash
# Usage: ./load_duckdb.sh /path/to/api.duckdb     (stop the API or use a maintenance window: DuckDB has one writer)
set -euo pipefail
cd "$(dirname "$0")"
if command -v sha256sum >/dev/null; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi   # Linux / macOS
duckdb "${1:?path to the DuckDB file}" -c ".read sql/load_duckdb.sql"
"""
SH_POSTGRES = """#!/usr/bin/env bash
# Usage: PGURI=postgresql://user@host:5432/db ./load_postgres.sh   (credentials via PGURI/PGPASSFILE, never in files)
set -euo pipefail
cd "$(dirname "$0")"
if command -v sha256sum >/dev/null; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi   # Linux / macOS
psql "${PGURI:?set PGURI}" -v ON_ERROR_STOP=1 -f sql/load_postgres.sql
"""


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _capital_faixa(c: str) -> str:
    w = " ".join(f"WHEN {c} <= {lim} THEN '{lab}'" for lim, lab in CAPITAL_FAIXAS)
    return f"CASE WHEN {c} IS NULL THEN NULL {w} ELSE 'mais de 10 milhoes' END"


def _cnpj_sql(src: str, pf_policy: str) -> str:
    """SELECT of the exported cnpj_enriquecido for each PF policy (anonimizar needs _hk loaded)."""
    from .socios import hmac_sql
    pf = "b.pessoa_fisica_vinculada"
    sel = []
    for c in COLUMN_NAMES:
        e = f"b.{c}"
        if pf_policy == "anonimizar":
            if c in ("cnpj", "cnpj_raiz"):
                e = f"CASE WHEN {pf} THEN {hmac_sql('b.' + c)} ELSE b.{c} END"
            elif c in DATE_COLUMNS:
                e = f"CASE WHEN {pf} THEN date_trunc('year', b.{c})::DATE ELSE b.{c} END"
            elif c in MUNICIPIO_COLUMNS:
                e = f"CASE WHEN {pf} AND COALESCE(cell.k, 0) < {K_MIN} THEN NULL ELSE b.{c} END"
            elif c == "capital_social":
                e = f"CASE WHEN {pf} THEN NULL ELSE b.{c} END"
        elif pf_policy == "suprimir" and c in PF_SUPPRESSED:
            e = f"CASE WHEN {pf} THEN NULL ELSE b.{c} END"
        sel.append(f"{e} AS {c}")
    sel.append(f"{_capital_faixa('b.capital_social')} AS capital_social_faixa")
    if pf_policy == "anonimizar":
        sel += [f"{pf} AS cnpj_anonimizado",
                f"({pf} AND b.municipio_rfb IS NOT NULL AND COALESCE(cell.k, 0) < {K_MIN}) AS municipio_generalizado"]
        frm = (f"FROM {src} b LEFT JOIN (SELECT municipio_rfb, cnae_divisao, COUNT(DISTINCT cnpj) AS k FROM {src} "
               "WHERE pessoa_fisica_vinculada GROUP BY ALL) cell USING (municipio_rfb, cnae_divisao) CROSS JOIN _hk")
    else:
        sel += ["false AS cnpj_anonimizado",
                f"({pf} AND b.municipio_rfb IS NOT NULL) AS municipio_generalizado" if pf_policy == "suprimir"
                else "false AS municipio_generalizado"]
        frm = f"FROM {src} b"
    where = "WHERE NOT b.pessoa_fisica_vinculada" if pf_policy == "remover" else ""
    return f"SELECT {', '.join(sel)} {frm} {where}"


PF_RISK_SETS = [
    ("município exportado × divisão CNAE", ["municipio_rfb", "cnae_divisao"]),
    ("UF × município exportado × CNAE × natureza × porte", ["uf", "municipio_rfb", "cnae_principal",
                                                           "natureza_juridica", "porte"]),
    ("UF × município exportado × CNAE × ano de início × situação", ["uf", "municipio_rfb", "cnae_principal",
                                                                   "data_inicio_atividade", "situacao_cadastral"]),
]


def pf_risk_report(con, pq: Path) -> tuple[str, list[dict]]:
    """k-anonymity of the exported PF-linked rows (MEI, empresário individual...) on the quasi-identifiers AS EXPORTED."""
    t = f"(SELECT * FROM read_parquet({q(pq)}) WHERE pessoa_fisica_vinculada)"
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    out = []
    for label, qi in PF_RISK_SETS:
        cols = ", ".join(f"COALESCE(CAST({c} AS VARCHAR), '(nulo)')" for c in qi)
        r = con.execute(f"""WITH c AS (SELECT {cols}, COUNT(*) AS k FROM {t} GROUP BY ALL)
                            SELECT COUNT(*), COUNT(*) FILTER (WHERE k < {K_MIN}), COUNT(*) FILTER (WHERE k = 1),
                                   COALESCE(SUM(k) FILTER (WHERE k < {K_MIN}), 0) FROM c""").fetchone()
        out.append({"conjunto": label, "quasi_identificadores": qi, "combinacoes": r[0], "combinacoes_k_lt_5": r[1],
                    "combinacoes_k_1": r[2], "linhas_em_k_lt_5": int(r[3]),
                    "pct_linhas_em_k_lt_5": round(100 * r[3] / n, 2) if n else 0.0})
    gen = con.execute(f"SELECT COUNT(*) FILTER (WHERE municipio_generalizado) FROM {t}").fetchone()[0]
    lines = ["", "## Relatório de risco de reidentificação (CNPJs vinculados a pessoa física)", "",
             f"Linhas com `pessoa_fisica_vinculada = true` no pacote: {n:,}. Município generalizado para UF "
             f"(célula município × divisão CNAE com k<{K_MIN}): {gen:,} linhas. `k` = linhas que compartilham a "
             "combinação, com os valores exatamente como exportados (datas já truncadas ao ano).", "",
             "| quase-identificadores | combinações | com k<5 | com k=1 | linhas em k<5 | % das linhas PF |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['conjunto']} | {r['combinacoes']:,} | {r['combinacoes_k_lt_5']:,} | {r['combinacoes_k_1']:,} | "
              f"{r['linhas_em_k_lt_5']:,} | {r['pct_linhas_em_k_lt_5']} |" for r in out]
    lines += ["", "**Recomendação.** As linhas acima de \"município × divisão CNAE\" combinam mais atributos e quase "
              "sempre têm células com k<5. Antes de abrir o pacote a outros pesquisadores, revise essas células e, "
              "onde k<5, generalize (CNAE → divisão ou seção, natureza → grupo, ano → período) ou suprima. O id HMAC "
              "não pode ser revertido sem o sal, mas o conjunto CNAE + município + ano de início pode bastar para achar "
              "um MEI no cadastro público da RFB.", ""]
    return "\n".join(lines), out


def run(s: Settings, state: State, pf_policy: str = "suprimir", with_socios: bool = False,
        hash_salt_env: str | None = None, socios_detail: str = "api") -> Path:
    if pf_policy not in PF_POLICIES:
        raise SystemExit(f"--pf-policy must be one of {', '.join(PF_POLICIES)}")
    if socios_detail not in SOCIOS_DETAILS:
        raise SystemExit(f"--socios-detail must be one of {', '.join(SOCIOS_DETAILS)}")
    local = with_socios and socios_detail == "local"
    src = s.build_dir / "cnpj_enriquecido"
    if not list(src.glob("*.parquet")):
        raise SystemExit("export: run `build` first")
    soc_src = s.socios_dir / "socios.parquet"
    salt = None
    if with_socios or pf_policy == "anonimizar":
        from .socios import read_salt
        who = "export --with-socios" if with_socios else "export --pf-policy anonimizar"
        salt = read_salt(hash_salt_env, who)          # fails without the salt, before writing anything
    if with_socios and not soc_src.exists():
        raise SystemExit("export --with-socios: run `socios` first")
    name = f"cnpj_enriquecido_ref{s.ref_rfb}" + (LOCAL_SUFFIX if local else "")
    final = s.export_dir / name
    scratch = s.temp_dir / "export_scratch"           # clear-text intermediates: on the HD, never in the package

    def work(tmp: Path) -> tuple[int, dict]:
        from .socios import hmac_sql, load_hmac_key
        (tmp / "data").mkdir(parents=True); (tmp / "sql").mkdir()
        shutil.rmtree(scratch, ignore_errors=True); scratch.mkdir(parents=True)
        con = connect(s)
        risk_md, risk, pf_risk = "", None, None
        try:
            if salt is not None:
                load_hmac_key(con, salt)
            b = f"read_parquet({q(src / '*.parquet')})"
            pq = tmp / "data" / "cnpj_enriquecido.parquet"
            con.execute(f"COPY ({_cnpj_sql(b, pf_policy)} ORDER BY cnpj) TO {q(pq)} "
                        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)")
            con.execute(f"COPY (SELECT {', '.join(EXPORT_COLUMN_NAMES)} FROM read_parquet({q(pq)})) "
                        f"TO {q(tmp / 'data' / 'cnpj_enriquecido.csv.gz')} (FORMAT CSV, HEADER, COMPRESSION GZIP)")
            n, u, pf = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj), SUM(pessoa_fisica_vinculada::INT) "
                                   f"FROM read_parquet({q(pq)})").fetchone()
            if pf_policy == "anonimizar":
                pf_md, pf_risk = pf_risk_report(con, pq)
                risk_md += pf_md
            n_soc = None
            if with_socios:
                clear = scratch / "vinculos.parquet"       # firm keys in clear, persons already pseudonymised
                cols = [c for c, _, _ in socios_columns(socios_detail)]
                con.execute(f"COPY (SELECT {', '.join(cols)} FROM ({_vinculos_sql(soc_src)})) "
                            f"TO {q(clear)} (FORMAT PARQUET, COMPRESSION ZSTD)")
                md, risk = risk_report(con, clear, str(src / "*.parquet"), socios_detail)
                risk_md += md
                spq = tmp / "data" / "socios_vinculos.parquet"
                if pf_policy == "anonimizar":   # PF-linked firms (MEI...) get the same HMAC ids as in cnpj_enriquecido
                    pfr = (f"(SELECT DISTINCT cnpj_raiz FROM {b} WHERE pessoa_fisica_vinculada)")
                    m = {"cnpj_basico": f"CASE WHEN v.cnpj_basico IN {pfr} THEN {hmac_sql('v.cnpj_basico')} "
                                        "ELSE v.cnpj_basico END",
                         "socio_id": f"CASE WHEN v.socio_tipo = 'PJ' AND v.socio_cnpj_raiz IN {pfr} "
                                     f"THEN {hmac_sql('v.socio_id')} ELSE v.socio_id END",
                         "socio_cnpj_raiz": f"CASE WHEN v.socio_cnpj_raiz IN {pfr} THEN {hmac_sql('v.socio_cnpj_raiz')} "
                                            "ELSE v.socio_cnpj_raiz END"}
                    sel = ", ".join(f"{m[c]} AS {c}" if c in m else f"v.{c}" for c in cols)
                    body = f"SELECT {sel} FROM read_parquet({q(clear)}) v CROSS JOIN _hk"
                else:
                    body = f"SELECT {', '.join(cols)} FROM read_parquet({q(clear)})"
                con.execute(f"COPY ({body} ORDER BY cnpj_basico, socio_id) TO {q(spq)} "
                            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)")
                con.execute(f"COPY (SELECT * FROM read_parquet({q(spq)})) TO {q(tmp / 'data' / 'socios_vinculos.csv.gz')} "
                            "(FORMAT CSV, HEADER, COMPRESSION GZIP)")
                n_soc = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(spq)})").fetchone()[0]
        finally:
            con.execute("DROP TABLE IF EXISTS _hk")
            con.close()
            shutil.rmtree(scratch, ignore_errors=True)
        if n != u:
            raise RuntimeError("export: duplicated cnpj")
        (tmp / "sql" / "ddl_duckdb.sql").write_text(ddl_duckdb())
        (tmp / "sql" / "ddl_postgres.sql").write_text(ddl_postgres())
        (tmp / "sql" / "load_duckdb.sql").write_text(load_duckdb_sql(with_socios, s.ref_rfb, socios_detail))
        (tmp / "sql" / "load_postgres.sql").write_text(load_postgres_sql(with_socios, s.ref_rfb, socios_detail))
        for fn, txt in (("load_duckdb.sh", SH_DUCKDB), ("load_postgres.sh", SH_POSTGRES)):
            (tmp / fn).write_text(txt); os.chmod(tmp / fn, 0o755)
        shutil.copy(_doc("data_dictionary.md"), tmp / "data_dictionary.md")
        banner = (LOCAL_BANNER if local else "")
        (tmp / "README.md").write_text(banner + _doc("EXPORT_README.md").read_text() + risk_md)
        if local:
            (tmp / "NAO_SUBIR.txt").write_text(LOCAL_BANNER)
        for r in (risk or []):
            log.info("export risk (socios PF, detail=%s): %s: %d of %d combinations with k<5; %d PF (%.2f%%) in them",
                     socios_detail, r["conjunto"], r["combinacoes_k_lt_5"], r["combinacoes"], r["pf_em_k_lt_5"],
                     r["pct_pf_em_k_lt_5"])
        for r in (pf_risk or []):
            log.info("export risk (PF-linked CNPJ rows): %s: %d of %d combinations with k<5; %d rows (%.2f%%)",
                     r["conjunto"], r["combinacoes_k_lt_5"], r["combinacoes"], r["linhas_em_k_lt_5"],
                     r["pct_linhas_em_k_lt_5"])
        if (s.build_dir / "coverage.md").exists():
            shutil.copy(s.build_dir / "coverage.md", tmp / "coverage.md")
        files = sorted(p for p in tmp.rglob("*") if p.is_file())
        sums = {str(p.relative_to(tmp)): _sha(p) for p in files}
        (tmp / "SHA256SUMS").write_text("".join(f"{h}  {f}\n" for f, h in sums.items()))
        rfb = json.loads(s.manifest.read_text()) if s.manifest.exists() else {}
        pseudo = {"method": "HMAC-SHA256 (hex)", "salt": "kept outside the package (environment variable)"}
        manifest = {"table": TABLE, "rows": n, "ref_rfb": s.ref_rfb, "pf_policy": pf_policy,
                    "cnpj_pseudonym": ({**pseudo, "message": "cnpj (14, normalised) / cnpj_raiz (8)",
                                        "rows": "pessoa_fisica_vinculada = true"} if pf_policy == "anonimizar" else None),
                    "pf_reidentification_risk": pf_risk,
                    "with_socios": with_socios, "socios_rows": n_soc,
                    "socios_detail": socios_detail if with_socios else None,
                    "api_upload_allowed": not local,
                    "socios_columns": cols if with_socios else None,
                    "socios_pseudonym": ({**pseudo, "message": "NOME_NORMALIZADO|miolo_do_CPF"} if with_socios else None),
                    "socios_reidentification_risk": risk,
                    "pf_linked_rows": pf,
                    "columns": [{"name": c, "duckdb": d, "postgres": p} for c, d, p in EXPORT_COLUMNS],
                    "files_sha256": sums, "pipeline_version": __version__, "duckdb_version": duckdb.__version__,
                    "rfb_files_sha256": {k: v.get("sha256") for k, v in rfb.get("files", {}).items()},
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        return n, {"pf_policy": pf_policy, "with_socios": with_socios, "socios_detail": socios_detail}
    run_unit(state, "export", name, final, work, force=True)
    log.info("export: package ready at %s (nothing was sent to any server)", final)
    return final
