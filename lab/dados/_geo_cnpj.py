"""Agregados por município de SP dos CNPJs devedores (L.6, 27/10/2026). Só agregados; nada por CNPJ sai daqui.

Fontes (só leitura):
- DEBITO (cemepi.duckdb), foto de dez/2025: valor por CNPJ de 14 dígitos (uma linha por CDA, maior valor se repetida);
- build local do enriquecimento RFB 2026-03 (pipelines/cnpj_enrich): município IBGE de cada CNPJ (estabelecimento);
- arquivos abertos da RFB 2026-03 (Estabelecimentos*.zip): estabelecimentos ATIVOS por município em SP (denominador);
- tabela TOM (RFB) -> IBGE do próprio pipeline (enrich/2026-03/codes/municipios_ibge.parquet);
- malha municipal do IBGE (API de malhas v3, qualidade mínima), simplificada aqui.
As funções devolvem tabelas por município. Células com menos de MIN_N CNPJs devedores são suprimidas antes de sair.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import polars as pl

import _lab_base as B

MIN_N = 5
SNAP = (2025, 12)
REF_RFB = "2026-03"
HERE = Path(__file__).resolve().parent
MALHA = HERE / "geo" / "sp_municipios_ibge_minima.geojson"

VALOR_SQL = f"""
SELECT ID_DEBITO, TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE) AS valor,
       NULLIF(REGEXP_REPLACE(COALESCE(CNPJ_DEVEDOR, ''), '[^0-9]', '', 'g'), '') AS cnpj
