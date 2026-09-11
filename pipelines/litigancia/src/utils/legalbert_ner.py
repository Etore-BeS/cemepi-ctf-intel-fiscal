"""Optional LegalBERT NER recall layer for PF name redaction."""

from __future__ import annotations

from dataclasses import dataclass

from utils.pf_redaction import RedactionSpan

DEFAULT_MODEL_NAME = "dominguesm/legal-bert-ner-base-cased-ptbr"
PESSOA_LABEL = "PESSOA"


@dataclass
class LegalBertConfig:
    model_name: str = DEFAULT_MODEL_NAME
    max_length: int = 512
    min_entity_score: float = 0.75


def legalbert_available() -> bool:
    try:
        from transformers import pipeline  # noqa: F401
        return True
    except ImportError:
        return False


def _load_pipeline(config: LegalBertConfig):
    from transformers import pipeline

    return pipeline(
        "token-classification",
        model=config.model_name,
        aggregation_strategy="simple",
    )


def find_pessoa_spans(
    text: str,
    *,
    config: LegalBertConfig | None = None,
) -> list[RedactionSpan]:
    """Detect PESSOA entities with LegalBERT; requires optional nlp dependencies."""
    if not legalbert_available():
        msg = "transformers is not installed; use dependency group 'nlp'"
        raise ImportError(msg)

    cfg = config or LegalBertConfig()
    ner = _load_pipeline(cfg)
    entities = ner(text[: cfg.max_length])

    spans: list[RedactionSpan] = []
    for entity in entities:
        label = str(entity.get("entity_group") or entity.get("entity") or "")
        label = label.replace("B-", "").replace("I-", "")
        if label != PESSOA_LABEL:
            continue
        score = float(entity.get("score", 0.0))
        if score < cfg.min_entity_score:
            continue
        start = int(entity["start"])
        end = int(entity["end"])
        spans.append(
            RedactionSpan(
                start=start,
                end=end,
                label="pessoa",
                source="legalbert",
                confidence=score,
                original_text=text[start:end],
            )
        )
    return spans


def anonymize_with_legalbert(
    decisao: str,
    *,
    autores_raw: str | None = None,
    reus_raw: str | None = None,
    config: LegalBertConfig | None = None,
):
    from utils.pf_redaction import anonymize_decision

    extra_spans = find_pessoa_spans(decisao, config=config)
    return anonymize_decision(
        decisao,
        autores_raw=autores_raw,
        reus_raw=reus_raw,
        extra_spans=extra_spans,
    )
