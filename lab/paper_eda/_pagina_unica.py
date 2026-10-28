#!/usr/bin/env python3
"""Monta lab/paper_eda/index.qmd: o EDA do paper inteiro numa página só (é a página de /paper_eda/).

Não executa código. Junta o markdown já executado das 8 páginas-parte (lab/_freeze/paper_eda/<parte>/),
que _gerar_paginas.py gera e o Quarto renderiza uma vez cada (com cache em disco). Assim a página única não
roda o EDA de novo nem duplica caches. Só usa a biblioteca padrão. Chamado no fim de _gerar_paginas.py; rode
de novo depois de renderizar uma parte:  python3 lab/paper_eda/_pagina_unica.py

O que faz com o markdown das partes:
- tira o cabeçalho YAML, o aviso "Sobre esta página", o aviso de qualidade repetido e o bloco de uso do cache;
- troca cada marcador solto "[lab: era ...]" por um callout "Alterado no lab" no começo da seção;
- dá âncoras fixas aos títulos numerados (s-a-4-2, s-b-5-18, ...) e aos avisos do laboratório (lab-...);
- põe callouts nas seções com estrelas de significância, CNPJ x sem CNPJ e só agregados;
- escreve no topo o quadro de novidades, com links para as seções;
- troca traces plotly scattergl (WebGL) por scatter (SVG), para abrir em qualquer navegador;
- figuras com mais de 1 MB (histogramas com todos os pontos) vão para paper_eda_figs/<célula>.html, abertas em
  iframe com carga preguiçosa, para a página abrir rápido.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
FREEZE = LAB / "_freeze" / "paper_eda"
OUT = HERE / "index.qmd"  # a página única É a entrada /paper_eda/; as 8 partes ficam fora do site (_quarto.yml)
FIGS = HERE / "paper_eda_figs"
PARTS = ["01_dados", "02_parte_a", "03_parte_b1", "04_parte_b2", "05_parte_b3", "06_parte_c", "07_parte_d", "08_parte_e"]
BIG_FIG = 1_000_000  # bytes de HTML por figura acima dos quais ela vai para arquivo à parte

# Páginas do laboratório trazidas inteiras para dentro desta página (CTF-03, 26/10/2026) e, desde 28/10/2026, colocadas
# dentro da seção temática certa (pedido do Étore: numeração única, tudo em inglês). Cada uma vira uma subseção numerada
# da seção indicada, montada do _freeze dela, sem executar nada. A L.5 (EDA completo) vira o apêndice 8.
# (fonte relativa a lab/, número, título, âncora, prefixo dos ids internos, parte depois da qual entra, códigos)
LAB_PAGES = [
    ("dados/2026-10-26_cnpj_enrich", "1.9", "Firm registry enrichment of debtor CNPJs (RFB, March 2026)", "lab-cnpj-rfb",
     "rfb", "01_dados", "CNPJ-ENRICH"),
    ("eda/2026-10-23_concentracao_e_liquidacao", "2.8", "Concentration by tax type and by debtor; settlement in BRL",
     "lab-conc", "conc", "02_parte_a", "IMP-09, IMP-10"),
    ("eda/2026-10-23_kmeans_top1", "3.12", "K-means clustering of the top 1% of CDAs and the redrawn Figure 3.5.18",
     "lab-km", "km", "05_parte_b3", "CTF-03, IMP-11, IMP-13"),
    ("dados/2026-10-27_cnpj_geo", "3.13", "Debtor firms by municipality of São Paulo state", "lab-cnpj-geo", "geo",
     "05_parte_b3", "GEO-01"),
    ("dados/2026-10-27_cnpj_firmas", "3.14", "Registration status and economic activity (CNAE) of debtor firms × origin and recovery",
     "lab-cnpj-firmas", "firm", "05_parte_b3", "LAB-CNPJ"),
    ("eda/2026-10-23_macro_selic_over_checagem", "5.8", "Overnight Selic rate and check of the macroeconomic correlations",
     "lab-macro", "mac", "07_parte_d", "MAC-03, MAC-07"),
    ("eda/2026-10-23_eda_completo", "8", "Appendix: the full EDA notebook (faithful conversion, includes partial 2026)",
     "lab-edac", "edac", "APPENDIX", "CTF-03"),
]
LAB_ANCHOR = {p[0].split("/")[-1]: p[3] for p in LAB_PAGES}
LAB_PREFIX = {p[0].split("/")[-1]: p[4] for p in LAB_PAGES}
LINK_KM = "#lab-km"
LINK_CONC = "#lab-conc"
LINK_MACRO = "#lab-macro"
# seções do paper: letra -> número (numeração única do lab, 28/10/2026)
SEC_NUM = {"A": "2", "B": "3", "C": "4", "D": "5", "E": "6"}
SEC_TITLE = {"0": "Computational environment", "1": "Data", "2": "The portfolio across tax types",
             "3": "The VAT (ICMS) portfolio", "4": "Statistical properties", "5": "Macroeconomic conditions",
             "6": "Discussion", "7": "Correspondence with the manuscript and the dissertation",
             "8": "Appendix: the full EDA notebook"}

# Avisos e saídas em português que vêm do _freeze das 8 partes (gerados por _gerar_paginas.py): traduzidos aqui, na
# montagem, para não reexecutar as partes. Casamento por espaço flexível; cada um tem de aparecer (senão para).
PT2EN = [
    ("## Selic over no lugar da meta", "## Overnight Selic instead of the target rate"),
    ("No laboratório, a variável `selic` é a **Selic over** (SGS 4189, acumulada no mês e anualizada, % a.a.), e não a meta "
     "(SGS 432). A troca e o efeito nas correlações estão na página [Selic over e checagem]",
     "In the lab, the variable `selic` is the **overnight Selic rate** (SGS 4189, compounded over the month and annualised, "
     "% p.a.), not the policy target rate (SGS 432). The change and its effect on the correlations are in "
     "[5.8 Overnight Selic rate and check of the macroeconomic correlations]"),
    ("**Também alterado no lab (MAC-07, 26/10/2026):** a série 4380 é o PIB mensal em R$ correntes (não o IBC-Br citado no "
     "texto do paper). Ela e o saldo de crédito PJ (20540) passam a entrar **deflacionados pelo IPCA** (SGS 433, índice "
     "encadeado das variações mensais, base dez/2025 = 1), em R$ de dez/2025, antes do log. Desocupação (24369) e ocupados "
     "(24379) são da PNAD Contínua, cujo valor mensal é o **trimestre móvel** que termina no mês (meses vizinhos repetem dois "
     "meses de entrevistas). No trimestre, entra o valor do último mês (o trimestre civil) e, no ano, a média dos quatro "
     "trimestres civis. No mês, o valor fica como está.",
     "**Also changed in the lab (MAC-07, 26 Oct 2026):** series 4380 is monthly GDP in current BRL (not the IBC-Br cited in "
     "the paper's text). It and the outstanding credit to firms (20540) now enter **deflated by the IPCA** (SGS 433, index "
     "chained from the monthly changes, December 2025 = 1), in BRL of December 2025, before taking logs. Unemployment "
     "(24369) and employed persons (24379) come from the PNAD Contínua, whose monthly value is the **rolling quarter** "
     "ending in that month (neighbouring months share two months of interviews). Quarterly series take the value of the "
     "last month (the calendar quarter), and annual series the mean of the four calendar quarters. Monthly series are used "
     "as they are."),
    ("## Concentração por tipo de tributo e por devedor", "## Concentration by tax type and by debtor"),
    ("A concentração por grupo de tributo (ICMS, IPVA, ITCMD, taxas) e por devedor (raiz do CNPJ), na foto de "
     "dezembro de 2025, está na página [Concentração e liquidação]",
     "Concentration by tax group (VAT (ICMS), IPVA, ITCMD, fees) and by debtor (CNPJ root), in the December 2025 snapshot, "
     "is in [2.8 Concentration by tax type and by debtor]"),
    (". Aqui fica a concentração do ICMS, como no paper.", ". This section keeps the concentration of VAT (ICMS), as in the paper."),
    ("## B.5.18: figura do paper e versão corrigida", "## B.5.18: the paper's figure, redrawn"),
    ("A figura abaixo usa as projeções do paper (PCA na escala mediana/IQR e t-SNE sobre o Gower), **redesenhadas no lab para "
     "ficarem legíveis (IMP-11, 27/10/2026)**: uma linha com todas as origens e uma por origem da CDA, contagem de CDAs por "
     "célula no lugar de pontos, e o PC1 em escala log de (PC1 − mínimo + 1), porque a maior parte das CDAs (as sem pagamento) "
     "fica empilhada no mínimo do PC1.",
     "The figure below uses the paper's projections (PCA on the median/IQR scale and t-SNE on the Gower distance), **redrawn "
     "in the lab to be legible (IMP-11, 27 Oct 2026)**: one row with all origins and one per CDA origin, the count of CDAs per "
     "cell instead of points, and PC1 on a log scale of (PC1 − minimum + 1), because most CDAs (those without payments) pile "
     "up at the minimum of PC1."),
    ("**Como ler:** cada coluna é uma projeção, com a mesma janela em todas as linhas; o tom é o número de CDAs na célula (mais "
     "escuro, mais CDAs) e o contorno cinza é o total de todas as origens, para comparar onde cada origem se concentra. Origens "
     "com menos de 5 CDAs não ganham linha própria. O PCA corrigido (escala do Gower) está em [K-means no top 1%]",
     "**How to read:** each column is a projection, with the same window in all rows; the shade is the number of CDAs in the "
     "cell (darker, more CDAs) and the grey contour is the total over all origins, to compare where each origin concentrates. "
     "Origins with fewer than 5 CDAs get no row of their own. The corrected PCA (Gower scale) is in [3.12 K-means in the top 1%]"),
    ("## Escolha do k", "## Choice of k"),
    ("O paper escolhe o número de grupos do hierárquico automaticamente, pela maior silhueta (`K_STAR`). Isso só reproduz o "
     "paper. **No laboratório, a solução principal é o K-means com k = 4, por decisão do autor (IMP-13, 27/10/2026).** O K-means "
     "com 100 inicializações por k, com os perfis de k = 4 e o diagnóstico de k = 2 a 8, está em [K-means no top 1%]",
     "The paper chooses the number of hierarchical clusters automatically, by the highest silhouette (`K_STAR`). This only "
     "reproduces the paper. **In the lab, the main solution is K-means with k = 4, by the author's decision (IMP-13, 27 Oct "
     "2026).** K-means with 100 initialisations per k, with the profiles for k = 4 and the diagnostics for k = 2 to 8, is in "
     "[3.12 K-means in the top 1%]"),
    ("## B.8.5 com valor no eixo Y", "## B.8.5 with value on the y-axis"),
    ("Além do histograma do paper (número de CDAs), a célula abaixo mostra o valor inscrito em R$ bilhões por ano até a saída. "
     "A versão com valor pago está em [Concentração e liquidação]",
     "Besides the paper's histogram (number of CDAs), the cell below shows the enrolled value in BRL billion by years to exit. "
     "The version with the amount paid is in [2.8 Concentration by tax type and by debtor]"),
    ("## Checagem das correlações anuais", "## Check of the annual correlations"),
    ("**Aviso (MAC-07): não use a correlação anual entre emprego e estoque no texto.** Ela vem da tendência comum e da "
     "queda de 2020: no painel anual do EDA completo, o estoque cai de R$ 410,6 bi (2019) para R$ 328,9 bi (2020), junto com a "
     "contagem de CDAs, o que parece efeito da extração do DEBITO, não da economia. Isso só pode ser corrigido com a DEBITO "
     "corrigida (EQ-02/EQ-05); até lá, fica este aviso. Com n = 10 anos, nenhum mapa anual abaixo sustenta afirmação. O que deu "
     "para corrigir (defasagem única, sem 2026, PIB e crédito reais, PNAD em trimestres civis) já está aplicado aqui e está "
     "detalhado, com antes e depois, em [Selic over e checagem]",
     "**Warning (MAC-07): do not use the annual correlation between employment and stock in the text.** It comes from the "
     "common trend and from the 2020 drop: in the annual panel of the full EDA, the stock falls from BRL 410.6 bn (2019) to "
     "BRL 328.9 bn (2020), together with the count of CDAs, which looks like an artefact of the DEBITO extraction, not of the "
     "economy. It can only be fixed with a corrected DEBITO table (EQ-02/EQ-05); until then, this warning stays. With n = 10 "
     "years, no annual map below supports a claim. What could be fixed (a single lag convention, no 2026, real GDP and credit, "
     "PNAD in calendar quarters) is already applied here and is detailed, with before and after, in "
     "[5.8 Overnight Selic rate and check of the macroeconomic correlations]"),
    (", seções 2.5 e 3.", ", subsections 5.8.2.5 and 5.8.3."),
    # saídas de células (tabela de qualidade e recorte)
    ('"foto ausente"', '"missing snapshot"'),
    ("<td>foto ausente</td>", "<td>missing snapshot</td>"),
    ("<td>linhas repetidas (somadas uma vez)</td>", "<td>repeated rows (counted once)</td>"),
    ("<td>foto parcial? (&lt; 80% da mediana móvel de 7 meses)</td>", "<td>partial snapshot? (&lt; 80% of the 7-month rolling median)</td>"),
    ("Recorte: 2016–2025; foto de referência 2025-12; extração congelada 2026-03.",
     "Window: 2016–2025; reference snapshot 2025-12; frozen extraction 2026-03."),
    ("Lab: sem ler CPF_DEVEDOR, CDAs com CNPJ e CPF ao mesmo tempo contam como CNPJ na fonte; o extrato (1.3) exclui "
     "essas CDAs, então a diferença fonte − extrato mede esse grupo.",
     "Lab: without reading CPF_DEVEDOR, CDAs with both a CNPJ and a CPF count as CNPJ in the source; the extract (1.3) "
     "excludes these CDAs, so the source − extract difference measures this group."),
]
OPTIONAL_PT = {'"foto ausente"', "<td>foto ausente</td>", "<td>foto parcial? (&lt; 80% da mediana móvel de 7 meses)</td>"}


def translate_part(md: str, state: dict) -> str:
    for pt, en in PT2EN:
        pat = re.compile(r"\s+".join(re.escape(w) for w in pt.split()))
        md, n = pat.subn(lambda m: en, md)
        state["pt2en"][pt] = state["pt2en"].get(pt, 0) + n
    return md


# Avisos que _gerar_paginas.py já põe nas partes (título -> âncora, rótulo, código da decisão)
CALLOUT_MAP = {
    "Overnight Selic instead of the target rate": ("lab-selic", "Changed in the lab", "MAC-03"),
    "Concentration by tax type and by debtor": ("lab-concentracao", "New in the lab", "IMP-10"),
    "B.5.18: the paper's figure, redrawn": ("lab-b518", "Changed in the lab", "IMP-11"),
    "Choice of k": ("lab-k", "New in the lab", "CTF-03"),
    "B.8.5 with value on the y-axis": ("lab-b885", "New in the lab", "IMP-09"),
    "Check of the annual correlations": ("lab-emprego", "New in the lab", "MAC-07"),
}

# O que cada marcador quer dizer (texto novo no paper; o "era" vem do marcador)
ERA_NOVO = {
    "era março de 2026": ("December 2025 (reference snapshot)", "March 2026"),
    "era até março de 2026": ("up to December 2025", "up to March 2026"),
    "era 2016–2026": ("2016–2025", "2016–2026"),
    "era 2026": ("2025", "2026"),
}

# Callouts extras por seção (âncora do título -> (âncora do aviso, rótulo, texto))
EXTRA = {
    "s-1-2": ("lab-cnpj-1-2", "Changed in the lab", "Debtors are identified as **CNPJ × no CNPJ**, without reading the CPF "
              "column (the paper used CPF × CNPJ × no identifier). CDAs with both a CNPJ and a CPF count as CNPJ."),
    "s-1-3-1": ("lab-cnpj", "Changed in the lab", "The table classifies CDAs as **CNPJ (legal entity)** and **no CNPJ (CPF or "
                "no identifier)**, without reading the CPF column. The paper had three classes: CPF, CNPJ and no identifier. "
                "The exclusion of individuals from the extract (1.3) is the same as in the paper."),
    "s-1-5": ("lab-estoque-mensal", "Changed in the lab", "The monthly stock adds up **one row per CDA and month**: DEBITO has "
              "repeated rows in several months (e.g. 2020-04 to 06, 2025-06/07) and the paper added them up. Nothing else is "
              "corrected; the quality table is just below, in [Quality of the monthly snapshots](#lab-qualidade)."),
    "s-a-1-4": ("lab-agregados", "Changed in the lab", "Instead of the paper's sample of 10 rows of the snapshot (individual "
                "records), the schema of the DEBITO table without the person columns. The lab shows aggregates only."),
    "s-b-5-24": ("lab-agregados-b524", "Changed in the lab", "Aggregates only: the paper showed the first rows with the hash."),
}
STAR_TEXT = ("Significance stars in tables and correlation maps: \\* p ≤ 0.10; \\*\\* p ≤ 0.05; \\*\\*\\* p ≤ 0.01 (no "
             "correction for multiple testing). In tables, each p column gets a “(sig.)” column with the stars next to it, "
             "and rows with p ≤ 0.10 in the first p column are in **bold** (no background colour). The caption above each "
             "table says which column defines the bold rows. All tables are complete, with stars and bold in every row; "
             "tables with more than 25 rows sit in a scrollable box. In heat maps, the colour is the sign and strength of "
             "the correlation and the stars are the significance.")

HEAD_RE = re.compile(r"^(#{2,4}) (.+?)\s*$")
NUM_RE = re.compile(r"^(Part [A-E]|[0-9A-E](?:\.\d+)*)\b")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def anchor_for(title: str) -> str | None:
    m = NUM_RE.match(title)
    if not m:
        return None
    return "s-" + m.group(1).lower().replace("part ", "part-").replace(".", "-")


def load_part(name: str) -> tuple[str, list[str]]:
    fj = FREEZE / name / "execute-results" / "html.json"
    src = HERE / f"{name}.qmd"
    if not fj.exists():
        sys.exit(f"{name}: sem _freeze. Renderize antes: quarto render lab/paper_eda/{name}.qmd")
    d = json.loads(fj.read_text(encoding="utf-8"))
    if d["hash"] != md5(src):
        sys.exit(f"{name}: _freeze desatualizado em relação a {src.name}. Renderize antes: quarto render lab/paper_eda/{name}.qmd")
    return d["result"]["markdown"], d["result"].get("includes", {}).get("include-in-header", [])


def strip_part(md: str) -> str:
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S)
    md = re.sub(r"::: \{\.callout-tip collapse=\"true\"\}\n## Sobre esta página.*?\n:::\n", "", md, count=1, flags=re.S)
    md = re.sub(r":::\{\.callout-important\}\n## Qualidade do DEBITO.*?\n:::\n", "", md, count=1, flags=re.S)
    i = md.find("#### lab: uso do cache nesta página")
    if i >= 0:
        md = md[:i]
    return md.strip("\n") + "\n"


def blocks(md: str):
    """Divide em (texto, é_código): cercas ``` são intocáveis."""
    out, buf, fence = [], [], False
    for ln in md.split("\n"):
        starts = ln.lstrip().startswith("```")
        if starts and not fence:
            if buf:
                out.append(("\n".join(buf), False)); buf = []
            fence = True; buf.append(ln)
            continue
        if starts and fence:
            buf.append(ln); out.append(("\n".join(buf), True)); buf = []; fence = False
            continue
        buf.append(ln)
    if buf:
        out.append(("\n".join(buf), fence))
    return out


