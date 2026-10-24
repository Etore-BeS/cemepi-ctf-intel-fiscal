"""Base comum das entradas do laboratório (L4) que reaproveitam o cache do paper EDA v1.

Por que existe: as entradas de 23/10/2026 (concentração, K-means, macro) usam o mesmo extrato ICMS,
o mesmo painel CDA x ano e o mesmo cache do paper EDA v1 (projects/macro/notebooks/articles/
paper_eda_v1.ipynb, células 0.1-0.4, 1.3, 1.4 e B.2.5). O código de chave de cache foi copiado sem
alteração para que as etapas já calculadas sejam reaproveitadas (HIT) em vez de reexecutadas.
Quando estabilizar, isto sobe para L3 (pipelines/divida_ativa), conforme docs/ESTRUTURA.md §4.

Regras:
- Caminhos vêm do .env da raiz do repositório (DATA_ROOT, CEMEPI_DATA_ROOT, CEMEPI_INTERIM,
  CEMEPI_CACHE, EDA_ARTIFACTS, EXTRACAO_ANO, EXTRACAO_MES). Só essas chaves são lidas; tokens e sal
  não entram no ambiente do kernel.
- Este módulo nunca lê NOME_DEVEDOR nem CPF_DEVEDOR. O extrato ICMS (só PJ) já existe no HD; se
  faltar, a execução para com mensagem, em vez de reextrair.
- Nada aqui imprime caminho local: print() das páginas passa por `scrub()`.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("POLARS_MAX_THREADS", "2")
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

_KEYS = ("DATA_ROOT", "CEMEPI_DATA_ROOT", "CEMEPI_INTERIM", "CEMEPI_CACHE", "EDA_ARTIFACTS",
         "EXTRACAO_ANO", "EXTRACAO_MES")


def find_repo(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for p in (start, *start.parents):
        if (p / "pyproject.toml").exists() and (p / "lab").exists():
            return p
    raise RuntimeError("Raiz do repositório não encontrada.")


REPO = find_repo()
_env = dotenv_values(REPO / ".env") if (REPO / ".env").exists() else {}
for _k in _KEYS:
    if _env.get(_k) and not os.environ.get(_k):
        os.environ[_k] = str(_env[_k])
del _env


def env_path(name: str, create: bool = False) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variável {name} ausente no .env da raiz (ver lab/README.md).")
    path = Path(value).expanduser()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise RuntimeError(f"{name} não existe: o HD está montado?")
    return path


DATA_ROOT = env_path("CEMEPI_DATA_ROOT")
DUCKDB_MAIN = DATA_ROOT / "api_ctf/cemepi.duckdb"
DUCKDB_MACRO = DATA_ROOT / "api_ctf/macro.duckdb"
INTERIM = env_path("CEMEPI_INTERIM")
CACHE = env_path("CEMEPI_CACHE")
EDA_ARTIFACTS = env_path("EDA_ARTIFACTS")
ICMS_DIR = INTERIM / "icms"
GARE_DIR = INTERIM / "gare_icms"
TMP = CACHE / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("POLARS_TEMP_DIR", str(TMP))

# --- saída sem caminhos locais -------------------------------------------------------------
_REPLACE = sorted({str(p): f"${n}" for n, p in (("CEMEPI_CACHE", CACHE), ("CEMEPI_INTERIM", INTERIM),
                                                 ("EDA_ARTIFACTS", EDA_ARTIFACTS), ("CEMEPI_DATA_ROOT", DATA_ROOT),
                                                 ("HOME", Path.home()))}.items(), key=lambda kv: -len(kv[0]))


def scrub(text: str) -> str:
    for a, b in _REPLACE:
        text = text.replace(a, b)
    return text


_print = builtins.print


def _safe_print(*args, **kw):
    _print(*(scrub(str(a)) for a in args), **kw)


builtins.print = _safe_print

# --- 0.3 cache (copiado do paper, sem alteração de chave) ----------------------------------
CACHE_DIR = CACHE / "results"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = CACHE_DIR / "manifest.jsonl"
CACHE_VERSION = "v2"
FORCE_ALL = os.environ.get("CEMEPI_FORCE_REFRESH", "0") == "1"
FORCE_STEPS = {s.strip() for s in os.environ.get("CEMEPI_FORCE_STEPS", "").split(",") if s.strip()}


def source_fingerprint() -> dict:
    return {f.name: [f.stat().st_size, int(f.stat().st_mtime)] for f in (DUCKDB_MAIN, DUCKDB_MACRO)}


SOURCE_FP = source_fingerprint()


def _canonical(obj) -> str:
    return json.dumps(obj or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _sha(text: str | None) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""


def key_from_parts(step, params, version, sql_sha, cache_version=CACHE_VERSION, source=None) -> str:
    payload = _canonical({"step": step, "params": params or {}, "version": str(version), "sql": sql_sha,
                          "cache_version": cache_version, "source": source or SOURCE_FP})
    return f"{step.replace('.', '_').replace(':', '-')}__{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def cache_key(step, params=None, version="1", sql=None) -> str:
    return key_from_parts(step, params, version, _sha(sql))


def _must_refresh(step, refresh=False) -> bool:
    return refresh or FORCE_ALL or step.split(":")[0] in FORCE_STEPS


def _log(step, key, path, params, version, sql, seconds) -> None:
    with MANIFEST.open("a") as fh:
        fh.write(json.dumps({"step": step, "key": key, "path": str(path), "params": params or {},
                             "version": str(version), "sql_sha": _sha(sql), "cache_version": CACHE_VERSION,
                             "source": SOURCE_FP, "seconds": round(seconds, 2),
                             "created": datetime.now(timezone.utc).isoformat()}, default=str) + "\n")


CACHE_LOG: list[tuple[str, str]] = []


def _report(step, hit) -> None:
    CACHE_LOG.append((step, "HIT" if hit else "MISS"))


def collect(lf) -> pl.DataFrame:
    return lf.collect(engine="streaming") if isinstance(lf, pl.LazyFrame) else lf


def duck(db: Path = DUCKDB_MAIN):
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    con.execute(f"SET memory_limit='1500MB'; SET threads=2; SET temp_directory='{TMP}'; "
                "SET preserve_insertion_order=false")
    return con


def cached(step, fn, params=None, version="1", sql=None, refresh=False, allow_empty=False) -> pl.LazyFrame:
    key = cache_key(step, params, version, sql)
    path = CACHE_DIR / f"{key}.parquet"
    hit = path.exists() and not _must_refresh(step, refresh)
    _report(step, hit)
    if hit:
        return pl.scan_parquet(path)
    t0, tmp = time.time(), path.with_suffix(".tmp.parquet")
    obj = fn()
    if isinstance(obj, pl.LazyFrame):
        obj.sink_parquet(tmp)
    else:
        obj.write_parquet(tmp)
    if not allow_empty and pl.scan_parquet(tmp).select(pl.len()).collect().item() == 0:
        tmp.unlink()
        raise ValueError(f"{step}: resultado vazio, não foi para o cache")
    tmp.replace(path)
    _log(step, key, path, params, version, sql, time.time() - t0)
    return pl.scan_parquet(path)


def cached_json(step, fn, params=None, version="1", refresh=False) -> Any:
    key = cache_key(step, params, version)
    path = CACHE_DIR / f"{key}.json"
    hit = path.exists() and not _must_refresh(step, refresh)
    _report(step, hit)
    if not hit:
        t0 = time.time()
        obj = fn()
        tmp = path.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(obj, default=str, indent=1))
        tmp.replace(path)
        _log(step, key, path, params, version, None, time.time() - t0)
    return json.loads(path.read_text())


def cached_memmap(step, n, build, params=None, version="1", refresh=False) -> np.memmap:
    params = {**(params or {}), "n": n}
    key = cache_key(step, params, version)
    path = CACHE_DIR / f"{key}.f32"
    hit = path.exists() and not _must_refresh(step, refresh)
    _report(step, hit)
    if not hit:
        tmp = path.with_suffix(".tmp")
        mm = np.memmap(tmp, dtype=np.float32, mode="w+", shape=(n, n))
        t0 = time.time()
        build(mm)
        mm.flush()
        del mm
        tmp.replace(path)
        _log(step, key, path, params, version, None, time.time() - t0)
    return np.memmap(path, dtype=np.float32, mode="r", shape=(n, n))


def query(step, sql, params=None, db=None, version="1", allow_empty=False) -> pl.LazyFrame:
    for banned in ("NOME_DEVEDOR", "CPF_DEVEDOR"):
        if banned in sql.upper():
            raise ValueError(f"{banned} não pode ser lido no laboratório")

    def run() -> pl.DataFrame:
        con = duck(db or DUCKDB_MAIN)
        try:
            return con.execute(sql).pl()
        finally:
            con.close()
    return cached(step, run, params, version=version, sql=sql, allow_empty=allow_empty)


def cache_path(step, sql, params=None, version="1") -> Path:
    return CACHE_DIR / cache_key(step, params, version, sql)


def cached_sql_parts(step, parts, params=None, version="1", refresh=False, allow_empty=False) -> pl.LazyFrame:
    import gc
    import duckdb
    sql_all = "\n;\n".join(f"-- {k}\n{v}" for k, v in sorted(parts.items()))
    out = cache_path(step, sql_all, params, version)
    done = out / "_DONE"
    hit = done.exists() and not _must_refresh(step, refresh)
    _report(step, hit)
    if not hit:
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        for name, sql in parts.items():
            part = out / f"part-{name}.parquet"
            if part.exists() and not _must_refresh(step, refresh):
                continue
            tmp = part.with_suffix(".tmp")
            con = duckdb.connect(config={"memory_limit": "1500MB", "threads": 2, "temp_directory": str(TMP),
                                         "preserve_insertion_order": False})
            try:
                con.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            finally:
                con.close()
                gc.collect()
            tmp.replace(part)
        if not allow_empty and pl.scan_parquet(out / "part-*.parquet").select(pl.len()).collect().item() == 0:
            raise ValueError(f"{step}: resultado vazio")
        done.write_text(cache_key(step, params, version, sql_all))
        _log(step, cache_key(step, params, version, sql_all), out, params, version, sql_all, time.time() - t0)
    return pl.scan_parquet(out / "part-*.parquet")


def parts_glob(step, parts, params=None, version="1") -> str:
    sql_all = "\n;\n".join(f"-- {k}\n{v}" for k, v in sorted(parts.items()))
    return str(cache_path(step, sql_all, params, version) / "part-*.parquet")


def scan_icms() -> pl.LazyFrame:
    return pl.scan_parquet(ICMS_DIR / "**/*.parquet", hive_partitioning=True)


def scan_gare() -> pl.LazyFrame:
    return pl.scan_parquet(GARE_DIR / "**/*.parquet", hive_partitioning=True)


# --- 0.4 semente e extração ------------------------------------------------------------------
SEED = 2026
np.random.seed(SEED)
SNAP_YEAR = int(os.environ.get("EXTRACAO_ANO", "2026"))
SNAP_MONTH = int(os.environ.get("EXTRACAO_MES", "3"))
EXTRACT = f"{SNAP_YEAR}-{SNAP_MONTH:02d}"
ENROL_MIN = date(1960, 1, 1)
ENROL_MAX = (date(SNAP_YEAR + SNAP_MONTH // 12, SNAP_MONTH % 12 + 1, 1) - timedelta(days=1))
YEARS = list(range(2016, SNAP_YEAR + 1))      # chave de cache do paper (inclui 2026)
LAST_FULL_YEAR = 2025                          # IMP-12: análises e gráficos do lab param em 2025


def plausible_enrol(lf: pl.LazyFrame) -> pl.LazyFrame:
    ok = pl.col("DATA_INSCRICAO").is_between(pl.lit(ENROL_MIN), pl.lit(ENROL_MAX))
    cols = [c for c in ("age", "enrol_year", "age_years") if c in lf.collect_schema().names()]
    return lf.with_columns(pl.when(ok).then(pl.col("DATA_INSCRICAO")).alias("DATA_INSCRICAO"),
                           *[pl.when(ok).then(pl.col(c)).alias(c) for c in cols])


# --- 1.3 extrato ICMS (só confere que existe; não reextrai) ----------------------------------
ICMS_TYPES = ["ICMS Declarado", "ICMS Autuação", "ICMS Autuação Simples Nacional",
              "ICMS Fecoep", "ICMS Alteração de GIA"]
EXTRACT_PARAMS = {"types": ICMS_TYPES, "snapshot": EXTRACT, "pj_only": True}
for _y in YEARS:
    for _d, _s in ((ICMS_DIR, "1.3"), (GARE_DIR, "1.3-gare")):
        _m = _d / f"year={_y}" / f"_KEY.{CACHE_VERSION}"
        if not (_m.exists() and _m.read_text() == cache_key(f"{_s}:{_y}", EXTRACT_PARAMS)):
            raise RuntimeError(f"Extrato {_s} {_y} ausente ou desatualizado: rode o paper EDA v1 (célula 1.3).")
        _report(f"{_s}:{_y}", True)


# --- 1.4 painel CDA x ano (SQL idêntico ao paper, para a chave bater) ------------------------
def icms_year(y: int) -> str:
    return f"read_parquet('{ICMS_DIR}/year={y}/*.parquet')"


ATTR_YEAR_SQL = """
SELECT ID_DEBITO,
       MIN(DATA_INSCRICAO) AS DATA_INSCRICAO,
       MIN(snap) AS first_snapshot, MAX(snap) AS last_snapshot,
       MIN(snap) FILTER (WHERE STATUS_AJUIZAMENTO_DEBITO = 'Ajuizado') AS first_filed_snapshot,
       MAX(VALOR_SEM_HONORARIOS) AS max_value,
       arg_max(TIPO_DEBITO, snap) AS TIPO_DEBITO, arg_max(ICMS_ORIGIN, snap) AS ICMS_ORIGIN,
       arg_max(CNPJ, snap) AS CNPJ, arg_max(CNPJ_ROOT, snap) AS CNPJ_ROOT
