# Gerado por _gerar_paginas.py. Não editar à mão.
from __future__ import annotations
# ---- Cabeçalho do laboratório (antes das células 0.x do paper) --------------------------------
# Gerado junto com as páginas por _gerar_paginas.py. Não edite as páginas à mão: edite o gerador.
import os as _os, sys as _sys, re as _re, json as _json, hashlib as _hashlib
from pathlib import Path as _Path
_root = next(p for p in (_Path.cwd(), *_Path.cwd().parents) if (p / "pyproject.toml").exists() and (p / "lab").exists())
_sys.path.insert(0, str(_root / "lab" / "eda"))
import _lab_base as LB  # lê só as chaves de caminho do .env (sem sal nem token) e troca print() por versão sem caminhos locais

LAB_LAST_YEAR = LB.LAST_FULL_YEAR             # 2025: nada de 2026 nas análises (decisão de 23/10/2026)
LAB_SNAP = (LAB_LAST_YEAR, 12)                # foto de dezembro de 2025 no lugar da de março de 2026
LAB_EXTRACT = f"{LAB_SNAP[0]}-{LAB_SNAP[1]:02d}"
LAB_NS = "lab26v1:"                           # prefixo das etapas de cache recalculadas no laboratório
_os.environ.setdefault("CEMEPI_DUCKDB_MAIN", "api_ctf/cemepi.duckdb")
_os.environ.setdefault("CEMEPI_DUCKDB_MACRO", "api_ctf/macro.duckdb")
_os.environ.setdefault("CEMEPI_EXTRACT", "2026-03")   # extração congelada (chave do cache do extrato)
_out = LB.DATA_ROOT / "interim" / "paper_eda_lab"
_os.environ["CEMEPI_FIG_DIR"] = str(_out / "figures")
_os.environ["CEMEPI_TAB_DIR"] = str(_out / "tables")

# Selic over (SGS 4189) no lugar da meta (SGS 432): mesmo arquivo e hash da página de 23/10/2026
SELIC_OVER_FILE = LB.DATA_ROOT / "interim/macro_bcb/sgs_4189_baixado_2026-10-23.json"
SELIC_OVER_SHA = "2842d28c2258209be22e098c9a47d06eec7b5f6b566be3f064e251f91fa43d0a"

def _selic_over_raw(schema):
    import polars as _pl
    raw = SELIC_OVER_FILE.read_bytes()
    assert _hashlib.sha256(raw).hexdigest() == SELIC_OVER_SHA, "arquivo da SGS 4189 mudou"
    d = _pl.DataFrame(_json.loads(raw)).select(
        _pl.lit(4189).alias("codigo_serie"),
        _pl.col("data").str.to_date("%d/%m/%Y").alias("data"),
        _pl.col("valor").cast(_pl.Float64).alias("valor"))
    return d.with_columns([_pl.col(c).cast(t) for c, t in schema.items()])

# Significância: estrelas e linhas em negrito com p <= 0,10 (decisão com a Profª Lívia; negrito no lugar do fundo amarelo, 26/10/2026)
def stars(p) -> str:
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ""
    if p != p:
        return ""
    return "***" if p <= 0.01 else "**" if p <= 0.05 else "*" if p <= 0.10 else ""

_PCOL = _re.compile(r"^(p|p_value.*|pvalue.*|p_bh|p_boot|q_bh|.*_p|.*_p_bh|fisher_p|lb_p.*|p_[a-z_]*)$", _re.I)
_NOT_P = _re.compile(r"^(pct|paid|pop|programme|portfolio|present|payers|payment|pca|pc\d)", _re.I)

def _p_columns(df):
    import polars as _pl
    return [c for c in df.columns if _PCOL.match(c) and not _NOT_P.match(c) and df.schema[c] in (_pl.Float64, _pl.Float32)]

_SIG_LEG = "* p ≤ 0,10; ** p ≤ 0,05; *** p ≤ 0,01"
_MAX_ROWS_SIG = 400  # acima disso a tabela sai como o polars mostra (só as primeiras e últimas linhas): sem estrelas nem negrito

def _df_html(df):
    import polars as _pl
    pcs = _p_columns(df)
    if not pcs:
        return df._repr_html_()
    if df.height > _MAX_ROWS_SIG:
        note = (f"<p class='lab-sig-legenda' style='font-size:0.8em;margin-bottom:.25rem'>Tabela longa ({df.height} linhas): "
                f"mostrada resumida, sem estrelas nem negrito. Significância pela coluna {pcs[0]}: {_SIG_LEG}.</p>")
        return note + df._repr_html_()
    pd_ = df.to_pandas()
    for c in pcs[::-1]:
        pd_.insert(list(pd_.columns).index(c) + 1, f"{c} (sig.)", [stars(v) for v in pd_[c]])
    main = pcs[0]
    def _bold(row):
        v = row[main]
        on = v == v and v is not None and v <= 0.10
        return ["font-weight: 700" if on else "" for _ in row]
    sty = pd_.style.apply(_bold, axis=1).format(precision=4, na_rep="")
    outras = f" Estrelas em todas as colunas de p ({', '.join(pcs)})." if len(pcs) > 1 else ""
    note = (f"<p class='lab-sig-legenda' style='font-size:0.8em;margin-bottom:.25rem'><b>Negrito</b> = linha com "
            f"{main} ≤ 0,10. Coluna “(sig.)”: {_SIG_LEG}.{outras}</p>")
    return note + sty.to_html()

try:
    get_ipython().display_formatter.formatters["text/html"].for_type_by_name("polars.dataframe.frame", "DataFrame", _df_html)
except NameError:
    pass

# Registro r -> p preenchido por corr_pair, para pôr estrelas nos mapas de calor de correlação
_RP: dict = {}
CACHE_LOG: list = []


# ---- célula 5 do paper ----
import os, sys, gc, random, time, json, hashlib, secrets
from pathlib import Path
from datetime import datetime, timezone, date, timedelta
from typing import Any, Callable
os.environ.setdefault("POLARS_MAX_THREADS", "2")  # must be set before polars is imported
import numpy as np
import polars as pl
from dotenv import load_dotenv, find_dotenv

# lab: o .env é lido por _lab_base (só chaves de caminho; sal e tokens ficam fora do kernel)

def env_path(name: str, create: bool = False) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set (see paper_eda_v1.env.example).")
    path = Path(value).expanduser()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise RuntimeError(f"{name}={path} does not exist; is the external drive mounted?")
    return path

