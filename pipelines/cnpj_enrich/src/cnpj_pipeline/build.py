"""Stage `build`: consolidate the enrich outputs into `cnpj_enriquecido` (one row per debtor CNPJ) + coverage report.

Grain and keys: one row per distinct debtor CNPJ (14 characters) found in DEBITO; `cnpj_raiz` (8) is also stored.
Match levels: 'estabelecimento' (the 14-character CNPJ is in Estabelecimentos), 'raiz' (only the root is in Empresas;
firm-level columns filled, establishment columns null), 'nao_encontrado'.
Duplicates in the RFB files (same CNPJ in two files) are resolved deterministically (latest data_situacao, then
file name) and counted in the report. Built per key bucket (unit 'kb=K'), so memory is bounded by one bucket.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

from .config import Settings, data_root, manifest_dir
from .db import connect, q
from .layouts import CNAE_SECTION, MATRIZ_FILIAL, PF_NATUREZAS_SQL, PORTE, REGIME_SIMPLES_DERIVADO, SITUACAO, sql_map
from .regime import signature as regime_signature
from .socios import _stale
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")

COLUMNS = [  # (name, duckdb type, postgres type) -- the published contract; data_dictionary.md documents each one
    ("cnpj", "VARCHAR", "char(14)"), ("cnpj_raiz", "VARCHAR", "char(8)"), ("dv_valido", "BOOLEAN", "boolean"),
    ("cnpj_alfanumerico", "BOOLEAN", "boolean"), ("match_nivel", "VARCHAR", "text"),
    ("matriz_filial", "VARCHAR", "text"),
    ("cnae_principal", "VARCHAR", "char(7)"), ("cnae_principal_descricao", "VARCHAR", "text"),
    ("cnae_divisao", "VARCHAR", "char(2)"), ("cnae_secao", "VARCHAR", "char(1)"),
    ("cnaes_secundarios", "VARCHAR", "text"),
    ("natureza_juridica", "VARCHAR", "char(4)"), ("natureza_juridica_descricao", "VARCHAR", "text"),
    ("qualificacao_responsavel", "VARCHAR", "text"), ("qualificacao_responsavel_descricao", "VARCHAR", "text"),
    ("porte", "VARCHAR", "char(2)"), ("porte_descricao", "VARCHAR", "text"),
    ("capital_social", "DECIMAL(18,2)", "numeric(18,2)"),
    ("opcao_simples", "BOOLEAN", "boolean"), ("data_opcao_simples", "DATE", "date"),
    ("data_exclusao_simples", "DATE", "date"),
    ("opcao_mei", "BOOLEAN", "boolean"), ("data_opcao_mei", "DATE", "date"), ("data_exclusao_mei", "DATE", "date"),
    ("data_inicio_atividade", "DATE", "date"),
    ("situacao_cadastral", "VARCHAR", "char(2)"), ("situacao_cadastral_descricao", "VARCHAR", "text"),
    ("data_situacao_cadastral", "DATE", "date"),
    ("motivo_situacao_cadastral", "VARCHAR", "text"), ("motivo_situacao_cadastral_descricao", "VARCHAR", "text"),
    ("situacao_especial", "VARCHAR", "text"), ("data_situacao_especial", "DATE", "date"),
    ("uf", "VARCHAR", "char(2)"), ("municipio_rfb", "VARCHAR", "char(4)"), ("municipio_ibge", "VARCHAR", "char(7)"),
    ("municipio_nome", "VARCHAR", "text"),
    ("n_estabelecimentos_rfb", "INTEGER", "integer"), ("n_estabelecimentos_ativos_rfb", "INTEGER", "integer"),
    ("pessoa_fisica_vinculada", "BOOLEAN", "boolean"),
    ("regime_ultimo", "VARCHAR", "text"), ("regime_ultimo_ano", "INTEGER", "integer"),
    ("regimes_por_ano", "VARCHAR", "text"), ("regime_derivado", "BOOLEAN", "boolean"),
    ("ref_rfb", "VARCHAR", "char(7)"),
]
SCHEMA_VERSION = 2          # bump when COLUMNS change: built buckets with another version are rebuilt
COLUMN_NAMES = [c for c, _, _ in COLUMNS]


def _date(c: str) -> str:
    return f"TRY_CAST(try_strptime(NULLIF(NULLIF(trim({c}), ''), '00000000'), '%Y%m%d') AS DATE)"


def _code(c: str, width: int) -> str:
    return f"NULLIF(lpad(trim({c}), {width}, '0'), repeat('0', {width}))"


def _sql_bucket(s: Settings, k: int) -> str:
    e = s.enrich_dir
    codes = e / "codes"

    def code_tbl(name: str) -> str:
        p = codes / f"{name}.parquet"
        return f"read_parquet({q(p)})" if p.exists() else "(SELECT NULL::VARCHAR AS codigo, NULL::VARCHAR AS descricao LIMIT 0)"
    ibge_p = codes / "municipios_ibge.parquet"
    ibge = (f"read_parquet({q(ibge_p)})" if ibge_p.exists()
            else "(SELECT NULL::VARCHAR AS codigo_tom, NULL::VARCHAR AS codigo_ibge, NULL::VARCHAR AS nome_ibge LIMIT 0)")
    sec = sql_map(CNAE_SECTION)
    reg_p = s.regime_dir / "regime.parquet"
    has_reg = reg_p.exists()
    reg_src = (f"SELECT cnpj_raiz, ano, forma_tributacao FROM read_parquet({q(reg_p)}) "
               f"WHERE kb = {k} AND cnpj_da_scp IS NULL AND ano IS NOT NULL" if has_reg else
               "SELECT NULL::VARCHAR AS cnpj_raiz, NULL::INTEGER AS ano, NULL::VARCHAR AS forma_tributacao LIMIT 0")
    derived = (f"CASE WHEN rs.cnpj_raiz IS NULL AND j.opcao_simples THEN '{REGIME_SIMPLES_DERIVADO}' END" if has_reg
               else "NULL::VARCHAR")
    derived_flag = ("CASE WHEN rs.cnpj_raiz IS NOT NULL THEN false WHEN j.opcao_simples THEN true END" if has_reg
                    else "NULL::BOOLEAN")
    return f"""
