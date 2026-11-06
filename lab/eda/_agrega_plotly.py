"""Agrega, antes de sair no _freeze, os traces plotly que guardariam um valor por CDA ou por devedor (DADOS-14).

O _freeze vai para o git público. Um histograma, box plot ou scatter do plotly leva os dados brutos para o HTML (o
navegador é que conta as faixas), então o _freeze guardava, por exemplo, 1,4 milhão de idades de CDA. Este módulo troca,
na hora de mostrar a figura, cada trace desses pelo agregado que o gráfico desenha:

- histogram  -> barras com as contagens por faixa (np.histogram; mesmo número de faixas, histnorm e cumulative);
- box/violin -> caixa com q1, mediana, q3, cercas (1,5 IQR, limitadas aos dados) e média; sem pontos individuais;
- histogram2d/histogram2dcontour -> heatmap das contagens;
- scatter/scattergl só de marcadores, com mais de LIMITE pontos no mesmo painel -> mapa de densidade 2D
  (heatmap das contagens; com vários grupos, um heatmap do total e o contorno de cada grupo na cor dele);
- scatter de linhas com mais de LIMITE pontos e eixo x numérico (Lorenz, ECDF) -> a mesma curva em 500 pontos.

Séries no tempo (eixo x de datas ou texto) não mudam. Nada usa WebGL. Instalado por _lab_base (import) para todas as
páginas do lab; desligue com CEMEPI_AGREGA_PLOTLY=0 (só para depurar: o resultado não pode ir para o git).
"""
from __future__ import annotations

import os

import numpy as np

LIMITE = 2000          # acima disso o trace é tratado como "um valor por unidade"
N_CURVA = 500          # pontos que sobram numa curva longa
N_GRADE = 60           # faixas por eixo no mapa de densidade
REGISTRO: list[dict] = []   # o que foi agregado nesta execução (para conferência)


def _d(obj):
    """Propriedade composta (marker, line) como dict, para passar de um tipo de trace a outro."""
    return obj.to_plotly_json() if obj is not None and hasattr(obj, "to_plotly_json") else obj


def _arr(v):
    if v is None:
        return None
    a = np.asarray(v)
    return a if a.ndim == 1 and a.size else None


def _num(a):
    if a is None:
        return None
    if np.issubdtype(a.dtype, np.datetime64) or a.dtype.kind in "OUSb":
        try:
            a = a.astype(float)
        except (TypeError, ValueError):
            return None
    return a.astype(float)


def _edges(data, xbins, nbins):
    d = data[np.isfinite(data)]
    if xbins is not None and xbins.size is not None and xbins.start is not None and xbins.end is not None:
        try:
            return np.arange(float(xbins.start), float(xbins.end) + float(xbins.size) * 0.5, float(xbins.size))
        except (TypeError, ValueError):
            pass
    if nbins:
        return np.histogram_bin_edges(d, bins=int(nbins))
    e = np.histogram_bin_edges(d, bins="auto")
    return e if e.size <= 201 else np.histogram_bin_edges(d, bins=200)


def _hist_to_bar(tr, go):
    horiz = tr.orientation == "h" or (_arr(tr.x) is None and _arr(tr.y) is not None)
    data = _num(_arr(tr.y if horiz else tr.x))
    other = _num(_arr(tr.x if horiz else tr.y))
    if data is None:
        return None
    w = other if (tr.histfunc == "sum" and other is not None and other.size == data.size) else None
    m = np.isfinite(data) & (np.isfinite(w) if w is not None else True)
    data, w = data[m], (w[m] if w is not None else None)
    edges = _edges(data, tr.ybins if horiz else tr.xbins, tr.nbinsy if horiz else tr.nbinsx)
    c, edges = np.histogram(data, bins=edges, weights=w)
    width = np.diff(edges)
    norm = tr.histnorm or ""
    tot = c.sum() or 1
    if norm == "percent":
        c = 100 * c / tot
    elif norm == "probability":
        c = c / tot
    elif norm == "density":
        c = c / width
    elif norm == "probability density":
        c = c / tot / width
    if tr.cumulative is not None and tr.cumulative.enabled:
        c = np.cumsum(c)
    mid = (edges[:-1] + edges[1:]) / 2
    kw = dict(name=tr.name, legendgroup=tr.legendgroup, showlegend=tr.showlegend, opacity=tr.opacity,
              marker=_d(tr.marker), xaxis=tr.xaxis, yaxis=tr.yaxis, offsetgroup=tr.offsetgroup)
    if horiz:
        bar = go.Bar(y=mid, x=c, width=width, orientation="h", skip_invalid=True, **kw)
    else:
        bar = go.Bar(x=mid, y=c, width=width, skip_invalid=True, **kw)
    bar.hovertemplate = "%{" + ("y" if horiz else "x") + ":.3g}: %{" + ("x" if horiz else "y") + ":.4g}<extra></extra>"
    return bar, int(data.size)


