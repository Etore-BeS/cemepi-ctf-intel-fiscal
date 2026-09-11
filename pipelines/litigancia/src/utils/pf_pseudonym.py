"""Stable pseudonymous IDs for natural persons in paper outputs."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from utils.party_identity import is_natural_person, normalize_name, split_parties

DEFAULT_SALT_ENV = "PF_HASH_SALT"
PF_ID_PREFIX = "pf_"
PF_ID_HEX_LEN = 16


@dataclass(frozen=True)
class PfPseudonymRecord:
    pf_id: str
    pf_role: str | None
    canonical_name: str


def _resolve_salt(salt: str | None = None) -> bytes:
    value = salt or os.getenv(DEFAULT_SALT_ENV) or "dev-only-change-for-publication"
    return value.encode("utf-8")


def canonical_pf_key(name: str, *, comarca: str | None = None) -> str:
    """Build canonical key for hashing; comarca optional scope disambiguation."""
    parts = [normalize_name(name)]
    if comarca:
        parts.append(normalize_name(comarca))
    return "|".join(p for p in parts if p)


def pf_id_from_name(
    name: str,
    *,
    comarca: str | None = None,
    salt: str | None = None,
) -> str:
    """Deterministic pseudonymous ID (HMAC-SHA256 prefix)."""
    key = canonical_pf_key(name, comarca=comarca)
    digest = hmac.new(_resolve_salt(salt), key.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{PF_ID_PREFIX}{digest[:PF_ID_HEX_LEN]}"


def infer_pf_role(name: str, autores_raw: str | None, reus_raw: str | None) -> str | None:
    normalized = normalize_name(name)
    autores = {normalize_name(n) for n in split_parties(autores_raw)}
    reus = {normalize_name(n) for n in split_parties(reus_raw)}
    in_autores = normalized in autores or any(normalized in a or a in normalized for a in autores if a)
    in_reus = normalized in reus or any(normalized in r or r in normalized for r in reus if r)
    if in_autores and not in_reus:
        return "autor"
    if in_reus and not in_autores:
        return "reu"
    if in_autores and in_reus:
        return "both"
    return None


def pseudonymize_face_parties(
    autores_raw: str | None,
    reus_raw: str | None,
    *,
    comarca: str | None = None,
    salt: str | None = None,
) -> list[PfPseudonymRecord]:
    """Return pseudonym records for PF parties only."""
    records: list[PfPseudonymRecord] = []
    seen: set[str] = set()
    for name in split_parties(autores_raw) + split_parties(reus_raw):
        if not is_natural_person(name):
            continue
        canonical = canonical_pf_key(name, comarca=comarca)
        if canonical in seen:
            continue
        seen.add(canonical)
        records.append(
            PfPseudonymRecord(
                pf_id=pf_id_from_name(name, comarca=comarca, salt=salt),
                pf_role=infer_pf_role(name, autores_raw, reus_raw),
                canonical_name=canonical,
            )
        )
    return records