FROM DEBITO
WHERE ANO_EXTRACAO = {SNAP[0]} AND MES_EXTRACAO = {SNAP[1]} AND CNPJ_DEVEDOR IS NOT NULL
"""


def _workdir() -> Path:
    from dotenv import dotenv_values
    return Path(dotenv_values(B.REPO / ".env")["CNPJ_PIPELINE_WORKDIR"])  # só esta chave do .env


def valor_por_cnpj() -> pl.DataFrame:
    """Valor da dívida por CNPJ (14 dígitos) na foto de dez/2025. Fica só no cache local; não é mostrado."""
    raw = B.collect(B.query("lab.geo:valor_cnpj14", VALOR_SQL, params={"snapshot": f"{SNAP[0]}-{SNAP[1]:02d}"}))
    return (raw.filter(pl.col("cnpj").str.len_chars() == 14)
            .sort("valor", descending=True, nulls_last=True).unique("ID_DEBITO", keep="first")
            .filter(pl.col("valor").is_not_null() & (pl.col("valor") > 0))
            .group_by("cnpj").agg(pl.len().alias("n_cda"), pl.col("valor").sum().alias("valor")))


def municipio_por_cnpj() -> pl.DataFrame:
    w = _workdir()
    return (pl.scan_parquet(str(w / f"build/{REF_RFB}/cnpj_enriquecido/*.parquet"))
            .select("cnpj", "uf", "municipio_ibge", "match_nivel").collect())


def _ativos_sp() -> pl.DataFrame:
    """Estabelecimentos ativos (situação 02) por código TOM em SP, lidos dos zips da RFB em streaming."""
    import pyarrow as pa
    import pyarrow.csv as pacsv
    w = _workdir() / "rfb" / REF_RFB
    cont: dict[str, int] = {}
    tot: dict[str, int] = {}
    for z in sorted(w.glob("Estabelecimentos*.zip")):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                with zf.open(name) as fh:
                    rd = pacsv.open_csv(fh, read_options=pacsv.ReadOptions(autogenerate_column_names=True, encoding="latin1",
                                                                         block_size=1 << 26),
                                        parse_options=pacsv.ParseOptions(delimiter=";", quote_char='"'),
                                        convert_options=pacsv.ConvertOptions(include_columns=["f5", "f19", "f20"],
                                                                             column_types={"f5": pa.string(), "f19": pa.string(),
                                                                                           "f20": pa.string()}))
                    for batch in rd:
                        d = pl.from_arrow(batch).filter(pl.col("f19") == "SP")
                        g = d.group_by("f20").agg(pl.len().alias("n"), (pl.col("f5") == "02").sum().alias("a"))
                        for mun, n, a in g.iter_rows():
                            tot[mun] = tot.get(mun, 0) + n
                            cont[mun] = cont.get(mun, 0) + a
    return pl.DataFrame({"codigo_tom": list(cont), "estab_ativos": [cont[k] for k in cont],
                         "estab_total": [tot[k] for k in cont]})


def ativos_sp() -> pl.DataFrame:
    return B.collect(B.cached("lab.geo:rfb_ativos_sp", _ativos_sp, params={"ref_rfb": REF_RFB, "uf": "SP", "situacao": "02"}))


def tom_ibge() -> pl.DataFrame:
    w = _workdir()
    return pl.read_parquet(w / f"enrich/{REF_RFB}/codes/municipios_ibge.parquet").filter(pl.col("uf") == "SP")


def tabela_municipios() -> tuple[pl.DataFrame, dict]:
    """Uma linha por município IBGE de SP: CNPJs devedores, valor (R$), estabelecimentos ativos, taxa por mil.
    Municípios com menos de MIN_N CNPJs devedores saem com as métricas nulas (suprimidas)."""
    v = valor_por_cnpj()
    m = municipio_por_cnpj()
    j = v.join(m, on="cnpj", how="left")
    info = {"cnpjs_foto": v.height, "valor_foto": float(v["valor"].sum()),
            "cnpjs_sem_cadastro": int(j["uf"].is_null().sum()),
            "cnpjs_fora_sp": int(((j["uf"] != "SP") & j["uf"].is_not_null()).sum()),
            "valor_fora_sp": float(j.filter((pl.col("uf") != "SP") & pl.col("uf").is_not_null())["valor"].sum()),
            "valor_sem_cadastro": float(j.filter(pl.col("uf").is_null())["valor"].sum())}
    sp = j.filter((pl.col("uf") == "SP") & pl.col("municipio_ibge").is_not_null())
    info["cnpjs_sp"] = sp.height
    info["valor_sp"] = float(sp["valor"].sum())
    agg = sp.group_by("municipio_ibge").agg(pl.len().alias("cnpjs"), pl.col("valor").sum().alias("valor"),
                                            pl.col("n_cda").sum().alias("cdas"))
    den = ativos_sp().join(tom_ibge().select("codigo_tom", "codigo_ibge", "nome_ibge"), on="codigo_tom", how="left")
    info["tom_sem_ibge"] = int(den["codigo_ibge"].is_null().sum())
    den = den.filter(pl.col("codigo_ibge").is_not_null()).group_by("codigo_ibge").agg(
        pl.col("nome_ibge").first(), pl.col("estab_ativos").sum())
    malha = json.loads(MALHA.read_text())
    cods = [f["properties"]["codarea"] for f in malha["features"]]
    t = (pl.DataFrame({"codigo_ibge": cods}).join(den, on="codigo_ibge", how="left")
         .join(agg.rename({"municipio_ibge": "codigo_ibge"}), on="codigo_ibge", how="left")
         .with_columns(pl.col("cnpjs").fill_null(0)))
    info["municipios"] = t.height
    info["municipios_suprimidos"] = int((t["cnpjs"] < MIN_N).sum())
    info["cnpjs_em_suprimidos"] = int(t.filter(pl.col("cnpjs") < MIN_N)["cnpjs"].sum())
    ok = pl.col("cnpjs") >= MIN_N
    t = t.with_columns(
        pl.when(ok).then(pl.col("cnpjs")).otherwise(None).alias("cnpjs"),
        pl.when(ok).then(pl.col("valor")).otherwise(None).alias("valor"),
        pl.when(ok).then(pl.col("cdas")).otherwise(None).alias("cdas"),
        pl.when(ok & (pl.col("estab_ativos") > 0)).then(1000 * pl.col("cnpjs") / pl.col("estab_ativos")).otherwise(None)
        .alias("por_mil_ativos"))
    return t.sort("codigo_ibge"), info


def malha_simplificada(decimais: int = 3) -> dict:
    """Malha do IBGE com coordenadas arredondadas (≈100 m) e pontos repetidos removidos, para caber no HTML."""
    g = json.loads(MALHA.read_text())

    def ring(r):
        out = []
        for x, y in r:
            p = [round(x, decimais), round(y, decimais)]
            if not out or p != out[-1]:
                out.append(p)
        if len(out) < 4:
            out = [[round(x, decimais), round(y, decimais)] for x, y in r]
        return out

    for f in g["features"]:
        geo = f["geometry"]
        if geo["type"] == "Polygon":
            geo["coordinates"] = [ring(r) for r in geo["coordinates"]]
        elif geo["type"] == "MultiPolygon":
            geo["coordinates"] = [[ring(r) for r in poly] for poly in geo["coordinates"]]
        f["properties"] = {"codarea": f["properties"]["codarea"]}
    return g