FROM (SELECT *, ANO_EXTRACAO * 100 + MES_EXTRACAO AS snap FROM {src})
GROUP BY ID_DEBITO
"""
ATTR_YEAR = {str(y): ATTR_YEAR_SQL.format(src=icms_year(y)) for y in YEARS}
attr_year = cached_sql_parts("1.4:attributes_by_year", ATTR_YEAR, params=EXTRACT_PARAMS)

BUCKETS = 8
ATTR_SQL = """
SELECT ID_DEBITO, MIN(DATA_INSCRICAO) AS DATA_INSCRICAO,
       arg_max(TIPO_DEBITO, last_snapshot) AS TIPO_DEBITO, arg_max(ICMS_ORIGIN, last_snapshot) AS ICMS_ORIGIN,
       arg_max(CNPJ, last_snapshot) AS CNPJ, arg_max(CNPJ_ROOT, last_snapshot) AS CNPJ_ROOT,
       MIN(first_snapshot) AS first_snapshot, MAX(last_snapshot) AS last_snapshot,
       MIN(first_filed_snapshot) AS first_filed_snapshot, MAX(max_value) AS max_value
FROM read_parquet('{src}') WHERE ID_DEBITO % {n} = {k}
GROUP BY ID_DEBITO
"""
ATTR = {f"b{k}": ATTR_SQL.format(src=parts_glob("1.4:attributes_by_year", ATTR_YEAR, EXTRACT_PARAMS), n=BUCKETS, k=k)
        for k in range(BUCKETS)}
attributes = plausible_enrol(cached_sql_parts("1.4:attributes", ATTR, params=EXTRACT_PARAMS))

YEAR_END_SQL = """
SELECT CAST({y} AS INTEGER) AS year, ID_DEBITO,
       arg_max(VALOR_SEM_HONORARIOS, MES_EXTRACAO) AS stock,
       arg_max(SITUACAO_DEBITO, MES_EXTRACAO) AS SITUACAO_DEBITO,
       arg_max(STATUS_AJUIZAMENTO_DEBITO, MES_EXTRACAO) AS STATUS_AJUIZAMENTO_DEBITO,
       MAX(MES_EXTRACAO) = 12 AS present_in_december
