"""Gera as páginas Quarto do paper EDA v1 no laboratório, a partir do notebook original (só leitura).

Uso (na raiz do worktree):  uv run --no-sync python lab/paper_eda/_gerar_paginas.py
Fonte: projects/macro/notebooks/paper_eda_v1.ipynb do checkout principal (env PAPER_EDA_NB muda o caminho).
O notebook e o _build_paper_eda_v1.py não são executados nem alterados: só as fontes das células são lidas.

O que o gerador faz:
1. Separa o notebook em páginas (dados, A, B em três partes, C, D, E).
2. Aplica as mudanças decididas nas reuniões como trocas de texto conferidas (cada troca tem que casar
   exatamente uma vez; se a fonte mudar, o gerador para em vez de gerar página errada).
3. Monta _prelude.py (seções 0 e 1 com as trocas) e, para cada página, inclui escondidas só as células de
   partes anteriores de que ela depende (análise de nomes por AST).
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import nbformat

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("PAPER_EDA_NB", Path.home() / "Code/cemepi-ctf-intel-fiscal/projects/macro/notebooks/paper_eda_v1.ipynb"))
nb = nbformat.read(SRC, 4)
CELLS = [[c.cell_type, c.source] for c in nb.cells]
SRC_SHA = hashlib.sha256(json.dumps([s for _, s in CELLS]).encode()).hexdigest()[:12]
ORIG_FIGS = []
for c in nb.cells:
    n = 0
    for o in c.get("outputs", []) if c.cell_type == "code" else []:
        d = o.get("data", {})
        n += int("application/vnd.plotly.v1+json" in d or "image/png" in d)
    ORIG_FIGS.append(n)
del nb

PRELUDE = range(3, 34)
PAGES = [
    ("01_dados", "0–1. Ambiente computacional e dados", range(3, 34)),
    ("02_parte_a", "Parte A. A carteira por tributo", range(34, 82)),
    ("03_parte_b1", "Parte B (1/3). Carteira de ICMS: evolução, tipos, origem e concentração", range(82, 132)),
    ("04_parte_b2", "Parte B (2/3). As CDAs mais concentradas", range(132, 187)),
    ("05_parte_b3", "Parte B (3/3). Idade, recuperação, pagamentos, ajuizamento, sazonalidade e programas", range(187, 256)),
    ("06_parte_c", "Parte C. Propriedades estatísticas", range(256, 285)),
    ("07_parte_d", "Parte D. Condições macroeconômicas", range(285, 331)),
    ("08_parte_e", "Parte E. Discussão e correspondência com o manuscrito", range(331, 337)),
]
LOG: list[str] = []

# ---------------------------------------------------------------------------------------------
# 1. Trocas de código. (célula ou None = procurar na faixa, texto antigo, texto novo)
# ---------------------------------------------------------------------------------------------
WRAPPERS = r'''
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
'''

STOCK_DEDUP = r'''def _stock_month_dedup() -> pl.DataFrame:
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

'''

QUALITY_CELL = r'''# lab: qualidade das fotos mensais do DEBITO (ICMS, PJ). Só sinaliza: nenhum valor é corrigido.
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
MONTH_QUALITY.filter(pl.col("quality") != "ok").select("month", "quality", "n_rows", "n_cda", "ref_n_cda", "stock_bn").to_pandas()'''

CUT_CELL = r'''# lab: recorte do laboratório. Nada de 2026 nas análises e gráficos (último ano completo: 2025).
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
print(f"Recorte: {YEARS[0]}–{YEARS[-1]}; foto de referência {EXTRACT}; extração congelada {os.environ['CEMEPI_EXTRACT']}.")'''

B85_EXTRA = r'''
# lab: o mesmo recorte com o valor no eixo Y (R$ bilhões), pedido na reunião de 23/10/2026
_vcol = "max_value" if "max_value" in settled.columns else None
_set = settled.with_columns(pl.col("years_to_exit").clip(0, 20).floor().alias("years_bin"))
_agg = [pl.len().alias("cdas")] + ([(pl.col(_vcol).sum() / 1e9).alias("value_bn")] if _vcol else [])
_val = _set.group_by("years_bin").agg(*_agg).sort("years_bin")
fig.show()
fig_val = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35], vertical_spacing=0.06)
if _vcol:
    fig_val.add_trace(go.Bar(x=_val["years_bin"], y=_val["value_bn"], marker_color=CEMEPI["deep_teal"], name="Enrolled value (max., R$ bn)"), 1, 1)
fig_val.add_trace(go.Bar(x=_val["years_bin"], y=_val["cdas"], marker_color=CEMEPI["sand"], name="CDAs"), 2, 1)
fig_val.update_yaxes(title_text="R$ billion", row=1, col=1)
fig_val.update_yaxes(title_text="CDAs", row=2, col=1)
fig_val.update_xaxes(title_text="Years from enrolment to exit after payment", row=2, col=1)
fig_val.update_layout(showlegend=True, legend=dict(orientation="h", y=1.08))
fig_val'''

def P(cell, old, new, count=1):
    return (cell, old, new, count)

PATCHES = [
    # 0.1: .env só com chaves de caminho (via _lab_base)
    P(5, "load_dotenv(find_dotenv(usecwd=True), override=False)",
      "# lab: o .env é lido por _lab_base (só chaves de caminho; sal e tokens ficam fora do kernel)"),
    # 0.3: log de cache sem print por etapa; embrulho do cache no fim da célula
    P(9, """    print(f"[cache] {step}: {'HIT' if hit else 'MISS, computing'}", flush=True)""",
      """    CACHE_LOG.append((step, "HIT" if hit else "MISS"))"""),
    P(9, "\ncache_status()", "\n" + WRAPPERS.strip("\n")),
    # 0.3: assinatura do macro.duckdb fixável (CEMEPI_MACRO_FP_PIN), ver comentário no código gerado
    P(9, 'def source_fingerprint() -> dict:\n    return {f.name: [f.stat().st_size, int(f.stat().st_mtime)] for f in (DUCKDB_MAIN, DUCKDB_MACRO)}', '# lab (26/10/2026): assinatura do macro.duckdb fixável por variável de ambiente.\n# Motivo: a chave do cache inclui tamanho e mtime do cemepi.duckdb e do macro.duckdb. Em 26/10/2026 (21:55 BRT) a\n# série SGS 4189 foi acrescentada ao macro.duckdb (798.720 -> 1.060.864 bytes), o que invalidava as 128 etapas do\n# cache (DEBITO inclusive) sem nenhum dado usado ter mudado. Verificação feita antes de fixar (read_only, 26/10/2026):\n# as 16 séries do banco antigo (backup ~/Backups/macro.duckdb.2026-10-26_pre4189, sha256 a87429ea…fe95) são idênticas\n# às do novo (sha256 1a5a5759…) por EXCEPT ALL nos dois sentidos, código a código; a única diferença é a 4189, que o lab\n# não lê do banco (usa o JSON do BCB). Uso, só ao executar localmente (o CI não executa nada, só lê o _freeze):\n#   fish: set -x CEMEPI_MACRO_FP_PIN 798720:1790442383\n# Sem a variável, o comportamento é o de antes (tamanho e mtime atuais do arquivo).\ndef source_fingerprint() -> dict:\n    fp = {f.name: [f.stat().st_size, int(f.stat().st_mtime)] for f in (DUCKDB_MAIN, DUCKDB_MACRO)}\n    pin = os.environ.get("CEMEPI_MACRO_FP_PIN", "").strip()\n    if pin:\n        size, mtime = (int(x) for x in pin.split(":"))\n        fp[DUCKDB_MACRO.name] = [size, mtime]\n    return fp'),
    # 0.5: exportação de PDF/SVG/PNG só sob pedido; estrelas nos mapas de correlação
    P(13, "    stem = FIG_DIR / label\n",
      "    stem = FIG_DIR / label\n    if os.environ.get(\"LAB_EXPORT_FIGS\", \"0\") != \"1\":  # lab: exportar só sob pedido\n        return fig\n"),
    P(13, """    if text is None:
        text = [[("" if np.isnan(v) else fmt.format(v)) for v in r] for r in z]""",
      """    _starred = False
    if text is None:
        _use = diverging and colorbar_title == "Correlation"
        text = [[("" if np.isnan(v) else fmt.format(v) + (stars(_RP.get(round(float(v), 12))) if _use else "")) for v in r] for r in z]
        _starred = _use and any(_RP.get(round(float(v), 12)) is not None for r in z for v in r if not np.isnan(v))"""),
    P(13, """                      meta={"min_height": 80 + 26 * len(z)})
    return fig""",
      """                      meta={"min_height": 80 + 26 * len(z)})
    if _starred:  # lab: estrelas de significância (decisão com a Profª Lívia)
        fig.add_annotation(text="Cor = sinal e força da correlação; estrelas = significância: * p ≤ 0,10  ** p ≤ 0,05  "
                                "*** p ≤ 0,01 (sem correção para testes múltiplos)", x=0, y=1.0, yanchor="bottom",
                           xref="paper", yref="paper", showarrow=False, xanchor="left", font_size=10)
        fig.update_layout(margin=dict(t=40))
    return fig"""),
    # 0.6: corr_pair registra p para as estrelas
    P(15, """    return float(r[0]), float(r[1])""",
      """    _RP[round(float(r[0]), 12)] = float(r[1])  # lab: para as estrelas nos mapas de calor
    return float(r[0]), float(r[1])"""),
    # 1.2: foto de dezembro de 2025, sem ler CPF_DEVEDOR
    P(21, """IDENT_SQL = \"\"\"CASE WHEN COALESCE(CPF_DEVEDOR, '') <> '' THEN 'CPF'
                    WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ' ELSE 'None' END\"\"\"""",
      """IDENT_SQL = \"\"\"CASE WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ' ELSE 'No CNPJ' END\"\"\"  # lab: sem ler CPF_DEVEDOR"""),
    P(21, "WHERE ANO_EXTRACAO = {SNAP_YEAR} AND MES_EXTRACAO = {SNAP_MONTH}",
      "WHERE ANO_EXTRACAO = {LAB_SNAP[0]} AND MES_EXTRACAO = {LAB_SNAP[1]}"),
    P(21, "DATE '{SNAP_YEAR}-{SNAP_MONTH:02d}-01'", "DATE '{LAB_SNAP[0]}-{LAB_SNAP[1]:02d}-01'"),
    P(21, 'query("1.2", SNAPSHOT_SQL, params={"snapshot": EXTRACT})',
      'query(LAB_NS + "1.2", SNAPSHOT_SQL, params={"snapshot": LAB_EXTRACT})'),
    # 1.3: tabela de contagens sem 2026 (o extrato fica como está, em cache)
    P(23, """                     .sort("year"))
save_tab(extraction_counts""", """                     .sort("year")).filter(pl.col("year") <= LAB_LAST_YEAR)  # lab: sem 2026
save_tab(extraction_counts"""),
    # 1.3.1: foto de dezembro de 2025, CNPJ contra sem CNPJ (sem ler CPF_DEVEDOR)
    P(25, """           CASE WHEN COALESCE(CPF_DEVEDOR, '') <> '' THEN 'CPF (natural person)'
                WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ (legal entity)'
                ELSE 'No identifier' END AS identifier,""",
      """           CASE WHEN COALESCE(CNPJ_DEVEDOR, '') <> '' THEN 'CNPJ (legal entity)'
                ELSE 'No CNPJ (CPF or no identifier)' END AS identifier,  -- lab: CNPJ x sem CNPJ, sem ler a coluna de pessoa"""),
    P(25, "WHERE ANO_EXTRACAO = {SNAP_YEAR} AND MES_EXTRACAO = {SNAP_MONTH} AND TIPO_DEBITO",
      "WHERE ANO_EXTRACAO = {LAB_SNAP[0]} AND MES_EXTRACAO = {LAB_SNAP[1]} AND TIPO_DEBITO"),
    P(25, 'query("1.3.1", DEBTOR_ID_SQL, params={"snapshot": EXTRACT})',
      'query(LAB_NS + "1.3.1", DEBTOR_ID_SQL, params={"snapshot": LAB_EXTRACT})'),
    P(25, """cached("1.3.1:extract_snapshot", lambda: icms.filter(
    (pl.col("year") == SNAP_YEAR) & (pl.col("MES_EXTRACAO") == SNAP_MONTH))""",
      """cached(LAB_NS + "1.3.1:extract_snapshot", lambda: icms.filter(
    (pl.col("year") == LAB_SNAP[0]) & (pl.col("MES_EXTRACAO") == LAB_SNAP[1]))"""),
    P(25, "params=EXTRACT_PARAMS))[0, 0]", 'params={**EXTRACT_PARAMS, "snap": LAB_EXTRACT}))[0, 0]'),
    P(25, """print(f"Legal-entity CDAs in the source snapshot""",
      """print("Lab: sem ler CPF_DEVEDOR, CDAs com CNPJ e CPF ao mesmo tempo contam como CNPJ na fonte; "
      "o extrato (1.3) exclui essas CDAs, então a diferença fonte − extrato mede esse grupo.")
print(f"Legal-entity CDAs in the source snapshot"""),
    # 1.5: estoque mensal sem linhas repetidas; série até dez/2025
    P(29, "def monthly_series() -> pl.DataFrame:", STOCK_DEDUP + "def monthly_series() -> pl.DataFrame:"),
    P(29, """    stock = (scan_icms().group_by("ANO_EXTRACAO", "MES_EXTRACAO", "ICMS_ORIGIN")
             .agg(pl.col("VALOR_SEM_HONORARIOS").sum().alias("stock"), pl.len().alias("n_cda")))
    stock = collect(stock).with_columns(pl.date(pl.col("ANO_EXTRACAO"), pl.col("MES_EXTRACAO"), 1).alias("month"))""",
      """    stock = stock_dedup.select("ANO_EXTRACAO", "MES_EXTRACAO", "ICMS_ORIGIN", "stock", "n_cda").with_columns(
        pl.date(pl.col("ANO_EXTRACAO"), pl.col("MES_EXTRACAO"), 1).alias("month"))  # lab: sem linhas repetidas"""),
    P(29, "pl.date(SNAP_YEAR, SNAP_MONTH, 1)", "pl.date(LAB_SNAP[0], LAB_SNAP[1], 1)"),
    P(29, 'cached("1.5", monthly_series, params=EXTRACT_PARAMS)',
      'cached(LAB_NS + "1.5", monthly_series, params={**EXTRACT_PARAMS, "cut": LAB_EXTRACT, "dedup": True})'),
    # 1.6: Selic over (SGS 4189) no lugar da meta (SGS 432); série até dez/2025
    P(31, """    432: ("selic", "Selic target rate (% p.a.)", "mean"),""",
      """    4189: ("selic", "Selic over, monthly, annualised (% p.a.; SGS 4189)", "mean"),  # lab: era a meta (SGS 432)"""),
    P(31, """        raw = con.execute(f"SELECT codigo_serie, data, valor FROM MACRO WHERE codigo_serie IN ({codes})").pl()""",
      """        raw = con.execute(f"SELECT codigo_serie, data, valor FROM MACRO WHERE codigo_serie IN ({codes})").pl()
        raw = pl.concat([raw.filter(pl.col("codigo_serie") != 4189), _selic_over_raw(raw.schema)])  # lab: 4189 do BCB"""),
    P(31, "pl.date(SNAP_YEAR, SNAP_MONTH, 1)", "pl.date(LAB_SNAP[0], LAB_SNAP[1], 1)"),
    P(31, 'cached("1.6:monthly", macro_monthly)',
      'cached(LAB_NS + "1.6:monthly", macro_monthly, params={"selic": "SGS 4189", "cut": LAB_EXTRACT, '
      '"deflator": "IPCA 433, base 2025-12", "pnad": "trimestre civil"})'),
    # 1.6 (MAC-07, 26/10/2026): PIB mensal (4380) e crédito PJ (20540) em R$ de dez/2025, deflacionados pelo IPCA (433);
    # PNAD Contínua (24369, 24379) é trimestre móvel: no trimestre e no ano entram só os trimestres civis (mar/jun/set/dez)
    P(31, '''    4380: ("gdp_monthly", "Monthly GDP, current prices (log)", "log"),''',
      '''    4380: ("gdp_monthly", "Monthly GDP, R$ of Dec 2025, IPCA-deflated (log)", "log"),  # lab: era preços correntes'''),
    P(31, '''    20540: ("credit_firms", "Credit to legal entities (log of balance)", "log"),''',
      '''    20540: ("credit_firms", "Credit to legal entities, R$ of Dec 2025, IPCA-deflated (log of balance)", "log"),  # lab: era nominal'''),
    P(31, '''    24369: ("unemployment", "Unemployment rate (%)", "mean"),''',
      '''    24369: ("unemployment", "Unemployment rate (%; PNAD rolling quarter ending in the month)", "mean"),'''),
    P(31, '''    24379: ("employed", "Employed persons (thousand)", "mean"),''',
      '''    24379: ("employed", "Employed persons (thousand; PNAD rolling quarter ending in the month)", "mean"),'''),
    P(31, '''"quarterly_aggregation": "end of quarter" if v[2] == "log" else "quarterly mean"}''',
      '''"quarterly_aggregation": "end of quarter" if v[2] == "log" or v[0] in PNAD_VARS else "quarterly mean"}'''),
    P(31, "def macro_monthly() -> pl.DataFrame:",
      '''PNAD_VARS = ["unemployment", "employed"]  # lab: trimestre móvel; no trimestre e no ano só os trimestres civis

def macro_monthly() -> pl.DataFrame:'''),
    P(31, '''    wide = wide.with_columns(pl.all().exclude("month").forward_fill())
''', '''    wide = wide.with_columns(pl.all().exclude("month").forward_fill())
    # lab (MAC-07): índice do IPCA encadeado das variações mensais (SGS 433), base dez/2025 = 1; PIB e crédito PJ reais
    wide = wide.with_columns(((1 + pl.col("ipca") / 100).cum_prod()).alias("_ipca_idx"))
    _base = wide.filter(pl.col("month") == pl.date(LAB_SNAP[0], LAB_SNAP[1], 1))["_ipca_idx"][0]
    wide = wide.with_columns((pl.col("gdp_monthly") / (pl.col("_ipca_idx") / _base)).alias("gdp_monthly"),
                             (pl.col("credit_firms") / (pl.col("_ipca_idx") / _base)).alias("credit_firms")).drop("_ipca_idx")
'''),
    P(31, '''.group_by("quarter").agg([pl.col(c).last() if c in LOG_VARS else pl.col(c).mean() for c in MACRO_VARS])''',
      '''.group_by("quarter").agg([pl.col(c).last() if c in LOG_VARS or c in PNAD_VARS else pl.col(c).mean() for c in MACRO_VARS])  # lab: PNAD = trimestre civil'''),
    # D.5.1: a chave do cache não via a troca das macros (MAC-07); sem isto o bootstrap voltaria do cache nominal
    P(None, '''cached_json("D.5.1", robust_monthly, params={"block": 12, "n_boot": 999})''',
      '''cached_json("D.5.1", robust_monthly, params={"block": 12, "n_boot": 999, "macro": "MAC-07: PIB e crédito reais, PNAD civil"})'''),
    P(296, '''    [pl.col(c).last() if c in LOG_VARS else pl.col(c).mean() for c in MACRO_VARS]).sort("year")''',
      '''    [pl.col(c).last() if c in LOG_VARS
     else pl.col(c).filter(pl.col("month").dt.month().is_in([3, 6, 9, 12])).mean() if c in PNAD_VARS  # lab: 4 trimestres civis
     else pl.col(c).mean() for c in MACRO_VARS]).sort("year")'''),
    # A.1.4: no lugar da amostra de 10 linhas (registro individual), só o esquema da tabela
    P(43, None, '''# lab: no lugar da amostra de 10 linhas da foto (registros individuais), o esquema da tabela DEBITO,
# sem as colunas de pessoa. O laboratório só mostra agregados.
def debito_schema() -> pl.DataFrame:
    con = duck()
    try:
        return con.execute("""SELECT column_name, data_type FROM information_schema.columns
                              WHERE table_name = 'DEBITO' AND column_name NOT IN ('NOME_DEVEDOR', 'CPF_DEVEDOR')
                              ORDER BY ordinal_position""").pl()
    finally:
        con.close()

probe = collect(cached("A.1.4", debito_schema))
save_tab(probe, "tab_probe_month")
probe'''),
    # A.2.1: não ler colunas de pessoa nem para contar nulos
    P(46, """        for t, c in cols:
            by_table.setdefault(t, []).append(c)""",
      """        for t, c in cols:
            if c.upper() in ("NOME_DEVEDOR", "CPF_DEVEDOR"):  # lab: colunas de pessoa não são lidas
                continue
            by_table.setdefault(t, []).append(c)"""),
    P(None, """((pl.col("identifier") == "None").mean() * 100).alias("debtor_identifier")""",
      """((pl.col("identifier") == "No CNPJ").mean() * 100).alias("debtor_identifier")"""),
    P(None, '("Missing debtor identifier (%)"', '("No CNPJ, i.e. CPF or no identifier (%) [lab: era só sem identificador]"'),
    # B.5.24: sal efêmero (o sal do projeto não entra no kernel) e só agregados
    P(186, """SALT = os.environ.get("CEMEPI_HASH_SALT")
if not SALT:
    raise RuntimeError("CEMEPI_HASH_SALT must be set in .env to anonymise debtor identifiers.")""",
      """SALT = secrets.token_hex(32)  # lab: sal efêmero por execução, nunca gravado; o sal do projeto não entra no kernel"""),
    P(186, "archetype_members.head()",
      """# lab: só agregados (o paper mostrava as primeiras linhas com o hash)
archetype_members.group_by("agglomerative").agg(pl.len().alias("rows"), (pl.col("value").sum() / 1e9).alias("value_bn")).sort("agglomerative")"""),
    # B.8.5: versão com valor monetário no eixo Y
    P(216, """save_fig(fig, "fig_settlement_proxy")
fig""", """save_fig(fig, "fig_settlement_proxy")""" + B85_EXTRA),
    # B.10: o paper tirava o ano parcial (2026) das médias; no lab 2025 é completo e entra
    P(None, 'pl.col("y") < SNAP_YEAR', 'pl.col("y") <= LAB_LAST_YEAR'),
    P(None, 'monthly_all.filter(pl.col("month") < pl.date(SNAP_YEAR, 1, 1))',
      'monthly_all.filter(pl.col("month") <= pl.date(LAB_LAST_YEAR, 12, 1))  # lab: 2025 completo entra'),
]