WITH keys AS (SELECT * FROM read_parquet({q(s.collect_keys / f'kb={k:03d}' / 'keys.parquet')}) WHERE cnpj IS NOT NULL),
est_raw AS (
    SELECT *, cnpj_basico || lpad(cnpj_ordem, 4, '0') || lpad(cnpj_dv, 2, '0') AS cnpj14, filename AS _src
    FROM read_parquet({q(e / 'estabelecimentos' / '*.parquet')}, filename = true) WHERE kb = {k}),
est AS (SELECT * FROM est_raw
        QUALIFY row_number() OVER (PARTITION BY cnpj14 ORDER BY {_date('data_situacao')} DESC NULLS LAST, _src DESC) = 1),
est_root AS (SELECT cnpj_basico, COUNT(*)::INTEGER AS n_est,
                    COUNT(*) FILTER (WHERE {_code('situacao_cadastral', 2)} = '02')::INTEGER AS n_ativos
             FROM est GROUP BY 1),
emp AS (SELECT * FROM read_parquet({q(e / 'empresas' / '*.parquet')}, filename = true) WHERE kb = {k}
        QUALIFY row_number() OVER (PARTITION BY cnpj_basico ORDER BY filename DESC) = 1),
simp AS (SELECT * FROM read_parquet({q(e / 'simples' / '*.parquet')}, filename = true) WHERE kb = {k}
         QUALIFY row_number() OVER (PARTITION BY cnpj_basico ORDER BY filename DESC) = 1),
cnae AS (SELECT lpad(codigo, 7, '0') AS codigo, descricao FROM {code_tbl('cnaes')}),
nat AS (SELECT lpad(codigo, 4, '0') AS codigo, descricao FROM {code_tbl('naturezas')}),
qual AS (SELECT lpad(codigo, 2, '0') AS codigo, descricao FROM {code_tbl('qualificacoes')}),
mot AS (SELECT lpad(codigo, 2, '0') AS codigo, descricao FROM {code_tbl('motivos')}),
mun AS (SELECT lpad(codigo, 4, '0') AS codigo, descricao FROM {code_tbl('municipios')}),
ibge AS (SELECT codigo_tom, codigo_ibge, nome_ibge FROM {ibge}),
reg AS (SELECT cnpj_raiz, ano, string_agg(DISTINCT forma_tributacao, ' + ' ORDER BY forma_tributacao) AS formas
        FROM ({reg_src}) GROUP BY 1, 2),
rs AS (SELECT cnpj_raiz, arg_max(formas, ano) AS regime_ultimo, max(ano)::INTEGER AS regime_ultimo_ano,
              string_agg(ano::VARCHAR || ':' || formas, '; ' ORDER BY ano) AS regimes_por_ano
       FROM reg GROUP BY 1),
