"""Figura B.5.18 legível (IMP-11, 27/10/2026): um painel por origem da CDA, só com contagens por célula.

O Étore achou o painel de PC1 ilegível ("bem ruim de enxergar alguma coisa"). Motivos: no PCA do paper o PC1 é quase só o
número de pagamentos (escala bruta, IQR = 0), então a maior parte das CDAs (as sem pagamento) fica empilhada no mínimo e a
cauda vai até centenas; e as três origens ficavam umas sobre as outras. Aqui:
- linhas: todas as origens (cinza) e cada origem com pelo menos MIN_N CDAs (na cor dela); origens com menos CDAs não ganham
  painel próprio (entram só em "todas"), para não mostrar a posição de poucas CDAs;
- colunas: as projeções (PCA do paper, PCA corrigido, t-SNE), com a mesma janela em todas as linhas da coluna;
- cada painel é um heatmap da contagem de CDAs por célula (grade N_GRADE × N_GRADE), tom em log10, e o contorno cinza de
  "todas as origens" como referência; eixo opcional em escala log (PC1 do paper: log10(PC1 − mínimo + 1)).
Nada de ponto por CDA: só contagens por célula (DADOS-14). Nada de WebGL.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

MIN_N = 5
N_GRADE = 50
JANELA = (0.5, 99.5)   # percentis da janela de cada eixo (CDAs fora dela são contadas no título da coluna)


def _rgba(hexcor: str, a: float) -> str:
    h = hexcor.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _br(n: int) -> str:
    return f"{n:,}"


def log_shift(v: np.ndarray) -> np.ndarray:
    """log10(v − mínimo + 1): eixo para variáveis com massa no mínimo e cauda longa (PC1 do paper)."""
    return np.log10(v - np.nanmin(v) + 1.0)


def fig_por_origem(paineis, origem, ordem, rotulos, cores, cor_total="#8A9499"):
    """paineis: lista de dicts {titulo, xy (n×2), eixo_x, eixo_y, idx (linhas de `origem`), log_x (bool)}."""
    linhas = [("__todas__", "all origins", cor_total)]
    omitidas = []
    for o in ordem:
        n_max = max(int((origem[p["idx"]] == o).sum()) for p in paineis)
        if n_max >= MIN_N:
            linhas.append((o, rotulos[o], cores[o]))
        else:
            omitidas.append(rotulos[o])
    nr, nc = len(linhas), len(paineis)
    tit_col = []
    for p in paineis:
        xy = p["xy"]
        lo, hi = np.percentile(xy, JANELA[0], axis=0), np.percentile(xy, JANELA[1], axis=0)
        p["_lo"], p["_hi"] = lo, hi
        p["_in"] = np.all((xy >= lo) & (xy <= hi), axis=1)
        fora = int((~p["_in"]).sum())
        tit_col.append(f"{p['titulo']}<br><span style='font-size:11px'>{_br(len(xy))} CDAs; {_br(fora)} outside the window "
                       f"({JANELA[0]:g}–{JANELA[1]:g}%)</span>")
    fig = make_subplots(rows=nr, cols=nc, subplot_titles=tit_col + [""] * (nc * (nr - 1)),
                        row_titles=[rot for _, rot, _ in linhas], horizontal_spacing=0.06, vertical_spacing=0.07)
    for c, p in enumerate(paineis, start=1):
        xy, org, ok = p["xy"], origem[p["idx"]], p["_in"]
        ex = np.linspace(p["_lo"][0], p["_hi"][0], N_GRADE + 1)
        ey = np.linspace(p["_lo"][1], p["_hi"][1], N_GRADE + 1)
        cx, cy = (ex[:-1] + ex[1:]) / 2, (ey[:-1] + ey[1:]) / 2
        tot, _, _ = np.histogram2d(xy[ok, 0], xy[ok, 1], [ex, ey])
        zmax = float(np.log10(tot.max())) if tot.max() > 0 else 1.0
        for r, (o, rot, cor) in enumerate(linhas, start=1):
            h = tot if o == "__todas__" else np.histogram2d(xy[ok & (org == o), 0], xy[ok & (org == o), 1], [ex, ey])[0]
            z = np.where(h > 0, np.log10(np.maximum(h, 1)), np.nan).T
            fig.add_trace(go.Heatmap(x=cx, y=cy, z=z, customdata=h.T.astype(int), zmin=0, zmax=zmax, showscale=False,
                                     colorscale=[[0, _rgba(cor, 0.25)], [1, _rgba(cor, 1.0)]], hoverongaps=False,
                                     name=rot, hovertemplate=f"{rot}<br>CDAs in the cell: %{{customdata}}<extra></extra>"), r, c)
            if o != "__todas__":
                fig.add_trace(go.Contour(x=cx, y=cy, z=np.log10(tot + 1).T, showscale=False, ncontours=6,
                                         contours=dict(coloring="none"), line=dict(color=cor_total, width=0.7),
                                         hoverinfo="skip", name="all origins (outline)"), r, c)
            fig.update_xaxes(range=[ex[0], ex[-1]], row=r, col=c, showticklabels=(r == nr))
            fig.update_yaxes(range=[ey[0], ey[-1]], row=r, col=c)
        fig.update_xaxes(title_text=p["eixo_x"], row=nr, col=c)
        fig.update_yaxes(title_text=p["eixo_y"], row=1, col=c)
        if p.get("log_x"):
            ticks = [d for d in (0, 1, 3, 10, 30, 100, 300) if np.log10(d + 1) <= ex[-1] + 1e-9]
            for r in range(1, nr + 1):
                fig.update_xaxes(tickvals=[np.log10(d + 1) for d in ticks], ticktext=[str(d) for d in ticks], row=r, col=c)
    fig.update_layout(height=260 * nr + 120, showlegend=False, margin=dict(t=90, b=60))
    return fig, omitidas
