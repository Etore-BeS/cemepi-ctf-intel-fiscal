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

# Páginas do laboratório trazidas inteiras para dentro desta página (CTF-03, 26/10/2026: "toda análise dentro de 1 HTML
# só"). Cada uma vira uma subseção de "L. Análises do laboratório", montada do _freeze dela, sem executar nada.
# (fonte relativa a lab/, número, título curto, âncora, prefixo dos ids internos)
LAB_PAGES = [
    ("eda/2026-10-23_kmeans_top1", "L.1", "K-means no top 1% e figura B.5.18 corrigida (CTF-03, IMP-11)", "lab-km", "km"),
    ("eda/2026-10-23_concentracao_e_liquidacao", "L.2", "Concentração por tributo e por devedor; saída em R$ (IMP-09, IMP-10)",
     "lab-conc", "conc"),
    ("eda/2026-10-23_macro_selic_over_checagem", "L.3", "Selic over e checagem das correlações macro (MAC-03, MAC-07)",
     "lab-macro", "mac"),
    ("dados/2026-10-26_cnpj_enrich", "L.4", "Enriquecimento CNPJ (RFB ref. 2026-03)", "lab-cnpj-rfb", "rfb"),
    ("eda/2026-10-23_eda_completo", "L.5", "EDA completo (conversão fiel do notebook, com 2026 parcial)", "lab-edac", "edac"),
    ("dados/2026-10-27_cnpj_geo", "L.6", "CNPJs devedores por município de SP (GEO-01)", "lab-cnpj-geo", "geo"),
]
LAB_ANCHOR = {src.split("/")[-1]: anc for src, _, _, anc, _ in LAB_PAGES}
LAB_PREFIX = {src.split("/")[-1]: pfx for src, _, _, _, pfx in LAB_PAGES}
LINK_KM = "#lab-km"
LINK_CONC = "#lab-conc"
LINK_MACRO = "#lab-macro"

# Avisos que _gerar_paginas.py já põe nas partes (título -> âncora, rótulo, código da decisão)
CALLOUT_MAP = {
    "Selic over no lugar da meta": ("lab-selic", "Alterado no lab", "MAC-03"),
    "Concentração por tipo de tributo e por devedor": ("lab-concentracao", "Novo no lab", "IMP-10"),
    "B.5.18: figura do paper e versão corrigida": ("lab-b518", "Alterado no lab", "IMP-11"),
    "Escolha do k": ("lab-k", "Novo no lab", "CTF-03"),
    "B.8.5 com valor no eixo Y": ("lab-b885", "Novo no lab", "IMP-09"),
    "Checagem das correlações anuais": ("lab-emprego", "Novo no lab", "MAC-07"),
}

# O que cada marcador quer dizer (texto novo no paper; o "era" vem do marcador)
ERA_NOVO = {
    "era março de 2026": "dezembro de 2025 (foto de referência)",
    "era até março de 2026": "até dezembro de 2025",
    "era 2016–2026": "2016–2025",
    "era 2026": "2025",
}