def callout(kind: str, label: str, body: str, anchor: str | None = None, extra: str = "") -> str:
    a = f"[]{{#{anchor}}}\n\n" if anchor else ""
    return f'{a}::: {{.callout-{kind} appearance="simple" title="{label}{extra}"}}\n{body}\n:::\n'


def transform(md: str, state: dict) -> str:
    # 1) avisos do gerador -> callouts rotulados com âncora
    def _co(m):
        kind, title = m.group(1), m.group(2).strip()
        if title not in CALLOUT_MAP:
            return m.group(0)
        anc, label, code = CALLOUT_MAP[title]
        state["anchors"].add(anc)
        return f'[]{{#{anc}}}\n\n::: {{.callout-{kind} title="{label} ({code}): {title}"}}\n'
    md = re.sub(r"^:::\s?\{\.callout-(note|warning)\}\n## (.+)\n", _co, md, flags=re.M)
    # 2) links entre partes viram âncoras na mesma página
    md = re.sub(r"\(0[1-8]_[a-z0-9_]+\.qmd(#[\w-]+)\)", r"(\1)", md)
    # 3) títulos do laboratório
    md = md.replace("#### lab: qualidade das fotos mensais {#lab-qualidade}",
                    "#### New in the lab: quality of the monthly DEBITO snapshots {#lab-qualidade}")
    md = md.replace("#### lab: recorte do laboratório",
                    "### 1.8 New in the lab: window ending in 2025, no 2026 {#lab-recorte}")
    if "{#lab-qualidade}" in md:
        state["anchors"].add("lab-qualidade")
    if "{#lab-recorte}" in md:
        state["anchors"].add("lab-recorte")
    return md


