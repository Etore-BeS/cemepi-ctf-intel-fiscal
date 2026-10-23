#!/usr/bin/env python3
"""Varredura de dados pessoais (PII) antes de publicar o laboratório no GitHub Pages.

Procura, em arquivos de texto:
  - cpf    : CPF formatado (000.000.000-00) ou não (11 dígitos), só se o dígito verificador for válido;
  - cnpj   : CNPJ formatado (00.000.000/0000-00) ou não (14 dígitos), idem;
  - email  : endereços de e-mail fora dos domínios permitidos;
  - coluna_proibida : colunas proibidas (NOME_DEVEDOR, CPF_DEVEDOR, nome_socio, cpf_cnpj_socio…)
             aparecendo COM VALOR ao lado (chave: valor, cabeçalho de tabela HTML/CSV/Markdown,
             tabela de texto do pandas/polars). A simples menção ao nome da coluna é permitida.

Saída: 0 = limpo; 1 = achados; 2 = erro de configuração.
Os achados são mascarados: o script nunca imprime o valor completo.

Uso:
  python3 scripts/pii_check.py                  # varre lab/ (fontes, _freeze e _site)
  python3 scripts/pii_check.py lab/_site        # só o HTML gerado
  python3 scripts/pii_check.py --config scripts/pii_allowlist.toml arquivo1 arquivo2
Só usa a biblioteca padrão (Python >= 3.11).
"""
from __future__ import annotations

import argparse
import bisect
import fnmatch
import hashlib
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("pii_allowlist.toml")
DEFAULT_FORBIDDEN = ["NOME_DEVEDOR", "CPF_DEVEDOR", "nome_socio", "cpf_cnpj_socio"]
DEFAULT_EXCLUDE = [
    "**/site_libs/**",
    "**/.quarto/**",
    "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.gif", "**/*.svg", "**/*.pdf",
    "**/*.parquet", "**/*.duckdb", "**/*.woff", "**/*.woff2", "**/*.ttf", "**/*.ico",
]
TEXT_SUFFIXES = {
    ".qmd", ".md", ".ipynb", ".json", ".html", ".htm", ".yml", ".yaml", ".csv", ".tsv",
    ".txt", ".py", ".js", ".tex", ".xml", ".toml", ".r", ".sql", "",
}
RULES = ("cpf", "cnpj", "email", "coluna_proibida")


# ----------------------------------------------------------------------------- validação
def cpf_valido(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or digits == digits[0] * 11:
        return False
    nums = [int(c) for c in digits]
    for n in (9, 10):
        s = sum(nums[i] * (n + 1 - i) for i in range(n))
        dv = (s * 10) % 11 % 10
        if dv != nums[n]:
            return False
    return True


def cnpj_valido(digits: str) -> bool:
    if len(digits) != 14 or not digits.isdigit() or digits == digits[0] * 14:
        return False
    nums = [int(c) for c in digits]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        s = sum(n * p for n, p in zip(nums[:pos], pesos))
        r = s % 11
        dv = 0 if r < 2 else 11 - r
        if dv != nums[pos]:
            return False
    return True


# ----------------------------------------------------------------------------- padrões
# Formatados: fronteira = não-dígito.
RE_CPF_FMT = re.compile(r"(?<![\d])(\d{3}\.\d{3}\.\d{3}-\d{2})(?![\d])")
RE_CNPJ_FMT = re.compile(r"(?<![\d])(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})(?![\d])")
# Não formatados: fronteira = não alfanumérico, não '.', ',' (evita floats, base64, hashes hex)
_B = r"(?<![0-9A-Za-z_.,+/-])"
_A = r"(?![0-9A-Za-z_]|[.,]\d)"
RE_CPF_RAW = re.compile(_B + r"(\d{11})" + _A)
RE_CNPJ_RAW = re.compile(_B + r"(\d{14})" + _A)
RE_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![A-Za-z0-9-])")

