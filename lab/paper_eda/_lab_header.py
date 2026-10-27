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
