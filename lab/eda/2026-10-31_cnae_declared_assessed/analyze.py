"""Declared versus assessed ICMS debt, by the official CNAE 2.3 hierarchy.

Population: the lab ICMS extract, December 2025 snapshot, legal entities only.
Payments: GARE dates before 2026-01-01. The 2026 payment partition is not read.
Firm attributes: the Receita Federal CNPJ registry already built for the lab
(`cnpj_enriquecido`). CNAE names and parents come from the IBGE CNAE-Subclasses
2.3 structure. Outputs are aggregates. No CNPJ, CDA or name is written.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dotenv import dotenv_values
from plotly.subplots import make_subplots
from scipy import stats

HERE = Path(__file__).resolve().parent
XLSX = HERE / "reference" / "CNAE_Subclasses_2_3_Estrutura_Detalhada.xlsx"
AS_OF = "2025-12-31"
PAY_END = "2026-01-01"
DECLARED = "#154C56"
ASSESSED = "#D55E00"
GREY = "#8A9399"

SECTION_EN = {
    "A": "Agriculture, forestry and fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity and gas",
    "E": "Water, sewerage and waste",
    "F": "Construction",
    "G": "Trade and repair of motor vehicles",
    "H": "Transportation and storage",
    "I": "Accommodation and food",
    "J": "Information and communication",
    "K": "Financial and insurance",
    "L": "Real estate",
    "M": "Professional, scientific and technical",
    "N": "Administrative and support services",
    "O": "Public administration",
    "P": "Education",
    "Q": "Health and social work",
    "R": "Arts, culture and sport",
    "S": "Other services",
    "T": "Domestic services",
    "U": "International organisations",
}
STATUS_EN = {
    "02": "Active",
    "04": "Unfit (inapta)",
    "03": "Suspended",
    "08": "Closed (baixada)",
    "01": "Null",
}
PORTE_EN = {
    "00": "Not informed",
    "01": "Micro",
    "03": "Small",
    "05": "Other sizes",
}
SEG_EN = {"declared": "Declared", "assessed": "Assessed"}
# Published lab page, section 1.3, December 2025, legal entities.
# Used only as a check. The analysis uses the extract itself.
PUBLISHED_PJ = {
    "ICMS (all types)": (2_325_280, 427.880344),
    "ICMS Autuação": (58_725, 311.253429),
    "ICMS Alteração de GIA": (36, 0.009339),
}


def repo_root() -> Path:
    for p in HERE.parents:
        if (p / "pyproject.toml").exists() and (p / "lab").exists():
            return p
    raise SystemExit("Repository root not found.")


def paths() -> dict[str, Path]:
    env = dotenv_values(repo_root() / ".env")
    interim = Path(env["CEMEPI_INTERIM"])
    work = Path(env["CNPJ_PIPELINE_WORKDIR"])
    out = {
        "icms": interim / "icms" / "year=2025" / "*.parquet",
        "gare": interim / "gare_icms",
        "firm": work / "build" / "2026-03" / "cnpj_enriquecido" / "*.parquet",
        "cnae_codes": work / "enrich" / "2026-03" / "codes" / "cnaes.parquet",
        "tmp": work / "tmp",
        "cache": Path(env["CEMEPI_DATA_ROOT"]) / "interim" / "cnae_segmentado_20261031",
    }
    for key in ("icms", "firm", "cnae_codes"):
        parent = out[key].parent
        if not parent.exists():
            raise SystemExit(f"{key} is not available. Is the external disk mounted?")
    if not out["gare"].exists():
        raise SystemExit("GARE extract is not available. Is the external disk mounted?")
    out["tmp"].mkdir(parents=True, exist_ok=True)
    out["cache"].mkdir(parents=True, exist_ok=True)
    return out


def digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def parse_cnae23(path: Path) -> pd.DataFrame:
    """Read the official IBGE outline. Each subclass inherits the open parents."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    tag = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as book:
        shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
        strings = [
            "".join(node.text or "" for node in si.iter(f"{tag}t"))
            for si in shared.findall("m:si", ns)
        ]
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[str, str]] = []
    for row in sheet.findall("m:sheetData/m:row", ns):
        item: dict[str, str] = {}
        for cell in row.findall("m:c", ns):
            ref = cell.get("r") or ""
            col = "".join(ch for ch in ref if ch.isalpha())
            raw = cell.find("m:v", ns)
            if raw is None or raw.text is None:
                continue
            item[col] = strings[int(raw.text)] if cell.get("t") == "s" else raw.text
        rows.append(item)

    sec = div = grp = cls = ""
    sec_name = div_name = grp_name = cls_name = ""
    records = []
    for item in rows:
        name = (item.get("F") or "").replace("\n", " ").strip()
        letter = (item.get("A") or "").strip()
        if letter:
            if len(letter) != 1 or not letter.isalpha():
                continue
            sec, sec_name = letter, name
            div = grp = cls = ""
            div_name = grp_name = cls_name = ""
        elif item.get("B"):
            div, div_name = digits(item["B"]).zfill(2), name
            grp = cls = ""
            grp_name = cls_name = ""
        elif item.get("C"):
            grp, grp_name = digits(item["C"]).zfill(3), name
            cls = ""
            cls_name = ""
        elif item.get("D"):
            cls, cls_name = digits(item["D"]).zfill(5), name
        elif item.get("E"):
            sub = digits(item["E"]).zfill(7)
            records.append(
                {
                    "subclass": sub,
                    "subclass_name": name,
                    "class": cls,
                    "class_name": cls_name,
                    "group": grp,
                    "group_name": grp_name,
                    "division": div,
                    "division_name": div_name,
                    "section": sec,
                    "section_name_pt": sec_name,
                    "in_cnae23": True,
                }
            )
    frame = pd.DataFrame.from_records(records).drop_duplicates("subclass")
    if frame.empty:
        raise SystemExit("CNAE 2.3 workbook has no subclasses.")
    return frame


