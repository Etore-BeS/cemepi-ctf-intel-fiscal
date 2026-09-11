"""Tests for bibliometric screening guardrails and criteria (Plano v2)."""

import json

from utils.bibliometric_screening import (
    CRITERIA_BY_BUSCA,
    DEFAULT_SCREENING_MODEL,
    ScreeningDecision,
    apply_json_guardrail,
    build_screening_agent,
    decision_to_record,
    merge_legacy_cache,
    load_cache,
)


def test_criteria_busca_a_has_six_codes():
    assert CRITERIA_BY_BUSCA["A"] == {
        "EC-A1",
        "EC-A2",
        "EC-A3",
        "EC-A4",
        "EC-A5",
        "EC-A6",
    }


def test_criteria_busca_b_has_four_codes():
    assert CRITERIA_BY_BUSCA["B"] == {
        "EC-B1",
        "EC-B2",
        "EC-B3",
        "EC-B4",
    }


def test_build_screening_agent_uses_pydantic_ai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    agent = build_screening_agent("instruções", DEFAULT_SCREENING_MODEL)
    assert agent is not None
    assert agent.model_settings is not None
    assert agent.model_settings.get("anthropic_cache_instructions") is True


def test_merge_legacy_cache(tmp_path):
    legacy = tmp_path / "legacy.jsonl"
    target = tmp_path / "target.jsonl"
    record = {
        "id_seq": 1,
        "reviewer": "ia1",
        "model": "claude-sonnet-4-6",
        "busca": "A",
        "decisao": "incluir",
        "criterio": None,
        "justificativa": "ok",
        "inconsistencia_json": False,
        "motivo_inconsistencia": None,
        "acao_guardrail": None,
    }
    legacy.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    added = merge_legacy_cache(target, [legacy], target_model=DEFAULT_SCREENING_MODEL)
    assert added == 1
    migrated = load_cache(target)[1]
    assert migrated["model"] == DEFAULT_SCREENING_MODEL
    assert migrated["migrated_from_model"] == "claude-sonnet-4-6"


def test_incluir_forces_null_criterio():
    record = decision_to_record(
        ScreeningDecision(
            decisao="incluir",
            criterio="EC-A1",
            justificativa="teste",
        ),
        "A",
        reviewer="ia1",
        model="claude-sonnet-4-6",
        id_seq=1,
    )
    assert record["decisao"] == "incluir"
    assert record["criterio"] is None
    assert record["inconsistencia_json"] is True


def test_excluir_requires_criterio():
    record = decision_to_record(
        ScreeningDecision(
            decisao="excluir",
            criterio="EC-A4",
            justificativa="idioma",
        ),
        "A",
        reviewer="ia1",
        model="claude-sonnet-4-6",
        id_seq=2,
    )
    assert record["decisao"] == "excluir"
    assert record["criterio"] == "EC-A4"
    assert record["inconsistencia_json"] is False


def test_busca_b_ec_b2_penal():
    record = decision_to_record(
        ScreeningDecision(
            decisao="excluir",
            criterio="EC-B2",
            justificativa="penal",
        ),
        "B",
        reviewer="ia1",
        model="claude-sonnet-4-6",
        id_seq=3,
    )
    assert record["criterio"] == "EC-B2"


def test_invalid_criterio_flagged():
    criterio, inconsistencia, motivo, _acao = apply_json_guardrail(
        "excluir",
        "EC-A99",
        CRITERIA_BY_BUSCA["A"],
    )
    assert criterio == "EC-A99"
    assert inconsistencia is True
    assert motivo == "criterio_ec_invalido"