def sectionize(md: str, state: dict) -> str:
    """Âncoras nos títulos numerados, promoção de 0.1/1.1/E.1 para ### e callouts por seção."""
    # títulos e níveis
    lines = md.split("\n")
    parts_b = blocks(md)
    # 1) Quais H2 não têm H3? (para promover #### X.n -> ###)
    text_only = "\n".join(t for t, code in parts_b if not code)
    heads = [(len(m.group(1)), m.group(2)) for m in (HEAD_RE.match(l) for l in text_only.split("\n")) if m]
    promote_ok, cur, has3 = set(), None, {}
    for lvl, t in heads:
        if lvl == 2:
            cur = t; has3.setdefault(cur, False)
        elif lvl == 3 and cur is not None and NUM_RE.match(t):  # títulos do lab não contam
            has3[cur] = True
    # 2) percorre blocos de texto, juntando marcadores por seção
    out_blocks: list[list] = []  # [texto, é_código]
    sec = {"idx": None, "eras": [], "stars": False, "anchor": None}
    cur_h2 = None

    def close_section():
        if sec["idx"] is None:
            return
        notes = []
        if sec["eras"]:
            uniq = list(dict.fromkeys(sec["eras"]))
            items = "; ".join(f"**{ERA_NOVO.get(e, ('?', e))[0]}** (was {ERA_NOVO.get(e, ('?', e))[1]})" for e in uniq)
            notes.append(callout("note", "Changed in the lab (IMP-12): data cut at 2025",
                                 f"Paper text adjusted to the [lab window](#lab-recorte): {items}."))
            state["n_era_sections"] += 1
        if sec["stars"]:
            anc = f"lab-sig-{sec['anchor'] or len(state['star_secs'])}"
            notes.append(callout("note", "Changed in the lab (MAC-05/06): significance stars", STAR_TEXT, anc))
            state["star_secs"].append((anc, sec["title"]))
        if sec["anchor"] in EXTRA:
            anc, label, body = EXTRA[sec["anchor"]]
            notes.append(callout("note", label, body, anc))
            state["anchors"].add(anc)
        if notes:
            t = out_blocks[sec["idx"]][0]
            out_blocks[sec["idx"]][0] = t + "\n\n" + "\n".join(notes)

    for text, code in parts_b:
        if code:
            if "(sig.)" in text or "no multiple-testing correction" in text:
                sec["stars"] = True
            out_blocks.append([text, True])
            continue
        new_lines = []
        for ln in text.split("\n"):
            m = HEAD_RE.match(ln)
            if m and not ln.startswith("## Novidades"):
                hashes, title = m.group(1), m.group(2)
                for e in re.findall(r"\[lab: ([^\]]+)\]", title):
                    pass
                title_eras = re.findall(r"\[lab: ([^\]]+)\]", title)
                title = re.sub(r"\s*\[lab: [^\]]+\]", "", title)
                if len(hashes) == 2:
                    cur_h2 = title
                anc = None if "{#" in title else anchor_for(title)
                if (len(hashes) == 4 and cur_h2 is not None and not has3.get(cur_h2, True)
                        and re.match(r"^[0-9A-E]\.\d+ ", title)):
                    hashes = "###"
                ln = f"{hashes} {title}" + (f" {{#{anc}}}" if anc else "")
                # fecha a seção anterior e abre esta
                if new_lines:
                    out_blocks.append(["\n".join(new_lines), False]); new_lines = []
                close_section()
                out_blocks.append([ln, False])
                sec.update(idx=len(out_blocks) - 1, eras=list(title_eras), stars=False, anchor=anc, title=title)
                if anc:
                    state["anchors"].add(anc)
                continue
            eras = re.findall(r"\[lab: ([^\]]+)\]", ln)
            if eras:
                if sec["idx"] is None:
                    # marcador antes de qualquer título: abre uma "seção" fictícia no começo
                    out_blocks.append(["", False]); sec.update(idx=len(out_blocks) - 1, eras=[], stars=False, anchor=None, title="")
                sec["eras"].extend(eras)
                state["n_markers"] += len(eras)
                ln = re.sub(r"\s*\[lab: [^\]]+\]", "", ln)
            new_lines.append(ln)
        if new_lines:
            out_blocks.append(["\n".join(new_lines), False])
    close_section()
    return "\n".join(t for t, _ in out_blocks)