j AS (
    SELECT k.cnpj, k.cnpj_raiz, k.dv_valido, k.cnpj_alfanumerico,
           CASE WHEN est.cnpj14 IS NOT NULL THEN 'estabelecimento'
                WHEN emp.cnpj_basico IS NOT NULL OR er.cnpj_basico IS NOT NULL THEN 'raiz'
                ELSE 'nao_encontrado' END AS match_nivel,
           {sql_map(MATRIZ_FILIAL)}[trim(est.matriz_filial)] AS matriz_filial,
           {_code('est.cnae_principal', 7)} AS cnae_principal,
           NULLIF(trim(regexp_replace(COALESCE(est.cnae_secundaria, ''), '\\s', '', 'g')), '') AS cnaes_secundarios,
           {_code('emp.natureza_juridica', 4)} AS natureza_juridica,
           {_code('emp.qualificacao_responsavel', 2)} AS qualificacao_responsavel,
           lpad(trim(emp.porte), 2, '0') AS porte,
           TRY_CAST(replace(replace(trim(emp.capital_social), '.', ''), ',', '.') AS DECIMAL(18,2)) AS capital_social,
           -- not in the Simples file = not an option (false), not unknown; the dates stay NULL
           CASE WHEN simp.cnpj_basico IS NULL THEN false
                ELSE CASE upper(trim(simp.opcao_simples)) WHEN 'S' THEN true WHEN 'N' THEN false END END AS opcao_simples,
           {_date('simp.data_opcao_simples')} AS data_opcao_simples,
           {_date('simp.data_exclusao_simples')} AS data_exclusao_simples,
           CASE WHEN simp.cnpj_basico IS NULL THEN false
                ELSE CASE upper(trim(simp.opcao_mei)) WHEN 'S' THEN true WHEN 'N' THEN false END END AS opcao_mei,
           {_date('simp.data_opcao_mei')} AS data_opcao_mei,
           {_date('simp.data_exclusao_mei')} AS data_exclusao_mei,
           {_date('est.data_inicio_atividade')} AS data_inicio_atividade,
           lpad(trim(est.situacao_cadastral), 2, '0') AS situacao_cadastral,
           {_date('est.data_situacao')} AS data_situacao_cadastral,
           lpad(trim(est.motivo_situacao), 2, '0') AS motivo_situacao_cadastral,
           NULLIF(trim(est.situacao_especial), '') AS situacao_especial,
           {_date('est.data_situacao_especial')} AS data_situacao_especial,
           NULLIF(upper(trim(est.uf)), '') AS uf,
           {_code('est.municipio', 4)} AS municipio_rfb,
           er.n_est AS n_estabelecimentos_rfb, er.n_ativos AS n_estabelecimentos_ativos_rfb
    FROM keys k
    LEFT JOIN est ON est.cnpj14 = k.cnpj
    LEFT JOIN emp ON emp.cnpj_basico = k.cnpj_raiz
    LEFT JOIN simp ON simp.cnpj_basico = k.cnpj_raiz
    LEFT JOIN est_root er ON er.cnpj_basico = k.cnpj_raiz)
SELECT j.cnpj, j.cnpj_raiz, j.dv_valido, j.cnpj_alfanumerico, j.match_nivel, j.matriz_filial,
       j.cnae_principal, cnae.descricao AS cnae_principal_descricao,
       left(j.cnae_principal, 2) AS cnae_divisao, {sec}[left(j.cnae_principal, 2)] AS cnae_secao,
       j.cnaes_secundarios, j.natureza_juridica, nat.descricao AS natureza_juridica_descricao,
       j.qualificacao_responsavel, qual.descricao AS qualificacao_responsavel_descricao,
       j.porte, {sql_map(PORTE)}[j.porte] AS porte_descricao, j.capital_social,
       j.opcao_simples, j.data_opcao_simples, j.data_exclusao_simples,
       j.opcao_mei, j.data_opcao_mei, j.data_exclusao_mei, j.data_inicio_atividade,
       j.situacao_cadastral, {sql_map(SITUACAO)}[j.situacao_cadastral] AS situacao_cadastral_descricao,
       j.data_situacao_cadastral, j.motivo_situacao_cadastral, mot.descricao AS motivo_situacao_cadastral_descricao,
       j.situacao_especial, j.data_situacao_especial, j.uf, j.municipio_rfb,
       ibge.codigo_ibge AS municipio_ibge, COALESCE(ibge.nome_ibge, mun.descricao) AS municipio_nome,
       j.n_estabelecimentos_rfb, j.n_estabelecimentos_ativos_rfb,
       (COALESCE(j.opcao_mei, false) OR COALESCE({PF_NATUREZAS_SQL.replace('natureza_juridica', 'j.natureza_juridica')}, false))
           AS pessoa_fisica_vinculada,
       COALESCE(rs.regime_ultimo, {derived}) AS regime_ultimo, rs.regime_ultimo_ano, rs.regimes_por_ano,
       {derived_flag} AS regime_derivado,
       '{s.ref_rfb}' AS ref_rfb