FROM {src}
GROUP BY ID_DEBITO
"""
YEAR_END = {str(y): YEAR_END_SQL.format(y=y, src=icms_year(y)) for y in YEARS}
year_end = cached_sql_parts("1.4:year_end", YEAR_END, params=EXTRACT_PARAMS)

GARE_YEAR = {"all": f"""
SELECT CAST(year AS INTEGER) AS year, ID_DEBITO,
       SUM(VALOR_RECEITA) AS paid, COUNT(*) AS n_payments,
       MIN(DATA_ARRECADACAO_GARE) AS first_payment_in_year
FROM read_parquet('{GARE_DIR}/**/*.parquet', hive_partitioning = true)
GROUP BY ALL
"""}
gare_year = cached_sql_parts("1.4:gare_year", GARE_YEAR, params=EXTRACT_PARAMS)

PANEL_SQL = """
WITH base AS (
    SELECT y.year, y.ID_DEBITO, y.stock, y.SITUACAO_DEBITO, y.STATUS_AJUIZAMENTO_DEBITO, y.present_in_december,
           COALESCE(g.paid, 0.0) AS paid, CAST(COALESCE(g.n_payments, 0) AS INTEGER) AS n_payments,
           g.first_payment_in_year, a.DATA_INSCRICAO, a.TIPO_DEBITO, a.ICMS_ORIGIN, a.CNPJ_ROOT
    FROM (SELECT * FROM read_parquet('{ye}') WHERE ID_DEBITO % {n} = {k}) y
    LEFT JOIN (SELECT * FROM read_parquet('{gy}') WHERE ID_DEBITO % {n} = {k}) g USING (year, ID_DEBITO)
    LEFT JOIN (SELECT * FROM read_parquet('{at}') WHERE ID_DEBITO % {n} = {k}) a USING (ID_DEBITO)
), rated AS (
    SELECT *, paid / NULLIF(stock, 0) AS rate,
           CAST(paid > 0 AS TINYINT) AS paid_flag,
           DATE_DIFF('day', DATA_INSCRICAO, MAKE_DATE(year, 12, 31)) / 365.25 AS age,
           YEAR(DATA_INSCRICAO) AS enrol_year
    FROM base
)
SELECT *,
       CASE WHEN LAG(year, 1) OVER w = year - 1 THEN LAG(rate, 1) OVER w END AS rate_l1,
       CASE WHEN LAG(year, 2) OVER w = year - 2 THEN LAG(rate, 2) OVER w END AS rate_l2,
       CASE WHEN LAG(year, 3) OVER w = year - 3 THEN LAG(rate, 3) OVER w END AS rate_l3