def connect(tmp: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        f"""
        SET memory_limit='4GB';
        SET threads=4;
        SET temp_directory='{tmp}';
        SET preserve_insertion_order=false
        """
    )
    return con


def build(
    con: duckdb.DuckDBPyConnection, src: dict[str, Path], hier: pd.DataFrame
) -> None:
    gare_files = [
        str(path)
        for year in range(2016, 2026)
        for path in sorted((src["gare"] / f"year={year}").glob("*.parquet"))
    ]
    if not gare_files:
        raise SystemExit("No GARE files for 2016-2025.")
    con.register("hier_df", hier)
    con.execute("CREATE TABLE hier AS SELECT * FROM hier_df")
    t0 = time.time()
    con.execute(
        f"""
        CREATE TABLE cda AS
        SELECT ID_DEBITO, TIPO_DEBITO, ICMS_ORIGIN AS segment,
               VALOR_SEM_HONORARIOS AS stock,
               DATA_INSCRICAO AS enrol,
               CASE
                 WHEN DATA_INSCRICAO BETWEEN DATE '1960-01-01' AND DATE '{AS_OF}'
                 THEN date_diff('day', DATA_INSCRICAO, DATE '{AS_OF}') / 365.25
               END AS debt_age,
               CNPJ AS cnpj, CNPJ_ROOT AS root
        FROM read_parquet('{src["icms"]}')
        WHERE MES_EXTRACAO = 12
        """
    )
    print(f"snapshot {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    con.execute(
        f"""
        CREATE TABLE pay AS
        SELECT ID_DEBITO,
               sum(VALOR_RECEITA) AS paid,
               count(*) AS n_pay
        FROM read_parquet({gare_files})
        WHERE DATA_ARRECADACAO_GARE < DATE '{PAY_END}'
        GROUP BY 1
        """
    )
    excluded = con.execute(
        f"""
        SELECT count(*) FROM read_parquet({gare_files})
        WHERE DATA_ARRECADACAO_GARE >= DATE '{PAY_END}'
        """
    ).fetchone()[0]
    print(
        f"payments {time.time() - t0:.0f}s; dates on or after {PAY_END}: {excluded}",
        flush=True,
    )
    t0 = time.time()
    con.execute(
        f"""
        CREATE TABLE firm AS
        SELECT cnpj, match_nivel, cnae_principal, porte,
               natureza_juridica, natureza_juridica_descricao,
               data_inicio_atividade, situacao_cadastral, uf, municipio_nome
        FROM read_parquet('{src["firm"]}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE rfb_cnae AS
        SELECT lpad(codigo, 7, '0') AS subclass, descricao
        FROM read_parquet('{src["cnae_codes"]}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE base AS
        SELECT c.ID_DEBITO, c.TIPO_DEBITO, c.segment, c.stock, c.enrol, c.debt_age,
               c.cnpj, c.root,
               coalesce(p.paid, 0) AS paid,
               coalesce(p.n_pay, 0) AS n_pay,
               coalesce(f.match_nivel, 'nao_encontrado') AS match_nivel,
               CASE WHEN f.match_nivel = 'estabelecimento' THEN f.cnae_principal END AS cnae7,
               CASE
                 WHEN f.data_inicio_atividade BETWEEN DATE '1900-01-01' AND DATE '{AS_OF}'
                  AND f.match_nivel = 'estabelecimento'
                 THEN date_diff('day', f.data_inicio_atividade, DATE '{AS_OF}') / 365.25
               END AS firm_age,
               f.porte, f.natureza_juridica, f.natureza_juridica_descricao,
               f.situacao_cadastral, f.uf, f.municipio_nome,
               h.section, h.division, h."group" AS grp, h.class, h.subclass,
               h.section_name_pt, h.division_name, h.group_name, h.class_name,
               h.subclass_name, h.in_cnae23
        FROM cda c
        LEFT JOIN pay p USING (ID_DEBITO)
        LEFT JOIN firm f ON f.cnpj = c.cnpj
        LEFT JOIN hier h
          ON h.subclass = CASE WHEN f.match_nivel = 'estabelecimento' THEN f.cnae_principal END
        WHERE c.segment IN ('declared', 'assessed')
        """
    )
    print(f"join {time.time() - t0:.0f}s", flush=True)