FROM j
LEFT JOIN cnae ON cnae.codigo = j.cnae_principal
LEFT JOIN nat ON nat.codigo = j.natureza_juridica
LEFT JOIN qual ON qual.codigo = j.qualificacao_responsavel
LEFT JOIN mot ON mot.codigo = j.motivo_situacao_cadastral
LEFT JOIN mun ON mun.codigo = j.municipio_rfb
LEFT JOIN ibge ON ibge.codigo_tom = j.municipio_rfb
LEFT JOIN rs ON rs.cnpj_raiz = j.cnpj_raiz
ORDER BY j.cnpj"""


def run(s: Settings, state: State, force: bool = False) -> Path:
    state.pin("key_buckets", s.key_buckets)
    out_dir = s.build_dir / "cnpj_enriquecido"
    con = connect(s)
    try:
        for kind in ("estabelecimentos", "empresas", "simples"):
            if not list((s.enrich_dir / kind).glob("*.parquet")):
                raise SystemExit(f"build: no enrich output for {kind}; run `enrich` first")
        rsig = regime_signature(state)
        if rsig == "none":
            log.warning("build: no regime_tributario output; regime_* columns will be NULL (run `regime` to fill them)")
        for k in range(s.key_buckets):
            if not (s.collect_keys / f"kb={k:03d}" / "keys.parquet").exists():
                continue
            def work(tmp: Path, k=k) -> tuple[int, dict]:
                con.execute(f"COPY ({_sql_bucket(s, k)}) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)")
                n, u = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM read_parquet({q(tmp)})").fetchone()
                if n != u:
                    raise RuntimeError(f"build kb={k}: {n} rows but {u} distinct CNPJs")
                return n, {"kb": k, "schema": SCHEMA_VERSION, "regime": rsig}
            unit = f"kb={k:03d}"
            stale = _stale(state, "build", unit, schema=SCHEMA_VERSION, regime=rsig)
            run_unit(state, "build", unit, out_dir / f"{unit}.parquet", work, force=force or stale)
        report(s, con, out_dir)
        write_data_manifest(s, con, out_dir)
    finally:
        con.close()
    return out_dir


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _symbolic(s: Settings, p: Path) -> str:
    """Path written as $DATA_ROOT/... or $CNPJ_PIPELINE_WORKDIR/...: the manifest is in git, local paths are not."""
    root = data_root()
    if root is not None:
        try:
            return "$DATA_ROOT/" + p.resolve().relative_to(root.resolve()).as_posix() + "/"
        except ValueError:
            pass
    return "$CNPJ_PIPELINE_WORKDIR/" + p.resolve().relative_to(s.workdir).as_posix() + "/"


def write_data_manifest(s: Settings, con, out_dir: Path) -> Path | None:
    """Git-tracked manifest of the derived table (docs/ESTRUTURA.md §9, decision D6).

    Only counts, hashes and provenance: no CNPJ, no name, no value, no local absolute path, no salt.
    """
    dest = manifest_dir()
    if dest is None:
        return None
    files = []
    for p in sorted(out_dir.glob("*.parquet")):
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(p)})").fetchone()[0]
        files.append({"arquivo": p.name, "sha256": _sha256(p), "linhas": n})
    rfb = json.loads(s.manifest.read_text()).get("files", {}) if s.manifest.exists() else {}
    commit = None
    try:
        here = Path(__file__).resolve().parent
        commit = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=10).stdout.strip() or None
        dirty = subprocess.run(["git", "-C", str(here), "status", "--porcelain", "--", str(here)],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        if commit and dirty:
            commit += "-dirty"
    except (OSError, subprocess.SubprocessError):
        pass
    reg = s.regime_dir / "regime.parquet"
    man = {
        "nome": "cnpj_enriquecido",
        "versao": f"rfb-{s.ref_rfb}",
        "caminho": _symbolic(s, out_dir),
        "build_commit": commit,
        "schema_version": SCHEMA_VERSION,
        "fonte": {
            "ref_rfb": s.ref_rfb,
            "rfb_zips": {k: v.get("sha256") for k, v in sorted(rfb.items()) if v.get("status") == "ok"},
            "regime_tributario": _sha256(reg) if reg.exists() else None,
            "debito": "CNPJs distintos de DEBITO (DuckDB CEMEPI, somente leitura; só CNPJ_DEVEDOR/tributo/valor)",
        },
        "arquivos": files,
        "linhas": sum(f["linhas"] for f in files),
        "filtros": ["1 linha por CNPJ devedor (14 dígitos)", "socios ficam fora (só local)"],
    }
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"cnpj_enriquecido_{s.ref_rfb}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    log.info("build: data manifest (git) -> %s", path)
    return path


def report(s: Settings, con, out_dir: Path) -> None:
    t = f"read_parquet({q(out_dir / '*.parquet')})"
    kt = f"read_parquet({q(s.collect_keys / 'kb=*' / 'keys_tributo.parquet')})"
    n, u = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT cnpj) FROM {t}").fetchone()
    if n != u:
        raise RuntimeError(f"cnpj_enriquecido: {n} rows but {u} distinct CNPJs")
    cov = con.execute(f"""
        WITH x AS (SELECT kt.tributo, kt.valor_ref, kt.n_cda_ref, e.*
                   FROM {kt} kt JOIN {t} e USING (cnpj)
                   UNION ALL
                   SELECT 'TODOS' AS tributo, NULL, NULL, e.* FROM {t} e)
        SELECT tributo,
               COUNT(*) AS cnpjs_devedores,
               ROUND(100 * AVG(dv_valido::INT), 2) AS pct_dv_valido,
               ROUND(100 * AVG((match_nivel = 'estabelecimento')::INT), 2) AS pct_casado_cnpj14,
               ROUND(100 * AVG((match_nivel <> 'nao_encontrado')::INT), 2) AS pct_casado_raiz_ou_cnpj14,
               ROUND(100 * SUM(valor_ref) FILTER (WHERE match_nivel <> 'nao_encontrado') / NULLIF(SUM(valor_ref), 0), 2)
                   AS pct_estoque_ref_casado,
               ROUND(100 * AVG(pessoa_fisica_vinculada::INT), 2) AS pct_pessoa_fisica_vinculada,
               ROUND(100 * AVG((municipio_ibge IS NOT NULL)::INT) FILTER (WHERE match_nivel = 'estabelecimento'), 2)
                   AS pct_municipio_ibge
        FROM x GROUP BY tributo ORDER BY tributo = 'TODOS', tributo""").fetchall()
    cols = ["tributo", "cnpjs_devedores", "pct_dv_valido", "pct_casado_cnpj14", "pct_casado_raiz_ou_cnpj14",
            "pct_estoque_ref_casado", "pct_pessoa_fisica_vinculada", "pct_municipio_ibge"]
    with (s.build_dir / "coverage.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols); w.writerows(cov)
    situ = con.execute(f"""SELECT COALESCE(situacao_cadastral_descricao, '(sem estabelecimento)'), COUNT(*)
                           FROM {t} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    lines = [f"# Cobertura do enriquecimento CNPJ (RFB {s.ref_rfb})", "",
             f"Tabela `cnpj_enriquecido`: {n:,} CNPJs devedores distintos (1 linha por CNPJ, sem duplicatas).", "",
             "Universo: CNPJs de devedores PJ na tabela DEBITO (todas as fotos, todos os tributos). "
             "`pct_estoque_ref_casado` usa o estoque (VALOR_SEM_HONORARIOS) da foto de referência "
             f"{s.ref_rfb}; CNPJs fora dessa foto não pesam nessa coluna. Um CNPJ que deve mais de um tributo "
             "aparece em cada linha de tributo, por isso as linhas não somam TODOS.", "",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join("" if v is None else (f"{v:,}" if isinstance(v, int) else str(v)) for v in r) + " |"
              for r in cov]
    lines += ["", "## Situação cadastral (RFB) dos CNPJs devedores", "", "| situação | CNPJs |", "|---|---|"]
    lines += [f"| {a} | {b:,} |" for a, b in situ]
    (s.build_dir / "coverage.md").write_text("\n".join(lines) + "\n")
    log.info("build: %d rows; coverage report -> %s", n, s.build_dir / "coverage.md")