def externalize_figs(md: str, state: dict) -> str:
    FIGS.mkdir(exist_ok=True)
    for old in FIGS.glob("*.html"):
        old.unlink()
    pat = re.compile(r"(::: \{#(c\d+(?:-\d+)?) \.cell-output \.cell-output-display[^}]*\}\n)```\{=html\}\n(.*?)\n```\n", re.S)

    def _ext(m):
        head, cell, html = m.group(1), m.group(2), m.group(3)
        if len(html) < BIG_FIG or "plotly-graph-div" not in html:
            return m.group(0)
        h = re.search(r"height:(\d+)px", html)
        height = int(h.group(1)) + 20 if h else 540
        (FIGS / f"{cell}.html").write_text(
            "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Figure " + cell +
            "</title></head>\n<body style=\"margin:0\">\n" + html + "\n</body></html>\n", encoding="utf-8")
        state["figs"].append((cell, len(html)))
        mb = len(html) / 1e6
        frame = (f'<iframe src="paper_eda_figs/{cell}.html" loading="lazy" title="Figure of cell {cell}" '
                 f'style="width:100%;height:{height}px;border:0"></iframe>\n'
                 f'<p class="small text-muted">Interactive figure loaded separately ({mb:.0f} MB: the histogram carries all '
                 f'points). <a href="paper_eda_figs/{cell}.html" target="_blank">Open in another tab</a>.</p>')
        return f"{head}```{{=html}}\n{frame}\n```\n"
    return pat.sub(_ext, md)