def validation(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    frame = con.execute(
        """
        SELECT TIPO_DEBITO AS raw_type,
               count(*) AS n_cda,
               round(sum(stock) / 1e9, 6) AS stock_bn
        FROM cda
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    total_n = int(frame.n_cda.sum())
    total_bn = float(frame.stock_bn.sum())
    rows = [frame]
    rows.append(
        pd.DataFrame(
            [
                {
                    "raw_type": "ICMS (all types)",
                    "n_cda": total_n,
                    "stock_bn": round(total_bn, 6),
                }
            ]
        )
    )
    out = pd.concat(rows, ignore_index=True)
    out["published_n"] = out.raw_type.map(
        lambda t: PUBLISHED_PJ.get(t, (None, None))[0]
    )
    out["published_bn"] = out.raw_type.map(
        lambda t: PUBLISHED_PJ.get(t, (None, None))[1]
    )
    out["delta_n"] = out.n_cda - out.published_n
    out["delta_bn"] = out.stock_bn - out.published_bn
    return out


def composition(
    con: duckdb.DuckDBPyConnection, level: str, code: str, name: str
) -> pd.DataFrame:
    frame = con.execute(
        f"""
        SELECT segment,
               coalesce({code}, 'unmatched') AS code,
               any_value({name}) AS name,
               count(*) AS n_cda,
               count(DISTINCT root) AS n_firms,
               sum(stock) AS stock,
               sum(paid) AS paid,
               count(*) FILTER (WHERE paid > 0) AS n_paid_cda
        FROM base
        GROUP BY 1, 2
        """
    ).df()
    frame.insert(0, "level", level)
    totals = frame.groupby("segment")[["n_cda", "stock", "paid"]].transform("sum")
    frame["share_cda"] = frame.n_cda / totals.n_cda
    frame["share_stock"] = frame.stock / totals.stock
    frame["share_collection"] = np.where(
        totals.paid > 0, frame.paid / totals.paid, np.nan
    )
    frame["payment_rate"] = frame.n_paid_cda / frame.n_cda
    frame["recovery"] = np.where(frame.stock > 0, frame.paid / frame.stock, np.nan)
    return frame


def concentration(con: duckdb.DuckDBPyConnection, code_sql: str) -> pd.DataFrame:
    """Gini and top 1% of positive CDA values inside each code, by segment."""
    return con.execute(
        f"""
        WITH x AS (
          SELECT segment, {code_sql} AS code, stock,
                 row_number() OVER (
                   PARTITION BY segment, {code_sql} ORDER BY stock
                 ) AS rn_asc,
                 row_number() OVER (
                   PARTITION BY segment, {code_sql} ORDER BY stock DESC
                 ) AS rn_desc,
                 count(*) OVER (PARTITION BY segment, {code_sql}) AS n
          FROM base
          WHERE stock > 0 AND {code_sql} IS NOT NULL
        )
        SELECT segment, code,
               any_value(n) AS n_positive,
               (2 * sum(rn_asc * stock)) / (any_value(n) * sum(stock))
                 - (any_value(n) + 1.0) / any_value(n) AS gini,
               sum(stock) FILTER (WHERE rn_desc <= greatest(1, ceil(0.01 * n)))
                 / sum(stock) AS top1_stock_share,
               greatest(1, ceil(0.01 * any_value(n))) AS top1_n
        FROM x
        GROUP BY 1, 2
        """
    ).df()


def lorenz_table(values: np.ndarray) -> dict:
    x = np.sort(values[np.isfinite(values) & (values > 0)])
    n = int(x.size)
    total = float(x.sum())
    k = int(np.ceil(0.01 * n))
    top = float(x[-k:].sum() / total)
    order = np.arange(1, n + 1)
    gini = float((2 * np.sum(order * x)) / (n * total) - (n + 1) / n)
    cuts = {}
    for pct in (0.50, 0.80, 0.90, 0.95, 0.99, 0.995, 0.999):
        m = max(1, int(np.ceil((1 - pct) * n)))
        cuts[f"largest_{100 * (1 - pct):.1f}pct_stock_share"] = float(
            x[-m:].sum() / total
        )
        cuts[f"largest_{100 * (1 - pct):.1f}pct_n"] = m
    curve_idx = np.unique(np.round(np.linspace(0, n - 1, 201)).astype(int))
    cum = np.cumsum(x)
    return {
        "n_positive": n,
        "stock": total,
        "gini": gini,
        "top1_n": k,
        "top1_cda_share": k / n,
        "top1_stock_share": top,
        "bottom99_stock_share": 1 - top,
        "cuts": cuts,
        "curve_p": ((curve_idx + 1) / n).tolist(),
        "curve_L": (cum[curve_idx] / total).tolist(),
    }


def mann_whitney(
    assessed: np.ndarray, declared: np.ndarray, outcome: str, unit: str
) -> dict:
    a = assessed[np.isfinite(assessed)]
    d = declared[np.isfinite(declared)]
    result = stats.mannwhitneyu(a, d, alternative="two-sided", method="asymptotic")
    n1, n2 = int(a.size), int(d.size)
    # U is for the first sample (Assessed). Positive r: Assessed tends to be larger.
    r_rb = float((2 * result.statistic) / (n1 * n2) - 1)
    return {
        "outcome": outcome,
        "unit": unit,
        "n_assessed": n1,
        "n_declared": n2,
        "median_assessed": float(np.median(a)),
        "median_declared": float(np.median(d)),
        "U_assessed": float(result.statistic),
        "rank_biserial": r_rb,
        "p_value": float(result.pvalue),
    }


def cramers(table: pd.DataFrame, attribute: str, note: str) -> dict | None:
    """Chi-square on firm counts. Levels with a cell under 5 are pooled first."""
    use = table.copy()
    if use.shape[0] < 2 or use.shape[1] < 2:
        return None
    small = (use < 5).any(axis=1)
    if small.any() and (~small).sum() >= 1:
        pooled = use.loc[small].sum()
        use = use.loc[~small]
        use.loc["Other (pooled)"] = pooled
    elif small.any():
        return None
    use = use.loc[use.sum(axis=1) > 0, use.sum(axis=0) > 0]
    if use.shape[0] < 2 or use.shape[1] < 2:
        return None
    chi2, p, dof, _expected = stats.chi2_contingency(use.to_numpy())
    n = float(use.to_numpy().sum())
    k = min(use.shape) - 1
    v = float(np.sqrt(chi2 / (n * k))) if k and n else np.nan
    return {
        "attribute": attribute,
        "n_firms": int(n),
        "n_levels": int(use.shape[0]),
        "chi2": float(chi2),
        "dof": int(dof),
        "p_value": float(p),
        "cramers_v": v,
        "note": note,
    }


def firm_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per CNPJ root and segment. Attributes follow the stock, not the CDA count."""
    return con.execute(
        """
        WITH by_code AS (
          SELECT root, segment,
                 sum(stock) AS stock,
                 sum(paid) AS paid,
                 count(*) AS n_cda,
                 count(DISTINCT enrol) AS n_enrol_dates,
                 sum(stock) FILTER (WHERE debt_age IS NOT NULL) AS age_stock,
                 sum(debt_age * stock) FILTER (WHERE debt_age IS NOT NULL AND stock > 0) AS age_w,
                 sum(stock) FILTER (WHERE firm_age IS NOT NULL) AS firm_age_stock,
                 sum(firm_age * stock) FILTER (WHERE firm_age IS NOT NULL AND stock > 0) AS firm_age_w
          FROM base
          GROUP BY 1, 2
        ),
        mode_section AS (
          SELECT root, segment, section
          FROM (
            SELECT root, segment, section, sum(stock) AS s,
                   row_number() OVER (PARTITION BY root, segment ORDER BY sum(stock) DESC) AS rk
            FROM base
            WHERE section IS NOT NULL
            GROUP BY 1, 2, 3
          )
          WHERE rk = 1
        ),
        mode_status AS (
          SELECT root, segment, status
          FROM (
            SELECT root, segment,
                   coalesce(situacao_cadastral, 'unknown') AS status,
                   sum(stock) AS s,
                   row_number() OVER (
                     PARTITION BY root, segment
                     ORDER BY sum(stock) DESC
                   ) AS rk
            FROM base
            GROUP BY 1, 2, 3
          )
          WHERE rk = 1
        ),
        mode_porte AS (
          SELECT root, segment, porte
          FROM (
            SELECT root, segment, coalesce(porte, 'unknown') AS porte, sum(stock) AS s,
                   row_number() OVER (PARTITION BY root, segment ORDER BY sum(stock) DESC) AS rk
            FROM base
            GROUP BY 1, 2, 3
          )
          WHERE rk = 1
        ),
        mode_nat AS (
          SELECT root, segment, natureza_juridica, natureza_juridica_descricao
          FROM (
            SELECT root, segment, natureza_juridica, natureza_juridica_descricao,
                   sum(stock) AS s,
                   row_number() OVER (PARTITION BY root, segment ORDER BY sum(stock) DESC) AS rk
            FROM base
            GROUP BY 1, 2, 3, 4
          )
          WHERE rk = 1
        ),
        mode_uf AS (
          SELECT root, segment, uf
          FROM (
            SELECT root, segment, uf, sum(stock) AS s,
                   row_number() OVER (PARTITION BY root, segment ORDER BY sum(stock) DESC) AS rk
            FROM base
            WHERE uf IS NOT NULL
            GROUP BY 1, 2, 3
          )
          WHERE rk = 1
        )
        SELECT b.*, s.section, st.status, po.porte, n.natureza_juridica,
               n.natureza_juridica_descricao, u.uf,
               CASE WHEN b.age_stock > 0 THEN b.age_w / b.age_stock END AS debt_age,
               CASE WHEN b.firm_age_stock > 0 THEN b.firm_age_w / b.firm_age_stock END AS firm_age
        FROM by_code b
        LEFT JOIN mode_section s USING (root, segment)
        LEFT JOIN mode_status st USING (root, segment)
        LEFT JOIN mode_porte po USING (root, segment)
        LEFT JOIN mode_nat n USING (root, segment)
        LEFT JOIN mode_uf u USING (root, segment)
        """
    ).df()


def dominant_firms(firms: pd.DataFrame) -> pd.DataFrame:
    """Assign each root to the segment that holds more of its stock."""
    wide = firms.sort_values("stock", ascending=False).drop_duplicates("root")
    return wide


def band_stock(
    con: duckdb.DuckDBPyConnection, expr: str, order: list[str]
) -> pd.DataFrame:
    frame = con.execute(
        f"""
        SELECT segment, {expr} AS band,
               count(*) AS n_cda,
               count(DISTINCT root) AS n_firms,
               sum(stock) AS stock
        FROM base
        GROUP BY 1, 2
        """
    ).df()
    frame["share_stock"] = frame.stock / frame.groupby("segment").stock.transform("sum")
    frame["share_cda"] = frame.n_cda / frame.groupby("segment").n_cda.transform("sum")
    frame["band"] = pd.Categorical(frame.band, order, ordered=True)
    return frame.sort_values(["segment", "band"])


def suppress(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    small = out.n_firms.between(1, 4)
    for col in out.columns:
        if col in {"level", "segment", "code", "name", "band"}:
            continue
        if pd.api.types.is_numeric_dtype(out[col]):
            out.loc[small, col] = np.nan
    return out


def style(fig: go.Figure, title: str, height: int = 560) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "x": 0, "font": {"size": 16}},
        font={"family": "Arial", "size": 13, "color": "#1c1c1c"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        margin={"l": 70, "r": 24, "t": 80, "b": 56},
        height=height,
        width=980,
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#E6E6E6", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#E6E6E6", zeroline=False)
    return fig


def write_fig(fig: go.Figure, path: Path) -> None:
    fig.write_image(path, scale=2)
    print("figure", path.name, flush=True)


def fig_lorenz(curves: dict[str, dict], path: Path) -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            name="Equality",
            line={"color": GREY, "dash": "dash", "width": 1.5},
        )
    )
    colors = {"Declared": DECLARED, "Assessed": ASSESSED, "Both": "#1B1B1B"}
    for name, curve in curves.items():
        fig.add_trace(
            go.Scatter(
                x=curve["curve_p"],
                y=curve["curve_L"],
                mode="lines",
                name=name,
                line={"color": colors[name], "width": 2.4},
            )
        )
        share = 100 * curve["top1_stock_share"]
        fig.add_annotation(
            x=0.99,
            y=1 - curve["top1_stock_share"],
            text=f"{name}: largest 1% hold {share:.1f}%",
            showarrow=False,
            yshift=18 if name != "Assessed" else -18,
            font={"size": 11, "color": colors[name]},
            xanchor="right",
        )
    fig.add_vline(x=0.99, line_color="#C9CED1", line_width=1)
    style(fig, "Lorenz curve of December 2025 stock, positive CDA values")
    fig.update_xaxes(
        title="Share of CDAs, from smallest value to largest",
        range=[0, 1],
        tickformat=".0%",
    )
    fig.update_yaxes(title="Share of stock", range=[0, 1], tickformat=".0%")
    write_fig(fig, path)


def fig_section(section: pd.DataFrame, path: Path) -> None:
    total = section.groupby("code").stock.sum().sort_values()
    codes = [c for c in total.index if c != "unmatched"]
    fig = go.Figure()
    for segment, color in (("declared", DECLARED), ("assessed", ASSESSED)):
        part = section[section.segment == segment].set_index("code").reindex(codes)
        labels = [SECTION_EN.get(c, c) for c in codes]
        fig.add_trace(
            go.Bar(
                y=labels,
                x=part.share_stock,
                orientation="h",
                name=SEG_EN[segment],
                marker_color=color,
            )
        )
    style(fig, "Share of segment stock by CNAE section", height=740)
    fig.update_layout(barmode="group")
    fig.update_xaxes(title="Share of the segment stock", tickformat=".0%")
    fig.update_yaxes(title="")
    write_fig(fig, path)


def fig_age(debt: pd.DataFrame, firm: pd.DataFrame, path: Path) -> None:
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Debt age", "Firm age"))
    for frame, col in ((debt, 1), (firm, 2)):
        bands = [
            b for b in frame.band.cat.categories if b in set(frame.band.astype(str))
        ]
        for segment, color in (("declared", DECLARED), ("assessed", ASSESSED)):
            part = frame[frame.segment == segment].set_index("band").reindex(bands)
            fig.add_trace(
                go.Bar(
                    x=[str(b) for b in bands],
                    y=part.share_stock,
                    name=SEG_EN[segment],
                    marker_color=color,
                    showlegend=(col == 1),
                ),
                row=1,
                col=col,
            )
    style(fig, "Share of stock by age band, as of 31 December 2025")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="Share of segment stock", tickformat=".0%")
    write_fig(fig, path)


