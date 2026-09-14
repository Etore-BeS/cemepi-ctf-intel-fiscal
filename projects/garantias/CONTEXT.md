# Garantias e risco protelatório

## Pergunta de pesquisa

Sem tipo de garantia no dump, o que dá para inferir sobre risco protelatório via proxies de cobrança (protesto, parcelamento, ajuizamento)?

## Escopo

Dump **não contém** tabela de garantia (carta-fiança, seguro, imóvel, depósito). Front limitado a proxies até nova fonte. Veredito operacional: **go-with-gaps**.

## Plano / premissa

- Plano D: `docs/planos/D_garantias.md` (cópia em `docs/`).
- Premissa dump: `docs/planos/00_premissa_dump.md` / `docs/api-dump/`.

## Estado (2026-09-14)

- Lacuna confirmada (meta + 404 + OpenAPI).
- EDA proxy em `notebooks/playground/proxy_cobranca_eda_v0.ipynb`.
- Briefing interno: `.local/briefing_viabilidade_garantias.md` (gitignored).
- README público voltado à Profa. Lívia.

## Shared env

Mono root `.env` + kernel `ctf-research`. Prefer `shared/cemepi_api` and `config.paths` after `uv sync`.
