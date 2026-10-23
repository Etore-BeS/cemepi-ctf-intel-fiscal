"""Stage `socios`: partners (quadro societário) of the debtor firms, from the RFB Socios*.zip files. LOCAL LAYER ONLY.

Requested by the researcher for a partner <-> firm network analysis. Every column of the RFB layout is kept, including
the partner name and the CPF/CNPJ as published (the RFB masks the CPF: '***123456**'). Outputs live only in
<workdir>/socios/<ref>/ and are never exported with names or CPF (see export.py, `--with-socios`).

Units (atomic, state.run_unit):
  socios      'SociosN.zip'      one Parquet per zip in parts/ (streamed CSV, semi-join, bounded RAM)
  socios_rede 'socios.parquet'   one row per link (vínculo) + code descriptions + flags
              'socios_arestas.parquet'   edge list ready for a network (source = cnpj_basico)
  socios_hash 'socios_hashed.parquet'    optional (--hash-salt-env VAR): HMAC-SHA256 pseudonyms instead of names/CPF
Rows kept: cnpj_basico is a debtor root OR (partner is a legal entity AND the root of its CNPJ is a debtor root), i.e.
the debtors' partners plus the firms in which a debtor is a partner (second degree for the network).
A part is recomputed when its zip (sha256 in the manifest) or the debtor key set changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from .collect import keys_glob
from .config import Settings
from .db import connect, q
from .enrich import _csv, _extract, _rejects
from .layouts import CODE_COLS, FAIXA_ETARIA, IDENTIFICADOR_SOCIO, SOCIOS_COLS, SOCIOS_FILES, sql_map
from .state import State, run_unit

log = logging.getLogger("cnpj_pipeline")

MASK_WARNING = ("socios: the RFB publishes the partner CPF MASKED (***xxxxxx**, 6 middle digits). For natural persons "
                "the key 'nome + CPF mascarado' is only APPROXIMATE: two homonyms with the same middle digits are merged, "
                "and spelling variants of one person are split. CNPJ of legal-entity partners is complete and exact.")
NULL_REP = "***000000**"          # RFB placeholder for "no legal representative"


def manifest_sha(s: Settings, name: str) -> str | None:
    if not s.manifest.exists():
        return None
    return json.loads(s.manifest.read_text()).get("files", {}).get(name, {}).get("sha256")


def keys_signature(state: State) -> str:
    """Changes whenever the debtor key set is recollected."""
    u = state.units("collect_keys")
    return hashlib.md5(json.dumps([(a, b, c, json.loads(d or "{}").get("years")) for a, b, c, d in u]).encode()).hexdigest()


def _stale(state: State, stage: str, unit: str, **expect) -> bool:
    prev = state.get(stage, unit)
    if prev is None:
        return False
    meta = json.loads(prev[2] or "{}")
    return any(meta.get(k) != v for k, v in expect.items())


def code_rel(s: Settings, zname: str, width: int, extract_root: Path) -> str:
    zp = s.rfb_dir / zname
    if not zp.exists():
        log.warning("socios: %s missing; its descriptions will be null", zname)
        return "(SELECT NULL::VARCHAR AS codigo, NULL::VARCHAR AS descricao LIMIT 0)"
    csv = _extract(zp, extract_root / zname)
    return (f"(SELECT lpad(trim(codigo), {width}, '0') AS codigo, min(trim(descricao)) AS descricao "
            f"FROM {_csv(csv, CODE_COLS)} WHERE NULLIF(trim(codigo), '') IS NOT NULL GROUP BY 1)")


def _date(c: str) -> str:
    return f"TRY_CAST(try_strptime(NULLIF(NULLIF(trim({c}), ''), '00000000'), '%Y%m%d') AS DATE)"


def _code(c: str, width: int) -> str:
    return f"CASE WHEN NULLIF(trim({c}), '') IS NULL THEN NULL ELSE lpad(trim({c}), {width}, '0') END"


def run(s: Settings, state: State, files: list[str] | None = None, force: bool = False,
        hash_salt_env: str | None = None) -> Path:
    state.pin("key_buckets", s.key_buckets)
    if not list(s.collect_keys.glob("kb=*/keys.parquet")):
        raise SystemExit("socios: no debtor keys; run `collect` first")
    names = [f for f in (files or SOCIOS_FILES) if f in SOCIOS_FILES]
    absent = [f for f in names if not (s.rfb_dir / f).exists()]
    if absent:
        raise SystemExit(f"socios: {absent} not downloaded; run `download --socios` first")
    log.warning(MASK_WARNING)
    out, parts = s.socios_dir, s.socios_dir / "parts"
    extract_root = s.temp_dir / "extract" / s.ref_rfb
    ksig = keys_signature(state)
    con = connect(s)
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE roots AS SELECT DISTINCT cnpj_raiz FROM read_parquet({keys_glob(s)}) "
                    "WHERE cnpj_raiz IS NOT NULL")
        sel = ", ".join(f"trim({c}) AS {c}" for c in SOCIOS_COLS)
        for zname in names:
            stem = zname.removesuffix(".zip")
            final = parts / f"{stem}.parquet"
            sha = manifest_sha(s, zname)
            stale = _stale(state, "socios", zname, sha256=sha, keys=ksig)
            if not (force or stale) and state.is_done("socios", zname) and final.exists():
                log.info("[socios] %s: done, skip", zname)
                continue
            csv = _extract(s.rfb_dir / zname, extract_root / stem)

            def work(tmp: Path, csv=csv, zname=zname, sha=sha) -> tuple[int, dict]:
                con.execute(f"""
                COPY (
                    WITH src AS (SELECT {sel} FROM {_csv(csv, SOCIOS_COLS)}),
                    x AS (SELECT *, CASE WHEN identificador_socio = '1' THEN cnpj_norm(cpf_cnpj_socio) END AS socio_cnpj
                          FROM src)
                    SELECT x.*, left(x.socio_cnpj, 8) AS socio_cnpj_raiz,
                           r1.cnpj_raiz IS NOT NULL AS empresa_devedora,
                           r2.cnpj_raiz IS NOT NULL AS socio_pj_devedor,
                           '{zname}' AS arquivo_origem
                    FROM x
                    LEFT JOIN roots r1 ON r1.cnpj_raiz = x.cnpj_basico
                    LEFT JOIN roots r2 ON r2.cnpj_raiz = left(x.socio_cnpj, 8)
                    WHERE r1.cnpj_raiz IS NOT NULL OR r2.cnpj_raiz IS NOT NULL
                ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
                n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
                return n, {"zip": zname, "sha256": sha, "keys": ksig, "rejects": _rejects(con)}
            run_unit(state, "socios", zname, final, work, force=True)
            shutil.rmtree(csv.parent, ignore_errors=True)

        done = [(u, r, json.loads(m or "{}").get("sha256")) for u, st, r, m in state.units("socios") if st == "done"]
        psig = hashlib.md5(json.dumps(done + [ksig, s.key_buckets]).encode()).hexdigest()
        have = sorted(parts.glob("Socios*.parquet"))
        if not have:
            raise SystemExit("socios: no parts")
        idm, fx = sql_map(IDENTIFICADOR_SOCIO), sql_map(FAIXA_ETARIA)
        qual = code_rel(s, "Qualificacoes.zip", 2, extract_root / "codes_socios")
        pais = code_rel(s, "Paises.zip", 3, extract_root / "codes_socios")
        src = "read_parquet([" + ", ".join(q(p) for p in have) + "])"

        def rede_work(tmp: Path) -> tuple[int, dict]:
            con.execute(f"""
            COPY (
                WITH p AS (SELECT DISTINCT * FROM {src}),
                qual AS {qual}, pais AS {pais}
                SELECT p.cnpj_basico,
                       p.identificador_socio, {idm}[p.identificador_socio] AS identificador_socio_descricao,
                       NULLIF(p.nome_socio, '') AS nome_socio,
                       NULLIF(p.cpf_cnpj_socio, '') AS cpf_cnpj_socio,
                       p.socio_cnpj, p.socio_cnpj_raiz,
                       {_code('p.qualificacao_socio', 2)} AS qualificacao_socio, q1.descricao AS qualificacao_socio_descricao,
                       {_date('p.data_entrada_sociedade')} AS data_entrada_sociedade,
                       {_code('p.pais', 3)} AS pais, pa.descricao AS pais_descricao,
                       NULLIF(p.representante_legal, '') AS representante_legal,
                       NULLIF(p.nome_representante, '') AS nome_representante,
                       {_code('p.qualificacao_representante', 2)} AS qualificacao_representante,
                       q2.descricao AS qualificacao_representante_descricao,
                       NULLIF(p.faixa_etaria, '') AS faixa_etaria, {fx}[p.faixa_etaria] AS faixa_etaria_descricao,
                       p.empresa_devedora, p.socio_pj_devedor,
                       kb(p.cnpj_basico, {s.key_buckets}) AS kb, p.arquivo_origem, '{s.ref_rfb}' AS ref_rfb
                FROM p
                LEFT JOIN qual q1 ON q1.codigo = {_code('p.qualificacao_socio', 2)}
                LEFT JOIN qual q2 ON q2.codigo = {_code('p.qualificacao_representante', 2)}
                LEFT JOIN pais pa ON pa.codigo = {_code('p.pais', 3)}
                ORDER BY p.cnpj_basico, p.identificador_socio, p.cpf_cnpj_socio, p.nome_socio
            ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)""")
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
            return n, {"parts": psig}
        ran = run_unit(state, "socios_rede", "socios.parquet", out / "socios.parquet", rede_work,
                       force=force or _stale(state, "socios_rede", "socios.parquet", parts=psig))

        def edges_work(tmp: Path) -> tuple[int, dict]:
            con.execute(f"""
            COPY (
                SELECT cnpj_basico AS source,
                       CASE WHEN identificador_socio = '1' AND socio_cnpj IS NOT NULL THEN socio_cnpj
                            ELSE COALESCE(cpf_cnpj_socio, '') || '|' || upper(COALESCE(nome_socio, '')) END AS target,
                       CASE identificador_socio WHEN '1' THEN 'PJ' WHEN '2' THEN 'PF' WHEN '3' THEN 'ESTRANGEIRO'
                            ELSE 'DESCONHECIDO' END AS target_tipo,
                       NOT (identificador_socio = '1' AND socio_cnpj IS NOT NULL) AS target_chave_aproximada,
                       socio_cnpj_raiz AS target_raiz,
                       cpf_cnpj_socio AS target_documento, nome_socio AS target_nome,
                       qualificacao_socio AS tipo_vinculo, qualificacao_socio_descricao AS tipo_vinculo_descricao,
                       data_entrada_sociedade,
                       empresa_devedora AS source_devedora, socio_pj_devedor AS target_devedor, ref_rfb
                FROM read_parquet({q(out / 'socios.parquet')})
                ORDER BY source, target
            ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)""")
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
            return n, {"parts": psig}
        run_unit(state, "socios_rede", "socios_arestas.parquet", out / "socios_arestas.parquet", edges_work,
                 force=force or ran or _stale(state, "socios_rede", "socios_arestas.parquet", parts=psig))
        shutil.rmtree(extract_root / "codes_socios", ignore_errors=True)
        _summary(con, out)
        if hash_salt_env:
            hashed(s, state, con, hash_salt_env)
    finally:
        con.close()
    return out


def _summary(con, out: Path) -> None:
    t = f"read_parquet({q(out / 'socios.parquet')})"
    n, firms, deb, second, pj, pf, est, pjdev = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT cnpj_basico), COUNT(DISTINCT cnpj_basico) FILTER (WHERE empresa_devedora),
               COUNT(*) FILTER (WHERE NOT empresa_devedora),
               COUNT(*) FILTER (WHERE identificador_socio = '1'), COUNT(*) FILTER (WHERE identificador_socio = '2'),
               COUNT(*) FILTER (WHERE identificador_socio = '3'), COUNT(*) FILTER (WHERE socio_pj_devedor)
        FROM {t}""").fetchone()
    log.info("socios: %s links in %s firms (%s debtor roots with partners); by partner type PJ=%s PF=%s "
             "estrangeiro=%s; %s links where the partner is a debtor firm; %s second-degree links "
             "(non-debtor firm whose PJ partner is a debtor)", n, firms, deb, pj, pf, est, pjdev, second)
    log.warning(MASK_WARNING)


# ---------------------------------------------------------------------------------------------- pseudonyms
def hmac_pads(key: bytes) -> tuple[bytes, bytes]:
    """RFC 2104 inner/outer pads for HMAC-SHA256."""
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key.ljust(64, b"\0")
    return bytes(b ^ 0x36 for b in key), bytes(b ^ 0x5C for b in key)


def load_hmac_key(con, key: bytes) -> None:
    """Put the pads in a temp table via bound parameters, so the key never appears in SQL text, logs or errors."""
    ipad, opad = hmac_pads(key)
    con.execute("CREATE OR REPLACE TEMP TABLE _hk (ipad BLOB, opad BLOB)")
    con.execute("INSERT INTO _hk VALUES (?, ?)", [ipad, opad])


def hmac_sql(m: str) -> str:
    """HMAC-SHA256 hex of the VARCHAR expression m (needs load_hmac_key and `_hk` in the FROM clause);
    equals hmac.new(key, m.encode(), hashlib.sha256).hexdigest()."""
    return f"sha256(_hk.opad || unhex(sha256(_hk.ipad || encode({m}))))"


def read_salt(salt_env: str | None, who: str = "socios") -> bytes:
    """The salt only ever comes from an environment variable; it is never written, logged or stored."""
    if not salt_env:
        raise SystemExit(f"{who}: --hash-salt-env VAR is required (the salt is never hardcoded)")
    salt = os.environ.get(salt_env)
    if not salt:
        raise SystemExit(f"{who}: environment variable {salt_env} is empty or not set (the salt is never hardcoded)")
    if len(salt) < 16:
        raise SystemExit(f"{who}: the salt in {salt_env} is too short (use at least 16 random characters)")
    return salt.encode()


def person_key_sql(nome: str, doc: str) -> str:
    """Message hashed for a person: normalised name + '|' + the digits of the masked CPF (the 6 middle digits).
    Normalisation: no accents, upper case, single spaces, trimmed. Same person -> same id in every firm."""
    return (f"trim(regexp_replace(upper(strip_accents(COALESCE({nome}, ''))), '\\s+', ' ', 'g')) || '|' || "
            f"regexp_replace(COALESCE({doc}, ''), '[^0-9A-Za-z]', '', 'g')")


def hashed(s: Settings, state: State, con, salt_env: str) -> Path:
    load_hmac_key(con, read_salt(salt_env))
    h = hmac_sql
    src = s.socios_dir / "socios.parquet"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({q(src)})").fetchall()]
    keep = [c for c in cols if c not in ("nome_socio", "cpf_cnpj_socio", "representante_legal", "nome_representante")]
    socio_msg = person_key_sql("nome_socio", "cpf_cnpj_socio")
    rep_msg = person_key_sql("nome_representante", "representante_legal")

    def work(tmp: Path) -> tuple[int, dict]:
        con.execute(f"""
        COPY (
            SELECT {', '.join(keep)},
                   {h(socio_msg)} AS socio_id,
                   CASE WHEN COALESCE(nome_representante, '') = '' AND COALESCE(representante_legal, '') IN ('', '{NULL_REP}')
                        THEN NULL ELSE {h(rep_msg)} END AS representante_id,
                   COALESCE(CASE WHEN identificador_socio = '1' THEN socio_cnpj END, {h(socio_msg)}) AS target_id
            FROM read_parquet({q(src)}) CROSS JOIN _hk
        ) TO {q(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet({q(tmp)})").fetchone()[0]
        return n, {"salt_env": salt_env}            # the salt itself is never stored or logged
    try:
        run_unit(state, "socios_hash", "socios_hashed.parquet", s.socios_dir / "socios_hashed.parquet", work, force=True)
    finally:
        con.execute("DROP TABLE IF EXISTS _hk")
    log.info("socios: pseudonymised copy (HMAC-SHA256, salt from $%s) -> %s", salt_env,
             s.socios_dir / "socios_hashed.parquet")
    return s.socios_dir / "socios_hashed.parquet"
