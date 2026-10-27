#!/usr/bin/env python3
"""Gera o EDA do paper num ÚNICO arquivo HTML autocontido, para abrir com duplo clique (file://), sem servidor.

Uso (fish, na raiz do worktree):
    uv run --no-sync python lab/paper_eda/_html_unico.py
Saída: lab/_site/paper_eda/EDA_paper_CEMEPI.html e uma cópia em ~/Desktop/EDA_paper_CEMEPI.html
(--sem-desktop não copia; --sem-render usa o lab/_site/paper_eda/index.html que já existe).

Não executa nenhuma célula: (1) remonta lab/paper_eda/index.qmd do _freeze (_pagina_unica.py), (2) renderiza só
essa página, que não tem código (o Quarto só converte o markdown), e (3) embute tudo no HTML:
- CSS e JS de site_libs, ícones e a fonte do tema (Source Sans Pro) como data: URI;
- jQuery, RequireJS, plotly.js 4.0.0 e MathJax 3 (tex-svg, que já traz as fontes) uma vez só. Os arquivos da CDN
  são baixados na primeira vez, conferidos pelo hash de integridade quando a página traz um, e guardados em
  ~/.cache/cemepi-lab/cdn/; depois disso funciona sem internet;
- as 4 figuras pesadas (paper_eda_figs/, histogramas com todos os pontos) vão comprimidas (gzip + base64) e só são
  desenhadas quando chegam perto da tela, para o arquivo abrir rápido.
Só biblioteca padrão.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
SITE = LAB / "_site"
PAGE = SITE / "paper_eda" / "index.html"
OUT = SITE / "paper_eda" / "EDA_paper_CEMEPI.html"
DESKTOP = Path.home() / "Desktop" / "EDA_paper_CEMEPI.html"
CACHE = Path.home() / ".cache" / "cemepi-lab" / "cdn"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
MATHJAX = "https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-svg.min.js"


def fetch(url: str, integrity: str | None = None) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / hashlib.sha256(url.encode()).hexdigest()[:24]
    if f.exists():
        data = f.read_bytes()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    if integrity:
        ok = False
        for item in integrity.split():
            alg, _, want = item.partition("-")
            if alg in ("sha256", "sha384", "sha512"):
                ok |= base64.b64encode(hashlib.new(alg, data).digest()).decode() == want
        if not ok:
            f.unlink(missing_ok=True)
            sys.exit(f"integridade não confere: {url}")
    f.write_bytes(data)
    return data


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def js_block(code: str, module: bool = False) -> str:
    code = code.replace("</script", "<\\/script")
    return f'<script{" type=\"module\"" if module else ""}>\n{code}\n</script>'


def inline_css(css: str, base: Path | None, base_url: str | None = None) -> str:
    def _font_import(m):
        url = m.group(1)
        sub = fetch(url).decode("utf-8")
        return inline_css(sub, None, url)
    css = re.sub(r'@import\s*(?:url\()?["\'](https://fonts\.googleapis\.com/[^"\']+)["\']\)?\s*;', _font_import, css)

    def _url(m):
        raw = m.group(1).strip("'\"")
        if raw.startswith(("data:", "#")):
            return m.group(0)
        clean = raw.split("?")[0].split("#")[0]
        if raw.startswith("http"):
            data = fetch(raw)
        elif base is not None:
            data = (base / clean).read_bytes()
        else:
            return m.group(0)
        mime = mimetypes.guess_type(clean)[0] or ("font/woff2" if clean.endswith("woff2") else "font/woff" if clean.endswith("woff") else "application/octet-stream")
        return f'url("{data_uri(data, mime)}")'
    return re.sub(r"url\(([^)]+)\)", _url, css)


def heavy_fig(cell: str) -> str:
    """Figura pesada: o fragmento do plotly vai comprimido e é desenhado ao chegar perto da tela."""
    src = (SITE / "paper_eda" / "paper_eda_figs" / f"{cell}.html")
    if not src.exists():
        src = HERE / "paper_eda_figs" / f"{cell}.html"
    h = src.read_text(encoding="utf-8")
    body = re.search(r"<body[^>]*>(.*)</body>", h, re.S).group(1)
    body = re.sub(r'<script[^>]*src="https://[^"]+"[^>]*></script>', "", body)           # plotly/mathjax já embutidos
    body = re.sub(r"<script>if \(window\.MathJax.*?</script>", "", body, flags=re.S)
    body = body.replace('"type":"scattergl"', '"type":"scatter"')
    b64 = base64.b64encode(gzip.compress(body.encode("utf-8"), 9)).decode()
    return (f'<div class="lab-fig-lenta" data-cell="{cell}"><div class="lab-fig-alvo"></div>'
            f'<p class="small text-muted lab-fig-aviso">Carregando a figura (histograma com todos os pontos)…</p>'
            f'<script type="application/octet-stream" class="lab-fig-dados">{b64}</script></div>')


LAZY_JS = r"""
(function () {
  async function carregar(el) {
    if (el.dataset.ok) return; el.dataset.ok = "1";
    try {
      var b64 = el.querySelector("script.lab-fig-dados").textContent.trim();
      var bin = Uint8Array.from(atob(b64), function (c) { return c.charCodeAt(0); });
      var txt = await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
      var alvo = el.querySelector(".lab-fig-alvo");
      alvo.appendChild(document.createRange().createContextualFragment(txt));
      var av = el.querySelector(".lab-fig-aviso"); if (av) av.remove();
    } catch (e) {
      el.querySelector(".lab-fig-aviso").textContent = "Não foi possível desenhar esta figura neste navegador (" + e + ").";
    }
  }
  function init() {
    var els = document.querySelectorAll(".lab-fig-lenta");
    if (!("IntersectionObserver" in window)) { els.forEach(carregar); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { io.unobserve(en.target); carregar(en.target); } });
    }, { rootMargin: "800px 0px" });
    els.forEach(function (el) { io.observe(el); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
"""


def build() -> tuple[str, dict]:
    h = PAGE.read_text(encoding="utf-8")
    base = PAGE.parent
    st = {"css": 0, "js": 0, "cdn": [], "figs": 0, "links": 0}
    seen: set[str] = set()

    # 1) CSS locais
    def _css(m):
        tag = m.group(0)
        href = re.search(r'href="([^"]+)"', tag).group(1)
        p = (base / href).resolve()
        st["css"] += 1
        return f"<style>\n{inline_css(p.read_text(encoding='utf-8'), p.parent)}\n</style>"
    h = re.sub(r'<link[^>]*href="(?!https?:)[^"]+\.css"[^>]*>', _css, h)

    # 2) scripts com src (locais e CDN), cada um uma vez
    tabsets = (SITE / "site_libs/quarto-html/tabsets/tabsets.js").read_text(encoding="utf-8")

    def _js(m):
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag).group(1)
        module = 'type="module"' in tag
        if "polyfill" in src or "mathjax@4" in src:
            return ""                                   # polyfill dispensável; MathJax 4 (CHTML) baixa fontes da CDN
        key = src.split("?")[0]
        if key in seen:
            return ""
        seen.add(key)
        if src.startswith("http"):
            integ = re.search(r'integrity="([^"]+)"', tag)
            code = fetch(src, integ.group(1) if integ else None).decode("utf-8")
            st["cdn"].append(key.rsplit("/", 1)[-1])
        else:
            code = (base / src).read_text(encoding="utf-8")
            st["js"] += 1
            if src.endswith("quarto.js"):               # import relativo não funciona num <script> embutido
                code = code.replace('from "./tabsets/tabsets.js"',
                                    f'from "{data_uri(tabsets.encode(), "text/javascript")}"')
        return js_block(code, module)
    h = re.sub(r"<script\b[^>]*\bsrc=\"[^\"]+\"[^>]*>\s*</script>", _js, h)
    h = h.replace('<script type="module">import "https://cdn.plot.ly/plotly-4.0.0.min"</script>', "")
    # MathJax 3 (SVG, sem arquivos de fonte) no lugar do 4 (CHTML)
    if MATHJAX not in seen:
        h = h.replace("</head>", js_block(fetch(MATHJAX).decode("utf-8")) + "\n</head>", 1)
        st["cdn"].append("tex-svg.min.js")

    # 3) figuras pesadas: iframe -> fragmento comprimido desenhado sob demanda
    def _fig(m):
        st["figs"] += 1
        return heavy_fig(m.group(1))
    h = re.sub(r'<iframe src="paper_eda_figs/([\w-]+)\.html"[^>]*></iframe>\s*<p class="small text-muted">.*?</p>', _fig, h, flags=re.S)
    h = h.replace("</body>", js_block(LAZY_JS) + "\n</body>", 1)

    # 4) CTF-03 (26/10/2026): todo o conteúdo do lab está neste arquivo (seção L), então não sobra link para o site
    #    publicado. Saem da barra o "Início" e os "Suplementos" (outras páginas); a própria página vira âncora local.
    #    Qualquer outro link relativo para .html é erro.
    h = re.sub(r'<li class="nav-item">\s*<a class="nav-link" href="\.\./(?:index|suplementos/index)\.html">.*?</li>',
               "", h, flags=re.S)

    def _href(m):
        rel = m.group(1)
        st["links"] += 1
        if rel in ("../paper_eda/index.html", "./index.html", "index.html", "../index.html"):
            return 'href="#"'
        sys.exit(f"link para conteúdo fora do arquivo: {rel}")
    h = re.sub(r'href="((?:\.\./|\./)[^"#]*\.html)', _href, h)
    h = h.replace('<div id="quarto-search" class="" title="Procurar"></div>', "")   # a busca precisa de servidor
    h = h.replace('"type":"scattergl"', '"type":"scatter"')
    return h, st


def main() -> int:
    args = set(sys.argv[1:])
    env = dict(os.environ)
    if "--sem-render" not in args:
        subprocess.run([sys.executable, str(HERE / "_pagina_unica.py")], check=True)
        quarto = shutil.which("quarto") or str(Path.home() / ".local/bin/quarto")
        # a página não tem código: o Quarto só converte o markdown (nenhuma célula roda)
        subprocess.run([quarto, "render", str(HERE / "index.qmd")], check=True, env=env, cwd=LAB.parent)
    h, st = build()
    left = [u for u in re.findall(r'<(?:script|link|img|iframe)[^>]+(?:src|href)="(https?://[^"]+)"', h)]
    if left:
        sys.exit(f"sobrou recurso externo: {left[:5]}")
    if "etore-bes.github.io" in h:
        sys.exit("sobrou link para o site publicado (etore-bes.github.io)")
    OUT.write_text(h, encoding="utf-8")
    msg = (f"{OUT.relative_to(LAB.parent)}: {OUT.stat().st_size / 1e6:.1f} MB; CSS {st['css']}, JS locais {st['js']}, "
           f"CDN embutidos {st['cdn']}, figuras pesadas {st['figs']}, links do lab {st['links']}")
    if "--sem-desktop" not in args and DESKTOP.parent.exists():
        shutil.copy2(OUT, DESKTOP)
        msg += f"; cópia em {DESKTOP}"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
