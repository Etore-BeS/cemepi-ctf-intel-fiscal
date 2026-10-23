"""RFB CSVs are Latin-1/Windows-1252. DuckDB's latin-1 reader rejects any byte 0x80-0x9F ("File is not latin-1
encoded"): the real Estabelecimentos0 (2026-03) has e-mail = 0x8F 0x8F on one line. The pipeline transcodes every
member to UTF-8 while extracting (cp1252; the 5 bytes undefined in cp1252 -> U+FFFD), counts what it changed and
keeps every line. Synthetic data only."""
import io
import json
import shutil
import sqlite3
import zipfile

import duckdb
import pytest

from cnpj_pipeline.encoding import transcode
from cnpj_pipeline.enrich import _csv, extract_all
from helpers import cli
from synth import SENTINEL


def test_transcode_maps_cp1252_and_replaces_undefined():
    raw = b'"1";"A\xc7\xe3o";"\x96\x80\x93x\x94";"\x8f\x8f@x.com";"N\x00L"\n'
    out = io.BytesIO()
    meta = transcode(io.BytesIO(raw), out, chunk=7)              # tiny chunks: boundaries never split a char
    assert out.getvalue().decode("utf-8") == '"1";"AÇão";"–€“x”";"\ufffd\ufffd@x.com";"N\x00L"\n'
    assert meta == {"source": "cp1252", "bytes_in": len(raw), "bytes_out": len(out.getvalue()),
                    "c1_mapped": 4, "c1_replaced": 2}


def test_utf8_path_equals_duckdb_latin1_outside_c1(tmp_path):
    """Units already written by the old reader (DuckDB latin-1) are byte-identical for files without 0x80-0x9F."""
    skip = set(range(0x80, 0xA0)) | {0x0A, 0x0D, 0x22, 0x3B}
    val = bytes(b for b in range(1, 256) if b not in skip)
    raw = b'"1";"' + val + b'"\n'
    (tmp_path / "raw.csv").write_bytes(raw)
    with (tmp_path / "raw.csv").open("rb") as s, (tmp_path / "utf8.csv").open("wb") as d:
        transcode(s, d)
    con = duckdb.connect()
    old = con.execute(f"SELECT * FROM {_csv(tmp_path / 'raw.csv', ['a', 'b'], encoding='latin-1')}").fetchall()
    new = con.execute(f"SELECT * FROM {_csv(tmp_path / 'utf8.csv', ['a', 'b'])}").fetchall()
    assert old == new and len(new[0][1]) == len(val)


def test_extract_all_reuses_only_its_own_utf8_output(tmp_path):
    zp = tmp_path / "x.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("M.CSV", b'"1";"\x8f"\n')
    dest = tmp_path / "ex"; dest.mkdir()
    (dest / "M.CSV").write_bytes(b'"1";"\x8f"\n')                # raw extraction left by the previous version
    [p] = extract_all(zp, dest)
    assert p.read_bytes() == '"1";"\ufffd"\n'.encode()
    mark = json.loads((dest / "M.CSV.utf8.json").read_text())
    assert mark["c1_replaced"] == 1 and mark["bytes_in"] == 8
    m0 = p.stat().st_mtime_ns
    extract_all(zp, dest)
    assert p.stat().st_mtime_ns == m0                            # complete -> reused, not rewritten
    p.write_bytes(p.read_bytes()[:-1])                           # truncated (killed mid-write) -> redone
    extract_all(zp, dest)
    assert p.read_bytes() == '"1";"\ufffd"\n'.encode()
    assert not list(dest.glob("*.part"))


# ------------------------------------------------------------------------------------------------ end to end
def _rezip(src, dst, edit):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zi.infolist():
            zo.writestr(i.filename, edit(zi.read(i)))


def _once(old, new):
    def f(b):
        assert old in b
        return b.replace(old, new, 1)
    return f


def _setup(world, wd, poison):
    cli(wd, "collect", "--duckdb", str(world["db"]))
    rfb = wd / "rfb" / "2026-03"; rfb.mkdir(parents=True, exist_ok=True)
    for p in (world["release"] / "2026-03").iterdir():
        shutil.copy(p, rfb / p.name)
    for p in world["regime_share"].iterdir():
        shutil.copy(p, rfb / p.name)
    if poison:
        R = world["release"] / "2026-03"
        _rezip(R / "Estabelecimentos0.zip", rfb / "Estabelecimentos0.zip",       # the real case: e-mail 0x8F 0x8F
               _once(f'"{SENTINEL}@x.com"'.encode(), f'"\x8f\x8f{SENTINEL}@x.com"'.encode("latin-1")))
        _rezip(R / "Simples.zip", rfb / "Simples.zip",                           # non-debtor line with cp1252 bytes
               lambda b: b + b'"99999999";"N";"00000000";"00000000";"N";"00000000";"0\x96\x80"\n')
        _rezip(R / "Socios0.zip", rfb / "Socios0.zip", _once(b"JOS\xc9 \xc7\xc3O", b"JOS\xc9 \xc7\x8f\x96O"))
        _rezip(world["regime_share"] / "entidades-lucro-real.zip", rfb / "entidades-lucro-real.zip",
               lambda b: b.replace(b"forma_de_tributacao", b"forma_de_tributa\x87\x8f\x9do", 1))
    for st in ("enrich", "socios", "regime"):
        cli(wd, st)
    return wd


def _parquets(wd, sub):
    con = duckdb.connect()
    return sorted(con.execute(f"SELECT * FROM read_parquet('{wd}/{sub}/*.parquet') ORDER BY ALL").fetchall(), key=repr)


@pytest.fixture(scope="module")
def pair(world, tmp_path_factory):
    return (_setup(world, tmp_path_factory.mktemp("enc_clean") / "wd", False),
            _setup(world, tmp_path_factory.mktemp("enc_poison") / "wd", True))


def test_c1_bytes_keep_every_line_and_are_counted(pair):
    clean, poison = pair
    for sub in ("enrich/2026-03/estabelecimentos", "enrich/2026-03/empresas", "enrich/2026-03/simples",
                "regime_tributario/2026-03/parts"):
        a, b = _parquets(clean, sub), _parquets(poison, sub)
        assert a and a == b, sub                                 # same rows; the bytes hit only dropped columns
    a, b = _parquets(clean, "socios/2026-03/parts"), _parquets(poison, "socios/2026-03/parts")
    assert len(a) == len(b)
    assert sum("JOSÉ Ç\ufffd–O" in repr(r) for r in b) == 1     # the partner name is kept, decoded, counted
    meta = {u: json.loads(m) for st, u, m in sqlite3.connect(poison / "state.sqlite").execute(
        "SELECT stage, unit, meta FROM units WHERE status='done'") if (st, u) and m}
    enc = lambda u: next(v["encoding"] for k, v in meta.items() if k.startswith(u))
    assert (enc("Estabelecimentos0.zip")["c1_mapped"], enc("Estabelecimentos0.zip")["c1_replaced"]) == (0, 2)
    assert (enc("Simples.zip")["c1_mapped"], enc("Simples.zip")["c1_replaced"]) == (2, 0)
    assert (enc("Socios0.zip")["c1_mapped"], enc("Socios0.zip")["c1_replaced"]) == (1, 1)
    assert (enc("entidades-lucro-real.zip")["c1_mapped"], enc("entidades-lucro-real.zip")["c1_replaced"]) == (1, 2)
    assert enc("Empresas0.zip")["c1_mapped"] == enc("Empresas0.zip")["c1_replaced"] == 0
    assert not list((poison / "tmp" / "extract").rglob("*.part"))