def find_repo(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for p in (start, *start.parents):
        if (p / "pyproject.toml").exists() and (p / "projects").exists():
            return p
    raise RuntimeError("Repository root not found.")

REPO = find_repo()

def repo_path(name: str, default: str) -> Path:
    path = Path(os.environ.get(name, default))
    path = path if path.is_absolute() else REPO / path
    path.mkdir(parents=True, exist_ok=True)
    return path

DATA_ROOT    = env_path("CEMEPI_DATA_ROOT")
DUCKDB_MAIN  = DATA_ROOT / os.environ.get("CEMEPI_DUCKDB_MAIN", "api_ctf/cemepi.duckdb")
DUCKDB_MACRO = DATA_ROOT / os.environ.get("CEMEPI_DUCKDB_MACRO", "api_ctf/macro.duckdb")
INTERIM      = env_path("CEMEPI_INTERIM", create=True)
CACHE        = env_path("CEMEPI_CACHE", create=True)
FIG_DIR      = repo_path("CEMEPI_FIG_DIR", "projects/macro/manuscript/paper_eda_v1/figures")
TAB_DIR      = repo_path("CEMEPI_TAB_DIR", "projects/macro/manuscript/paper_eda_v1/tables")
ICMS_DIR     = INTERIM / "icms"
GARE_DIR     = INTERIM / "gare_icms"
ALL_DIR      = INTERIM / "all_taxes_snapshot"
TMP          = CACHE / "tmp"
for p in (ICMS_DIR, GARE_DIR, ALL_DIR, TMP):
    p.mkdir(parents=True, exist_ok=True)
for f in (DUCKDB_MAIN, DUCKDB_MACRO):
    if not f.exists():
        raise RuntimeError(f"Database not found: {f}")
print(f"data: {DATA_ROOT}\ninterim: {INTERIM}\ncache: {CACHE}\nfigures: {FIG_DIR}\ntables: {TAB_DIR}")

# ---- célula 7 do paper ----
os.environ.setdefault("POLARS_TEMP_DIR", str(TMP))

def collect(lf: pl.LazyFrame) -> pl.DataFrame:
    return lf.collect(engine="streaming")

def duck(db: Path = DUCKDB_MAIN):
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    con.execute(f"SET memory_limit='1500MB'; SET threads=2; SET temp_directory='{TMP}'; "
                "SET preserve_insertion_order=false")
    return con

# ---- célula 9 do paper ----
CACHE_DIR = CACHE / "results"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = CACHE_DIR / "manifest.jsonl"
CACHE_VERSION = "v2"
FORCE_ALL = os.environ.get("CEMEPI_FORCE_REFRESH", "0") == "1"
FORCE_STEPS = {s.strip() for s in os.environ.get("CEMEPI_FORCE_STEPS", "").split(",") if s.strip()}

# lab (26/10/2026): assinatura do macro.duckdb fixável por variável de ambiente.
# Motivo: a chave do cache inclui tamanho e mtime do cemepi.duckdb e do macro.duckdb. Em 26/10/2026 (21:55 BRT) a
# série SGS 4189 foi acrescentada ao macro.duckdb (798.720 -> 1.060.864 bytes), o que invalidava as 128 etapas do
# cache (DEBITO inclusive) sem nenhum dado usado ter mudado. Verificação feita antes de fixar (read_only, 26/10/2026):
# as 16 séries do banco antigo (backup ~/Backups/macro.duckdb.2026-10-26_pre4189, sha256 a87429ea…fe95) são idênticas
# às do novo (sha256 1a5a5759…) por EXCEPT ALL nos dois sentidos, código a código; a única diferença é a 4189, que o lab
# não lê do banco (usa o JSON do BCB). Uso, só ao executar localmente (o CI não executa nada, só lê o _freeze):
#   fish: set -x CEMEPI_MACRO_FP_PIN 798720:1790442383
# Sem a variável, o comportamento é o de antes (tamanho e mtime atuais do arquivo).
def source_fingerprint() -> dict:
    fp = {f.name: [f.stat().st_size, int(f.stat().st_mtime)] for f in (DUCKDB_MAIN, DUCKDB_MACRO)}
    pin = os.environ.get("CEMEPI_MACRO_FP_PIN", "").strip()
    if pin:
        size, mtime = (int(x) for x in pin.split(":"))
        fp[DUCKDB_MACRO.name] = [size, mtime]
    return fp

SOURCE_FP = source_fingerprint()

def _canonical(obj) -> str:
    return json.dumps(obj or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)

def _sha(text: str | None) -> str:
    return hashlib.sha256(text.encode()).hexdigest() if text else ""

def key_from_parts(step: str, params: dict | None, version: str, sql_sha: str,
                   cache_version: str = CACHE_VERSION, source: dict | None = None) -> str:
    # Explicit inputs only: step id, canonical params, step version, SQL hash, global version, source fingerprint
    payload = _canonical({"step": step, "params": params or {}, "version": str(version), "sql": sql_sha,
                          "cache_version": cache_version, "source": source or SOURCE_FP})
    return f"{step.replace('.', '_').replace(':', '-')}__{hashlib.sha256(payload.encode()).hexdigest()[:16]}"

def cache_key(step: str, params: dict | None = None, version: str = "1", sql: str | None = None) -> str:
    return key_from_parts(step, params, version, _sha(sql))

def _must_refresh(step: str, refresh: bool = False) -> bool:
    return refresh or FORCE_ALL or step.split(":")[0] in FORCE_STEPS

def _log(step: str, key: str, path: Path, params: dict | None, version: str, sql: str | None, seconds: float) -> None:
    with MANIFEST.open("a") as fh:
        fh.write(json.dumps({"step": step, "key": key, "path": str(path), "params": params or {},
                             "version": str(version), "sql_sha": _sha(sql), "cache_version": CACHE_VERSION,
                             "source": SOURCE_FP, "seconds": round(seconds, 2),
                             "created": datetime.now(timezone.utc).isoformat()}, default=str) + "\n")

def _report(step: str, hit: bool) -> None:
    CACHE_LOG.append((step, "HIT" if hit else "MISS"))

def cached(step: str, fn: Callable[[], pl.LazyFrame | pl.DataFrame], params: dict | None = None,
           version: str = "1", sql: str | None = None, refresh: bool = False, allow_empty: bool = False) -> pl.LazyFrame:
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
        raise ValueError(f"{step}: empty result, not cached (pass allow_empty=True if expected)")
    tmp.replace(path)
    _log(step, key, path, params, version, sql, time.time() - t0)
    return pl.scan_parquet(path)

def cached_json(step: str, fn: Callable[[], Any], params: dict | None = None,
                version: str = "1", refresh: bool = False) -> Any:
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

def cached_partitioned(step: str, out_dir: Path, years, build_year: Callable[[int, Path], None],
                       params: dict | None = None, version: str = "1", refresh: bool = False) -> pl.LazyFrame:
    for y in years:
        part = out_dir / f"year={y}"
        key = cache_key(f"{step}:{y}", params, version)
        marker = part / f"_KEY.{CACHE_VERSION}"
        hit = marker.exists() and marker.read_text() == key and not _must_refresh(step, refresh)
        _report(f"{step}:{y}", hit)
        if hit:
            continue
        part.mkdir(parents=True, exist_ok=True)
        for f in part.glob("*"):
            f.unlink()
        t0 = time.time()
        build_year(y, part)
        marker.write_text(key)
        _log(f"{step}:{y}", key, part, params, version, None, time.time() - t0)
        print(f"{step} year {y}: {time.time() - t0:,.1f} s")
    return pl.scan_parquet(out_dir / "**/*.parquet", hive_partitioning=True)

def cached_memmap(step: str, n: int, build: Callable[[np.memmap], None], params: dict | None = None,
                  version: str = "1", refresh: bool = False) -> np.memmap:
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

def query(step: str, sql: str, params: dict | None = None, db: Path | None = None, version: str = "1",
          allow_empty: bool = False) -> pl.LazyFrame:
    def run() -> pl.DataFrame:
        con = duck(db or DUCKDB_MAIN)
        try:
            return con.execute(sql).pl()
        finally:
            con.close()
    return cached(step, run, params, version=version, sql=sql, allow_empty=allow_empty)

def cached_sql_parts(step: str, parts: dict[str, str], params: dict | None = None,
                     version: str = "1", refresh: bool = False, allow_empty: bool = False) -> pl.LazyFrame:
    # Each named DuckDB query is written to its own Parquet file; finished parts are kept, so an
    # interrupted build resumes. Memory is bounded by the largest single part.
    sql_all = "\n;\n".join(f"-- {k}\n{v}" for k, v in sorted(parts.items()))
    out = cache_path(step, sql_all, params, version)
    done = out / "_DONE"
    hit = done.exists() and not _must_refresh(step, refresh)
    _report(step, hit)
    if not hit:
        import duckdb
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        for name, sql in parts.items():
            part = out / f"part-{name}.parquet"
            if part.exists() and not _must_refresh(step, refresh):
                continue
            t1, tmp = time.time(), part.with_suffix(".tmp")
            con = duckdb.connect(config={"memory_limit": "1500MB", "threads": 2, "temp_directory": str(TMP),
                                         "preserve_insertion_order": False})
            try:
                con.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            finally:
                con.close()
                gc.collect()
            tmp.replace(part)
            print(f"  {step} [{name}] {time.time() - t1:,.1f} s", flush=True)
        if not allow_empty and pl.scan_parquet(out / "part-*.parquet").select(pl.len()).collect().item() == 0:
            raise ValueError(f"{step}: empty result, not cached (pass allow_empty=True if expected)")
        done.write_text(cache_key(step, params, version, sql_all))
        _log(step, cache_key(step, params, version, sql_all), out, params, version, sql_all, time.time() - t0)
    return pl.scan_parquet(out / "part-*.parquet")

def cache_path(step: str, sql: str, params: dict | None = None, version: str = "1") -> Path:
    return CACHE_DIR / cache_key(step, params, version, sql)

def parts_glob(lf_parts_step: str, parts: dict[str, str], params: dict | None = None, version: str = "1") -> str:
    sql_all = "\n;\n".join(f"-- {k}\n{v}" for k, v in sorted(parts.items()))
    return str(cache_path(lf_parts_step, sql_all, params, version) / "part-*.parquet")

def cache_status(show: bool = True) -> pl.DataFrame:
    # Recomputes each recorded step's key from the manifest under the current CACHE_VERSION and source; computes nothing
    latest = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text().splitlines():
            e = json.loads(line)
            if "version" in e:
                latest[e["step"]] = e
    rows = []
    for step, e in sorted(latest.items()):
        key = key_from_parts(step, e["params"], e["version"], e["sql_sha"])
        path = Path(e["path"])
        marker = path / f"_KEY.{CACHE_VERSION}"
        if path.is_dir() and (path / "_DONE").exists():
            marker = path / "_DONE"
        exists = marker.exists() and marker.read_text() == key if path.is_dir() else path.exists()
        rows.append({"step": step, "status": "HIT" if key == e["key"] and exists else "MISS",
                     "key": key, "seconds_to_build": e["seconds"]})
    out = pl.DataFrame(rows, schema={"step": pl.Utf8, "status": pl.Utf8, "key": pl.Utf8, "seconds_to_build": pl.Float64})
    if show:
        print(out.group_by("status").len())
    return out

def scan_icms() -> pl.LazyFrame:
    return pl.scan_parquet(ICMS_DIR / "**/*.parquet", hive_partitioning=True)

def scan_gare() -> pl.LazyFrame:
    return pl.scan_parquet(GARE_DIR / "**/*.parquet", hive_partitioning=True)
# ---- lab: espaço de nomes do cache e guarda de colunas de pessoa --------------------------------
_paper_cached, _paper_cached_json, _paper_cached_memmap = cached, cached_json, cached_memmap
_paper_query, _paper_cached_sql_parts, _paper_cache_path = query, cached_sql_parts, cache_path
_paper_scan_icms, _paper_scan_gare = scan_icms, scan_gare

def _ns(step: str) -> str:
    # A seção 1 (extrato, painel) reaproveita o cache do paper. O resto muda com o corte em 2025 e é recalculado
    # com prefixo próprio, porque a chave do paper não inclui o corpo das funções.
    return step if step.startswith(("1.", LAB_NS)) else LAB_NS + step

def cached(step, fn, params=None, version="1", sql=None, refresh=False, allow_empty=False):
    return _paper_cached(_ns(step), fn, params, version, sql, refresh, allow_empty)

def cached_json(step, fn, params=None, version="1", refresh=False):
    return _paper_cached_json(_ns(step), fn, params, version, refresh)

def cached_memmap(step, n, build, params=None, version="1", refresh=False):
    return _paper_cached_memmap(_ns(step), n, build, params, version, refresh)

def cached_sql_parts(step, parts, params=None, version="1", refresh=False, allow_empty=False):
    return _paper_cached_sql_parts(_ns(step), parts, params, version, refresh, allow_empty)

def cache_path(step, sql, params=None, version="1"):
    return _paper_cache_path(_ns(step), sql, params, version)

_BANNED = _re.compile(r"\b(NOME_DEVEDOR|CPF_DEVEDOR)\b", _re.I)

def query(step, sql, params=None, db=None, version="1", allow_empty=False):
    step = _ns(step)
    if _BANNED.search(_re.sub(r"EXCLUDE\s*\([^)]*\)", "", sql, flags=_re.I)):
        key = cache_key(step, params, version, sql)
        if not (CACHE_DIR / f"{key}.parquet").exists() or _must_refresh(step):
            raise RuntimeError(f"{step}: a consulta toca CPF_DEVEDOR/NOME_DEVEDOR. O laboratório só lê o agregado "
                               "que o paper já deixou em cache, e ele não existe.")
    return _paper_query(step, sql, params, db, version, allow_empty)

# ---- célula 11 do paper ----
NOTEBOOK_BUILD = "e9907bb2870c"
SEED = 2026
random.seed(SEED)
np.random.seed(SEED)
EXTRACT = os.environ.get("CEMEPI_EXTRACT", "2026-03")
SNAP_YEAR, SNAP_MONTH = (int(x) for x in EXTRACT.split("-"))
ENROL_MIN = date(1960, 1, 1)
ENROL_MAX = (date(SNAP_YEAR + SNAP_MONTH // 12, SNAP_MONTH % 12 + 1, 1) - timedelta(days=1))

def enrol_date_sql(col: str = "DATA_INSCRICAO") -> str:
    # Enrolment dates outside [1960-01-01, last snapshot] are data-entry errors and are set to NULL
    d = f"TRY_CAST(LEFT(CAST({col} AS VARCHAR), 10) AS DATE)"
    return f"CASE WHEN {d} BETWEEN DATE '{ENROL_MIN}' AND DATE '{ENROL_MAX}' THEN {d} END"

def plausible_enrol(lf: pl.LazyFrame) -> pl.LazyFrame:
    ok = pl.col("DATA_INSCRICAO").is_between(pl.lit(ENROL_MIN), pl.lit(ENROL_MAX))
    cols = [c for c in ("age", "enrol_year", "age_years") if c in lf.collect_schema().names()]
    return lf.with_columns(pl.when(ok).then(pl.col("DATA_INSCRICAO")).alias("DATA_INSCRICAO"),
                           *[pl.when(ok).then(pl.col(c)).alias(c) for c in cols])
YEARS = list(range(2016, SNAP_YEAR + 1))
ENV_INFO = {"python": sys.version.split()[0], "polars": pl.__version__, "extract": EXTRACT, "notebook_build": NOTEBOOK_BUILD}
ENV_INFO

# ---- célula 13 do paper ----
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

CEMEPI = {"deep_teal": "#154C56", "teal": "#186D7F", "mid_teal": "#267586", "sand": "#BD9869",
          "green": "#5EA65B", "cream": "#FFF4E5", "grey": "#8A9499"}
CAT_ORDER = [CEMEPI[k] for k in ("deep_teal", "sand", "teal", "green", "mid_teal")]
DASHES = ["solid", "dash", "dot", "dashdot", "longdash"]
SEQ = [[0.0, "#FFFFFF"], [1.0, CEMEPI["deep_teal"]]]
DIV = [[0.0, CEMEPI["sand"]], [0.5, "#FFFFFF"], [1.0, CEMEPI["deep_teal"]]]
PX_PER_IN, SINGLE_IN, DOUBLE_IN, RATIO, EXPORT_SCALE = 96, 3.5, 7.0, 0.62, 6

pio.templates["cemepi"] = go.layout.Template(layout=go.Layout(
    font=dict(family="Montserrat, Arial, sans-serif", size=11, color=CEMEPI["deep_teal"]),
    colorway=CAT_ORDER, paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
    margin=dict(l=10, r=30, t=15, b=10, autoexpand=True),
    xaxis=dict(showgrid=False, zeroline=False, showline=True, linecolor=CEMEPI["deep_teal"], ticks="outside",
               automargin=True),
    yaxis=dict(showgrid=True, gridcolor="#E6E6E6", gridwidth=0.5, zeroline=False,
               showline=True, linecolor=CEMEPI["deep_teal"], ticks="outside", automargin=True),
    colorscale=dict(sequential=SEQ, diverging=DIV),
    annotationdefaults=dict(font=dict(size=10)),
    legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0)))
pio.templates.default = "cemepi"

def bn(x):
    return x / 1e9

def color(i: int) -> str:
    return CAT_ORDER[i % len(CAT_ORDER)]

FILING_LABELS = {"Ajuizado": "Filed", "Liberado para ajuizamento": "Cleared for filing",
                 "Aguardando liberação para ajuizar": "Awaiting clearance"}

def filing_label(col: str = "STATUS_AJUIZAMENTO_DEBITO") -> pl.Expr:
    return pl.col(col).replace_strict(FILING_LABELS, default="Unrecognised code", return_dtype=pl.Utf8).alias(col)

def wrap(text: str, width: int) -> str:
    if not text or len(text) <= width or "<br>" in text:
        return text
    words, out, line = text.split(" "), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return "<br>".join(out + [line])

def _axis_values(fig: go.Figure, name: str) -> list:
    kind = name[0]
    ref = kind + name[5:]
    out = []
    for t in fig.data:
        if (getattr(t, kind + "axis", None) or kind) == ref and getattr(t, kind, None) is not None:
            out.extend(t[kind])
    return out

def _is_year(values: list) -> bool:
    nums = [v for v in values if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)]
    return bool(nums) and len(nums) == len(values) and all(1950 <= float(v) <= 2100 and float(v) == int(v) for v in nums)

def polish(fig: go.Figure) -> go.Figure:
    lay = fig.layout
    labels = globals().get("AGE_LABELS")
    for name in [k for k in lay.to_plotly_json() if k.startswith(("xaxis", "yaxis"))] or ["xaxis", "yaxis"]:
        ax = lay[name]
        values = _axis_values(fig, name)
        title = ax.title.text or ""
        if name.startswith("xaxis") and not title and ax.showticklabels is not False and _is_year(values):
            title = "Year"
        if title:
            ax.title.text = wrap(title, 34 if name.startswith("yaxis") else 60)
        if name.startswith("yaxis") and ax.type == "category":
            continue
        present = {str(v) for v in values if v is not None}
        if labels and present and present <= set(labels):
            ax.categoryorder, ax.categoryarray = "array", list(labels)
    if lay.showlegend and lay.legend.orientation == "h" and lay.legend.y is not None and lay.legend.y < 0:
        lay.legend.update(y=1.02, yanchor="bottom", x=0, xanchor="left")
    lay.legend.itemsizing = "constant"
    return fig

def save_fig(fig: go.Figure, label: str, width: str = "single", ratio: float = RATIO,
             height: int | None = None) -> go.Figure:
    polish(fig)
    if width == "single" and fig.layout.showlegend and len(fig.data) > 2:
        width, ratio = "double", min(ratio, 0.5)
    w_in = SINGLE_IN if width == "single" else DOUBLE_IN
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    w = int(w_in * PX_PER_IN)
    h = height or max(int(w_in * ratio * PX_PER_IN), int(meta.get("min_height", 0)))
    stem = FIG_DIR / label
    if os.environ.get("LAB_EXPORT_FIGS", "0") != "1":  # lab: exportar só sob pedido
        return fig
    fig.write_image(stem.with_suffix(".pdf"), width=w, height=h)
    fig.write_image(stem.with_suffix(".svg"), width=w, height=h)
    fig.write_image(stem.with_suffix(".png"), width=w, height=h, scale=EXPORT_SCALE)
    return fig

def save_tab(df: pl.DataFrame, label: str, fmt: str = "{:,.2f}") -> pl.DataFrame:
    def cell(v):
        if isinstance(v, float):
            return "" if v != v else fmt.format(v)
        return str(v).replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
    header = " & ".join(c.replace("_", r"\_") for c in df.columns) + r" \\"
    rows = [" & ".join(cell(v) for v in r) + r" \\" for r in df.iter_rows()]
    body = [r"\begin{tabular}{" + "l" * df.width + "}", r"\toprule", header, r"\midrule",
            *rows, r"\bottomrule", r"\end{tabular}"]
    (TAB_DIR / f"{label}.tex").write_text("\n".join(body))
    return df

def lines(df: pl.DataFrame, x: str, y: str, group: str | None = None, log_y: bool = False,
          y_title: str = "", x_title: str = "", markers: bool = True, end_labels: bool = False,
          order: list | None = None, names: dict | None = None) -> go.Figure:
    fig = go.Figure()
    groups = [(None, df)] if group is None else [(k[0], d) for k, d in df.sort(group).group_by(group, maintain_order=True)]
    if order is not None:
        rank = {v: i for i, v in enumerate(order)}
        groups = sorted(groups, key=lambda gd: rank.get(gd[0], len(rank)))
    if names is not None:
        groups = [(names.get(g, g), d) for g, d in groups]
    ends = []
    for i, (g, d) in enumerate(groups):
        d = d.sort(x)
        fig.add_trace(go.Scatter(x=d[x], y=d[y], mode="lines+markers" if markers else "lines", name=str(g),
                                 line=dict(color=color(i), dash=DASHES[i % 5], width=1.6), marker=dict(size=4)))
        if g is not None and d.height:
            ends.append((i, g, d[x][-1], float(d[y][-1])))
    multi = group is not None and len(groups) > 1
    if len(y_title) > 28 and "<br>" not in y_title:
        cut = y_title.rfind(" ", 0, len(y_title) // 2 + 6)
        y_title = y_title[:cut] + "<br>" + y_title[cut + 1:] if cut > 0 else y_title
    fig.update_layout(xaxis_title=x_title, yaxis_title=y_title, showlegend=multi,
                      legend=dict(orientation="h", yanchor="top", y=-0.22, x=0.5, xanchor="center", title_text=""))
    if log_y:
        fig.update_yaxes(type="log", dtick=1)
    if end_labels and multi and ends:
        pos = sorted((np.log10(v) if log_y and v > 0 else v) for *_, v in ends)
        span = (max(pos) - min(pos)) or 1.0
        if all(b - a > 0.06 * span for a, b in zip(pos, pos[1:])):
            for i, g, xe, ye in ends:
                fig.add_annotation(x=xe, y=ye, text=str(g), showarrow=False, xanchor="left", xshift=5,
                                   font=dict(color=color(i), size=10))
            longest = max(len(str(g)) for _, g, *_ in ends)
            fig.update_layout(margin=dict(r=max(30, 12 + 6 * longest)))
    return fig

def bars(df: pl.DataFrame, cat: str, val: str, horizontal: bool = True, log: bool = False,
         val_title: str = "", text_fmt: str = "{:,.1f}") -> go.Figure:
    d = df.sort(val)
    text = [text_fmt.format(v) for v in d[val]]
    common = dict(text=text, textposition="outside", cliponaxis=False, marker_color=CEMEPI["deep_teal"])
    tr = (go.Bar(x=d[val], y=d[cat], orientation="h", **common) if horizontal else go.Bar(x=d[cat], y=d[val], **common))
    fig = go.Figure(tr)
    axis = "xaxis" if horizontal else "yaxis"
    vals = d[val].cast(pl.Float64).to_numpy()
    vmax = float(np.nanmax(vals)) if vals.size else 1.0
    if log:
        pos = vals[vals > 0]
        lo = np.log10(pos.min()) if pos.size else 0.0
        hi = np.log10(vmax) if vmax > 0 else 1.0
        rng = [np.floor(lo) if lo > 0 else lo - 0.2, hi + 0.5 * max(hi - lo, 1.0) + 0.3]
        fig.update_layout(**{axis: dict(type="log", range=rng, dtick=1)})
    else:
        fig.update_layout(**{axis: dict(range=[0, vmax * 1.3])})
    fig.update_layout(**{f"{axis}_title": val_title})
    if horizontal:
        fig.update_layout(xaxis=dict(showgrid=True, gridcolor="#E6E6E6"), yaxis=dict(showgrid=False),
                          meta={"min_height": 60 + 24 * d.height})
    return fig

def heatmap(mat: pl.DataFrame, row: str, diverging: bool = True, zmin=None, zmax=None,
            fmt: str = "{:.2f}", colorbar_title: str = "Correlation", text: list | None = None) -> go.Figure:
    cols = [c for c in mat.columns if c != row]
    z = mat.select(cols).to_numpy().astype(float)
    _starred = False
    if text is None:
        _use = diverging and colorbar_title == "Correlation"
        text = [[("" if np.isnan(v) else fmt.format(v) + (stars(_RP.get(round(float(v), 12))) if _use else "")) for v in r] for r in z]
        _starred = _use and any(_RP.get(round(float(v), 12)) is not None for r in z for v in r if not np.isnan(v))
    fig = go.Figure(go.Heatmap(z=z, x=cols, y=[str(v) for v in mat[row].to_list()], text=text, texttemplate="%{text}",
                               colorscale=DIV if diverging else SEQ,
                               zmin=-1 if diverging and zmin is None else zmin,
                               zmax=1 if diverging and zmax is None else zmax,
                               zmid=0 if diverging else None, colorbar=dict(title=colorbar_title)))
    fig.update_layout(yaxis=dict(showgrid=False, autorange="reversed", type="category"),
                      xaxis=dict(showgrid=False, tickangle=-35, type="category"),
                      meta={"min_height": 80 + 26 * len(z)})
    if _starred:  # lab: estrelas de significância (decisão com a Profª Lívia)
        fig.add_annotation(text="Cor = sinal e força da correlação; estrelas = significância: * p ≤ 0,10  ** p ≤ 0,05  "
                                "*** p ≤ 0,01 (sem correção para testes múltiplos)", x=0, y=1.0, yanchor="bottom",
                           xref="paper", yref="paper", showarrow=False, xanchor="left", font_size=10)
        fig.update_layout(margin=dict(t=40))
    return fig

def ecdf(df: pl.DataFrame, value: str, group: str, log_x: bool = True, x_title: str = "",
         max_points: int = 2000) -> go.Figure:
    fig = go.Figure()
    for i, (k, d) in enumerate(df.sort(group).group_by(group, maintain_order=True)):
        v = np.sort(d[value].drop_nulls().to_numpy())
        v = v[v > 0] if log_x else v
        if v.size == 0:
            continue
        q = np.linspace(0, 1, min(max_points, v.size))
        xs = np.quantile(v, q)
        fig.add_trace(go.Scatter(x=xs, y=q, mode="lines", name=str(k[0]),
                                 line=dict(color=color(i), dash=DASHES[i % 5])))
    fig.update_layout(xaxis_title=x_title, yaxis_title="Cumulative share", showlegend=True,
                      legend=dict(x=0.02, y=0.98))
    if log_x:
        fig.update_xaxes(type="log")
    return fig

def shade_window(fig: go.Figure, x0, x1, text: str | None = None) -> go.Figure:
    fig.add_vrect(x0=x0, x1=x1, fillcolor=CEMEPI["cream"], opacity=1.0, layer="below", line_width=0,
                  annotation_text=text, annotation_position="bottom left", annotation_font_size=9)
    return fig

PROGRAMMES = [("PEP 2019", "2019-07-01", "2019-12-31"), ("Acordo Paulista 2024", "2024-04-01", "2024-12-31")]

def shade_programmes(fig: go.Figure) -> go.Figure:
    for name, a, b in PROGRAMMES:
        shade_window(fig, a, b, name)
    return fig

# ---- célula 15 do paper ----
from scipy import stats
from statsmodels.stats.multitest import multipletests

def kruskal_test(df: pl.DataFrame, value: str, group: str) -> dict:
    samples = [d[value].drop_nulls().to_numpy() for _, d in df.group_by(group)]
    samples = [s for s in samples if s.size > 0]
    h, p = stats.kruskal(*samples)
    n, k = sum(s.size for s in samples), len(samples)
    return {"variable": value, "H": float(h), "df": k - 1, "p_value": float(p),
            "epsilon_sq": float((h - k + 1) / (n - k)) if n > k else float("nan")}

def mann_whitney(a: np.ndarray, b: np.ndarray) -> dict:
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    return {"U": float(u), "p_value": float(p), "rank_biserial": float(1 - 2 * u / (a.size * b.size)),
            "n_a": int(a.size), "n_b": int(b.size)}

def pairwise_tests(df: pl.DataFrame, value: str, group: str, max_n: int = 200_000) -> pl.DataFrame:
    rng = np.random.default_rng(SEED)
    data = {}
    for k, d in df.group_by(group):
        v = d[value].drop_nulls().to_numpy().astype(float)
        data[k[0]] = rng.choice(v, size=max_n, replace=False) if v.size > max_n else v
    keys, rows = sorted(data), []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if data[a].size == 0 or data[b].size == 0:
                continue
            mw = mann_whitney(data[a], data[b])
            ks = stats.ks_2samp(data[a], data[b])
            ad = stats.anderson_ksamp([data[a], data[b]], method=stats.PermutationMethod(n_resamples=199, random_state=SEED)) \
                if min(data[a].size, data[b].size) < 1000 else stats.anderson_ksamp([data[a], data[b]])
            rows.append({"variable": value, "group_a": a, "group_b": b, **mw,
                         "ks_stat": float(ks.statistic), "ks_p": float(ks.pvalue),
                         "ad_stat": float(ad.statistic), "ad_p": float(ad.pvalue)})
    out = pl.DataFrame(rows)
    if out.height:
        for col in ("p_value", "ks_p", "ad_p"):
            out = out.with_columns(pl.Series(f"{col}_bh", multipletests(out[col].to_numpy(), method="fdr_bh")[1]))
    return out

def dunn_test(df: pl.DataFrame, value: str, group: str) -> pl.DataFrame:
    d = df.select(value, group).drop_nulls()
    ranks = stats.rankdata(d[value].to_numpy())
    d = d.with_columns(pl.Series("_r", ranks))
    n = d.height
    _, counts = np.unique(ranks, return_counts=True)
    tie = 1 - (counts ** 3 - counts).sum() / (n ** 3 - n)
    g = d.group_by(group).agg(pl.col("_r").mean().alias("mr"), pl.len().alias("n")).sort(group)
    rows = []
    for i in range(g.height):
        for j in range(i + 1, g.height):
            se = np.sqrt(tie * n * (n + 1) / 12 * (1 / g["n"][i] + 1 / g["n"][j]))
            z = (g["mr"][i] - g["mr"][j]) / se
            rows.append({"variable": value, "group_a": g[group][i], "group_b": g[group][j],
                         "z": float(z), "p_value": float(2 * stats.norm.sf(abs(z)))})
    out = pl.DataFrame(rows)
    return out.with_columns(pl.Series("p_bh", multipletests(out["p_value"].to_numpy(), method="fdr_bh")[1]))

def chi2_table(ct: np.ndarray) -> dict:
    chi2, p, dof, expected = stats.chi2_contingency(ct)
    n = ct.sum()
    v = np.sqrt(chi2 / (n * (min(ct.shape) - 1)))
    res = {"chi2": float(chi2), "df": int(dof), "p_value": float(p), "cramers_v": float(v)}
    if ct.shape == (2, 2) and (expected < 5).any():
        res["fisher_p"] = float(stats.fisher_exact(ct)[1])
    return res

def corr_pair(x: np.ndarray, y: np.ndarray, method: str = "spearman") -> tuple[float, float]:
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 4:
        return float("nan"), float("nan")
    r = stats.spearmanr(x[m], y[m]) if method == "spearman" else stats.pearsonr(x[m], y[m])
    _RP[round(float(r[0]), 12)] = float(r[1])  # lab: para as estrelas nos mapas de calor
    return float(r[0]), float(r[1])

def lorenz(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    cum = np.cumsum(v) / v.sum()
    share = np.arange(1, v.size + 1) / v.size
    gini = 1 - 2 * np.trapezoid(np.cumsum(v[::-1]) / v.sum(), dx=1 / v.size)
    return share, cum, float(gini)

def top_shares(values: np.ndarray, fractions=(0.001, 0.01, 0.1)) -> dict:
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    return {f"top_{f * 100:g}pct": float(v[: max(1, int(round(f * v.size)))].sum() / v.sum()) for f in fractions}

# ---- célula 18 do paper ----
def table_inventory() -> pl.DataFrame:
    con = duck()
    try:
        tables = con.execute("SELECT table_name FROM duckdb_tables() WHERE NOT starts_with(table_name, '_') ORDER BY 1").fetchall()
        rows = []
        for (t,) in tables:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            ncol = con.execute(f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name = '{t}'").fetchone()[0]
            rows.append({"table": t, "rows": n, "columns": ncol})
    finally:
        con.close()
    return pl.DataFrame(rows)

inventory = collect(cached("1.1", table_inventory))
DESCRIPTION = {
    "DEBITO": "Monthly snapshots of enrolled debt certificates (CDA)",
    "ARRECADACAO": "Payments through state payment slips (GARE)",
    "DEBITO_RECEITA": "Debt components (principal, interest, fines)",
    "SOLICITACAOPARCELAMENTO": "Instalment requests",
    "STATUS_PROTESTO": "Protest status of CDAs",
    "AJUIZAMENTO": "Lawsuit filing events",
    "FATURAMENTO_MASCARADO": "Masked firm revenue",
}
sources = inventory.with_columns(pl.col("table").replace_strict(DESCRIPTION, default="").alias("content"))
save_tab(sources.select("table", "content", "rows", "columns"), "tab_sources", fmt="{:,.0f}")
sources

# ---- célula 21 do paper ----
BUCKET_SQL = """CASE WHEN TIPO_DEBITO ILIKE '%ICMS%'  THEN 'ICMS'
                     WHEN TIPO_DEBITO ILIKE '%IPVA%'  THEN 'IPVA'
                     WHEN TIPO_DEBITO ILIKE '%ITCMD%' THEN 'ITCMD'
                     ELSE 'Fees and other' END"""
IDENT_SQL = """CASE WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ' ELSE 'No CNPJ' END"""  # lab: sem ler CPF_DEVEDOR

SNAPSHOT_SQL = f"""
SELECT ID_DEBITO, TIPO_DEBITO, {BUCKET_SQL} AS bucket, {IDENT_SQL} AS identifier,
       TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE) AS DATA_INSCRICAO,
       SITUACAO_DEBITO, STATUS_AJUIZAMENTO_DEBITO, TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE) AS value,
       DATE_DIFF('day', TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE), DATE '{LAB_SNAP[0]}-{LAB_SNAP[1]:02d}-01') / 365.25 AS age_years
FROM DEBITO
WHERE ANO_EXTRACAO = {LAB_SNAP[0]} AND MES_EXTRACAO = {LAB_SNAP[1]}
"""
snapshot_all = plausible_enrol(query(LAB_NS + "1.2", SNAPSHOT_SQL, params={"snapshot": LAB_EXTRACT}))
collect(snapshot_all.group_by("bucket").agg(pl.len().alias("n_cda"), pl.col("value").sum().alias("stock")).sort("bucket"))

# ---- célula 23 do paper ----
IDENT_SQL = """CASE WHEN COALESCE(CPF_DEVEDOR, '') <> '' THEN 'CPF'
                    WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ' ELSE 'None' END"""
ICMS_TYPES = ["ICMS Declarado", "ICMS Autuação", "ICMS Autuação Simples Nacional",
              "ICMS Fecoep", "ICMS Alteração de GIA"]
ICMS_TYPES_SQL = ", ".join(f"'{t}'" for t in ICMS_TYPES)
PJ_FILTER = "COALESCE(CPF_DEVEDOR, '') = '' AND COALESCE(CNPJ_DEVEDOR, '') <> ''"

def extract_icms_year(year: int, part: Path) -> None:
    con = duck()
    try:
        con.execute(f"""
        COPY (
            SELECT ANO_EXTRACAO, MES_EXTRACAO, ID_DEBITO, CDA_COMPLETA,
                   TRY_CAST(LEFT(DATA_INSCRICAO, 10) AS DATE) AS DATA_INSCRICAO,
                   SITUACAO_DEBITO, TIPO_DEBITO,
                   CASE WHEN TIPO_DEBITO = 'ICMS Declarado' THEN 'declared'
                        WHEN TIPO_DEBITO LIKE 'ICMS Autua%' THEN 'assessed'
                        ELSE 'other' END AS ICMS_ORIGIN,
                   STATUS_AJUIZAMENTO_DEBITO,
                   TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE) AS VALOR_SEM_HONORARIOS,
                   TRY_CAST(VALOR_VH AS DOUBLE) AS VALOR_VH,
                   REGEXP_REPLACE(CNPJ_DEVEDOR, '[^0-9]', '', 'g') AS CNPJ,
                   LEFT(REGEXP_REPLACE(CNPJ_DEVEDOR, '[^0-9]', '', 'g'), 8) AS CNPJ_ROOT
            FROM DEBITO
            WHERE TIPO_DEBITO IN ({ICMS_TYPES_SQL})
              AND ANO_EXTRACAO = {year} AND MES_EXTRACAO BETWEEN 1 AND 12
              AND {PJ_FILTER}
        ) TO '{part}/part-0.parquet' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 500000)""")
    finally:
        con.close()

def extract_gare_year(year: int, part: Path) -> None:
    con = duck()
    try:
        con.execute(f"""
        COPY (
            SELECT a.ID_DEBITO, a.ID_GARE_SEFAZ, CAST(a.DATA_ARRECADACAO_GARE AS DATE) AS DATA_ARRECADACAO_GARE,
                   a.VALOR_RECEITA, a.VALOR_TOTAL_GARE, a.JUROS_MORA, a.MULTA_MORA, a.STATUS_AJUIZAMENTO_DEBITO
            FROM ARRECADACAO a
            SEMI JOIN (SELECT DISTINCT ID_DEBITO FROM read_parquet('{ICMS_DIR}/**/*.parquet')) i
                 ON a.ID_DEBITO = i.ID_DEBITO
            WHERE YEAR(a.DATA_ARRECADACAO_GARE) = {year}
        ) TO '{part}/part-0.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    finally:
        con.close()

EXTRACT_PARAMS = {"types": ICMS_TYPES, "snapshot": EXTRACT, "pj_only": True}
icms = cached_partitioned("1.3", ICMS_DIR, YEARS, extract_icms_year, params=EXTRACT_PARAMS)
gare = cached_partitioned("1.3-gare", GARE_DIR, YEARS, extract_gare_year, params=EXTRACT_PARAMS)

SOURCE_COUNTS_SQL = f"""
SELECT ANO_EXTRACAO AS year, {IDENT_SQL} AS identifier, COUNT(*) AS rows
FROM DEBITO
WHERE TIPO_DEBITO IN ({ICMS_TYPES_SQL}) AND ANO_EXTRACAO BETWEEN {YEARS[0]} AND {YEARS[-1]}
  AND MES_EXTRACAO BETWEEN 1 AND 12
GROUP BY ALL
"""
source_counts = collect(query("1.3:source", SOURCE_COUNTS_SQL, params=EXTRACT_PARAMS)).pivot(
    on="identifier", index="year", values="rows").fill_null(0)
extracted = collect(cached("1.3:extracted_counts", lambda: icms.group_by("year").agg(
    pl.len().alias("rows_extracted"),
    pl.col("ID_DEBITO").n_unique().alias("distinct_cda"),
    pl.col("MES_EXTRACAO").n_unique().alias("months"),
    (pl.col("VALOR_SEM_HONORARIOS").sum() / 1e9).alias("value_sum_bn")), params=EXTRACT_PARAMS))
gare_counts = collect(cached("1.3:gare_counts", lambda: gare.group_by("year").agg(
    pl.len().alias("gare_rows"), (pl.col("VALOR_RECEITA").sum() / 1e9).alias("gare_bn")), params=EXTRACT_PARAMS))
extraction_counts = (source_counts.join(extracted, on="year").join(gare_counts, on="year", how="left")
                     .with_columns((pl.col("rows_extracted") == pl.col("CNPJ")).alias("matches_source"))
                     .sort("year")).filter(pl.col("year") <= LAB_LAST_YEAR)  # lab: sem 2026
save_tab(extraction_counts, "tab_extraction_counts", fmt="{:,.1f}")
extraction_counts

# ---- célula 25 do paper ----
DEBTOR_ID_SQL = f"""
WITH snap AS (
    SELECT TIPO_DEBITO AS raw_type,
           CASE WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ (legal entity)'
                ELSE 'No CNPJ (CPF or no identifier)' END AS identifier,  -- lab: CNPJ x sem CNPJ, sem ler a coluna de pessoa
           TRY_CAST(VALOR_SEM_HONORARIOS AS DOUBLE) AS value
    FROM DEBITO
    WHERE ANO_EXTRACAO = {LAB_SNAP[0]} AND MES_EXTRACAO = {LAB_SNAP[1]} AND TIPO_DEBITO IN ({ICMS_TYPES_SQL})
), stacked AS (
    SELECT * FROM snap
    UNION ALL
    SELECT 'ICMS (all types)', identifier, value FROM snap
)
SELECT raw_type, identifier,
       COUNT(*) AS n_cda,
       SUM(value) / 1e9 AS stock_bn,
       100 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY raw_type) AS pct_cda,
       100 * SUM(value) / SUM(SUM(value)) OVER (PARTITION BY raw_type) AS pct_stock
FROM stacked
GROUP BY raw_type, identifier
ORDER BY raw_type, identifier
"""
debtor_id = collect(query(LAB_NS + "1.3.1", DEBTOR_ID_SQL, params={"snapshot": LAB_EXTRACT}))
save_tab(debtor_id, "tab_debtor_identifier", fmt="{:,.3f}")

total = debtor_id.filter(pl.col("raw_type") == "ICMS (all types)")
excluded = total.filter(pl.col("identifier") != "CNPJ (legal entity)")
kept_source = int(total.filter(pl.col("identifier") == "CNPJ (legal entity)")["n_cda"].sum())
kept_extract = collect(cached(LAB_NS + "1.3.1:extract_snapshot", lambda: icms.filter(
    (pl.col("year") == LAB_SNAP[0]) & (pl.col("MES_EXTRACAO") == LAB_SNAP[1])).select(pl.len().alias("n")),
    params={**EXTRACT_PARAMS, "snap": LAB_EXTRACT}))[0, 0]
print(f"Excluded: {excluded['n_cda'].sum():,} CDAs ({excluded['pct_cda'].sum():.3f}% of CDAs, "
      f"{excluded['pct_stock'].sum():.3f}% of the ICMS stock).")
print("Lab: sem ler CPF_DEVEDOR, CDAs com CNPJ e CPF ao mesmo tempo contam como CNPJ na fonte; "
      "o extrato (1.3) exclui essas CDAs, então a diferença fonte − extrato mede esse grupo.")
print(f"Legal-entity CDAs in the source snapshot: {kept_source:,}; in the extract: {kept_extract:,}.")
debtor_id

# ---- célula 27 do paper ----
# Built in DuckDB from one yearly file at a time, then combined in ID buckets, so no step holds the full extract
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
panel.collect_schema()

# ---- célula 29 do paper ----
def _stock_month_dedup() -> pl.DataFrame:
    # lab: uma linha por CDA e mês antes de somar o estoque. O DEBITO tem meses com linhas repetidas
    # (2020-04 a 06 em dobro, 2020-01, 2018-10 e meses de 2024); o paper somava todas as linhas.
    import duckdb
    out = []
    for y in YEARS:
        con = duckdb.connect(config={"memory_limit": "1500MB", "threads": 2, "temp_directory": str(TMP),
                                     "preserve_insertion_order": False})
        try:
            out.append(con.execute(f"""
                SELECT ANO_EXTRACAO, MES_EXTRACAO, ICMS_ORIGIN, SUM(v) AS stock, COUNT(*) AS n_cda, SUM(r) AS n_rows
                FROM (SELECT ANO_EXTRACAO, MES_EXTRACAO, ID_DEBITO, arg_max(ICMS_ORIGIN, VALOR_SEM_HONORARIOS) AS ICMS_ORIGIN,
                             MAX(VALOR_SEM_HONORARIOS) AS v, COUNT(*) AS r
                      FROM read_parquet('{ICMS_DIR}/year={y}/*.parquet')
                      GROUP BY ANO_EXTRACAO, MES_EXTRACAO, ID_DEBITO)
                GROUP BY ALL""").pl())
        finally:
            con.close()
    return pl.concat(out)

stock_dedup = collect(cached(LAB_NS + "1.5:stock_dedup", _stock_month_dedup, params=EXTRACT_PARAMS))

def monthly_series() -> pl.DataFrame:
    stock = stock_dedup.select("ANO_EXTRACAO", "MES_EXTRACAO", "ICMS_ORIGIN", "stock", "n_cda").with_columns(
        pl.date(pl.col("ANO_EXTRACAO"), pl.col("MES_EXTRACAO"), 1).alias("month"))  # lab: sem linhas repetidas
    new = collect(attributes.with_columns(pl.col("DATA_INSCRICAO").dt.truncate("1mo").alias("month"))
                  .group_by("month", "ICMS_ORIGIN").agg(pl.len().alias("n_new"),
                                                         pl.col("max_value").sum().alias("value_new")))
    pay = collect(scan_gare().join(attributes.select("ID_DEBITO", "ICMS_ORIGIN"), on="ID_DEBITO", how="left")
                  .with_columns(pl.col("DATA_ARRECADACAO_GARE").dt.truncate("1mo").alias("month"))
                  .group_by("month", "ICMS_ORIGIN").agg(pl.col("VALOR_RECEITA").sum().alias("collection"),
                                                         pl.col("ID_DEBITO").n_unique().alias("n_payers")))
    by_origin = (stock.select("month", "ICMS_ORIGIN", "stock", "n_cda")
                 .join(new, on=["month", "ICMS_ORIGIN"], how="full", coalesce=True)
                 .join(pay, on=["month", "ICMS_ORIGIN"], how="full", coalesce=True)
                 .filter(pl.col("month").is_between(pl.date(YEARS[0], 1, 1), pl.date(LAB_SNAP[0], LAB_SNAP[1], 1))))
    total = by_origin.group_by("month").agg(pl.exclude("ICMS_ORIGIN").sum()).with_columns(pl.lit("all").alias("ICMS_ORIGIN"))
    out = pl.concat([by_origin, total.select(by_origin.columns)]).sort("ICMS_ORIGIN", "month")
    return out.with_columns([pl.col(c).diff().over("ICMS_ORIGIN").alias(f"d_{c}")
                             for c in ("stock", "n_new", "collection", "n_payers")])

monthly = collect(cached(LAB_NS + "1.5", monthly_series, params={**EXTRACT_PARAMS, "cut": LAB_EXTRACT, "dedup": True}))
monthly_all = monthly.filter(pl.col("ICMS_ORIGIN") == "all")
monthly_all.tail(6)

# lab: qualidade das fotos mensais do DEBITO (ICMS, PJ). Só sinaliza: nenhum valor é corrigido.
_q = (stock_dedup.group_by("ANO_EXTRACAO", "MES_EXTRACAO")
      .agg(pl.col("n_rows").sum(), pl.col("n_cda").sum(), (pl.col("stock").sum() / 1e9).alias("stock_bn"))
      .with_columns(pl.date("ANO_EXTRACAO", "MES_EXTRACAO", 1).alias("month")).drop("ANO_EXTRACAO", "MES_EXTRACAO"))
MONTH_QUALITY = (pl.DataFrame({"month": pl.date_range(pl.date(2016, 1, 1), pl.date(*LAB_SNAP, 1), "1mo", eager=True)})
                 .join(_q, on="month", how="left").sort("month"))
_ref = MONTH_QUALITY["n_cda"].rolling_median(window_size=7, center=True, min_samples=3)
MONTH_QUALITY = MONTH_QUALITY.with_columns(pl.Series("ref_n_cda", _ref)).with_columns(
    pl.when(pl.col("n_cda").is_null()).then(pl.lit("foto ausente"))
    .when(pl.col("n_rows") > pl.col("n_cda")).then(pl.lit("linhas repetidas (somadas uma vez)"))
    .when(pl.col("n_cda") < 0.8 * pl.col("ref_n_cda")).then(pl.lit("foto parcial? (< 80% da mediana móvel de 7 meses)"))
    .otherwise(pl.lit("ok")).alias("quality"))
MONTH_QUALITY.filter(pl.col("quality") != "ok").select("month", "quality", "n_rows", "n_cda", "ref_n_cda", "stock_bn").to_pandas()

# ---- célula 31 do paper ----
MACRO_SERIES = {
    4189: ("selic", "Selic over, monthly, annualised (% p.a.; SGS 4189)", "mean"),  # lab: era a meta (SGS 432)
    433: ("ipca", "IPCA inflation (% per month)", "mean"),
    7478: ("ipca15", "IPCA-15 inflation (% per month)", "mean"),
    189: ("igpm", "IGP-M inflation (% per month)", "mean"),
    1: ("ptax", "PTAX exchange rate (R$/US$)", "mean"),
    4380: ("gdp_monthly", "Monthly GDP, current prices (log)", "log"),
    20540: ("credit_firms", "Credit to legal entities (log of balance)", "log"),
    24369: ("unemployment", "Unemployment rate (%)", "mean"),
    24379: ("employed", "Employed persons (thousand)", "mean"),
    29037: ("hh_indebtedness", "Household indebtedness (% of income)", "mean"),
    29034: ("hh_commitment", "Household income commitment (%, s.a.)", "mean"),
}

def macro_monthly() -> pl.DataFrame:
    codes = ", ".join(str(c) for c in MACRO_SERIES)
    con = duck(DUCKDB_MACRO)
    try:
        raw = con.execute(f"SELECT codigo_serie, data, valor FROM MACRO WHERE codigo_serie IN ({codes})").pl()
        raw = pl.concat([raw.filter(pl.col("codigo_serie") != 4189), _selic_over_raw(raw.schema)])  # lab: 4189 do BCB
    finally:
        con.close()
    raw = raw.with_columns(pl.col("data").dt.truncate("1mo").alias("month"),
                           pl.col("codigo_serie").replace_strict({k: v[0] for k, v in MACRO_SERIES.items()}).alias("var"))
    wide = raw.group_by("month", "var").agg(pl.col("valor").mean()).pivot(on="var", index="month", values="valor").sort("month")
    wide = wide.with_columns(pl.all().exclude("month").forward_fill())
    logs = [v[0] for v in MACRO_SERIES.values() if v[2] == "log"]
    wide = wide.with_columns([pl.col(c).log() for c in logs])
    return wide.filter(pl.col("month").is_between(pl.date(YEARS[0] - 4, 1, 1), pl.date(LAB_SNAP[0], LAB_SNAP[1], 1)))

macro_m = collect(cached(LAB_NS + "1.6:monthly", macro_monthly, params={"selic": "SGS 4189", "cut": LAB_EXTRACT}))
MACRO_VARS = [v[0] for v in MACRO_SERIES.values()]
LOG_VARS = [v[0] for v in MACRO_SERIES.values() if v[2] == "log"]
macro_q = (macro_m.with_columns(pl.col("month").dt.truncate("1q").alias("quarter"))
           .group_by("quarter").agg([pl.col(c).last() if c in LOG_VARS else pl.col(c).mean() for c in MACRO_VARS])
           .sort("quarter"))
macro_dictionary = pl.DataFrame([{"variable": v[0], "description": v[1], "sgs_code": k,
                                  "monthly_aggregation": "log of monthly mean" if v[2] == "log" else "monthly mean",
                                  "quarterly_aggregation": "end of quarter" if v[2] == "log" else "quarterly mean"}
                                 for k, v in MACRO_SERIES.items()])
save_tab(macro_dictionary, "tab_macro_dictionary")
macro_dictionary

# ---- célula 33 do paper ----
data_dictionary = pl.DataFrame([
    ("ID_DEBITO", "Integer", "Debt identifier (one per CDA)"),
    ("DATA_INSCRICAO", "Date", "Date of enrolment in the dívida ativa"),
    ("TIPO_DEBITO", "Text", "Raw debt type recorded in the source"),
    ("ICMS_ORIGIN", "Category", "declared, assessed (infraction notice, AIIM) or other"),
    ("SITUACAO_DEBITO", "Category", "Administrative situation of the CDA"),
    ("STATUS_AJUIZAMENTO_DEBITO", "Category", "Judicial filing status"),
    ("stock", "R$", "Value without fees in the last snapshot of the year"),
    ("paid", "R$", "Sum of GARE revenue paid on the CDA in the year"),
    ("rate", "Ratio", "Non-accumulated recovery rate: paid / stock"),
    ("paid_flag", "0/1", "At least one payment in the year"),
    ("age", "Years", "Time from enrolment to 31 December of the year"),
    ("enrol_year", "Year", "Enrolment cohort"),
    ("CNPJ_ROOT", "Text", "First eight digits of the CNPJ (firm); hashed in outputs"),
    ("collection", "R$", "Monthly GARE revenue on ICMS CDAs"),
    ("n_payers", "Count", "Monthly number of CDAs with a payment"),
    ("n_new", "Count", "Monthly number of newly enrolled CDAs"),
], schema=["variable", "type", "definition"], orient="row")
glossary = pl.DataFrame([
    ("CDA", "Certidão de Dívida Ativa: certificate of enrolled tax debt"),
    ("GARE", "Guia de Arrecadação Estadual: state payment slip"),
    ("AIIM", "Auto de Infração e Imposição de Multa: tax infraction notice"),
    ("Stock", "Outstanding value of enrolled debt at a given snapshot"),
    ("Cohort", "Set of CDAs enrolled in the same calendar year"),
], schema=["term", "definition"], orient="row")
save_tab(data_dictionary, "tab_data_dictionary")
data_dictionary

# lab: recorte do laboratório. Nada de 2026 nas análises e gráficos (último ano completo: 2025).
# O extrato e o painel do paper (até 2026-03) são reaproveitados do cache e filtrados aqui.
SNAP_YEAR, SNAP_MONTH = LAB_SNAP
EXTRACT = LAB_EXTRACT
YEARS = [y for y in YEARS if y <= LAB_LAST_YEAR]
ENROL_MAX = date(LAB_LAST_YEAR, 12, 31)
_CUT = LAB_SNAP[0] * 100 + LAB_SNAP[1]

def scan_icms() -> pl.LazyFrame:
    return _paper_scan_icms().filter(pl.col("year") <= LAB_LAST_YEAR)

def scan_gare() -> pl.LazyFrame:
    return _paper_scan_gare().filter(pl.col("DATA_ARRECADACAO_GARE") <= pl.date(LAB_LAST_YEAR, 12, 31))

icms, gare = scan_icms(), scan_gare()
year_end = year_end.filter(pl.col("year") <= LAB_LAST_YEAR)
gare_year = gare_year.filter(pl.col("year") <= LAB_LAST_YEAR)
panel = panel.filter(pl.col("year") <= LAB_LAST_YEAR)
attributes = (attributes.filter(pl.col("first_snapshot") <= _CUT)
              .with_columns(pl.min_horizontal("last_snapshot", pl.lit(_CUT)).alias("last_snapshot"),
                            pl.when(pl.col("first_filed_snapshot") <= _CUT).then(pl.col("first_filed_snapshot"))
                            .alias("first_filed_snapshot")))
print(f"Recorte: {YEARS[0]}–{YEARS[-1]}; foto de referência {EXTRACT}; extração congelada {os.environ['CEMEPI_EXTRACT']}.")
