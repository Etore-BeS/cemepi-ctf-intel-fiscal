#!/usr/bin/env python3
"""Monta a árvore que o GitHub Pages publica: só a página única do EDA.

A raiz do site e /paper_eda/ servem essa página. A home do lab e os suplementos
não entram. Não apaga fontes. Só biblioteca padrão.

Uso, na raiz do repositório, depois de `quarto render lab`:
    python3 lab/paper_eda/_publish_eda_only.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
SITE = LAB / "_site"
OUT = LAB / "_site_pages"
PAGE = SITE / "paper_eda" / "index.html"


def root_html(html: str) -> str:
    """A mesma página na raiz: caminhos relativos deixam de subir um nível."""
    html = html.replace("../suplementos/index.html", "index.html")
    html = html.replace("../paper_eda/index.html", "paper_eda/")
    html = html.replace("../index.html", "index.html")
    html = html.replace("../site_libs/", "site_libs/")
    html = html.replace('src="paper_eda_figs/', 'src="paper_eda/paper_eda_figs/')
    html = html.replace('href="paper_eda_figs/', 'href="paper_eda/paper_eda_figs/')
    return html


def paper_html(html: str) -> str:
    """Em /paper_eda/, Home aponta para a raiz (a mesma página). Suplementos não são publicados."""
    html = html.replace("../suplementos/index.html", "../index.html")
    html = html.replace("../paper_eda/index.html", "index.html")
    return html


def main() -> int:
    if not PAGE.is_file():
        sys.exit(f"falta {PAGE}. Rode antes: quarto render lab")
    if not (SITE / "site_libs").is_dir():
        sys.exit(f"falta {SITE / 'site_libs'}. Rode antes: quarto render lab")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "paper_eda").mkdir(parents=True)
    shutil.copytree(SITE / "site_libs", OUT / "site_libs")
    figs = SITE / "paper_eda" / "paper_eda_figs"
    if figs.is_dir():
        shutil.copytree(figs, OUT / "paper_eda" / "paper_eda_figs")
    src = PAGE.read_text(encoding="utf-8")
    (OUT / "paper_eda" / "index.html").write_text(paper_html(src), encoding="utf-8")
    (OUT / "index.html").write_text(root_html(src), encoding="utf-8")
    banned = ["suplementos", "eda/", "dados/"]
    leaked = [
        p.name
        for p in OUT.iterdir()
        if p.name not in {"index.html", "site_libs", "paper_eda"}
    ]
    if leaked:
        sys.exit(f"a árvore publicada tem entradas a mais: {leaked}")
    text = (OUT / "index.html").read_text(encoding="utf-8")
    if "suplementos/" in text:
        sys.exit("a página da raiz ainda aponta para suplementos/")
    print(
        f"{OUT.relative_to(LAB)}: EDA na raiz e em paper_eda/; sem {', '.join(banned)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