FROM rated
WINDOW w AS (PARTITION BY ID_DEBITO ORDER BY year)
"""
PANEL = {f"b{k}": PANEL_SQL.format(ye=parts_glob("1.4:year_end", YEAR_END, EXTRACT_PARAMS),
                                   gy=parts_glob("1.4:gare_year", GARE_YEAR, EXTRACT_PARAMS),
                                   at=parts_glob("1.4:attributes", ATTR, EXTRACT_PARAMS), n=BUCKETS, k=k)
         for k in range(BUCKETS)}
panel = plausible_enrol(cached_sql_parts("1.4:panel", PANEL, params=EXTRACT_PARAMS))


def cache_summary() -> pl.DataFrame:
    d = pl.DataFrame(CACHE_LOG, schema=["etapa", "status"], orient="row")
    return d.group_by("status").len().sort("status")


# --- estatística descritiva (paper 0.6) ------------------------------------------------------
def lorenz(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    cum = np.cumsum(v) / v.sum()
    share = np.arange(1, v.size + 1) / v.size
    gini = 1 - 2 * np.trapezoid(np.cumsum(v[::-1]) / v.sum(), dx=1 / v.size)
    return share, cum, float(gini)


def top_shares(values: np.ndarray, fractions=(0.001, 0.01, 0.1)) -> dict:
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    return {f"top_{f * 100:g}pct": float(v[: max(1, int(round(f * v.size)))].sum() / v.sum()) for f in fractions}


# --- figuras ---------------------------------------------------------------------------------
import plotly.graph_objects as go  # noqa: E402
import plotly.io as pio  # noqa: E402

CEMEPI = {"deep_teal": "#154C56", "teal": "#186D7F", "mid_teal": "#267586", "sand": "#BD9869",
          "green": "#5EA65B", "cream": "#FFF4E5", "grey": "#8A9499", "rust": "#8C4A3A", "orange": "#C47B3A"}
# Paleta categórica com tons bem distintos (a do paper usava deep_teal e teal lado a lado).
CAT = [CEMEPI[k] for k in ("deep_teal", "sand", "green", "rust", "grey", "orange")]
DASHES = ["solid", "dash", "dot", "dashdot", "longdash"]
pio.templates["cemepi_lab"] = go.layout.Template(layout=go.Layout(
    font=dict(family="Montserrat, Arial, sans-serif", size=12, color=CEMEPI["deep_teal"]),
    colorway=CAT, paper_bgcolor="white", plot_bgcolor="white",
    xaxis=dict(showgrid=False, zeroline=False, showline=True, linecolor=CEMEPI["deep_teal"], ticks="outside"),
    yaxis=dict(showgrid=True, gridcolor="#E6E6E6", zeroline=False, showline=True, linecolor=CEMEPI["deep_teal"],
               ticks="outside"),
    legend=dict(orientation="h", y=1.08, x=0)))
pio.templates.default = "cemepi_lab"


def color(i: int) -> str:
    return CAT[i % len(CAT)]


PROGRAMMES = [("PEP 2019", "2019-07-01", "2019-12-31"), ("Acordo Paulista 2024", "2024-04-01", "2024-12-31")]


def fmt_brl_bi(x: float) -> str:
    return f"R$ {x / 1e9:,.1f} bi".replace(",", "X").replace(".", ",").replace("X", ".")