MJ3_SRC = '<script src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-svg.min.js"></script>'
MJ3_CFG = re.compile(r"<script>if \(window\.MathJax && window\.MathJax\.Hub[^<]*</script>")


def drop_plotly_mathjax(md: str) -> str:
    """As saídas do plotly trazem MathJax 3 (uma cópia por figura) e o Quarto já carrega o MathJax 4: os dois juntos
    dão erro de JS e as fórmulas não aparecem. Fica só o do Quarto."""
    return MJ3_CFG.sub("", md.replace(MJ3_SRC, ""))


def header(state: dict, includes: list[str]) -> str:
    inc = "\n".join(dict.fromkeys(includes)).strip()
    # includes do plotly: sem MathJax 3 e sem o import de módulo sem extensão (404); o plotly.js vem com cada figura
    inc = "\n".join(l for l in inc.split("\n")
                    if not l.lstrip().startswith("if (window.MathJax") and 'import "https://cdn.plot.ly' not in l
                    and "tex-svg.min.js" not in l)
    inc_yaml = "\n".join("      " + l for l in inc.split("\n"))
    return f"""---
title: "The paper's EDA (v1) in the lab"
subtitle: "The whole paper_eda_v1 on one page, with the lab analyses inside their thematic sections and the changes from the October 2026 meetings"
description: "Sections 0 to 8 of the article's exploratory data analysis, with data up to 2025, the overnight Selic rate, significance stars and DEBITO quality warnings."
date: 2026-10-28
date-format: "D MMMM YYYY"
lang: en
toc: true
toc-depth: 4
toc-expand: true
toc-location: left
toc-title: "Contents"
include-after-body:
  - _toc_colapsavel.html   # sumário fixo na rolagem, setinha para abrir/fechar cada tópico, figuras na largura do corpo
format:
  html:
    grid:                  # só nesta página: corpo largo em telas grandes (o grid do Quarto encolhe em telas menores)
      sidebar-width: 270px
      body-width: 1180px
      margin-width: 120px
number-sections: false
code-fold: true
code-summary: "Code"
resources:
  - paper_eda_figs/*.html
include-in-header:
  - text: |
{inc_yaml}
---
"""


GUIDE_MERMAID = """```{mermaid}
%%| fig-width: 7
flowchart TD
  S0["0 Computational environment"] --> S1["1 Data<br/>1.8 Lab window · 1.9 Firm registry enrichment (RFB)"]
  S1 --> S2["2 The portfolio across tax types<br/>2.8 Concentration by tax type and by debtor"]
  S2 --> S3["3 The VAT (ICMS) portfolio<br/>3.12 K-means, top 1% · 3.13 Municipalities · 3.14 Firm status and CNAE"]
  S3 --> S4["4 Statistical properties"]
  S4 --> S5["5 Macroeconomic conditions<br/>5.8 Overnight Selic and correlation check"]
  S5 --> S6["6 Discussion"]
  S6 --> S7["7 Correspondence with the manuscript"]
  S1 -.-> S8["8 Appendix: the full EDA notebook"]
  classDef paper fill:#FFF4E5,stroke:#154C56,color:#154C56
  classDef app fill:#ffffff,stroke:#8A9499,color:#4D4D4D,stroke-dasharray: 4 3
  class S0,S1,S2,S3,S4,S5,S6,S7 paper
  class S8 app
```"""


def guide(state: dict) -> str:
    rows = [("0", "s-0", "Paths, memory, caching, seed, figure style and statistical utilities."),
            ("1", "s-1", "Sources, extracts, panel, monthly and macro series, glossary; **1.8** the lab window (data up to 2025); **1.9** firm registry enrichment."),
            ("2", "s-part-a", "All tax types: volume, missing values, grouping, value, age, recovery; **2.8** concentration "
                              "by tax type and by debtor."),
            ("3", "s-part-b", "VAT (ICMS): evolution, debt types, declared × assessed, concentration, top CDAs, age, "
                              "recovery, payments, judicial filing, seasonality, programmes; **3.12** K-means, **3.13** "
                              "municipalities, **3.14** firm status and CNAE."),
            ("4", "s-part-c", "Distribution, correlation choice, stationarity, persistence."),
            ("5", "s-part-d", "Monthly, quarterly, annual and panel correlations, models; **5.8** overnight Selic and the "
                              "correlation check."),
            ("6", "s-part-e", "Synthesis, limitations, ethics, future research."),
            ("7", "s-7", "Where each result goes in the manuscript and the dissertation."),
            ("8", "lab-edac", "The full EDA notebook, converted as is (includes partial 2026 data; read with care).")]
    lst = "\n".join(f"- [**{n}** {SEC_TITLE[n]}](#{a}): {d}" for n, a, d in rows)
    return f"""## Contents and reading guide {{#guia}}

**VAT (ICMS).** ICMS is the Brazilian states' tax on the circulation of goods and on interstate and inter-municipal
transport and communication services. It is a subnational value-added tax: an OECD Economics Department working paper
describes Brazil's consumption taxes as including "a state-level VAT (ICMS)" (Arnold et al., 2025), and the OECD
classification of taxes records it under value added taxes (heading 5111). It is written "VAT (ICMS)" throughout; the
names of the raw debt types in the data (e.g. *ICMS Declarado*, *ICMS Autuação*) are kept as they appear in the database.

**How the page is organised.** One numbering scheme for the whole page (1, 1.1, 1.1.1, …). Sections 0 to 7 follow the
paper; the analyses added in the lab sit inside the section they belong to (1.8, 1.9, 2.8, 3.12–3.14, 5.8), each opened by a
**New in the lab** box; section 8 is an appendix. The diagram shows the reading order; dashed = appendix.

{GUIDE_MERMAID}

{lst}

*Reference:* Arnold, J. M., Battiau, P., Fall, F., & Spies, K. (2025). *The reform of Brazil's consumption tax system*.
OECD Economics Department Working Papers, No. 1848. https://doi.org/10.1787/2a85e559-en
"""