# Trocas de texto em todo o código fora do prelúdio (rótulos de período)
CODE_GLOBAL = [("2016-2026", "2016-2025"), ("2016–2026", "2016–2025"),
               # CTF-03 (26/10/2026): sem WebGL. O B.5.18 vira mapa de densidade (_agrega_plotly), mas o código mostrado
               # também deixa de pedir scattergl
               ("go.Scattergl(", "go.Scatter(")]

# Trocas em markdown (texto do Étore): só datas e Selic, sempre com marcador visível
MD_GLOBAL = [
    ("January 2016 to March 2026", "January 2016 to December 2025 [lab: era até março de 2026]"),
    ("the March 2026 snapshot", "the December 2025 snapshot [lab: era março de 2026]"),
    ("March 2026 snapshot", "December 2025 snapshot [lab: era março de 2026]"),
    ("2016–2026", "2016–2025 [lab: era 2016–2026]"),
    ("2016-2026", "2016–2025 [lab: era 2016–2026]"),
    ("year 2026", "year 2025 [lab: era 2026]"),
    ("before March 2026", "before December 2025 [lab: era março de 2026]"),
]

# Markdown que descreve a fonte ou o extrato (até mar/2026, fato) fica como está
MD_KEEP = {2, 17, 22, 26}

# Avisos e ligações inseridos depois de células (markdown)
LINK_KM = "../eda/2026-10-23_kmeans_top1.qmd"
LINK_CONC = "../eda/2026-10-23_concentracao_e_liquidacao.qmd"
LINK_MACRO = "../eda/2026-10-23_macro_selic_over_checagem.qmd"
CALLOUTS = {
    30: f""":::{{.callout-note}}
## Selic over no lugar da meta
No laboratório, a variável `selic` é a **Selic over** (SGS 4189, acumulada no mês e anualizada, % a.a.), e não a meta
(SGS 432). A troca e o efeito nas correlações estão na página [Selic over e checagem]({LINK_MACRO}).

**Também alterado no lab (MAC-07, 26/10/2026):** a série 4380 é o PIB mensal em R$ correntes (não o IBC-Br citado no
texto do paper). Ela e o saldo de crédito PJ (20540) passam a entrar **deflacionados pelo IPCA** (SGS 433, índice
encadeado das variações mensais, base dez/2025 = 1), em R$ de dez/2025, antes do log. Desocupação (24369) e ocupados
(24379) são da PNAD Contínua, cujo valor mensal é o **trimestre móvel** que termina no mês (meses vizinhos repetem dois
meses de entrevistas). No trimestre, entra o valor do último mês (o trimestre civil) e, no ano, a média dos quatro
trimestres civis. No mês, o valor fica como está.
:::""",
    125: f""":::{{.callout-note}}
## Concentração por tipo de tributo e por devedor
A concentração por grupo de tributo (ICMS, IPVA, ITCMD, taxas) e por devedor (raiz do CNPJ), na foto de
dezembro de 2025, está na página [Concentração e liquidação]({LINK_CONC}). Aqui fica a concentração do ICMS, como no paper.
:::""",
    171: f""":::{{.callout-warning}}
## B.5.18: figura do paper e versão corrigida
A figura abaixo é a do paper (PCA com escala mediana/IQR), apontada como errada na reunião de 23/10/2026.
A versão corrigida (PCA na escala do Gower e t-SNE) está em [K-means no top 1%]({LINK_KM}) e está
**a validar pelo Étore**.
:::""",
    173: f""":::{{.callout-warning}}
## Escolha do k
O paper escolhe o número de grupos do hierárquico automaticamente, pela maior silhueta (`K_STAR`). Isso só reproduz o
paper. **No laboratório, a solução principal é o K-means com k = 4, por decisão do autor (IMP-13, 27/10/2026).** O K-means
com 100 inicializações por k, com os perfis de k = 4 e o diagnóstico de k = 2 a 8, está em [K-means no top 1%]({LINK_KM}).
:::""",
    215: f""":::{{.callout-note}}
## B.8.5 com valor no eixo Y
Além do histograma do paper (número de CDAs), a célula abaixo mostra o valor inscrito em R$ bilhões por ano até a saída.
A versão com valor pago está em [Concentração e liquidação]({LINK_CONC}).
:::""",
    294: f""":::{{.callout-warning}}
## Checagem das correlações anuais
**Aviso (MAC-07): não use a correlação anual entre emprego e estoque no texto.** Ela vem da tendência comum e da
queda de 2020: no painel anual do EDA completo, o estoque cai de R$ 410,6 bi (2019) para R$ 328,9 bi (2020), junto com a contagem de CDAs,
o que parece efeito da extração do DEBITO, não da economia. Isso só pode ser corrigido com a DEBITO corrigida
(EQ-02/EQ-05); até lá, fica este aviso. Com n = 10 anos, nenhum mapa anual abaixo sustenta afirmação. O que deu para
corrigir (defasagem única, sem 2026, PIB e crédito reais, PNAD em trimestres civis) já está aplicado aqui e está
detalhado, com antes e depois, em [Selic over e checagem]({LINK_MACRO}), seções 2.5 e 3.
:::""",
}