def fig_status_porte(status: pd.DataFrame, porte: pd.DataFrame, path: Path) -> None:
    fig = make_subplots(
        rows=1, cols=2, subplot_titles=("Cadastral status", "Firm size")
    )
    for frame, col in ((status, 1), (porte, 2)):
        keys = list(dict.fromkeys(frame["name"]))
        for segment, color in (("declared", DECLARED), ("assessed", ASSESSED)):
            part = (
                frame[frame.segment == segment]
                .drop_duplicates("name")
                .set_index("name")
            )
            part = part.reindex(keys)
            fig.add_trace(
                go.Bar(
                    x=keys,
                    y=part.share_stock,
                    name=SEG_EN[segment],
                    marker_color=color,
                    showlegend=(col == 1),
                ),
                row=1,
                col=col,
            )
    style(fig, "Share of segment stock by registry status and size")
    fig.update_layout(barmode="group")
    fig.update_yaxes(tickformat=".0%", title="Share of segment stock")
    write_fig(fig, path)


def fig_payment(section: pd.DataFrame, path: Path) -> None:
    keep = section.groupby("code").share_stock.max()
    codes = [
        c
        for c in keep[keep >= 0.02].sort_values(ascending=False).index
        if c != "unmatched"
    ]
    fig = go.Figure()
    for segment, color in (("declared", DECLARED), ("assessed", ASSESSED)):
        part = section[section.segment == segment].set_index("code").reindex(codes)
        fig.add_trace(
            go.Bar(
                x=[SECTION_EN.get(c, c) for c in codes],
                y=part.payment_rate,
                name=SEG_EN[segment],
                marker_color=color,
            )
        )
    style(
        fig,
        "CDAs with any payment, by CNAE section (sections with at least 2% of a segment)",
    )
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="Share of CDAs with a payment", tickformat=".0%")
    fig.update_xaxes(tickangle=-35)
    write_fig(fig, path)