def novidades(state: dict) -> str:
    stars = state["star_secs"]
    star_links = ", ".join(f"[{t.split(' ')[0]}](#{a})" for a, t in stars[:12]) + (" …" if len(stars) > 12 else "")
    return f"""<!-- Gerado por lab/paper_eda/_pagina_unica.py a partir do _freeze das partes. Não editar à mão. -->

{guide(state)}

## What changed relative to the published paper {{#novidades}}

::: {{.callout-important title="What the lab changed"}}
The paper's text is kept and its conclusions are not rewritten. Each change below links to the section it affects, where
a **New in the lab** or **Changed in the lab** box explains it. Numbers that changed appear in the tables and figures of
that section.

1. **Data cut at 2025, no 2026 (IMP-12).** Last full year: 2025; reference snapshot: December 2025 (was March 2026).
   [1.8 Lab window](#lab-recorte). The paper's text was adjusted in {state['n_markers']} places in
   {state['n_era_sections']} sections, each with a “Changed in the lab (IMP-12)” box.
2. **Overnight Selic instead of the target rate (MAC-03).** The variable `selic` is now the overnight Selic rate
   (SGS 4189), not the policy target (SGS 432). [1.6 Macroeconomic series](#lab-selic) · [Section 5](#s-part-d) ·
   [5.8 Overnight Selic and correlation check]({LINK_MACRO}).
3. **Significance stars and significant rows in bold (MAC-05/06).** \\* p ≤ 0.10; \\*\\* p ≤ 0.05;
   \\*\\*\\* p ≤ 0.01; bold = p ≤ 0.10 in the column named in each table's caption; in
   {len(stars)} sections: {star_links}.
4. **3.8.5 with value in BRL on the y-axis (IMP-09).** [3.8.5 Time to settlement](#lab-b885).
5. **Concentration by tax type and by debtor (IMP-10).** [3.4 Concentration](#lab-concentracao) ·
   [2.8 Concentration by tax type and by debtor]({LINK_CONC}).
6. **Macroeconomic correlations corrected (MAC-07).** One lag convention (macro at t − k), no 2026, monthly GDP and
   credit to firms deflated by the IPCA (BRL of December 2025) and PNAD Contínua in calendar quarters, by quarter and by
   year. **Warning:** the annual employment × stock correlation comes from the common trend and from the 2020 drop, which
   looks like an artefact of the DEBITO extraction (EQ-02/EQ-05), and must not go into the text.
   [1.6](#lab-selic) · [5.3 Annual correlations](#lab-emprego) · [before and after]({LINK_MACRO}).
7. **Figure 3.5.18 made legible (IMP-11, 27 Oct 2026):** one panel per origin, count of CDAs per cell and PC1 on a log
   scale, in 3.5.18 and in 3.12. [3.5.18 Distance structure](#lab-b518) · [3.12 K-means in the top 1%]({LINK_KM}).
8. **K-means with k = 4 as the main solution (IMP-13, CTF-03).** k = 4 by the author's decision (27 Oct 2026); k = 2 to 8
   are kept as diagnostics. [3.5.19 Clustering](#lab-k) · [3.12 K-means in the top 1%]({LINK_KM}).
9. **DEBITO quality flagged, not corrected.** Missing snapshots (2018-12; 2020-10 to 12), partial ones (2018-11, 2024-03,
   **2024-12**) and repeated rows (e.g. 2020-04 to 06, **2025-06 and 2025-07**); the monthly stock adds up one row per CDA
   and month. [Quality table](#lab-qualidade) · [1.5 Monthly series](#lab-estoque-mensal).
10. **CNPJ × no CNPJ instead of CPF × CNPJ.** The CPF column is not read. [1.2](#lab-cnpj-1-2) ·
    [1.3.1 Debtor identification](#lab-cnpj).
11. **Aggregates only.** Nothing by CPF, name or individual CDA. [2.1.4](#lab-agregados) · [3.5.24](#lab-agregados-b524).
12. **All lab analyses on this page, inside their sections (CTF-03; 28 Oct 2026).** The entries of `lab/eda/` and
    `lab/dados/` are complete here: [1.9 Firm registry enrichment](#lab-cnpj-rfb) · [2.8 Concentration](#lab-conc) ·
    [3.12 K-means](#lab-km) · [3.13 Municipalities](#lab-cnpj-geo) · [3.14 Firm status and CNAE](#lab-cnpj-firmas) ·
    [5.8 Overnight Selic and correlation check](#lab-macro) · [8 Full EDA notebook](#lab-edac).
13. **Charts aggregated before publication (DADOS-14).** No figure carries one value per CDA or per debtor: histograms
    become bars with the count per bin (3.5.4, 3.5.5, 3.5.16, 3.8.5 in BRL, 3.9.3), the boxes of 3.2.3 are drawn from
    quartiles, fences and means computed beforehand, without individual points, the point clouds of 3.5.18 and of
    [3.12](#lab-km) become 2D density maps (60 × 60 cells; outline of each group in its colour) and long curves (3.5.9,
    the Lorenz curve of 3.5.13) keep 500 points. Nothing uses WebGL.
14. **Debtor firms by municipality of São Paulo state (GEO-01, 27 Oct 2026).** Choropleth map (IBGE boundaries, no
    external tiles) with the count, the value and debtor firms per thousand active establishments; municipality from the
    RFB registry of March 2026; municipalities with fewer than 5 CNPJs suppressed. [3.13](#lab-cnpj-geo).
15. **Registration status and CNAE of debtor firms (LAB-CNPJ, 27 Oct 2026).** The analyses of section 10 of the AVD-01
    article: registration status (RFB, March 2026) × recovery by origin, with Kaplan–Meier curves to the first payment
    (2016–2025 cohort), and CNAE division × origin (weight of assessed debt and recovery); divisions with any cell of fewer
    than 5 CNPJs are pooled whole. [3.14](#lab-cnpj-firmas).
16. **The whole lab in English, one numbering scheme (28 Oct 2026).** Paper sections A to E are now 2 to 6; the lab
    analyses moved into their sections; VAT (ICMS) naming as in the papers. [Reading guide](#guia).
:::

::: {{.callout-warning title="DEBITO quality (flagged only, nothing corrected)"}}
- The snapshots of **2018-12** and of **2020-10 to 2020-12** are missing. The 2020 M10–M12 file repeats M04–M06, and
  the 2018 M10–M12 file is truncated (2018-11 partial). The snapshots of **2024-03** and **2024-12** are partial (fewer
  than 80% of the rolling median of CDAs; 2024-12 has about 1.27 million CDAs against about 2.1 million in the
  neighbouring months).
- There are repeated rows in several months, including **2025-06 and 2025-07**. In the lab the monthly stock adds up one
  row per CDA and month (the paper added up the repeated ones).
- Consequence for the panel: the “year end” of 2018 is the last snapshot in which each CDA appears (October or
  November), and that of 2020 is September. That of 2024 comes from the partial December snapshot.
  [Full table](#lab-qualidade).
:::

::: {{.callout-tip collapse="true" title="How this page is built"}}
Generated by `lab/paper_eda/_gerar_paginas.py` (code of each part, from `paper_eda_v1.ipynb`, without running or
changing the notebook) and assembled by `lab/paper_eda/_pagina_unica.py`, which joins the already executed output of
the 8 parts (`lab/_freeze/paper_eda/`) and of the lab entries. `# lab:` comments in the code mark what was changed.
Very heavy figures open in a separate file ({len(state['figs'])} figures). Exported figures and tables (only on request,
`LAB_EXPORT_FIGS=1`) go to `interim/paper_eda_lab/` on the external drive, with the paper's `fig_*`/`tab_*` labels.
:::
"""