QUALITY_NOTE = """:::{.callout-important}
## Qualidade do DEBITO (só sinalizado, nada corrigido)
- Faltam as fotos de **2018-12** e de **2020-10 a 2020-12**. O arquivo de 2020 M10–M12 repete o de M04–M06, e o de
  2018 M10–M12 está truncado (2018-11 parcial). A foto de **2024-03** está parcial.
- Consequência no painel: o "fim de ano" de 2018 é a última foto em que cada CDA aparece (out. ou nov.), e o de 2020 é setembro.
- No laboratório, o estoque mensal soma **uma linha por CDA e mês** (o paper somava as linhas repetidas). A tabela de
  qualidade está na página [Dados](01_dados.qmd#lab-qualidade).
:::"""
QUALITY_PAGES = {"01_dados", "03_parte_b1", "05_parte_b3", "06_parte_c", "07_parte_d"}

# ---------------------------------------------------------------------------------------------
def apply_patches():
    used = [0] * len(PATCHES)
    for i, (cell, old, new, count) in enumerate(PATCHES):
        targets = [cell] if cell is not None else [j for j, (t, s) in enumerate(CELLS) if t == "code" and old and old in s]
        assert len(targets) == 1, f"troca {i} casou com {len(targets)} células: {old[:60]!r}"
        j = targets[0]
        t, s = CELLS[j]
        assert t == "code", (i, j)
        if old is None:
            CELLS[j][1] = new
        else:
            n = s.count(old)
            assert n == count, f"troca {i} (célula {j}) casou {n} vezes: {old[:60]!r}"
            CELLS[j][1] = s.replace(old, new)
        used[i] = j
        LOG.append(f"troca {i:2d} na célula {j}")
    for j, (t, s) in enumerate(CELLS):
        if t == "code" and j not in PRELUDE:
            for a, b in CODE_GLOBAL:
                if a in s:
                    LOG.append(f"código {j}: {a} -> {b} ({s.count(a)}x)")
                    s = s.replace(a, b)
            CELLS[j][1] = s
        elif t == "markdown" and j not in MD_KEEP:
            for a, b in MD_GLOBAL:
                if a in s:
                    k = s.count(a)
                    s = re.sub(re.escape(a) + r"(?! \[lab)", b, s)
                    LOG.append(f"markdown {j}: {a!r} ({k}x)")
            CELLS[j][1] = s


