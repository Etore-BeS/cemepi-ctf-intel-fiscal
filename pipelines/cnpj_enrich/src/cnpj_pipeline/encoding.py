"""Source encoding of the RFB CSVs and the streaming transcoder used while extracting them.

The RFB open CNPJ files (and the ECF regime files) are ISO-8859-1 written by Windows systems, i.e. Windows-1252 in
practice. DuckDB's `encoding='latin-1'` reader refuses ANY byte 0x80-0x9F ("File is not latin-1 encoded"), and the
real 2026-03 release has such bytes (Estabelecimentos0: e-mail = 0x8F 0x8F on one line; Estabelecimentos2: one 0x8F).
So every member is decoded here as cp1252 and written as UTF-8, and DuckDB reads UTF-8:
  * 0x00-0x7F and 0xA0-0xFF -> the same code point as Latin-1 (identical to what DuckDB's latin-1 reader produced);
  * 0x80-0x9F defined in cp1252 (e.g. 0x96 '–', 0x80 '€') -> that character, counted as `c1_mapped`;
  * 0x81, 0x8D, 0x8F, 0x90, 0x9D (undefined in cp1252) -> U+FFFD, counted as `c1_replaced`.
No line is dropped or added (newline, quote and ';' bytes are ASCII and untouched). Counts only, never content.
"""
from __future__ import annotations

import re
from typing import BinaryIO

SOURCE_ENCODING = "cp1252"
CHUNK = 1 << 22                                   # 4 MiB: bounded RAM; single-byte source, so no split characters
_C1 = re.compile(rb"[\x80-\x9f]")
_UNDEFINED = frozenset(b"\x81\x8d\x8f\x90\x9d")


def transcode(src: BinaryIO, dst: BinaryIO, chunk: int = CHUNK) -> dict:
    """Stream src (cp1252 bytes) to dst (UTF-8). Returns byte counts and how many 0x80-0x9F bytes were changed."""
    n_in = n_out = mapped = replaced = 0
    while True:
        b = src.read(chunk)
        if not b:
            break
        n_in += len(b)
        hits = _C1.findall(b)
        if hits:
            bad = sum(1 for h in hits if h[0] in _UNDEFINED)
            replaced += bad
            mapped += len(hits) - bad
            out = b.decode(SOURCE_ENCODING, errors="replace").encode("utf-8")
        else:
            out = b.decode("latin-1").encode("utf-8")      # fast path, same result as cp1252 without C1 bytes
        dst.write(out)
        n_out += len(out)
    return {"source": SOURCE_ENCODING, "bytes_in": n_in, "bytes_out": n_out, "c1_mapped": mapped,
            "c1_replaced": replaced}


def summarize(metas: list[dict]) -> dict:
    """Totals of several transcode() results (one per member) for a unit's meta."""
    keys = ("bytes_in", "bytes_out", "c1_mapped", "c1_replaced")
    return {"source": SOURCE_ENCODING, **{k: sum(int(m.get(k, 0)) for m in metas) for k in keys}}
