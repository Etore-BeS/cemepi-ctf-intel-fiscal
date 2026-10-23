#!/usr/bin/env python3
"""Confere se o _freeze/ do Quarto está em dia com as fontes do laboratório.

No CI não há dados nem Jupyter: o site só pode ser montado a partir do _freeze/.
Se um .qmd/.ipynb com código mudou e não foi renderizado localmente, o Quarto
tentaria executá-lo no CI e falharia. Este script dá a mensagem clara antes disso.

Regra (verificada empiricamente no Quarto 1.10.19, 23/10/2026): o campo "hash" de
_freeze/<caminho-sem-extensão>/execute-results/html.json é o MD5 do arquivo-fonte.
Isso é detalhe interno do Quarto; se mudar numa versão futura, o próprio
`quarto render` no CI continua falhando quando o freeze estiver desatualizado.

Pastas listadas em --frozen (padrão: suplementos) usam `freeze: true`: basta existir freeze.

Saída: 0 = em dia; 1 = faltando/desatualizado.
Uso: python3 scripts/check_freeze.py [--project lab] [--frozen suplementos]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

CODE_CHUNK = re.compile(r"^\s*```+\s*\{\s*(python|r|julia|bash|sql)\b", re.IGNORECASE | re.MULTILINE)


def has_code(src: Path) -> bool:
    if src.suffix == ".qmd":
        return bool(CODE_CHUNK.search(src.read_text(encoding="utf-8", errors="replace")))
    if src.suffix == ".ipynb":
        try:
            nb = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return True
        return any(c.get("cell_type") == "code" and "".join(c.get("source", [])).strip() for c in nb.get("cells", []))
    return False


def sources(project: Path):
    for p in sorted(project.rglob("*")):
        if p.suffix not in (".qmd", ".ipynb") or not p.is_file():
            continue
        rel = p.relative_to(project)
        if any(part.startswith(("_", ".")) for part in rel.parts):
            continue  # _freeze, _site, _modelos, .quarto… (o Quarto também ignora)
        yield p, rel


def check(project: Path, frozen_dirs: list[str]) -> list[str]:
    problems = []
    for src, rel in sources(project):
        if not has_code(src):
            continue
        fj = project / "_freeze" / rel.with_suffix("") / "execute-results" / "html.json"
        if not fj.exists():
            problems.append(f"{rel}: sem _freeze (renderize localmente: quarto render {project.as_posix()})")
            continue
        if rel.parts and rel.parts[0] in frozen_dirs:
            continue
        try:
            stored = json.loads(fj.read_text(encoding="utf-8")).get("hash")
        except json.JSONDecodeError:
            problems.append(f"{rel}: _freeze ilegível ({fj})")
            continue
        current = hashlib.md5(src.read_bytes()).hexdigest()
        if stored != current:
            problems.append(f"{rel}: _freeze desatualizado (fonte mudou depois do último render local)")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Confere o _freeze do Quarto.")
    ap.add_argument("--project", default="lab")
    ap.add_argument("--frozen", nargs="*", default=["suplementos"])
    ap.add_argument("files", nargs="*", help="ignorado (compatibilidade com pre-commit)")
    a = ap.parse_args(argv)
    project = Path(a.project)
    if not project.is_dir():
        print(f"check_freeze: projeto {project} não existe", file=sys.stderr)
        return 1
    probs = check(project, a.frozen)
    for p in probs:
        print(f"check_freeze: {p}")
    if probs:
        print("\nRode localmente (com acesso ao HD) `quarto render lab` e faça commit de lab/_freeze/.", file=sys.stderr)
        return 1
    print("check_freeze: _freeze em dia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