# ---------------------------------------------------------------------------------------------
# 2. Dependências entre células (nomes de nível de módulo)
# ---------------------------------------------------------------------------------------------
BUILTINS = set(dir(builtins)) | {"get_ipython", "display", "__name__"}

def top_defs(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: set[str] = set()

    def targets(t):
        for n in ast.walk(t):
            if isinstance(n, ast.Name):
                out.add(n.id)

    def visit(stmts):
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(s.name)
            elif isinstance(s, (ast.Assign,)):
                for t in s.targets:
                    targets(t)
            elif isinstance(s, (ast.AugAssign, ast.AnnAssign)):
                targets(s.target)
            elif isinstance(s, (ast.Import, ast.ImportFrom)):
                for a in s.names:
                    out.add((a.asname or a.name).split(".")[0])
            elif isinstance(s, (ast.For, ast.AsyncFor)):
                targets(s.target)
                visit(s.body); visit(s.orelse)
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                for it in s.items:
                    if it.optional_vars is not None:
                        targets(it.optional_vars)
                visit(s.body)
            elif isinstance(s, ast.If) or isinstance(s, ast.While):
                visit(s.body); visit(s.orelse)
            elif isinstance(s, ast.Try):
                visit(s.body); visit(s.orelse); visit(s.finalbody)
                for h in s.handlers:
                    visit(h.body)
            elif isinstance(s, ast.Expr) and isinstance(s.value, ast.NamedExpr):
                targets(s.value.target)
    visit(tree.body)
    return out

def uses(src: str) -> set[str]:
    """Nomes globais lidos pela célula (escopo correto: ignora variáveis locais de funções, lambdas e compreensões)."""
    import symtable
    try:
        top = symtable.symtable(src, "<cell>", "exec")
    except SyntaxError:
        return set()
    out: set[str] = set()

    def walk(t, is_module):
        for sym in t.get_symbols():
            if not sym.is_referenced():
                continue
            if is_module or sym.is_global() or (sym.is_free() and False):
                out.add(sym.get_name())
        for ch in t.get_children():
            walk(ch, False)
    walk(top, True)
    return out - BUILTINS


HEADER_SRC = (HERE / "_lab_header.py").read_text()
PRE_NAMES = top_defs(HEADER_SRC) | {"_paper_cached", "_paper_query", "_paper_scan_icms", "_paper_scan_gare", "_CUT",
                                     "MONTH_QUALITY", "_q", "_ref", "_ns", "_BANNED"}

def _imported(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {(a.asname or a.name).split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}

IMPORTED = set().union(*(_imported(s) for t, s in CELLS if t == "code"))

def deps_for(page_cells: list[int]) -> list[int]:
    defs = [top_defs(s) if t == "code" else set() for t, s in CELLS]
    pre = set(PRE_NAMES)
    for j in PRELUDE:
        pre |= defs[j]
    start = page_cells[0]
    in_page = set().union(*(defs[j] for j in page_cells))
    need: set[int] = set()
    todo = [(j, uses(CELLS[j][1]) - in_page) for j in page_cells if CELLS[j][0] == "code"]
    while todo:
        j, names = todo.pop()
        for name in {n for n in names - pre if len(n) > 2 or n in IMPORTED}:  # nomes de 1-2 letras são temporários, salvo apelidos de import
            prov = [k for k in range(PRELUDE.stop, min(j, start)) if name in defs[k]]
            if not prov:
                continue
            k = prov[-1]
            if k not in need:
                need.add(k)
                todo.append((k, uses(CELLS[k][1]) - defs[k]))
    return sorted(need)


# ---------------------------------------------------------------------------------------------
# 3. Escrita
# ---------------------------------------------------------------------------------------------
def chunk(src: str, opts: list[str] | None = None) -> str:
    o = "".join(f"#| {x}\n" for x in (opts or []))
    return f"```{{python}}\n{o}{src.rstrip()}\n```\n"

def front(title: str, desc: str) -> str:
    return f"""---
title: "{title}"
description: "{desc}"
date: 2026-10-26
jupyter: python3
execute:
  echo: true
format:
  html:
    code-fold: true
    code-summary: "Código"
---
"""

PRELUDE_CHUNK = chunk('exec(compile(open("_prelude.py").read(), "_prelude.py", "exec"))', ["label: prelude", "include: false"])
CACHE_CHUNK = chunk('pl.DataFrame(CACHE_LOG, schema=["etapa", "status"], orient="row").group_by("status").len().sort("status")',
                    ["label: lab-cache", "echo: false"])

def banner(name: str) -> str:
    return f"""::: {{.callout-tip collapse="true"}}
## Sobre esta página (porte do paper EDA v1 para o laboratório)
Gerada por `lab/paper_eda/_gerar_paginas.py` a partir de `paper_eda_v1.ipynb` (fontes sha256 `{SRC_SHA}`), sem rodar
nem alterar o notebook original. Texto do paper mantido em inglês; o que o laboratório mudou aparece marcado com
**[lab: ...]** ou em comentários `# lab:` no código. Mudanças em todas as páginas: nada de 2026 (último ano completo 2025;
foto de referência dez/2025), Selic over (SGS 4189) no lugar da meta, estrelas de significância (* p ≤ 0,10; ** p ≤ 0,05;
*** p ≤ 0,01) e linhas em negrito quando p ≤ 0,10 nas tabelas, e só agregados (nada por CPF, nome ou CDA individual).
:::
"""

def write_pages():
    inventory = {}
    prelude_parts = [HEADER_SRC]
    for j in PRELUDE:
        if CELLS[j][0] == "code":
            prelude_parts.append(f"# ---- célula {j} do paper ----\n{CELLS[j][1]}")
            if j == 29:
                prelude_parts.append(QUALITY_CELL)
    prelude_parts.append(CUT_CELL)
    body = "\n\n".join(prelude_parts).replace("from __future__ import annotations\n", "")
    (HERE / "_prelude.py").write_text("# Gerado por _gerar_paginas.py. Não editar à mão.\nfrom __future__ import annotations\n" + body + "\n")

    for name, title, rng in PAGES:
        cells = list(rng)
        out = [front(title, f"Porte do paper EDA v1 ({title.split('.')[0]}) com as mudanças das reuniões de out/2026."), banner(name)]
        if name in QUALITY_PAGES:
            out.append(QUALITY_NOTE)
        if name == "01_dados":
            out.append(chunk(HEADER_SRC, ["label: lab-header", "include: false"]))
        else:
            out.append(PRELUDE_CHUNK)
            dep = deps_for(cells)
            for k in dep:
                out.append(chunk(CELLS[k][1], [f"label: dep-{k}", "include: false"]))
            LOG.append(f"{name}: dependências escondidas {dep}")
        n_code = n_md = 0
        for j in cells:
            t, s = CELLS[j]
            if t == "markdown":
                n_md += 1
                s = re.sub(r"^# ", "## ", s, flags=re.M) if j == 0 else s
                out.append(s.strip() + "\n")
            else:
                n_code += 1
                out.append(chunk(s, [f"label: c{j}"]))
                if name == "01_dados" and j == 29:
                    out.append("#### lab: qualidade das fotos mensais {#lab-qualidade}\n")
                    out.append(chunk(QUALITY_CELL, ["label: lab-qualidade-tab"]))
            if j in CALLOUTS:
                out.append(CALLOUTS[j] + "\n")
        if name == "01_dados":
            out.append("#### lab: recorte do laboratório\n")
            out.append(chunk(CUT_CELL, ["label: lab-recorte"]))
        if n_code:
            out.append("#### lab: uso do cache nesta página\n")
            out.append(CACHE_CHUNK)
        (HERE / f"{name}.qmd").write_text("\n".join(out))
        inventory[name] = {"cells": len(cells), "code": n_code, "markdown": n_md,
                           "figs_original": sum(ORIG_FIGS[j] for j in cells),
                           "fig_labels": sorted({m for j in cells for m in re.findall(r"`(fig_[A-Za-z0-9_]+)`", CELLS[j][1])}),
                           "tab_labels": sorted({m for j in cells for m in re.findall(r"`(tab_[A-Za-z0-9_]+)`", CELLS[j][1])})}
    (HERE / "_inventario.json").write_text(json.dumps({"fonte_sha": SRC_SHA, "paginas": inventory}, indent=1, ensure_ascii=False))
    return inventory


if __name__ == "__main__":
    apply_patches()
    inv = write_pages()
    print("\n".join(LOG))
    for k, v in inv.items():
        print(k, {a: b for a, b in v.items() if a not in ("fig_labels", "tab_labels")}, len(v["fig_labels"]), "fig labels")
    # Página única (lab/paper_eda/index.qmd): junta o resultado já executado das partes (lab/_freeze/paper_eda/).
    # As partes não são páginas do site (ficam fora do render em _quarto.yml). Se uma parte mudou, renderize-a
    # antes (quarto render lab/paper_eda/<parte>.qmd, ver lab/README.md) e rode de novo; _pagina_unica.py avisa.
    import subprocess
    subprocess.run([sys.executable, str(HERE / "_pagina_unica.py")], check=False)
