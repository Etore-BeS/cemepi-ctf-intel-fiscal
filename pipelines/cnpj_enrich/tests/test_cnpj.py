import random

import duckdb

from cnpj_pipeline.cnpj import SQL_MACROS, check_digits_ok, key_bucket, make_cnpj, normalize


def test_known_examples():
    assert check_digits_ok("11222333000181")
    assert not check_digits_ok("11222333000182")
    assert check_digits_ok("12ABC34501DE35")          # RFB example of the alphanumeric CNPJ
    assert not check_digits_ok("00000000000000")
    assert normalize("11.222.333/0001-81") == "11222333000181"
    assert normalize("1222333000181") == "01222333000181"
    assert normalize("12.abc.345/01de-35") == "12ABC34501DE35"
    assert normalize("") is None and normalize(None) is None


def test_sql_macros_match_python():
    con = duckdb.connect(); con.execute(SQL_MACROS)
    rng = random.Random(3)
    vals = ["11.222.333/0001-81", "1222333000181", "12ABC34501DE35", "00000000000000", "abc", "123", None]
    for _ in range(300):
        b = "".join(rng.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(12))
        vals += [make_cnpj(b), b + f"{rng.randint(0, 99):02d}"]
    con.execute("CREATE TABLE v(x VARCHAR)"); con.executemany("INSERT INTO v VALUES (?)", [(v,) for v in vals])
    got = con.execute("SELECT x, cnpj_norm(x), cnpj_dv_ok(cnpj_norm(x)), kb(left(cnpj_norm(x), 8), 16) FROM v").fetchall()
    for x, n, ok, k in got:
        assert n == normalize(x), x
        assert ok == check_digits_ok(n), x
        if n:
            assert k == key_bucket(n[:8], 16)