LAB_LINK_RE = re.compile(r"\]\((?:(?:\.\./)?(?:eda|dados)/)?(2026-[\w-]+?)\.(?:qmd|html)(#[\w-]+)?\)")
SITE_LINK_RE = re.compile(r"\]\((?:\.\./)?paper_eda/(?:index\.qmd|index\.html)?(#[\w-]+)?\)")


def lab_links(md: str) -> str:
    """Links para as páginas do lab (e para /paper_eda/) viram âncoras desta página."""
    def _r(m):
        slug, frag = m.group(1), m.group(2)
        if slug not in LAB_ANCHOR:
            sys.exit(f"link para página do lab que não está na página única: {slug}")
        if frag:
            return f"](#{LAB_PREFIX[slug]}-{frag[1:]})"
        return f"](#{LAB_ANCHOR[slug]})"
    md = LAB_LINK_RE.sub(_r, md)
    return SITE_LINK_RE.sub(lambda m: f"]({m.group(1) or '#guia'})", md)


def _slug(t: str) -> str:
    """Como o identificador automático do pandoc (minúsculas, acentos mantidos, tudo até a primeira letra sai)."""
    t = re.sub(r"\{.*?\}", "", t)
    t = re.sub(r"[`*_\[\]()]", "", t).strip().lower()
    t = "".join(ch for ch in t if ch.isalnum() or ch in "_-. ")
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"^[^a-zà-ÿ]+", "", t)
    return t or "secao"


def load_lab_page(src: str):
    fj = LAB / "_freeze" / src / "execute-results" / "html.json"
    qmd = LAB / f"{src}.qmd"
    if not fj.exists():
        sys.exit(f"{src}: sem _freeze. Renderize antes (ver lab/README.md)")
    d = json.loads(fj.read_text(encoding="utf-8"))
    if d["hash"] != md5(qmd):
        sys.exit(f"{src}: _freeze desatualizado em relação a {qmd.name}. Renderize antes.")
    return d["result"]["markdown"], d["result"].get("includes", {}).get("include-in-header", [])


def escape_reais(md: str) -> str:
    """"R$" no texto vira "R\\$": com dois "R$" na mesma linha o pandoc lia o trecho entre eles como fórmula
    (MathJax: "macro parameter character #" depois que os links viraram âncoras). Código e HTML bruto não mudam."""
    out = []
    for k, bloco in enumerate(re.split(r"(```.*?```)", md, flags=re.S)):
        if k % 2:
            out.append(bloco); continue
        out.append("\n".join(l if l.lstrip().startswith("<") else re.sub(r"(?<!\\)R\$", r"R\\$", l)
                             for l in bloco.split("\n")))
    return "".join(out)


INNER_NUM = re.compile(r"^(?:\d+[a-z]?(?:\.\d+)*\.?|\d+[A-Z](?:-\w+)?\s+—|[A-E]\.\d+(?:\.\d+)*)\s+")


def lab_section(src: str, num: str, short: str, anchor: str, pfx: str, codes: str, state: dict) -> tuple[str, list[str]]:
    md, inc = load_lab_page(src)
    m = re.match(r"\A---\n(.*?)\n---\n", md, flags=re.S)
    yml = m.group(1) if m else ""
    md = md[m.end():] if m else md
    md = escape_reais(md)
    desc = re.search(r'^description:\s*"(.*)"\s*$', yml, flags=re.M)
    title = re.search(r'^title:\s*"(.*)"\s*$', yml, flags=re.M)
    appendix = "." not in num          # o apêndice é um título de nível 2 (## 8. ...)
    shift = 1 if appendix else 2
    # ids internos com prefixo da página (fig-x -> fig-km-x; cell-fig-x -> cell-fig-km-x; setup -> km-setup)
    ids = set(re.findall(r"\{#([\w-]+)", md)) | set(re.findall(r"\bid=\"([\w-]+)\"", md))
    def newid(i):
        mm = re.match(r"^(cell-)?(fig-|tbl-)?(.*)$", i)
        return f"{mm.group(1) or ''}{mm.group(2) or ''}{pfx}-{mm.group(3)}"
    def _id(m):
        return "{#" + newid(m.group(1))
    md = re.sub(r"\{#([\w-]+)", _id, md)
    md = re.sub(r"@((?:fig|tbl)-[\w-]+)", lambda m: "@" + newid(m.group(1)), md)
    md = re.sub(r"\]\(#([\w-]+)\)", lambda m: f"](#{newid(m.group(1))})", md)
    # títulos: ## e ### ganham número da seção (num.k, num.k.j), sem o número antigo; #### fica sem número.
    # Todos descem de nível (2 -> 4 numa subseção, 2 -> 3 no apêndice) e ganham id fixo.
    out, fence, seen, k, j = [], False, set(), 0, 0
    for ln in md.split("\n"):
        if ln.lstrip().startswith("```"):
            fence = not fence
        hm = None if fence else re.match(r"^(#{2,4}) (.+?)\s*$", ln)
        if hm:
            lvl, t = len(hm.group(1)), hm.group(2)
            if "{#" not in t:
                base = f"{pfx}-{_slug(t)}"
                sid, n = base, 2
                while sid in seen:
                    sid = f"{base}-{n}"; n += 1
                seen.add(sid)
                t = f"{t} {{#{sid}}}"
            if lvl == 2:
                k += 1; j = 0
                t = f"{num}.{k} " + INNER_NUM.sub("", t)
            elif lvl == 3:
                j += 1
                t = f"{num}.{k}.{j} " + INNER_NUM.sub("", t)
            ln = "#" * (lvl + shift) + " " + t
        out.append(ln)
    md = "\n".join(out)
    md = lab_links(md)
    hl = "##" if appendix else "###"
    sep = ". " if appendix else " "
    head = (f"{hl} {num}{sep}{short} {{#{anchor}}}\n\n"
            f"::: {{.callout-tip appearance=\"simple\" title=\"New in the lab ({codes}): {title.group(1) if title else short}\"}}\n"
            f"{desc.group(1) if desc else ''} Full content of the lab entry `lab/{src}.qmd`, assembled from its _freeze "
            f"(nothing is executed here).\n:::\n\n")
    state["anchors"].add(anchor)
    state["lab_pages"].append((num, short, anchor))
    return head + md.strip("\n") + "\n", inc


