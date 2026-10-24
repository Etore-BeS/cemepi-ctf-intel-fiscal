"""CNPJ normalisation and check digits (numeric and the alphanumeric CNPJ of IN RFB 2.229/2024).

Rule (RFB): each of the first 12 characters is worth ord(char) - 48 (digits 0-9 -> 0-9, letters A-Z -> 17-42);
weights 5,4,3,2,9,8,7,6,5,4,3,2 for the first check digit and 6,5,4,3,2,9,8,7,6,5,4,3,2 for the second; remainder of
the weighted sum by 11; check digit = 0 if remainder < 2 else 11 - remainder. The two check digits are always numeric.
"""
from __future__ import annotations

import hashlib
import re

W1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
W2 = [6] + W1
_VALID = re.compile(r"[0-9A-Z]{12}[0-9]{2}")


def normalize(raw: str | None) -> str | None:
    """Uppercase, keep [0-9A-Z]; left-pad digit-only strings of 12-13 chars (lost leading zeros)."""
    if raw is None:
        return None
    s = re.sub(r"[^0-9A-Z]", "", str(raw).upper())
    if not s:
        return None
    if s.isdigit() and 12 <= len(s) < 14:
        s = s.zfill(14)
    return s


def _dv(chars: str, weights: list[int]) -> int:
    r = sum((ord(c) - 48) * w for c, w in zip(chars, weights)) % 11
    return 0 if r < 2 else 11 - r


def check_digits_ok(cnpj: str | None) -> bool:
    if not cnpj or not _VALID.fullmatch(cnpj) or len(set(cnpj)) == 1:
        return False
    d1 = _dv(cnpj[:12], W1)
    d2 = _dv(cnpj[:12] + str(d1), W2)
    return cnpj[12:] == f"{d1}{d2}"


def make_cnpj(base12: str) -> str:
    """Append the two check digits to a 12-character base (used by the synthetic tests)."""
    d1 = _dv(base12, W1)
    return base12 + str(d1) + str(_dv(base12 + str(d1), W2))


def key_bucket(raiz: str, n: int) -> int:
    """Stable bucket of an 8-character root; same formula as SQL_MACROS.kb (md5, not engine-specific hash())."""
    return int(hashlib.md5(raiz.encode()).hexdigest()[:8], 16) % n


# DuckDB macros (identical semantics to the Python functions above; tested against them).
SQL_MACROS = r"""
CREATE OR REPLACE MACRO _cnpj_clean(x) AS regexp_replace(upper(CAST(x AS VARCHAR)), '[^0-9A-Z]', '', 'g');
CREATE OR REPLACE MACRO cnpj_norm(x) AS
    CASE WHEN _cnpj_clean(x) = '' THEN NULL
         WHEN regexp_full_match(_cnpj_clean(x), '[0-9]{12,13}') THEN lpad(_cnpj_clean(x), 14, '0')
         ELSE _cnpj_clean(x) END;
CREATE OR REPLACE MACRO _cnpj_wsum(c, w) AS
    list_sum(list_transform(range(1, len(w) + 1),
             lambda i: (ascii(substr(c, CAST(i AS INTEGER), 1)) - 48) * w[i])) % 11;
CREATE OR REPLACE MACRO _cnpj_dv(c, w) AS CASE WHEN _cnpj_wsum(c, w) < 2 THEN 0 ELSE 11 - _cnpj_wsum(c, w) END;
CREATE OR REPLACE MACRO cnpj_dv_ok(c) AS
    COALESCE(regexp_full_match(c, '[0-9A-Z]{12}[0-9]{2}')
             AND len(list_distinct(string_split(c, ''))) > 1
             AND substr(c, 13, 1) = CAST(_cnpj_dv(substr(c, 1, 12), [5,4,3,2,9,8,7,6,5,4,3,2]) AS VARCHAR)
             AND substr(c, 14, 1) = CAST(_cnpj_dv(substr(c, 1, 13), [6,5,4,3,2,9,8,7,6,5,4,3,2]) AS VARCHAR),
             false);
CREATE OR REPLACE MACRO kb(raiz, n) AS CAST(('0x' || left(md5(raiz), 8)) AS UBIGINT) % n;
"""