def _box_stats(v):
    v = v[np.isfinite(v)]
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    lo = v[v >= q1 - 1.5 * iqr].min()
    hi = v[v <= q3 + 1.5 * iqr].max()
    return q1, med, q3, lo, hi, v.mean()


def _box_to_stats(tr, go):
    horiz = tr.orientation == "h"
    val = _num(_arr(tr.x if horiz else tr.y))
    pos = _arr(tr.y if horiz else tr.x)
    if val is None:
        return None
    if pos is not None and pos.size == val.size:
        cats = list(dict.fromkeys(pos.tolist()))
        groups = [val[pos == c] for c in cats]
    else:
        cats = [tr.name if tr.name is not None else (tr.x0 if not horiz else tr.y0) or ""]
        groups = [val]
    st = np.array([_box_stats(g) for g in groups])
    kw = dict(name=tr.name, legendgroup=tr.legendgroup, showlegend=tr.showlegend, marker=_d(tr.marker), line=_d(tr.line),
              fillcolor=tr.fillcolor, xaxis=tr.xaxis, yaxis=tr.yaxis, offsetgroup=tr.offsetgroup, boxpoints=False,
              q1=st[:, 0], median=st[:, 1], q3=st[:, 2], lowerfence=st[:, 3], upperfence=st[:, 4], mean=st[:, 5])
    box = go.Box(y=cats, orientation="h", skip_invalid=True, **kw) if horiz else go.Box(x=cats, skip_invalid=True, **kw)
    return box, int(val.size)


def _density(traces, go, layout):
    # grupo sem nenhum ponto neste painel (ex.: categoria fora da subamostra do t-SNE): não entra no mapa
    traces = [t for t in traces if _num(_arr(t.x)) is not None and _num(_arr(t.y)) is not None]
    xs = [_num(_arr(t.x)) for t in traces]
    ys = [_num(_arr(t.y)) for t in traces]
    allx = np.concatenate([x[np.isfinite(x)] for x in xs]); ally = np.concatenate([y[np.isfinite(y)] for y in ys])
    ex = np.linspace(allx.min(), allx.max(), N_GRADE + 1); ey = np.linspace(ally.min(), ally.max(), N_GRADE + 1)
    cx, cy = (ex[:-1] + ex[1:]) / 2, (ey[:-1] + ey[1:]) / 2
    ref = traces[0]
    out = []
    tot = np.zeros((N_GRADE, N_GRADE))
    per = []
    for t, x, y in zip(traces, xs, ys):
        m = np.isfinite(x) & np.isfinite(y)
        h, _, _ = np.histogram2d(x[m], y[m], bins=[ex, ey])
        tot += h; per.append((t, h))
    z = np.where(tot > 0, tot, np.nan).T
    out.append(go.Heatmap(x=cx, y=cy, z=z, colorscale=[[0, "#E6EEF0"], [1, "#154C56"]], showscale=False,
                          name="CDAs per cell", hovertemplate="x %{x:.3g}, y %{y:.3g}: %{z} CDAs<extra></extra>",
                          xaxis=ref.xaxis, yaxis=ref.yaxis, showlegend=False))
    if len(per) > 1:
        for t, h in per:
            col = None
            if t.marker is not None and isinstance(t.marker.color, str):
                col = t.marker.color
            if h.sum() == 0:
                continue
            out.append(go.Contour(x=cx, y=cy, z=h.T, contours=dict(coloring="none", start=max(1.0, h.max() * 0.1),
                                  end=h.max(), size=max(1.0, h.max() * 0.3)), line=dict(color=col, width=1.5),
                                  showscale=False, name=t.name, legendgroup=t.legendgroup, showlegend=t.showlegend,
                                  hoverinfo="skip", xaxis=t.xaxis, yaxis=t.yaxis))
    return out, int(sum(np.isfinite(x).sum() for x in xs))


def _curve(tr):
    x, y = _num(_arr(tr.x)), _num(_arr(tr.y))
    if x is None or y is None or x.size != y.size:
        return False
    idx = np.unique(np.linspace(0, x.size - 1, N_CURVA).round().astype(int))
    n = int(x.size)
    tr.x, tr.y = x[idx], y[idx]
    for a in ("customdata", "text", "hovertext"):
        v = getattr(tr, a, None)
        if v is not None and not isinstance(v, str) and len(v) == n:
            setattr(tr, a, np.asarray(v)[idx])
    return n


