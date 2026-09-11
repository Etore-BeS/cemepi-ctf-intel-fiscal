"""Build manual-review CSVs for tipo_movimentacao and tipo_sentença normalization.

Memory strategy: aggregate unique values in DuckDB over ``delta_scan`` (never
materialize full tables), then enrich the small frequency table in Polars.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl

NAO_MAPEADO = "__NAO_MAPEADO__"

STATUS_REVISAO_DEFAULT = "pendente"
RESULTADO_DEFAULT = "inconclusivo"
CREDITO_DEFAULT = "inconclusivo"
CONFIANCA_DEFAULT = "baixa"

DEFAULT_DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "3GB")
DEFAULT_DUCKDB_THREADS = int(os.getenv("DUCKDB_THREADS", "1"))

COVERAGE_TARGET_PCT = 95.0
INCONCLUSIVO_MAX_PCT = 10.0
ROUND1_COVERAGE_TARGET_PCT = 80.0

EXEMPLO_CONTEXTO_MAX_LEN = 240

# First line of tipo_movimentacao — review grain (full raw text has ~32M distinct values).
MOVIMENTACAO_FIRST_LINE_EXPR = (
    "trim(regexp_extract(replace(tipo_movimentacao, chr(13), ''), '^[^\\n]*'))"
)


class StatusRevisao(StrEnum):
    PENDENTE = "pendente"
    APROVADO = "aprovado"
    DESCARTAR = "descartar"
    DUVIDA = "duvida"


class ResultadoProcesso(StrEnum):
    FAVORAVEL = "favoravel"
    DESFAVORAVEL = "desfavoravel"
    INCONCLUSIVO = "inconclusivo"


class CreditoRecuperado(StrEnum):
    SIM = "sim"
    NAO = "nao"
    INCONCLUSIVO = "inconclusivo"


class ConfiancaRegra(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


MOVIMENTACAO_COLUMNS: tuple[str, ...] = (
    "valor_bruto",
    "frequencia",
    "percentual_base",
    "exemplo_contexto",
    "normalizado_proposto",
    "normalizado_final",
    "status_revisao",
    "observacoes",
    "revisor",
    "data_revisao",
)

SENTENCA_COLUMNS: tuple[str, ...] = (
    "valor_bruto",
    "frequencia",
    "percentual_base",
    "normalizado_proposto",
    "normalizado_final",
    "resultado_processo",
    "credito_recuperado",
    "confianca_regra",
    "status_revisao",
    "justificativa",
    "revisor",
    "data_revisao",
)

MOVIMENTACAO_EDITABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "normalizado_proposto",
        "normalizado_final",
        "status_revisao",
        "observacoes",
        "revisor",
        "data_revisao",
    }
)

SENTENCA_EDITABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "normalizado_proposto",
        "normalizado_final",
        "resultado_processo",
        "credito_recuperado",
        "confianca_regra",
        "status_revisao",
        "justificativa",
        "revisor",
        "data_revisao",
    }
)


@dataclass(frozen=True, slots=True)
class SentencaRule:
    pattern: re.Pattern[str]
    resultado: ResultadoProcesso
    credito: CreditoRecuperado
    confianca: ConfiancaRegra
    justificativa: str


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _duckdb_connect(*, memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET threads={DEFAULT_DUCKDB_THREADS}")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET memory_limit='{_escape_sql_literal(memory_limit)}'")
    con.execute("SET temp_directory='/tmp'")
    return con


def _rule(
    pattern: str,
    *,
    resultado: ResultadoProcesso,
    credito: CreditoRecuperado,
    confianca: ConfiancaRegra,
    justificativa: str,
) -> SentencaRule:
    return SentencaRule(
        pattern=re.compile(pattern, re.IGNORECASE),
        resultado=resultado,
        credito=credito,
        confianca=confianca,
        justificativa=justificativa,
    )


# Ordered rules: first match wins. Perspective: FESP as creditor in execução fiscal.
SENTENCA_RULES: tuple[SentencaRule, ...] = (
    _rule(
        r"satisfação da obrigação|pagamento integral do débito",
        resultado=ResultadoProcesso.FAVORAVEL,
        credito=CreditoRecuperado.SIM,
        confianca=ConfiancaRegra.ALTA,
        justificativa="Extinção por quitação do débito.",
    ),
    _rule(
        r"prescrição intercorrente|declarada decadência ou prescrição|extinta a punibilidade por prescrição",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.NAO,
        confianca=ConfiancaRegra.ALTA,
        justificativa="Extinção por prescrição/decadência sem recuperação.",
    ),
    _rule(
        r"cancelamento da dívida ativa|renúncia ao crédito",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.NAO,
        confianca=ConfiancaRegra.ALTA,
        justificativa="Crédito cancelado ou renunciado pelo credor.",
    ),
    _rule(
        r"sem resolução do mérito por desistência|autos extintos por renúncia",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.NAO,
        confianca=ConfiancaRegra.MEDIA,
        justificativa="Encerramento sem julgamento de mérito por desistência/renúncia.",
    ),
    _rule(
        r"devedor não encontrado|inexistência de bens penhoráveis",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.NAO,
        confianca=ConfiancaRegra.MEDIA,
        justificativa="Encerramento por impossibilidade prática de cobrança.",
    ),
    _rule(
        r"julgados procedentes os embargos à execução|julgada procedente a impugnação à execução",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.NAO,
        confianca=ConfiancaRegra.ALTA,
        justificativa="Defesa do executado acolhida integralmente.",
    ),
    _rule(
        r"julgados improcedentes os embargos à execução|julgada improcedente a impugnação à execução",
        resultado=ResultadoProcesso.FAVORAVEL,
        credito=CreditoRecuperado.INCONCLUSIVO,
        confianca=ConfiancaRegra.MEDIA,
        justificativa="Defesa rejeitada; recuperação depende de satisfação posterior.",
    ),
    _rule(
        r"homologad[oa].*transação|homologado o acordo",
        resultado=ResultadoProcesso.INCONCLUSIVO,
        credito=CreditoRecuperado.INCONCLUSIVO,
        confianca=ConfiancaRegra.BAIXA,
        justificativa="Acordo homologado; recuperação depende dos termos.",
    ),
    _rule(
        r"julgada procedente em parte|julgado procedente em parte|parcialmente procedente",
        resultado=ResultadoProcesso.INCONCLUSIVO,
        credito=CreditoRecuperado.INCONCLUSIVO,
        confianca=ConfiancaRegra.MEDIA,
        justificativa="Resultado parcial; exige leitura do dispositivo.",
    ),
    _rule(
        r"julgada improcedente a ação|julgado improcedente o pedido",
        resultado=ResultadoProcesso.FAVORAVEL,
        credito=CreditoRecuperado.INCONCLUSIVO,
        confianca=ConfiancaRegra.MEDIA,
        justificativa="Pedido do executado rejeitado; execução pode prosseguir.",
    ),
    _rule(
        r"julgada procedente a ação|julgado procedente o pedido",
        resultado=ResultadoProcesso.DESFAVORAVEL,
        credito=CreditoRecuperado.INCONCLUSIVO,
        confianca=ConfiancaRegra.BAIXA,
        justificativa="Pedido acolhido; desfecho econômico depende do contexto.",
    ),
)


def propose_movimentacao_normalization(valor_bruto: str) -> str:
    first_line = valor_bruto.split("\n", maxsplit=1)[0].strip()
    return first_line or NAO_MAPEADO


def classify_sentenca(valor_bruto: str) -> tuple[str, str, str, str]:
    for rule in SENTENCA_RULES:
        if rule.pattern.search(valor_bruto):
            return (
                rule.resultado,
                rule.credito,
                rule.confianca,
                rule.justificativa,
            )
    return (
        RESULTADO_DEFAULT,
        CREDITO_DEFAULT,
        CONFIANCA_DEFAULT,
        "Sem regra automática; revisão manual necessária.",
    )


def _log_progress(message: str) -> None:
    print(message, flush=True)


def _counts_to_polars(rows: list[tuple[str, ...]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(
            {"valor_bruto": [], "frequencia": [], "exemplo_contexto": []},
            schema={
                "valor_bruto": pl.Utf8,
                "frequencia": pl.Int64,
                "exemplo_contexto": pl.Utf8,
            },
        )
    if len(rows[0]) == 2:
        return pl.DataFrame(
            {"valor_bruto": [row[0] for row in rows], "frequencia": [row[1] for row in rows]},
            schema={"valor_bruto": pl.Utf8, "frequencia": pl.Int64},
        )
    return pl.DataFrame(
        {
            "valor_bruto": [row[0] for row in rows],
            "frequencia": [row[1] for row in rows],
            "exemplo_contexto": [row[2] for row in rows],
        },
        schema={
            "valor_bruto": pl.Utf8,
            "frequencia": pl.Int64,
            "exemplo_contexto": pl.Utf8,
        },
    )


def aggregate_movimentacao_counts(
    table_path: Path,
    *,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
    log: bool = False,
) -> pl.DataFrame:
    """Count by first line of ``tipo_movimentacao`` via DuckDB (OOM-safe).

    Full raw strings have tens of millions of distinct values; manual review
    uses the first line as the bucket key (~thousands of rows).
    """
    path_lit = _escape_sql_literal(str(table_path))
    if log:
        _log_progress(
            f"[movimentacao] aggregating by first line from {table_path} "
            f"(memory_limit={memory_limit})..."
        )
    t0 = time.perf_counter()
    con = _duckdb_connect(memory_limit=memory_limit)
    try:
        rows = con.execute(
            f"""
            SELECT
                {MOVIMENTACAO_FIRST_LINE_EXPR} AS valor_bruto,
                count(*)::BIGINT AS frequencia,
                arg_max(tipo_movimentacao, length(tipo_movimentacao)) AS exemplo_contexto
            FROM delta_scan('{path_lit}')
            WHERE tipo_movimentacao IS NOT NULL
              AND {MOVIMENTACAO_FIRST_LINE_EXPR} <> ''
            GROUP BY 1
            ORDER BY frequencia DESC
            """
        ).fetchall()
    finally:
        con.close()
    if log:
        elapsed = time.perf_counter() - t0
        _log_progress(f"[movimentacao] aggregation done: {len(rows):,} buckets in {elapsed:.1f}s")
    return _counts_to_polars([(str(v), int(f), str(ex)) for v, f, ex in rows])


def aggregate_sentenca_counts(
    table_path: Path,
    *,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
    log: bool = False,
) -> pl.DataFrame:
    """Unnest ``tipo_sentença`` JSON arrays and count via DuckDB (OOM-safe)."""
    path_lit = _escape_sql_literal(str(table_path))
    if log:
        _log_progress(
            f"[sentenca] aggregating from {table_path} (memory_limit={memory_limit})..."
        )
    t0 = time.perf_counter()
    con = _duckdb_connect(memory_limit=memory_limit)
    try:
        rows = con.execute(
            f"""
            SELECT
                tipos.val AS valor_bruto,
                count(*)::BIGINT AS frequencia
            FROM delta_scan('{path_lit}') AS src,
            LATERAL (
                SELECT unnest(
                    from_json(src."tipo_sentença", '["VARCHAR"]')
                ) AS val
            ) AS tipos
            WHERE src."tipo_sentença" IS NOT NULL
              AND tipos.val IS NOT NULL
              AND tipos.val <> ''
            GROUP BY 1
            ORDER BY frequencia DESC
            """
        ).fetchall()
    finally:
        con.close()
    if log:
        elapsed = time.perf_counter() - t0
        _log_progress(f"[sentenca] aggregation done: {len(rows):,} buckets in {elapsed:.1f}s")
    return _counts_to_polars([(str(v), int(f)) for v, f in rows])


def _add_percentual(df: pl.DataFrame) -> pl.DataFrame:
    total = int(df["frequencia"].sum()) if not df.is_empty() else 0
    if total == 0:
        return df.with_columns(pl.lit(0.0).alias("percentual_base"))
    return df.with_columns(
        (pl.col("frequencia").cast(pl.Float64) / total * 100).round(4).alias("percentual_base")
    )


def _empty_movimentacao_frame() -> pl.DataFrame:
    return pl.DataFrame({col: [] for col in MOVIMENTACAO_COLUMNS})


def _empty_sentenca_frame() -> pl.DataFrame:
    return pl.DataFrame({col: [] for col in SENTENCA_COLUMNS})


def enrich_movimentacao_review(df_counts: pl.DataFrame) -> pl.DataFrame:
    """Add manual-review columns to an already-aggregated frequency table."""
    if df_counts.is_empty():
        return _empty_movimentacao_frame()

    exemplo_source = (
        pl.col("exemplo_contexto")
        if "exemplo_contexto" in df_counts.columns
        else pl.col("valor_bruto")
    )
    return (
        _add_percentual(df_counts)
        .with_columns(
            exemplo_source.str.replace_all("\n", " ").str.slice(0, EXEMPLO_CONTEXTO_MAX_LEN).alias(
                "exemplo_contexto"
            ),
            pl.when(pl.col("valor_bruto").str.len_chars() > 0)
            .then(pl.col("valor_bruto"))
            .otherwise(pl.lit(NAO_MAPEADO))
            .alias("normalizado_proposto"),
            pl.lit(NAO_MAPEADO).alias("normalizado_final"),
            pl.lit(STATUS_REVISAO_DEFAULT).alias("status_revisao"),
            pl.lit("").alias("observacoes"),
            pl.lit("").alias("revisor"),
            pl.lit("").alias("data_revisao"),
        )
        .select(MOVIMENTACAO_COLUMNS)
    )


def _apply_sentenca_classification(df_counts: pl.DataFrame) -> pl.DataFrame:
    valores = df_counts["valor_bruto"].to_list()
    classificacoes = [classify_sentenca(v) for v in valores]
    return df_counts.with_columns(
        pl.Series("resultado_processo", [c[0] for c in classificacoes], dtype=pl.Utf8),
        pl.Series("credito_recuperado", [c[1] for c in classificacoes], dtype=pl.Utf8),
        pl.Series("confianca_regra", [c[2] for c in classificacoes], dtype=pl.Utf8),
        pl.Series("justificativa", [c[3] for c in classificacoes], dtype=pl.Utf8),
    )


def enrich_sentenca_review(df_counts: pl.DataFrame) -> pl.DataFrame:
    """Add manual-review columns to an already-aggregated frequency table."""
    if df_counts.is_empty():
        return _empty_sentenca_frame()

    return (
        _add_percentual(df_counts)
        .pipe(_apply_sentenca_classification)
        .with_columns(
            pl.col("valor_bruto").alias("normalizado_proposto"),
            pl.lit(NAO_MAPEADO).alias("normalizado_final"),
            pl.lit(STATUS_REVISAO_DEFAULT).alias("status_revisao"),
            pl.lit("").alias("revisor"),
            pl.lit("").alias("data_revisao"),
        )
        .select(SENTENCA_COLUMNS)
    )


def build_movimentacao_review(
    table_path: Path,
    *,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
    log: bool = False,
) -> pl.DataFrame:
    counts = aggregate_movimentacao_counts(table_path, memory_limit=memory_limit, log=log)
    if log:
        _log_progress("[movimentacao] enriching review columns...")
    return enrich_movimentacao_review(counts)


def build_sentenca_review(
    table_path: Path,
    *,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
    log: bool = False,
) -> pl.DataFrame:
    counts = aggregate_sentenca_counts(table_path, memory_limit=memory_limit, log=log)
    if log:
        _log_progress("[sentenca] enriching review columns...")
    return enrich_sentenca_review(counts)


def build_movimentacao_review_lazy(lf_mov: pl.LazyFrame) -> pl.DataFrame:
    """Legacy entrypoint: prefer ``build_movimentacao_review(path)`` for large tables."""
    df_counts = (
        lf_mov.select("tipo_movimentacao")
        .filter(pl.col("tipo_movimentacao").is_not_null())
        .group_by("tipo_movimentacao")
        .agg(pl.len().alias("frequencia"))
        .sort("frequencia", descending=True)
        .rename({"tipo_movimentacao": "valor_bruto"})
        .collect(engine="streaming")
    )
    return enrich_movimentacao_review(df_counts)


def build_sentenca_review_lazy(lf_face: pl.LazyFrame) -> pl.DataFrame:
    """Legacy entrypoint: prefer ``build_sentenca_review(path)`` for large tables."""
    df_counts = (
        lf_face.select("tipo_sentença")
        .filter(pl.col("tipo_sentença").is_not_null())
        .with_columns(pl.col("tipo_sentença").str.json_decode(dtype=pl.List(pl.Utf8)).alias("tipos"))
        .explode("tipos")
        .filter(pl.col("tipos").is_not_null() & (pl.col("tipos") != ""))
        .group_by("tipos")
        .agg(pl.len().alias("frequencia"))
        .sort("frequencia", descending=True)
        .rename({"tipos": "valor_bruto"})
        .collect(engine="streaming")
    )
    return enrich_sentenca_review(df_counts)


def _merge_manual_edits(
    fresh: pl.DataFrame,
    existing_path: Path,
    *,
    editable_columns: frozenset[str],
    key: str = "valor_bruto",
) -> pl.DataFrame:
    if not existing_path.exists():
        return fresh

    existing = pl.read_csv(existing_path, infer_schema_length=10_000)
    if existing.is_empty() or key not in existing.columns:
        return fresh

    preserve_cols = [col for col in editable_columns if col in existing.columns]
    if not preserve_cols:
        return fresh

    merged = fresh.join(
        existing.select([key, *preserve_cols]),
        on=key,
        how="left",
        suffix="_manual",
    )
    exprs: list[pl.Expr] = []
    for col in fresh.columns:
        manual_col = f"{col}_manual"
        if col in preserve_cols and manual_col in merged.columns:
            exprs.append(
                pl.when(pl.col(manual_col).is_not_null() & (pl.col(manual_col) != ""))
                .then(pl.col(manual_col))
                .otherwise(pl.col(col))
                .alias(col)
            )
        else:
            exprs.append(pl.col(col))
    return merged.select(exprs)


def coverage_stats(df: pl.DataFrame) -> dict[str, float | int]:
    if df.is_empty():
        return {
            "total_rows": 0,
            "total_frequency": 0,
            "mapped_frequency_pct": 0.0,
            "inconclusivo_frequency_pct": 0.0,
            "approved_rows": 0,
        }

    total_freq = int(df["frequencia"].sum())
    mapped_freq = (
        int(df.filter(pl.col("normalizado_final") != NAO_MAPEADO)["frequencia"].sum())
        if total_freq
        else 0
    )

    stats: dict[str, float | int] = {
        "total_rows": len(df),
        "total_frequency": total_freq,
        "mapped_frequency_pct": round(mapped_freq / total_freq * 100, 4) if total_freq else 0.0,
        "approved_rows": int(df.filter(pl.col("status_revisao") == StatusRevisao.APROVADO).height),
    }

    if "resultado_processo" in df.columns:
        inconclusivo_freq = int(
            df.filter(pl.col("resultado_processo") == ResultadoProcesso.INCONCLUSIVO)["frequencia"].sum()
        )
        stats["inconclusivo_frequency_pct"] = (
            round(inconclusivo_freq / total_freq * 100, 4) if total_freq else 0.0
        )
    else:
        stats["inconclusivo_frequency_pct"] = 0.0

    return stats


def passes_quality_gates(stats: dict[str, float | int], *, is_sentenca: bool) -> tuple[bool, list[str]]:
    failures: list[str] = []
    mapped_pct = float(stats["mapped_frequency_pct"])
    if mapped_pct < COVERAGE_TARGET_PCT:
        failures.append(
            f"cobertura por frequência {mapped_pct:.2f}% abaixo da meta de {COVERAGE_TARGET_PCT}%"
        )

    if is_sentenca:
        inconclusivo_pct = float(stats["inconclusivo_frequency_pct"])
        if inconclusivo_pct > INCONCLUSIVO_MAX_PCT:
            failures.append(
                f"inconclusivos {inconclusivo_pct:.2f}% acima do limite de {INCONCLUSIVO_MAX_PCT}%"
            )

    return not failures, failures


def export_review_csvs(
    *,
    movimentacoes_path: Path,
    face_path: Path,
    output_dir: Path,
    merge_existing: bool = True,
    memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT,
    log: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build and write both review CSVs, processing one table at a time."""
    output_dir.mkdir(parents=True, exist_ok=True)
    mov_path = output_dir / "manual_review_tipo_movimentacao.csv"
    sent_path = output_dir / "manual_review_tipo_sentenca.csv"

    if log:
        _log_progress(f"Output dir: {output_dir}")
        sys.stdout.flush()

    df_mov = build_movimentacao_review(movimentacoes_path, memory_limit=memory_limit, log=log)
    if merge_existing:
        if log:
            _log_progress(f"[movimentacao] merging manual edits from {mov_path}...")
        df_mov = _merge_manual_edits(df_mov, mov_path, editable_columns=MOVIMENTACAO_EDITABLE_COLUMNS)
    if log:
        _log_progress(f"[movimentacao] writing {mov_path} ({len(df_mov):,} rows)...")
    df_mov.write_csv(mov_path)
    if log:
        _log_progress(f"[movimentacao] done.")

    df_sent = build_sentenca_review(face_path, memory_limit=memory_limit, log=log)
    if merge_existing:
        if log:
            _log_progress(f"[sentenca] merging manual edits from {sent_path}...")
        df_sent = _merge_manual_edits(df_sent, sent_path, editable_columns=SENTENCA_EDITABLE_COLUMNS)
    if log:
        _log_progress(f"[sentenca] writing {sent_path} ({len(df_sent):,} rows)...")
    df_sent.write_csv(sent_path)
    if log:
        _log_progress("[sentenca] done.")

    return df_mov, df_sent


def build_round_slice(
    df: pl.DataFrame,
    *,
    round_number: Literal[1, 2, 3],
    cumulative_coverage_pct: float = ROUND1_COVERAGE_TARGET_PCT,
) -> pl.DataFrame:
    if df.is_empty():
        return df

    if round_number == 1:
        return (
            df.with_columns(
                (pl.col("percentual_base").cum_sum() - pl.col("percentual_base")).alias("_prev_cum")
            )
            .filter(pl.col("_prev_cum") < cumulative_coverage_pct)
            .drop("_prev_cum")
        )

    if round_number == 2:
        return df.filter(pl.col("status_revisao").is_in([STATUS_REVISAO_DEFAULT, StatusRevisao.PENDENTE]))

    if "resultado_processo" in df.columns:
        return df.filter(
            (pl.col("status_revisao") == StatusRevisao.DUVIDA)
            | (pl.col("resultado_processo") == ResultadoProcesso.INCONCLUSIVO)
        )
    return df.filter(pl.col("status_revisao") == StatusRevisao.DUVIDA)


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