def fig_repeated(rep: pd.DataFrame, path: Path) -> None:
    fig = go.Figure()
    for segment, color in (("declared", DECLARED), ("assessed", ASSESSED)):
        part = rep[rep.segment == segment]
        fig.add_trace(
            go.Bar(
                x=part["multiplicity"],
                y=part["share_stock"],
                name=SEG_EN[segment],
                marker_color=color,
                text=[f"{int(n):,} firms" for n in part.n_firms],
                textposition="outside",
            )
        )
    style(fig, "Stock held by firms with one CDA or with two or more CDAs")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="Share of segment stock", tickformat=".0%", range=[0, 1.08])
    write_fig(fig, path)


def main() -> None:
    t0 = time.time()
    src = paths()
    tables = HERE / "tables"
    figures = HERE / "figures"
    tables.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)

    hier = parse_cnae23(XLSX)
    print(f"CNAE 2.3 subclasses in the official file: {len(hier)}", flush=True)
    con = connect(src["tmp"])
    build(con, src, hier)
    check = validation(con)
    check.to_csv(tables / "validation_dec2025.csv", index=False)
    print(check.to_string(index=False), flush=True)
    published_all = PUBLISHED_PJ["ICMS (all types)"][0]
    got_all = int(check.loc[check.raw_type == "ICMS (all types)", "n_cda"].iloc[0])
    if abs(got_all - published_all) > 100:
        raise SystemExit(
            f"December 2025 CDA count {got_all} is far from the published {published_all}."
        )

    levels = [
        ("section", "section", "section_name_pt"),
        ("division", "division", "division_name"),
        ("group", "grp", "group_name"),
        ("class", "class", "class_name"),
        ("subclass", "subclass", "subclass_name"),
    ]
    comp = {}
    for level, code, name in levels:
        frame = composition(con, level, code, name)
        comp[level] = frame
        suppress(frame).to_csv(tables / f"cnae_{level}.csv", index=False)
        print(level, len(frame), flush=True)

    conc_sec = concentration(con, "section")
    conc_div = concentration(con, "division")
    conc_sec.to_csv(tables / "cnae_section_concentration.csv", index=False)
    conc_div.to_csv(tables / "cnae_division_concentration.csv", index=False)

    debt_order = [
        "0 to 1 year",
        "1 to 3 years",
        "3 to 5 years",
        "5 to 10 years",
        "Over 10 years",
        "Unknown date",
    ]
    firm_order = [
        "Under 2 years",
        "2 to 5 years",
        "5 to 10 years",
        "10 to 20 years",
        "20 years or more",
        "Unknown date",
    ]
    debt_bands = band_stock(
        con,
        """
        CASE
          WHEN debt_age IS NULL THEN 'Unknown date'
          WHEN debt_age <= 1 THEN '0 to 1 year'
          WHEN debt_age <= 3 THEN '1 to 3 years'
          WHEN debt_age <= 5 THEN '3 to 5 years'
          WHEN debt_age <= 10 THEN '5 to 10 years'
          ELSE 'Over 10 years'
        END
        """,
        debt_order,
    )
    firm_bands = band_stock(
        con,
        """
        CASE
          WHEN firm_age IS NULL THEN 'Unknown date'
          WHEN firm_age < 2 THEN 'Under 2 years'
          WHEN firm_age < 5 THEN '2 to 5 years'
          WHEN firm_age < 10 THEN '5 to 10 years'
          WHEN firm_age < 20 THEN '10 to 20 years'
          ELSE '20 years or more'
        END
        """,
        firm_order,
    )
    debt_bands.to_csv(tables / "debt_age_bands.csv", index=False)
    firm_bands.to_csv(tables / "firm_age_bands.csv", index=False)

    summary = con.execute(
        """
        SELECT segment,
               count(*) AS n_cda,
               count(DISTINCT cnpj) AS n_cnpj,
               count(DISTINCT root) AS n_firms,
               sum(stock) AS stock,
               sum(stock) FILTER (WHERE stock > 0) AS stock_positive,
               sum(paid) AS paid,
               count(*) FILTER (WHERE paid > 0) AS n_paid,
               count(*) FILTER (WHERE debt_age > 10) AS n_age_over_10,
               sum(stock) FILTER (WHERE debt_age > 10) AS stock_age_over_10,
               sum(stock) FILTER (WHERE debt_age IS NOT NULL) AS stock_age_known,
               median(debt_age) AS median_debt_age,
               median(firm_age) AS median_firm_age,
               count(*) FILTER (WHERE match_nivel = 'estabelecimento') AS n_estab,
               count(*) FILTER (WHERE cnae7 IS NOT NULL AND NOT coalesce(in_cnae23, false)) AS n_cnae_outside
        FROM base
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    summary["payment_rate"] = summary.n_paid / summary.n_cda
    summary["recovery"] = summary.paid / summary.stock
    summary["stock_share_over_10"] = summary.stock_age_over_10 / summary.stock_age_known
    summary.to_csv(tables / "segment_summary.csv", index=False)
    print(summary.to_string(index=False), flush=True)

    arrays = con.execute(
        """
        SELECT segment, stock, debt_age, firm_age, paid, root
        FROM base
        """
    ).df()
    lorenz = {}
    for segment, label in (("declared", "Declared"), ("assessed", "Assessed")):
        lorenz[label] = lorenz_table(
            arrays.loc[arrays.segment == segment, "stock"].to_numpy()
        )
    lorenz["Both"] = lorenz_table(arrays.stock.to_numpy())
    points = []
    for label, curve in lorenz.items():
        row = {
            "segment": label,
            "n_positive": curve["n_positive"],
            "gini": curve["gini"],
            "top1_n": curve["top1_n"],
            "top1_cda_share": curve["top1_cda_share"],
            "top1_stock_share": curve["top1_stock_share"],
        }
        row.update(curve["cuts"])
        points.append(row)
    pd.DataFrame(points).to_csv(tables / "top1_justification.csv", index=False)

    mw_rows = []
    for outcome, col in (
        ("stock", "stock"),
        ("debt_age_years", "debt_age"),
        ("firm_age_years", "firm_age"),
    ):
        mask = arrays[col].notna()
        if outcome == "stock":
            mask = arrays.stock > 0
        a = arrays.loc[mask & (arrays.segment == "assessed"), col].to_numpy()
        d = arrays.loc[mask & (arrays.segment == "declared"), col].to_numpy()
        mw_rows.append(mann_whitney(a, d, outcome, "CDA"))

    firms = firm_frame(con)
    firms["multiplicity"] = np.where(firms.n_cda >= 2, "Two or more CDAs", "One CDA")
    for outcome, col in (
        ("firm_stock", "stock"),
        ("firm_debt_age_years", "debt_age"),
        ("firm_age_years", "firm_age"),
    ):
        a = firms.loc[
            (firms.segment == "assessed") & firms[col].notna(), col
        ].to_numpy()
        d = firms.loc[
            (firms.segment == "declared") & firms[col].notna(), col
        ].to_numpy()
        mw_rows.append(mann_whitney(a, d, outcome, "CNPJ root, within segment"))
    pd.DataFrame(mw_rows).to_csv(tables / "tests_mannwhitney.csv", index=False)

    dom = dominant_firms(firms)
    both = set(firms.loc[firms.segment == "declared", "root"]) & set(
        firms.loc[firms.segment == "assessed", "root"]
    )
    overlap = {
        "firms_in_both_segments": len(both),
        "firms_declared_only": int(
            ((firms.segment == "declared") & ~firms.root.isin(both)).sum()
        ),
        "firms_assessed_only": int(
            ((firms.segment == "assessed") & ~firms.root.isin(both)).sum()
        ),
        "firms_two_or_more_cdas_any_segment": int(
            firms.groupby("root").n_cda.sum().ge(2).sum()
        ),
        "firms_two_or_more_enrol_dates": int(
            firms.groupby("root").n_enrol_dates.max().ge(2).sum()
        ),
    }
    (tables / "enrollment_overlap.json").write_text(json.dumps(overlap, indent=2))
    print("overlap", overlap, flush=True)

    rep = firms.groupby(["segment", "multiplicity"], as_index=False).agg(
        n_firms=("root", "nunique"),
        n_cda=("n_cda", "sum"),
        stock=("stock", "sum"),
        paid=("paid", "sum"),
    )
    rep["share_stock"] = rep.stock / rep.groupby("segment").stock.transform("sum")
    rep["share_firms"] = rep.n_firms / rep.groupby("segment").n_firms.transform("sum")
    rep["payment_rate_firm"] = np.nan
    paid_firms = (
        firms.assign(any_paid=firms.paid > 0)
        .groupby(["segment", "multiplicity"])
        .any_paid.mean()
    )
    rep["payment_rate_firm"] = [
        paid_firms.get((s, m), np.nan)
        for s, m in zip(rep.segment, rep.multiplicity, strict=True)
    ]
    rep.to_csv(tables / "repeated_enrollments.csv", index=False)

    def counts(frame: pd.DataFrame, col: str) -> pd.DataFrame:
        tab = pd.crosstab(frame[col].fillna("Unknown"), frame.segment)
        for segment in ("assessed", "declared"):
            if segment not in tab.columns:
                tab[segment] = 0
        return tab[["assessed", "declared"]]

    tests = []
    for attribute, col, note in (
        ("CNAE section", "section", "Dominant section inside the dominant segment."),
        (
            "cadastral status",
            "status",
            "Code of the establishment that holds the stock.",
        ),
        ("firm size", "porte", "Receita Federal size code."),
        ("UF", "uf", "State of the establishment that holds the stock."),
        (
            "repeated enrollment",
            "multiplicity",
            "Two or more CDAs versus one, inside the segment.",
        ),
    ):
        source = firms if attribute == "repeated enrollment" else dom
        result = cramers(counts(source, col), attribute, note)
        if result:
            tests.append(result)
    nature = dom.copy()
    top_nat = nature.natureza_juridica.value_counts().head(8).index
    nature["nature_group"] = np.where(
        nature.natureza_juridica.isin(top_nat),
        nature.natureza_juridica_descricao.fillna(nature.natureza_juridica),
        "Other legal natures",
    )
    result = cramers(
        counts(nature, "nature_group"),
        "legal nature",
        "Top 8 natures by firm count, plus other.",
    )
    if result:
        tests.append(result)
    pd.DataFrame(tests).to_csv(tables / "tests_chi2.csv", index=False)

    status = composition(
        con, "status", "coalesce(situacao_cadastral, 'unknown')", "situacao_cadastral"
    )
    status["name"] = status.code.map(
        lambda c: STATUS_EN.get(str(c), "Unknown or not matched")
    )
    status.to_csv(tables / "status.csv", index=False)
    porte = composition(con, "porte", "coalesce(porte, 'unknown')", "porte")
    porte["name"] = porte.code.map(lambda c: PORTE_EN.get(str(c), "Unknown"))
    porte.to_csv(tables / "porte.csv", index=False)
    uf = composition(con, "uf", "coalesce(uf, 'unknown')", "uf")
    suppress(uf).to_csv(tables / "uf.csv", index=False)
    nature_cda = composition(
        con,
        "legal_nature",
        "coalesce(natureza_juridica, 'unknown')",
        "natureza_juridica_descricao",
    )
    suppress(nature_cda).to_csv(tables / "legal_nature.csv", index=False)
    mun = con.execute(
        """
        SELECT segment, municipio_nome AS municipality, uf,
               count(*) AS n_cda, count(DISTINCT root) AS n_firms, sum(stock) AS stock
        FROM base
        WHERE municipio_nome IS NOT NULL
        GROUP BY 1, 2, 3
        """
    ).df()
    mun["share_stock"] = mun.stock / mun.groupby("segment").stock.transform("sum")
    top_mun = (
        mun.sort_values("stock", ascending=False)
        .groupby("segment", as_index=False)
        .head(15)
    )
    suppress(top_mun).to_csv(tables / "municipality_top15.csv", index=False)

    outside = con.execute(
        """
        SELECT cnae7, any_value(subclass_name) AS name_in_hierarchy, count(*) AS n_cda,
               count(DISTINCT root) AS n_firms
        FROM base
        WHERE cnae7 IS NOT NULL AND NOT coalesce(in_cnae23, false)
        GROUP BY 1
        ORDER BY n_cda DESC
        """
    ).df()
    # Names of codes outside the 2.3 file come from the RFB code table, still aggregated.
    if not outside.empty:
        rfb_names = con.execute("SELECT subclass, descricao FROM rfb_cnae").df()
        outside = outside.merge(
            rfb_names, left_on="cnae7", right_on="subclass", how="left"
        )
    suppress(outside).drop(
        columns=[c for c in outside.columns if c in {"subclass"}], errors="ignore"
    ).to_csv(tables / "cnae_outside_2_3.csv", index=False)

    fig_lorenz(lorenz, figures / "lorenz_by_segment.png")
    fig_section(comp["section"], figures / "cnae_section_stock.png")
    fig_age(debt_bands, firm_bands, figures / "age_debt_and_firm.png")
    fig_status_porte(status, porte, figures / "status_and_size.png")
    fig_payment(comp["section"], figures / "payment_by_section.png")
    fig_repeated(rep, figures / "repeated_enrollments.png")

    key = {
        "as_of": AS_OF,
        "payments_before": PAY_END,
        "cnae23_subclasses": len(hier),
        "cnae23_sha256": "96853a62e65a200ddb54b28bc7e8ad22fc7a0306cd3f21c21e63f1b934dc06ac",
        "validation": check.to_dict(orient="records"),
        "segment_summary": summary.to_dict(orient="records"),
        "top1": points,
        "mannwhitney": mw_rows,
        "chi2": tests,
        "overlap": overlap,
        "repeated": rep.to_dict(orient="records"),
        "seconds": round(time.time() - t0, 1),
    }
    (tables / "key_numbers.json").write_text(json.dumps(key, indent=2, default=str))
    print(f"done in {key['seconds']}s", flush=True)
    con.close()


if __name__ == "__main__":
    main()
