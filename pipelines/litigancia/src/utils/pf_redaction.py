"""Deterministic PF redaction for court decision text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.party_identity import (
    is_natural_person,
    natural_person_parties,
    normalize_name,
    split_parties,
)

REDACTION_PLACEHOLDERS = {
    "cpf": "[CPF]",
    "rg": "[RG]",
    "oab": "[OAB]",
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "pf_name": "[PESSOA]",
    "pessoa": "[PESSOA]",
}

CPF_RE = re.compile(
    r"\b(?:CPF\s*(?:n[°º]\.?|n\.?)?\s*)?"
    r"(?:\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2})\b",
    re.IGNORECASE,
)
RG_RE = re.compile(
    r"\bRG\s*(?:n[°º]\.?|n\.?)?\s*(?:\d{1,2}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{1,2}|\d{5,12})\b",
    re.IGNORECASE,
)
OAB_RE = re.compile(
    r"\bOAB[/\s-]*(?:/SP|SP)?\s*\d[\d./-]{3,12}\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
)
# Brazilian phone: require label OR explicit DDD; separators hyphen/space only (not tab).
# Avoids false positives on CNJ metadata such as ".8.26.0319\t2021".
PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"(?:Tel(?:efone)?|Fone|Cel(?:ular)?|WhatsApp|Contato)\s*:?\s*"
    r"(?:\+55[\s-]*)?(?:\(?\d{2}\)?[\s-]*)?\d{4,5}[- ]\d{4}"
    r"|"
    r"(?:\+55[\s-]*)?(?:\(\d{2}\)\s+|\d{2}\s+)(?:9?\d{4}[- ]\d{4})"
    r")"
    r"(?!\d)",
    re.IGNORECASE,
)

DETERMINISTIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("cpf", CPF_RE),
    ("rg", RG_RE),
    ("oab", OAB_RE),
    ("email", EMAIL_RE),
    ("phone", PHONE_RE),
]

SPAN_PRIORITY = {
    "cpf": 50,
    "rg": 40,
    "oab": 40,
    "email": 30,
    "phone": 30,
    "pf_name": 20,
    "pessoa": 15,
}


@dataclass(frozen=True)
class RedactionSpan:
    start: int
    end: int
    label: str
    source: str
    confidence: float
    original_text: str


@dataclass
class RedactionResult:
    decisao_anonimizada: str
    redaction_spans: list[RedactionSpan] = field(default_factory=list)
    redaction_counts: dict[str, int] = field(default_factory=dict)


def _find_party_name_spans(text: str, party_names: list[str]) -> list[RedactionSpan]:
    spans: list[RedactionSpan] = []
    for name in party_names:
        if not is_natural_person(name):
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        for match in pattern.finditer(text):
            spans.append(
                RedactionSpan(
                    start=match.start(),
                    end=match.end(),
                    label="pf_name",
                    source="face_party",
                    confidence=1.0,
                    original_text=match.group(0),
                )
            )
    return spans


def _find_regex_spans(text: str) -> list[RedactionSpan]:
    spans: list[RedactionSpan] = []
    for label, pattern in DETERMINISTIC_RULES:
        for match in pattern.finditer(text):
            spans.append(
                RedactionSpan(
                    start=match.start(),
                    end=match.end(),
                    label=label,
                    source="regex",
                    confidence=1.0,
                    original_text=match.group(0),
                )
            )
    return spans


def _is_pj_name_span(span: RedactionSpan, pj_names: list[str]) -> bool:
    normalized_span = normalize_name(span.original_text)
    if not normalized_span:
        return False
    for name in pj_names:
        normalized_name = normalize_name(name)
        if not normalized_name:
            continue
        if normalized_name in normalized_span or normalized_span in normalized_name:
            return True
    return False


def _resolve_overlaps(spans: list[RedactionSpan]) -> list[RedactionSpan]:
    if not spans:
        return []

    ordered = sorted(
        spans,
        key=lambda span: (
            span.start,
            -(span.end - span.start),
            -SPAN_PRIORITY.get(span.label, 0),
            -span.confidence,
        ),
    )
    selected: list[RedactionSpan] = []
    for candidate in ordered:
        overlaps = any(
            not (candidate.end <= kept.start or candidate.start >= kept.end)
            for kept in selected
        )
        if overlaps:
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda span: span.start)


def _apply_spans(text: str, spans: list[RedactionSpan]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(REDACTION_PLACEHOLDERS.get(span.label, "[REDACTED]"))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _count_labels(spans: list[RedactionSpan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.label] = counts.get(span.label, 0) + 1
    return counts


def anonymize_decision(
    decisao: str,
    *,
    autores_raw: str | None = None,
    reus_raw: str | None = None,
    extra_spans: list[RedactionSpan] | None = None,
    pj_names: list[str] | None = None,
) -> RedactionResult:
    """Return a derived anonymized view; raw decisao is never mutated in-place."""
    pf_names = natural_person_parties(autores_raw, reus_raw)
    all_pj = [name for name in (pj_names or []) if name and not is_natural_person(name)]
    for raw in (autores_raw, reus_raw):
        for part in split_parties(raw):
            if not is_natural_person(part):
                all_pj.append(part)

    spans = _find_regex_spans(decisao)
    spans.extend(_find_party_name_spans(decisao, pf_names))
    if extra_spans:
        spans.extend(extra_spans)

    safe_spans: list[RedactionSpan] = []
    for span in spans:
        if span.label in {"pf_name", "pessoa"} and _is_pj_name_span(span, all_pj):
            continue
        safe_spans.append(span)

    resolved = _resolve_overlaps(safe_spans)
    return RedactionResult(
        decisao_anonimizada=_apply_spans(decisao, resolved),
        redaction_spans=resolved,
        redaction_counts=_count_labels(resolved),
    )


def build_redaction_validation_rows(
    cd_processo: str,
    id_processo: str,
    result: RedactionResult,
) -> list[dict[str, str | int | float]]:
    return [
        {
            "cd_processo": cd_processo,
            "id_processo": id_processo,
            "label": span.label,
            "source": span.source,
            "confidence": span.confidence,
            "start": span.start,
            "end": span.end,
            "original_text": span.original_text,
            "replacement": REDACTION_PLACEHOLDERS.get(span.label, "[REDACTED]"),
            "manual_label": "",
            "notes": "",
        }
        for span in result.redaction_spans
    ]
