"""Tests for normalization review CSV builders (no lake required)."""

from __future__ import annotations

import polars as pl

from utils.normalization_review import (
    MOVIMENTACAO_COLUMNS,
    NAO_MAPEADO,
    CreditoRecuperado,
    ResultadoProcesso,
    build_round_slice,
    classify_sentenca,
    coverage_stats,
    enrich_movimentacao_review,
    enrich_sentenca_review,
    passes_quality_gates,
    propose_movimentacao_normalization,
)


def test_propose_movimentacao_uses_first_line() -> None:
    raw = "Conclusos para Sentença\nJuiz: Maria"
    assert propose_movimentacao_normalization(raw) == "Conclusos para Sentença"


def test_classify_sentenca_satisfacao() -> None:
    resultado, credito, confianca, _ = classify_sentenca(
        "Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação"
    )
    assert resultado == ResultadoProcesso.FAVORAVEL
    assert credito == CreditoRecuperado.SIM
    assert confianca == "alta"


def test_classify_sentenca_prescricao() -> None:
    resultado, credito, _, _ = classify_sentenca("Declarada Decadência ou Prescrição")
    assert resultado == ResultadoProcesso.DESFAVORAVEL
    assert credito == CreditoRecuperado.NAO


def test_classify_sentenca_unknown_is_inconclusivo() -> None:
    resultado, credito, confianca, justificativa = classify_sentenca("Proferidas Outras Decisões não Especificadas")
    assert resultado == ResultadoProcesso.INCONCLUSIVO
    assert credito == CreditoRecuperado.INCONCLUSIVO
    assert confianca == "baixa"
    assert "revisão manual" in justificativa


def test_enrich_movimentacao_review_columns() -> None:
    counts = pl.DataFrame(
        {
            "valor_bruto": ["Despacho", "Citação"],
            "frequencia": [80, 20],
            "exemplo_contexto": ["Despacho\nDetalhe extra", "Citação"],
        }
    )
    df = enrich_movimentacao_review(counts)
    assert list(df.columns) == list(MOVIMENTACAO_COLUMNS)
    assert df.height == 2
    assert df["percentual_base"].to_list() == [80.0, 20.0]
    assert df["normalizado_proposto"].to_list() == ["Despacho", "Citação"]
    assert "Detalhe extra" in df["exemplo_contexto"][0]
    assert df["normalizado_final"].unique().to_list() == [NAO_MAPEADO]


def test_enrich_sentenca_review_applies_rules() -> None:
    counts = pl.DataFrame(
        {
            "valor_bruto": [
                "Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação",
                "Proferidas Outras Decisões não Especificadas",
            ],
            "frequencia": [900, 100],
        }
    )
    df = enrich_sentenca_review(counts)
    assert df.filter(pl.col("valor_bruto").str.contains("Satisfação"))["credito_recuperado"][0] == "sim"
    assert df.filter(pl.col("valor_bruto").str.contains("Outras"))["resultado_processo"][0] == "inconclusivo"


def test_build_round_slice_top_coverage() -> None:
    df = pl.DataFrame(
        {
            "valor_bruto": ["a", "b", "c"],
            "frequencia": [50, 30, 20],
            "percentual_base": [50.0, 30.0, 20.0],
            "status_revisao": ["pendente", "pendente", "pendente"],
        }
    )
    round1 = build_round_slice(df, round_number=1, cumulative_coverage_pct=80.0)
    assert round1.height == 2
    assert round1["valor_bruto"].to_list() == ["a", "b"]


def test_coverage_stats_and_quality_gates() -> None:
    df = pl.DataFrame(
        {
            "valor_bruto": ["a", "b"],
            "frequencia": [60, 40],
            "percentual_base": [60.0, 40.0],
            "normalizado_final": ["MAPEADO", NAO_MAPEADO],
            "status_revisao": ["aprovado", "pendente"],
            "resultado_processo": ["favoravel", "inconclusivo"],
        }
    )
    stats = coverage_stats(df)
    assert stats["total_rows"] == 2
    assert stats["mapped_frequency_pct"] == 60.0
    assert stats["inconclusivo_frequency_pct"] == 40.0

    ok, failures = passes_quality_gates(stats, is_sentenca=True)
    assert not ok
    assert any("cobertura" in failure for failure in failures)


def test_coverage_stats_empty_frame() -> None:
    stats = coverage_stats(
        pl.DataFrame(
            {
                "frequencia": pl.Series([], dtype=pl.Int64),
                "normalizado_final": pl.Series([], dtype=pl.Utf8),
                "status_revisao": pl.Series([], dtype=pl.Utf8),
            }
        )
    )
    assert stats["total_rows"] == 0
    assert stats["mapped_frequency_pct"] == 0.0