BENIGN_VALUE = re.compile(
    r"""^(
        |null|none|nan|na|n/a|-|—|\.\.\.|…
        |\*+
        |\[?[A-Z_]*HASH[^\]]*\]?
        |[0-9a-f]{16,}
        |(pl\.|pa\.|np\.)?(utf8|string|str|object|category|categorical|varchar|text|int64|float64)(\(\))?
        |[-+]?\d[\d.,eE+-]*%?          # número / percentual (taxa de nulos etc.)
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
TABLE_SEPARATORS = (",", ";", "\t", "|", "│", "┆")
MD_RULE_LINE = re.compile(r"^\s*\|?\s*:?-{3,}")
INDEX_ROW = re.compile(r"^\s*(?:\\n|\")?\s*\d+\s+\S")


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    masked: str
    raw: str = field(repr=False)

    def __str__(self) -> str:  # nunca imprime o valor bruto
        return f"{self.path}:{self.line}: [{self.rule}] {self.masked}"


@dataclass
class AllowEntry:
    path: str
    rules: list[str]
    values: set[str]
    sha256: set[str]

    def matches(self, f: Finding) -> bool:
        if not fnmatch.fnmatch(f.path, self.path):
            return False
        if "*" not in self.rules and f.rule not in self.rules:
            return False
        if not self.values and not self.sha256:
            return True
        return f.raw in self.values or hashlib.sha256(f.raw.encode()).hexdigest() in self.sha256


@dataclass
class Config:
    exclude: list[str]
    forbidden_columns: list[str]
    allowed_email_domains: set[str]
    allowed_emails: set[str]
    allow: list[AllowEntry]


def load_config(path: Path | None) -> Config:
    data: dict = {}
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"config não encontrada: {path}")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    allow = []
    for i, e in enumerate(data.get("allow", [])):
        if "path" not in e:
            raise ValueError(f"[[allow]] #{i + 1} sem 'path'")
        rules = e.get("rules", ["*"])
        bad = [r for r in rules if r != "*" and r not in RULES]
        if bad:
            raise ValueError(f"[[allow]] #{i + 1}: regra desconhecida {bad}; use {RULES} ou '*'")
        allow.append(AllowEntry(e["path"], rules, set(e.get("values", [])), {s.lower() for s in e.get("sha256", [])}))
    return Config(
        exclude=DEFAULT_EXCLUDE + list(data.get("exclude", [])),
        forbidden_columns=list(data.get("forbidden_columns", DEFAULT_FORBIDDEN)),
        allowed_email_domains={d.lower() for d in data.get("allowed_email_domains", [])},
        allowed_emails={e.lower() for e in data.get("allowed_emails", [])},
        allow=allow,
    )


# ----------------------------------------------------------------------------- máscara
def mask_digits(s: str) -> str:
    total = sum(c.isdigit() for c in s)
    out, seen = [], 0
    for c in s:
        if c.isdigit():
            seen += 1
            out.append(c if seen > total - 2 else "*")
        else:
            out.append(c)
    return "".join(out)


def mask_email(s: str) -> str:
    user, _, dom = s.partition("@")
    return f"{user[:1]}***@{dom}"


# ----------------------------------------------------------------------------- varredura
class _Lines:
    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)


def _col_regex(cols: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(c) for c in sorted(cols, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_]+\.)?(" + alt + r")(?![A-Za-z0-9_])", re.IGNORECASE)


def scan_text(text: str, relpath: str, cfg: Config) -> list[Finding]:
    found: list[Finding] = []
    lines = _Lines(text)

    def add(pos: int, rule: str, masked: str, raw: str) -> None:
        found.append(Finding(relpath, lines.line_of(pos), rule, masked, raw))

    # CPF / CNPJ
    for rx, rule, ok in ((RE_CPF_FMT, "cpf", cpf_valido), (RE_CPF_RAW, "cpf", cpf_valido),
                         (RE_CNPJ_FMT, "cnpj", cnpj_valido), (RE_CNPJ_RAW, "cnpj", cnpj_valido)):
        for m in rx.finditer(text):
            raw = m.group(1)
            if ok(re.sub(r"\D", "", raw)):
                add(m.start(1), rule, mask_digits(raw), raw)

    # e-mails
    for m in RE_EMAIL.finditer(text):
        raw = m.group(1)
        dom = raw.rsplit("@", 1)[1].lower()
        if raw.lower() in cfg.allowed_emails or any(dom == d or dom.endswith("." + d) for d in cfg.allowed_email_domains):
            continue
        add(m.start(1), "email", mask_email(raw), raw)

    # colunas proibidas com valor
    if cfg.forbidden_columns:
        found.extend(_scan_columns(text, relpath, cfg, lines))
    return found


def _scan_columns(text: str, relpath: str, cfg: Config, lines: _Lines) -> list[Finding]:
    out: list[Finding] = []
    col_rx = _col_regex(cfg.forbidden_columns)
    alt = col_rx.pattern
    # 1) chave: valor  /  chave = valor   (JSON, YAML, SQL, Python dict)
    kv = re.compile(
        alt + r"""\\?["']?\s*(?:==|=|:)\s*"""
        r"""(?:\\"((?:[^"\\]|\\[^"])*)\\"|"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s,;}\]\)<"'\\]+))""",
        re.IGNORECASE,
    )
    for m in kv.finditer(text):
        val = next((g for g in m.groups()[1:] if g is not None), "")
        val = re.sub(r"(\\n)+$", "", val).strip()
        if BENIGN_VALUE.match(val):
            continue
        out.append(Finding(relpath, lines.line_of(m.start()), "coluna_proibida",
                           f"{m.group(1)} com valor (len={len(val)})", f"{m.group(1)}={val}"))
    # 2) cabeçalho de tabela HTML
    th = re.compile(r"<th[^>]*>\s*" + alt + r"\s*</th>", re.IGNORECASE)
    for m in th.finditer(text):
        out.append(Finding(relpath, lines.line_of(m.start()), "coluna_proibida",
                           f"{m.group(1)} como coluna de tabela HTML", m.group(0)))
    # 3) cabeçalho de tabela delimitada (CSV/TSV/Markdown/polars) e 4) tabela de texto do pandas
    phys = text.split("\n")
    for i, line in enumerate(phys):
        m = col_rx.search(line)
        if not m:
            continue
        if "[" in line[: m.start()] and "]" in line[m.end():]:
            continue  # lista JSON de rótulos (ex.: eixo de gráfico) — só menção
        nxt = _next_data_line(phys, i)
        if nxt is None:
            continue
        seps = [s for s in TABLE_SEPARATORS if line.count(s) >= 1]
        header_fields = _clean(_fields(line, seps))
        is_header = len(header_fields) >= 2 and len(_clean(_fields(nxt, seps))) >= 2 and any(f.strip(" \"'`").split(".")[-1].lower() == m.group(1).lower() for f in header_fields)
        if is_header and any(nxt.count(s) >= max(1, line.count(s) - 1) for s in seps):
            out.append(Finding(relpath, i + 1, "coluna_proibida",
                               f"{m.group(1)} como coluna de tabela com dados", line.strip()[:200]))
        elif not seps and INDEX_ROW.match(nxt):
            out.append(Finding(relpath, i + 1, "coluna_proibida",
                               f"{m.group(1)} como coluna de tabela de texto", line.strip()[:200]))
    return out


def _clean(fields: list[str]) -> list[str]:
    out = []
    for f in fields:
        f = f.replace("\\n", "").strip().strip("\"'` ").strip()
        if f:
            out.append(f)
    return out


def _fields(line: str, seps: list[str]) -> list[str]:
    parts = [line]
    for s in seps:
        parts = [p for chunk in parts for p in chunk.split(s)]
    return parts


def _next_data_line(phys: list[str], i: int) -> str | None:
    for j in range(i + 1, min(i + 4, len(phys))):
        cand = phys[j]
        if not cand.strip() or MD_RULE_LINE.match(cand) or set(cand.strip()) <= set("─━┄┈═╌-+|│┆:╞╡╪ \"\\n,"):
            continue
        return cand
    return None


def is_excluded(relpath: str, cfg: Config) -> bool:
    return any(fnmatch.fnmatch(relpath, g) or fnmatch.fnmatch("/" + relpath, "*/" + g.lstrip("*/")) for g in cfg.exclude)


def iter_files(paths: list[Path]):
    for p in paths:
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*")) if f.is_file())
        elif p.is_file():
            yield p


def scan_paths(paths: list[Path], cfg: Config, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for f in iter_files(paths):
        try:
            rel = f.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f.as_posix()
        if is_excluded(rel, cfg) or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        data = f.read_bytes()
        if b"\x00" in data[:8192]:
            continue  # binário
        text = data.decode("utf-8", errors="replace")
        for fd in scan_text(text, rel, cfg):
            if not any(a.matches(fd) for a in cfg.allow):
                findings.append(fd)
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", default=["lab"], help="arquivos ou pastas (padrão: lab)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="TOML com allowlist (padrão: scripts/pii_allowlist.toml)")
    ap.add_argument("--no-config", action="store_true", help="ignora o arquivo de configuração")
    ap.add_argument("--root", default=".", help="raiz para caminhos relativos (padrão: .)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(None if args.no_config else Path(args.config))
    except (FileNotFoundError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"pii_check: erro de configuração: {e}", file=sys.stderr)
        return 2
    paths = [Path(p) for p in args.paths]
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(f"pii_check: aviso: {p} não existe (ignorado)", file=sys.stderr)
    findings = scan_paths([p for p in paths if p.exists()], cfg, Path(args.root))
    for fd in findings:
        print(fd)
    if findings:
        print(f"\npii_check: {len(findings)} achado(s). Remova o dado ou, se for falso positivo, "
              f"registre em {args.config} ([[allow]] com path, rules e sha256).", file=sys.stderr)
        return 1
    print("pii_check: nenhum dado pessoal encontrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
