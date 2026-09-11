"""PF/PJ party classification helpers shared by CNPJ extraction and redaction."""

from __future__ import annotations

import re
import unicodedata

MISSING = "NÃO HÁ REGISTRO"

_PJ_INDICATOR_RE = re.compile(
    r"\b(?:"
    r"ltda\.?|ltd\.?|lt\.?\b|s\.?\s*a\.?|s/a|s/?c\b|me\b|epp\b|eireli|scp\b|ss\b|cia\.?|"
    r"companhia|empresa|ind[uú]stria|ind\b|com[eé]rcio|\bcom\b|servi[cç][oa]s?|"
    r"associa[cç][aã]o|associad\w*|funda[cç][aã]o|cooperativa|instituto|banco|"
    r"construtora|incorpora[dt]ora|empreendimento|imobili[aá]ria|"
    r"log[ií]stica|holding|participa[cç][oõ]es|tecnologia|"
    r"consultoria|assessoria|engenharia|transportes|solu[cç][oõ]es|"
    r"desenvolvimento|habitacional|urbano|prefeitura|munic[ií]pio|"
    r"fazenda|estado|governo|secretaria|autarquia|"
    r"superin\w+|depart\w+|diretoria|prfn|"
    r"uni[aã]o federal|importa[cç][aã]o|importacao|exp\b|"
    r"inform\w*|digital\w*|impress\w*|repres\w*|admin\w*|"
    r"alianc\w*|estrat\w*|mark\w*|system\b|sistem\w*"
    r")\b|&",
    re.IGNORECASE,
)


def normalize_name(text: str) -> str:
    if not text or text == MISSING:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.upper()
    normalized = re.sub(r"[^A-Z0-9 ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def is_natural_person(name: str | None) -> bool:
    """True when the party name has no legal-entity indicator (PF, not PJ)."""
    if not name:
        return False
    return not _PJ_INDICATOR_RE.search(name)


_CADASTRAL_CNPJ_PATTERNS = frozenset(
    {
        "debtor_address_block",
        "debtor_header_block",
        "debtor_executado_cnpj_first",
        "debtor_requerido",
        "debtor_executado_inline",
        "debtor_inline_dash",
        "debtor_name_comma_cnpj",
        "creditor_header_cnpj",
        "substitution_creditor",
        "payment_creditor",
        "debtor_contra",
    }
)


def should_reject_as_natural_person(
    *,
    party_syntax: str | None,
    candidate: str | None,
    pattern_id: str,
) -> bool:
    """Reject CNPJ only when every available party name looks like PF."""
    if pattern_id in _CADASTRAL_CNPJ_PATTERNS and party_syntax is None:
        return False

    names = [name for name in (party_syntax, candidate) if name]
    if not names:
        return False
    return all(is_natural_person(name) for name in names)


def split_parties(raw: str | None) -> list[str]:
    if raw is None or raw in ("", MISSING):
        return []
    return [part.strip() for part in raw.split(",") if part.strip() and part.strip() != MISSING]


def natural_person_parties(autores_raw: str | None, reus_raw: str | None) -> list[str]:
    """Return PF party names from FACE autores/reus lists."""
    names: list[str] = []
    for name in split_parties(autores_raw) + split_parties(reus_raw):
        if is_natural_person(name):
            names.append(name)
    return names
