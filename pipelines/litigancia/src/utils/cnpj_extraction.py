"""Syntax-first CNPJ extraction from court decision text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from utils.party_identity import (
    normalize_name,
    should_reject_as_natural_person,
    split_parties,
)

CNPJ_CANDIDATE_RE = re.compile(
    r"\b\d{2}[.]?\d{3}[.]?\d{3}/?\d{4}-?\d{2}\b",
    re.IGNORECASE,
)
CNPJ_DIGITS = r"\d{2}[.]?\d{3}[.]?\d{3}/?\d{4}-?\d{2}"

_CDA_LIST_RE = re.compile(r"CDA/Outros\s*n[ºo°.]+:\s*([\d ,]+)", re.I)
_SP_PROCESS_CDA_LIST_RE = re.compile(r"\b\d{14}(?:\s*,\s*\d{14})+\b")

DEFAULT_CONTEXT_CHARS = 120
DEFAULT_FACE_MATCH_THRESHOLD = 0.20

SYNTAX_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "payment_creditor",
        re.compile(
            rf"(?:creditad[oa]|levantamento|conta corrente)[^.]{{0,180}}?CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "autores",
    ),
    (
        "substitution_creditor",
        re.compile(
            rf"(?:Uni[aã]o Federal|Fazenda Nacional)[^.]{{0,50}}?CNPJ[:\s-]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "autores",
    ),
    (
        "creditor_header_cnpj",
        re.compile(
            rf"Exequente:\s*(?P<party>.{{5,120}}?),\s*CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})"
            rf"(?=[\s\S]*Executad)",
            re.I,
        ),
        "autores",
    ),
    (
        "debtor_header_block",
        re.compile(
            rf"Exequente:\s*.{{5,120}}?\s*Executad[oa]:\s*(?P<party>.{{3,120}}?)\s*,?\s*CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I | re.S,
        ),
        "reus",
    ),
    (
        "debtor_requerido",
        re.compile(
            rf"Requerid[oa]:\s*(?P<party>.{{3,120}}?)\s*CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "reus",
    ),
    (
        "debtor_executado_inline",
        re.compile(
            rf"Executad[oa]:\s*(?P<party>.{{3,120}}?)\s*,?\s*CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "reus",
    ),
    (
        "debtor_executado_cnpj_first",
        re.compile(
            rf"Executad[oa]:\s*CNPJ\s*:?\s*(?P<cnpj>{CNPJ_DIGITS})\s*(?P<party>[A-ZÀ-Ú].{{5,120}}?)(?=\s+Juiz|\s+Vistos|\s+C\s*O\s*N|\n|$)",
            re.I,
        ),
        "reus",
    ),
    (
        "debtor_contra",
        re.compile(rf"\bcontra\s+CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})", re.I),
        "reus",
    ),
    (
        "debtor_address_block",
        re.compile(
            rf"(?:CEP\s*\d{{5}}-?\d{{3}}[^C]{{0,60}}|com endereço[^C]{{10,120}})CNPJ[:\s]*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "reus",
    ),
    (
        "debtor_inline_dash",
        re.compile(
            rf"(?P<party>[A-ZÀ-Ú][^–—\n]{{5,120}}?)\s*[–—]\s*CNPJ\s*(?:n[°º]\.?)?\s*(?P<cnpj>{CNPJ_DIGITS})",
            re.I,
        ),
        "reus",
    ),
    (
        "debtor_name_comma_cnpj",
        re.compile(
            rf"(?P<party>[A-ZÀ-Ú][^,\n]{{8,160}}?),\s*CNPJ\s*(?P<cnpj>{CNPJ_DIGITS})(?!\s*,?\s*\d)",
            re.I,
        ),
        "reus",
    ),
]


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def format_cnpj(raw: str) -> str:
    digits = only_digits(raw)
    if len(digits) != 14:
        return raw
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def is_valid_cnpj(raw: str) -> bool:
    digits = only_digits(raw)
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    nums = [int(char) for char in digits]
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum1 = sum(n * w for n, w in zip(nums[:12], weights1, strict=True))
    remainder1 = 0 if sum1 % 11 < 2 else 11 - (sum1 % 11)
    if nums[12] != remainder1:
        return False
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    sum2 = sum(n * w for n, w in zip(nums[:13], weights2, strict=True))
    remainder2 = 0 if sum2 % 11 < 2 else 11 - (sum2 % 11)
    return nums[13] == remainder2


def is_cda_number(cnpj: str, snippet: str) -> bool:
    cnpj_digits = only_digits(cnpj)
    match = _CDA_LIST_RE.search(snippet)
    if match and cnpj_digits in only_digits(match.group(1)):
        return True
    for list_match in _SP_PROCESS_CDA_LIST_RE.finditer(snippet):
        if cnpj_digits in only_digits(list_match.group(0)):
            return True
    return False


def token_jaccard(left: str, right: str) -> float:
    left_tokens = set(normalize_name(left).split())
    right_tokens = set(normalize_name(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def snippet_around(
    text: str,
    start: int,
    end: int,
    width: int = DEFAULT_CONTEXT_CHARS,
) -> str:
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[lo:hi].replace("\n", " ")).strip()


def match_party_to_face(
    party_name: str | None,
    expected_pole: str,
    autores: list[str],
    reus: list[str],
) -> tuple[str | None, str | None, float]:
    if not party_name:
        return None, expected_pole, 0.0
    pool = autores if expected_pole == "autores" else reus
    best_name: str | None = None
    best_score = 0.0
    normalized_party = normalize_name(party_name)
    for name in pool:
        score = token_jaccard(party_name, name)
        normalized_name = normalize_name(name)
        if normalized_name[:12] and normalized_name[:12] in normalized_party:
            score = max(score, 0.5)
        if score > best_score:
            best_score, best_name = score, name
    return best_name, expected_pole, round(best_score, 3)


def jaccard_fallback(
    snippet: str,
    autores: list[str],
    reus: list[str],
) -> tuple[str | None, str | None, float]:
    best_name: str | None = None
    best_pole: str | None = None
    best_score = 0.0
    for name in autores:
        score = token_jaccard(snippet, name)
        if score > best_score:
            best_score, best_name, best_pole = score, name, "autores"
    for name in reus:
        score = token_jaccard(snippet, name)
        if score > best_score:
            best_score, best_name, best_pole = score, name, "reus"
    return best_name, best_pole, round(best_score, 3)


@dataclass
class SyntaxMatch:
    pattern_id: str
    expected_face_pole: str
    party_from_syntax: str | None
    method: str


def attribute_by_syntax(snippet: str, cnpj: str) -> SyntaxMatch | None:
    target = only_digits(cnpj)
    for pattern_id, pattern, pole in SYNTAX_PATTERNS:
        match = pattern.search(snippet)
        if not match or only_digits(match.group("cnpj")) != target:
            continue
        party = match.groupdict().get("party")
        party_clean = party.strip() if party else None
        return SyntaxMatch(
            pattern_id=pattern_id,
            expected_face_pole=pole,
            party_from_syntax=party_clean,
            method="syntax_first",
        )
    return None


def classify_confidence(
    *,
    method: str,
    face_match_score: float,
    candidate_party: str | None,
    face_match_threshold: float = DEFAULT_FACE_MATCH_THRESHOLD,
) -> str:
    if (
        method == "syntax_first"
        and candidate_party
        and face_match_score >= face_match_threshold
    ):
        return "high"
    if method == "syntax_first":
        return "medium"
    return "low"


@dataclass
class CnpjMention:
    cd_processo: str
    id_processo: str
    cnpj: str | None
    snippet: str
    syntax_pattern: str
    party_from_syntax: str | None
    expected_face_pole: str | None
    candidate_party: str | None
    face_pole: str | None
    match_score: float
    confidence: str
    method: str
    rejected_reason: str | None
    autores: str | None
    reus: str | None
    decision_preview: str


def extract_mentions(
    cd_processo: str,
    id_processo: str,
    decisao: str,
    autores_raw: str | None,
    reus_raw: str | None,
    *,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    face_match_threshold: float = DEFAULT_FACE_MATCH_THRESHOLD,
) -> list[CnpjMention]:
    autores = split_parties(autores_raw)
    reus = split_parties(reus_raw)
    mentions: list[CnpjMention] = []
    seen: set[tuple[str, str, int]] = set()

    for match in CNPJ_CANDIDATE_RE.finditer(decisao):
        raw = match.group(0)
        if not is_valid_cnpj(raw):
            continue
        cnpj = format_cnpj(raw)
        key = (cd_processo, cnpj, match.start())
        if key in seen:
            continue
        seen.add(key)

        snippet = snippet_around(decisao, match.start(), match.end(), width=context_chars)

        if is_cda_number(cnpj, snippet):
            mentions.append(
                CnpjMention(
                    cd_processo=cd_processo,
                    id_processo=id_processo,
                    cnpj=None,
                    snippet=snippet,
                    syntax_pattern="cda_number",
                    party_from_syntax=None,
                    expected_face_pole=None,
                    candidate_party=None,
                    face_pole=None,
                    match_score=0.0,
                    confidence="null",
                    method="rejected",
                    rejected_reason="cda_number",
                    autores=autores_raw,
                    reus=reus_raw,
                    decision_preview=decisao[:400].replace("\n", " "),
                )
            )
            continue

        syntax = attribute_by_syntax(snippet, cnpj)

        if syntax is not None:
            candidate, pole, score = match_party_to_face(
                syntax.party_from_syntax,
                syntax.expected_face_pole,
                autores,
                reus,
            )
            if syntax.pattern_id == "debtor_contra" and not candidate:
                candidate, pole, score = jaccard_fallback(snippet, [], reus)
            if (
                syntax.pattern_id == "debtor_address_block"
                and score < face_match_threshold
            ):
                fallback_name, fallback_pole, fallback_score = jaccard_fallback(
                    snippet, [], reus
                )
                if fallback_score > score:
                    candidate, pole, score = (
                        fallback_name,
                        fallback_pole,
                        fallback_score,
                    )
            method = syntax.method
            pattern_id = syntax.pattern_id
            party_syntax = syntax.party_from_syntax
            expected_pole = syntax.expected_face_pole
        else:
            candidate, pole, score = jaccard_fallback(snippet, autores, reus)
            method = "jaccard_fallback"
            pattern_id = "unclassified"
            party_syntax = None
            expected_pole = pole

        if should_reject_as_natural_person(
            party_syntax=party_syntax,
            candidate=candidate,
            pattern_id=pattern_id,
        ):
            mentions.append(
                CnpjMention(
                    cd_processo=cd_processo,
                    id_processo=id_processo,
                    cnpj=None,
                    snippet=snippet,
                    syntax_pattern=pattern_id,
                    party_from_syntax=party_syntax,
                    expected_face_pole=expected_pole,
                    candidate_party=candidate,
                    face_pole=pole,
                    match_score=score,
                    confidence="null",
                    method=method,
                    rejected_reason="natural_person",
                    autores=autores_raw,
                    reus=reus_raw,
                    decision_preview=decisao[:400].replace("\n", " "),
                )
            )
            continue

        confidence = classify_confidence(
            method=method,
            face_match_score=score,
            candidate_party=candidate,
            face_match_threshold=face_match_threshold,
        )
        mentions.append(
            CnpjMention(
                cd_processo=cd_processo,
                id_processo=id_processo,
                cnpj=cnpj,
                snippet=snippet,
                syntax_pattern=pattern_id,
                party_from_syntax=party_syntax,
                expected_face_pole=expected_pole,
                candidate_party=candidate,
                face_pole=pole,
                match_score=score,
                confidence=confidence,
                method=method,
                rejected_reason=None,
                autores=autores_raw,
                reus=reus_raw,
                decision_preview=decisao[:400].replace("\n", " "),
            )
        )
    return mentions