# Callouts extras por seção (âncora do título -> (âncora do aviso, rótulo, texto))
EXTRA = {
    "s-1-2": ("lab-cnpj-1-2", "Alterado no lab", "Identificação do devedor por **CNPJ × sem CNPJ**, sem ler a coluna de CPF "
              "(no paper: CPF × CNPJ × sem identificador). CDAs com CNPJ e CPF ao mesmo tempo contam como CNPJ."),
    "s-1-3-1": ("lab-cnpj", "Alterado no lab", "A tabela classifica as CDAs em **CNPJ (pessoa jurídica)** e **sem CNPJ (CPF ou sem "
                "identificador)**, sem ler a coluna de CPF. No paper eram três classes: CPF, CNPJ e sem identificador. A "
                "exclusão de pessoas físicas no extrato (1.3) é a mesma do paper."),
    "s-1-5": ("lab-estoque-mensal", "Alterado no lab", "O estoque mensal soma **uma linha por CDA e mês**: o DEBITO tem linhas "
              "repetidas em vários meses (ex.: 2020-04 a 06, 2025-06/07) e o paper as somava. Nada é corrigido além disso; "
              "a tabela de qualidade está logo abaixo, em [Qualidade das fotos mensais](#lab-qualidade)."),
    "s-a-1-4": ("lab-agregados", "Alterado no lab", "No lugar da amostra de 10 linhas da foto (registros individuais), o "
                "esquema da tabela DEBITO sem as colunas de pessoa. O laboratório só mostra agregados."),
    "s-b-5-24": ("lab-agregados-b524", "Alterado no lab", "Só agregados: o paper mostrava as primeiras linhas com o hash."),
}
STAR_TEXT = ("Estrelas de significância nas tabelas e nos mapas de correlação: \\* p ≤ 0,10; \\*\\* p ≤ 0,05; \\*\\*\\* "
             "p ≤ 0,01 (sem correção para testes múltiplos). Nas tabelas, cada coluna de p ganha ao lado uma coluna "
             "“(sig.)” com as estrelas, e as linhas com p ≤ 0,10 na primeira coluna de p ficam em **negrito** (sem cor de "
             "fundo). A legenda acima de cada tabela diz qual coluna define o negrito. Todas as tabelas saem completas, "
             "com estrelas e negrito em todas as linhas; as com mais de 25 linhas ficam numa caixa rolável. Nos mapas de calor, a cor é o sinal e a força da "
             "correlação e as estrelas são a significância.")

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
                    "#### Novo no lab: qualidade das fotos mensais do DEBITO {#lab-qualidade}")
    md = md.replace("#### lab: recorte do laboratório",
                    "### Novo no lab: recorte em 2025, sem 2026 {#lab-recorte}")
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
            items = "; ".join(f"**{ERA_NOVO.get(e, '?')}** ({e})" for e in uniq)
            notes.append(callout("note", "Alterado no lab (IMP-12): corte em 2025",
                                 f"Texto do paper ajustado ao [recorte do laboratório](#lab-recorte): {items}."))
            state["n_era_sections"] += 1
        if sec["stars"]:
            anc = f"lab-sig-{sec['anchor'] or len(state['star_secs'])}"
            notes.append(callout("note", "Alterado no lab (MAC-05/06): estrelas de significância", STAR_TEXT, anc))
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
            "<!doctype html>\n<html lang=\"pt-BR\"><head><meta charset=\"utf-8\"><title>Figura " + cell +
            "</title></head>\n<body style=\"margin:0\">\n" + html + "\n</body></html>\n", encoding="utf-8")
        state["figs"].append((cell, len(html)))
        mb = len(html) / 1e6
        frame = (f'<iframe src="paper_eda_figs/{cell}.html" loading="lazy" title="Figura da célula {cell}" '
                 f'style="width:100%;height:{height}px;border:0"></iframe>\n'
                 f'<p class="small text-muted">Figura interativa carregada à parte ({mb:.0f} MB: o histograma leva todos '
                 f'os pontos). <a href="paper_eda_figs/{cell}.html" target="_blank">Abrir em outra aba</a>.</p>')
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
title: "EDA do paper (v1) no laboratório"
subtitle: "O paper_eda_v1 inteiro numa página, com as mudanças das reuniões de outubro de 2026"
description: "Partes 0–1 e A a E do EDA do artigo, com corte em 2025, Selic over, estrelas de significância e avisos de qualidade do DEBITO."
date: 2026-10-26
date-format: "DD/MM/YYYY"
toc: true
toc-depth: 4
toc-expand: true
toc-location: left
toc-title: "Sumário"
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
code-summary: "Código"
resources:
  - paper_eda_figs/*.html
include-in-header:
  - text: |
{inc_yaml}
---
"""


def novidades(state: dict) -> str:
    stars = state["star_secs"]
    star_links = ", ".join(f"[{t.split(' ')[0]}](#{a})" for a, t in stars[:12]) + (" …" if len(stars) > 12 else "")
    return f"""<!-- Gerado por lab/paper_eda/_pagina_unica.py a partir do _freeze das partes. Não editar à mão. -->

## Novidades em relação ao paper publicado {{#novidades}}

::: {{.callout-important title="Quadro de novidades (o que o laboratório mudou)"}}
Texto do paper mantido em inglês e sem reescrever conclusões. Cada mudança abaixo leva à seção afetada, onde há um
aviso **Novo no lab** ou **Alterado no lab**. Os números que mudaram aparecem nas tabelas e figuras da própria seção.

1. **Corte em 2025, sem 2026 (IMP-12).** Último ano completo: 2025; foto de referência: dezembro de 2025 (era março de
   2026). [Recorte do laboratório](#lab-recorte). O texto do paper foi ajustado em {state['n_markers']} trechos de
   {state['n_era_sections']} seções, cada uma com um aviso “Alterado no lab (IMP-12)”.
2. **Selic over no lugar da meta (MAC-03).** A variável `selic` passa a ser a Selic over (SGS 4189), não a meta (SGS 432).
   [1.6 Séries macroeconômicas](#lab-selic) · [Parte D](#s-part-d) · [página de checagem]({LINK_MACRO}).
3. **Estrelas de significância e linhas significativas em negrito (MAC-05/06).** \\* p ≤ 0,10; \\*\\* p ≤ 0,05;
   \\*\\*\\* p ≤ 0,01; negrito = p ≤ 0,10 na coluna indicada na legenda de cada tabela; em
   {len(stars)} seções: {star_links}.
4. **B.8.5 com valor em R$ no eixo Y (IMP-09).** [B.8.5 Time to settlement](#lab-b885).
5. **Concentração por tipo de tributo e por devedor (IMP-10).** [B.4 Concentration](#lab-concentracao) · [página de
   concentração]({LINK_CONC}).
6. **Correlações macro corrigidas (MAC-07).** Uma convenção de defasagem (macro em t − k), sem 2026, PIB mensal e
   crédito PJ deflacionados pelo IPCA (R$ de dez/2025) e PNAD Contínua em trimestres civis no trimestre e no ano.
   **Aviso:** a correlação anual emprego × estoque vem da tendência comum e da queda de 2020, que parece efeito da
   extração do DEBITO (EQ-02/EQ-05), e não deve ir para o texto. [1.6](#lab-selic) · [D.3 Annual correlations](#lab-emprego) ·
   [antes e depois](#lab-macro).
7. **Figura 5.18 legível (IMP-11, 27/10/2026):** um painel por origem, contagem de CDAs por célula e PC1 em escala log, no B.5.18 e no L.1. [B.5.18 Distance structure](#lab-b518) · [L.1 K-means no top 1%]({LINK_KM}).
8. **K-means com k = 4 como solução principal (IMP-13, CTF-03).** k = 4 por decisão do autor (27/10/2026); k = 2 a 8 ficam como diagnóstico. [B.5.19 Clustering](#lab-k) ·
   [K-means no top 1%]({LINK_KM}).
9. **Qualidade do DEBITO sinalizada, sem correção.** Fotos ausentes (2018-12; 2020-10 a 12), parciais (2018-11, 2024-03,
   **2024-12**) e linhas repetidas (ex.: 2020-04 a 06, **2025-06 e 2025-07**); o estoque mensal soma uma linha por CDA e mês.
   [Tabela de qualidade](#lab-qualidade) · [1.5 Monthly series](#lab-estoque-mensal).
10. **CNPJ × sem CNPJ no lugar de CPF × CNPJ.** A coluna de CPF não é lida. [1.2](#lab-cnpj-1-2) ·
    [1.3.1 Debtor identification](#lab-cnpj).
11. **Só agregados.** Nada por CPF, nome ou CDA individual. [A.1.4](#lab-agregados) · [B.5.24](#lab-agregados-b524).
12. **Todas as análises do laboratório nesta página (CTF-03).** As entradas de `lab/eda/` e `lab/dados/` estão inteiras em
    [L. Análises do laboratório](#s-lab), com subtópicos no sumário: [L.1 K-means](#lab-km) · [L.2 Concentração](#lab-conc) ·
    [L.3 Selic over e checagem macro](#lab-macro) · [L.4 Enriquecimento CNPJ](#lab-cnpj-rfb) · [L.5 EDA completo](#lab-edac) · [L.6 CNPJs por município](#lab-cnpj-geo).
13. **Gráficos agregados antes de publicar (DADOS-14).** Nenhuma figura leva mais um valor por CDA ou por devedor: os
    histogramas viram barras com a contagem por faixa (B.5.4, B.5.5, B.5.16, B.8.5 em R$, B.9.3), as caixas do B.2.3 são
    desenhadas com quartis, cercas e média já calculados, sem pontos individuais, as nuvens de pontos do B.5.18 e do
    [L.1](#lab-km) viram mapa de densidade 2D (60 × 60 células; contorno de cada grupo na cor dele) e as curvas longas
    (B.5.9, Lorenz do B.5.13) ficam com 500 pontos. Nada usa WebGL.
14. **CNPJs devedores por município de SP (GEO-01, 27/10/2026).** Mapa coroplético (malha do IBGE, sem tiles externos) com
    contagem, valor e CNPJs devedores por mil estabelecimentos ativos; município do cadastro RFB 2026-03; municípios com
    menos de 5 CNPJs suprimidos. [L.6](#lab-cnpj-geo).
:::

::: {{.callout-warning title="Qualidade do DEBITO (só sinalizado, nada corrigido)"}}
- Faltam as fotos de **2018-12** e de **2020-10 a 2020-12**. O arquivo de 2020 M10–M12 repete o de M04–M06, e o de
  2018 M10–M12 está truncado (2018-11 parcial). As fotos de **2024-03** e de **2024-12** estão parciais (menos de 80% da
  mediana móvel de CDAs; 2024-12 tem cerca de 1,27 milhão de CDAs contra cerca de 2,1 milhões nos meses vizinhos).
- Há linhas repetidas em vários meses, inclusive **2025-06 e 2025-07**. No laboratório o estoque mensal soma uma linha
  por CDA e mês (o paper somava as repetidas).
- Consequência no painel: o “fim de ano” de 2018 é a última foto em que cada CDA aparece (out. ou nov.), e o de 2020 é
  setembro. O de 2024 vem da foto parcial de dezembro. [Tabela completa](#lab-qualidade).
:::

::: {{.callout-tip collapse="true" title="Como esta página é feita"}}
Gerada por `lab/paper_eda/_gerar_paginas.py` (código de cada parte, a partir de `paper_eda_v1.ipynb`, sem rodar nem
alterar o notebook) e montada por `lab/paper_eda/_pagina_unica.py`, que junta o resultado já executado das 8 partes
(`lab/_freeze/paper_eda/`). Comentários `# lab:` no código marcam o que foi trocado. Figuras muito pesadas abrem em
arquivo à parte ({len(state['figs'])} figuras). Figuras e tabelas exportadas (só sob pedido, `LAB_EXPORT_FIGS=1`) vão
para `interim/paper_eda_lab/` no HD, com os rótulos `fig_*`/`tab_*` do paper.
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
    return SITE_LINK_RE.sub(lambda m: f"]({m.group(1) or '#novidades'})", md)


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


def lab_section(src: str, num: str, short: str, anchor: str, pfx: str, state: dict) -> tuple[str, list[str]]:
    md, inc = load_lab_page(src)
    m = re.match(r"\A---\n(.*?)\n---\n", md, flags=re.S)
    yml = m.group(1) if m else ""
    md = md[m.end():] if m else md
    md = escape_reais(md)
    desc = re.search(r'^description:\s*"(.*)"\s*$', yml, flags=re.M)
    title = re.search(r'^title:\s*"(.*)"\s*$', yml, flags=re.M)
    # ids internos com prefixo da página (fig-x -> fig-km-x; cell-fig-x -> cell-fig-km-x; setup -> km-setup)
    ids = set(re.findall(r"\{#([\w-]+)", md)) | set(re.findall(r"\bid=\"([\w-]+)\"", md))
    def newid(i):
        mm = re.match(r"^(cell-)?(fig-|tbl-)?(.*)$", i)
        return f"{mm.group(1) or ''}{mm.group(2) or ''}{pfx}-{mm.group(3)}"
    def _id(m):
        return "{#" + newid(m.group(1))
    md = re.sub(r"\{#([\w-]+)", _id, md)
    md = re.sub(r"@((?:fig|tbl)-[\w-]+)", lambda m: "@" + newid(m.group(1)), md)
    md = re.sub(r"\]\(#([\w-]+)\)", lambda m: f"](#{newid(m.group(1))})" if m.group(1) in ids or True else m.group(0), md)
    # títulos: ## -> ####, ### -> #####, #### -> ###### (fora das cercas de código); todo título ganha id fixo
    out, fence, seen = [], False, set()
    for ln in md.split("\n"):
        if ln.lstrip().startswith("```"):
            fence = not fence
        hm = None if fence else re.match(r"^(#{2,4}) (.+?)\s*$", ln)
        if hm:
            t = hm.group(2)
            if "{#" not in t:
                base = f"{pfx}-{_slug(t)}"
                sid, k = base, 2
                while sid in seen:
                    sid = f"{base}-{k}"; k += 1
                seen.add(sid)
                t = f"{t} {{#{sid}}}"
            ln = "#" * (len(hm.group(1)) + 2) + " " + t
        out.append(ln)
    md = "\n".join(out)
    md = lab_links(md)
    head = (f"### {num} {short} {{#{anchor}}}\n\n"
            f"::: {{.callout-tip appearance=\"simple\" title=\"Novo no lab: {title.group(1) if title else short}\"}}\n"
            f"{desc.group(1) if desc else ''} Conteúdo completo da entrada do laboratório `lab/{src}.qmd`, montado do "
            f"_freeze dela (nada é executado aqui).\n:::\n\n")
    state["anchors"].add(anchor)
    state["lab_pages"].append((num, short, anchor))
    return head + md.strip("\n") + "\n", inc


def lab_block(state: dict) -> tuple[str, list[str]]:
    bodies, incs = [], []
    for src, num, short, anchor, pfx in LAB_PAGES:
        b, inc = lab_section(src, num, short, anchor, pfx, state)
        bodies.append(f"<!-- lab: {src} -->\n\n{b}")
        incs += inc
    intro = ("## L. Análises do laboratório {#s-lab}\n\n"
             "As análises novas do laboratório, inteiras, nesta mesma página. Cada uma é uma entrada datada em `lab/eda/` ou "
             "`lab/dados/`, com a pergunta, os dados, o que foi encontrado e o que **não** concluir. Os avisos “Novo no "
             "lab” e “Alterado no lab” nas seções do paper levam até aqui.\n\n"
             + "\n".join(f"- [{n} {t}](#{a})" for n, t, a in state["lab_pages"]) + "\n")
    return intro + "\n\n" + "\n\n".join(bodies), incs


def main() -> int:
    state = {"anchors": set(), "star_secs": [], "n_markers": 0, "n_era_sections": 0, "figs": []}
    bodies, includes = [], []
    for name in PARTS:
        md, inc = load_part(name)
        includes += inc
        md = transform(strip_part(md), state)
        md = sectionize(md, state)
        bodies.append(f"<!-- parte: {name} -->\n\n{md}")
    state["lab_pages"] = []
    lab_md, lab_inc = lab_block(state)
    includes += lab_inc
    body = "\n\n".join(bodies)
    body = lab_links(body) + "\n\n" + lab_md
    body = drop_plotly_mathjax(body)
    # scattergl (WebGL) -> scatter (SVG): alguns navegadores/máquinas não têm WebGL e o plot some com
    # "WebGL is not supported". Só o tipo do trace muda; os dados vêm do _freeze, que não é alterado.
    state["n_gl"] = body.count('"type":"scattergl"')
    body = body.replace('"type":"scattergl"', '"type":"scatter"')
    assert "scattergl" not in body, "sobrou scattergl em outra forma"
    body = externalize_figs(body, state)
    page = header(state, includes) + "\n" + novidades(state) + "\n" + body
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
    defined = set(re.findall(r"\{#([\w-]+)[\s}]", page)) | {"novidades"}
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