def aggregate(fig):
    """Agrega no lugar (idempotente) e devolve a figura."""
    if os.environ.get("CEMEPI_AGREGA_PLOTLY", "1") == "0":
        return fig
    import plotly.graph_objects as go
    novos, grupos, mudou = [], {}, []
    def _n(tr):
        return max([a.size for a in (_arr(getattr(tr, "x", None)), _arr(getattr(tr, "y", None))) if a is not None] or [0])
    # histogramas e caixas: se um trace da figura passa do limite, todos os do mesmo tipo são agregados (a figura não
    # mistura grupo agregado com grupo em valores individuais)
    forcar = {t for t in ("histogram", "box", "violin") if any(tr.type == t and _n(tr) > LIMITE for tr in fig.data)}
    for tr in fig.data:
        t = tr.type
        n = _n(tr)
        if n <= LIMITE and not (t in forcar and n > 0):
            novos.append(tr); continue
        if t == "histogram":
            r = _hist_to_bar(tr, go)
            if r: novos.append(r[0]); mudou.append(("histogram", tr.name, r[1])); continue
        if t in ("box", "violin"):
            r = _box_to_stats(tr, go)
            if r: novos.append(r[0]); mudou.append((t, tr.name, r[1])); continue
        if t in ("histogram2d", "histogram2dcontour"):
            r = _density([tr], go, fig.layout)
            novos.extend(r[0][:1]); mudou.append((t, tr.name, r[1])); continue
        if t in ("scatter", "scattergl"):
            mode = tr.mode or ("markers" if n > 20 else "lines+markers")
            if "lines" not in mode and _num(_arr(tr.x)) is not None and _num(_arr(tr.y)) is not None:
                grupos.setdefault((tr.xaxis or "x", tr.yaxis or "y"), []).append(tr)
                novos.append(("grupo", (tr.xaxis or "x", tr.yaxis or "y"))); continue
            if _num(_arr(tr.x)) is not None and _arr(tr.x).dtype.kind not in "MOUS":
                k = _curve(tr)
                if k: mudou.append(("curva", tr.name, k))
        novos.append(tr)
    # painéis com marcadores: se o painel passa do limite somando os grupos, vira densidade (inclui grupos pequenos)
    for tr in fig.data:
        if tr.type in ("scatter", "scattergl") and "lines" not in (tr.mode or "markers"):
            key = (tr.xaxis or "x", tr.yaxis or "y")
            if key in grupos and tr not in grupos[key]:
                grupos[key].append(tr)
                novos = [v for v in novos if v is not tr]
    feitos = set()
    final = []
    for v in novos:
        if isinstance(v, tuple) and v[0] == "grupo":
            if v[1] in feitos:
                continue
            feitos.add(v[1])
            r = _density(grupos[v[1]], go, fig.layout)
            final.extend(r[0]); mudou.append(("scatter->densidade", "/".join(str(t.name) for t in grupos[v[1]]), r[1]))
        else:
            final.append(v)
    if mudou:
        fig.data = []
        for tr in final:
            fig.add_trace(tr)
        if any(m[0] == "histogram" for m in mudou) and fig.layout.bargap is None:
            fig.layout.bargap = 0
        REGISTRO.extend({"tipo": m[0], "trace": m[1], "valores": m[2]} for m in mudou)
        log = os.environ.get("CEMEPI_AGREGA_LOG")          # opcional: lista do que foi agregado (conferência local)
        if log:
            import json
            titulo = fig.layout.title.text if fig.layout.title and fig.layout.title.text else ""
            with open(log, "a", encoding="utf-8") as f:
                for m in mudou:
                    f.write(json.dumps({"titulo": str(titulo)[:120], "tipo": m[0], "trace": str(m[1]),
                                        "valores": int(m[2]) if isinstance(m[2], (int, np.integer)) else str(m[2])},
                                       ensure_ascii=False) + "\n")
    return fig


def install():
    """Agrega toda figura antes de ser mostrada (fig.show(), fig no fim da célula, display(fig))."""
    from plotly.basedatatypes import BaseFigure
    if getattr(BaseFigure, "_lab_agrega_instalado", False):
        return
    for nome in ("show", "_ipython_display_", "_repr_mimebundle_", "to_html", "to_json", "write_html"):
        orig = getattr(BaseFigure, nome, None)
        if orig is None:
            continue
        def wrap(self, *a, __orig=orig, **k):
            aggregate(self)
            return __orig(self, *a, **k)
        wrap.__name__ = nome
        setattr(BaseFigure, nome, wrap)
    BaseFigure._lab_agrega_instalado = True