def lab_blocks(state: dict) -> tuple[dict, list[str]]:
    """Seções do lab agrupadas pela parte do paper depois da qual entram ("APPENDIX" = depois de todas)."""
    by_part, incs = {}, []
    for src, num, short, anchor, pfx, part, codes in LAB_PAGES:
        b, inc = lab_section(src, num, short, anchor, pfx, codes, state)
        by_part.setdefault(part, []).append(f"<!-- lab: {src} -->\n\n{b}")
        incs += inc
    return by_part, incs


VAT_RE = re.compile(r"(?<![\w(/`-])ICMS(?![\w)/`-]|\s+(?:Declarado|Autuação|Autuacao|Fecoep|Alteração|Alteracao|Simples)\b)")


def renumber_and_vat(md: str) -> str:
    """Numeração única (A..E -> 2..6, 'Part X' -> seção N) e 'VAT (ICMS)' no texto. Código, HTML e `código` intactos."""
    def sec(m):
        return SEC_NUM[m.group(1)]
    def fix_text(t: str) -> str:
        t = re.sub(r"^(#{2,6}) Part ([A-E])\. ", lambda m: f"{m.group(1)} {SEC_NUM[m.group(2)]}. ", t, flags=re.M)
        t = re.sub(r"^## Correspondence with the manuscript and the dissertation\s*$",
                   "## 7. Correspondence with the manuscript and the dissertation {#s-7}", t, flags=re.M)
        t = re.sub(r"\bParts ([A-E])\s*(?:–|-|to)\s*([A-E])\b", lambda m: f"Sections {sec(m)}–{SEC_NUM[m.group(2)]}", t)
        t = re.sub(r"\bParts ([A-E]) and ([A-E])\b", lambda m: f"Sections {sec(m)} and {SEC_NUM[m.group(2)]}", t)
        t = re.sub(r"\bPart ([A-E])\b", lambda m: f"Section {sec(m)}", t)
        t = re.sub(r"(?<![\w.#/-])([A-E])\.(\d+(?:\.\d+)*)(?![\w-])", lambda m: f"{sec(m)}.{m.group(2)}", t)
        return t
    def fix_vat(line: str) -> str:
        if line.lstrip().startswith("<"):
            return line
        pieces = re.split(r"(`[^`]*`)", line)
        return "".join(p if p.startswith("`") else VAT_RE.sub("VAT (ICMS)", p) for p in pieces)
    out = []
    for text, code in blocks(md):
        if code:
            out.append(text); continue
        text = fix_text(text)
        text = "\n".join(fix_vat(l) for l in text.split("\n"))
        out.append(text)
    return "\n".join(out)


def main() -> int:
    state = {"anchors": set(), "star_secs": [], "n_markers": 0, "n_era_sections": 0, "figs": [], "pt2en": {}, "lab_pages": []}
    includes = []
    lab_by_part, lab_inc = lab_blocks(state)
    includes += lab_inc
    bodies = []
    for name in PARTS:
        md, inc = load_part(name)
        includes += inc
        md = translate_part(strip_part(md), state)
        md = transform(md, state)
        md = sectionize(md, state)
        bodies.append(f"<!-- parte: {name} -->\n\n{md}")
        bodies += lab_by_part.get(name, [])
    bodies += lab_by_part.get("APPENDIX", [])
    falta = [k for k, n in state["pt2en"].items() if n == 0 and k not in OPTIONAL_PT]
    if falta:
        sys.exit(f"tradução sem casamento no _freeze das partes: {[f[:60] for f in falta]}")
    body = lab_links("\n\n".join(bodies))
    body = renumber_and_vat(body)
    body = drop_plotly_mathjax(body)
    # scattergl (WebGL) -> scatter (SVG): alguns navegadores/máquinas não têm WebGL e o plot some com
    # "WebGL is not supported". Só o tipo do trace muda; os dados vêm do _freeze, que não é alterado.
    state["n_gl"] = body.count('"type":"scattergl"')
    body = body.replace('"type":"scattergl"', '"type":"scatter"')
    assert "scattergl" not in body, "sobrou scattergl em outra forma"
    body = externalize_figs(body, state)
    page = header(state, includes) + "\n" + renumber_and_vat(novidades(state)) + "\n" + body
    # ids únicos (CTF-03): uma célula com o mesmo rótulo de um título (ex.: lab-recorte) vira cell-<rótulo>;
    # os links continuam indo para o título.
    heads = set(re.findall(r"^#+ .*\{#([\w-]+)[\s}]", page, flags=re.M))
    page = re.sub(r"^(:{3,} \{#)([\w-]+)( \.cell)", lambda m: f"{m.group(1)}cell-{m.group(2)}{m.group(3)}"
                  if m.group(2) in heads else m.group(0), page, flags=re.M)
    ids = re.findall(r"\{#([\w-]+)[\s}]", re.sub(r"```.*?```", "", page, flags=re.S))
    dup = sorted({i for i in ids if ids.count(i) > 1}) if len(ids) != len(set(ids)) else []
    if dup:
        sys.exit(f"ids repetidos: {dup[:20]}")
    # todo link #âncora interno precisa existir
    targets = set(re.findall(r"\]\(#([\w-]+)\)", page))
    defined = set(re.findall(r"\{#([\w-]+)[\s}]", page)) | {"novidades", "guia"}
    missing = sorted(targets - defined)
    if missing:
        sys.exit(f"âncoras sem destino: {missing}")
    assert "[lab:" not in re.sub(r"```.*?```", "", page, flags=re.S), "sobrou marcador [lab: ...] no texto"
    OUT.write_text(page, encoding="utf-8")
    print(f"{OUT.relative_to(LAB)}: {len(page)/1e6:.1f} MB; marcadores {state['n_markers']} em {state['n_era_sections']} seções; "
          f"seções com estrelas {len(state['star_secs'])}; scattergl->scatter {state['n_gl']}; figuras à parte {[(c, round(n/1e6, 1)) for c, n in state['figs']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
