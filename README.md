# CeMEPI CTF — Inteligência Fiscal (research monorepo)

Working copy for Contencioso Tributário Fiscal (CTF) research fronts.  
Historical archive (do **not** write): `habitual-tax-debtor-research`.

## Mission & map
- Mission: [`docs/mission.md`](docs/mission.md)
- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Spec: [`docs/superpowers/specs/2026-09-10-monorepo-mece-design.md`](docs/superpowers/specs/2026-09-10-monorepo-mece-design.md)
- Agent rules: [`AGENTS.md`](AGENTS.md) · Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md) · Glossary: [`CONTEXT.md`](CONTEXT.md)
- Grounded refs (SoT): [`docs/references/referencias_grounded.md`](docs/references/referencias_grounded.md)

## Toolchain
- Python env: `uv sync` (see `pyproject.toml` / `uv.lock`). Jupyter kernel name typically `ctf-research` if you register the venv that way.
- Optional local `data` symlink → your dump root (gitignored; do not commit HD paths).
- One `.env` at repo root — copy from [`.env.example`](.env.example) (`CEMEPI_API_TOKEN`, `LAKE_ROOT`, `DUMP_ROOT`, …).

## Layout

```text
shared/cemepi_api/          # dump HTTP client
pipelines/litigancia/       # lake scrape / transform / ops + SoT
projects/
  litigancia/               # research writing (notebooks, manuscript)
  monitoramento/            # front B
  macro/                    # front C
  garantias/                # front D
docs/                       # portfolio only
  mission.md, architecture.md
  api-dump/, planos/, references/
```

| Path | Role |
|---|---|
| `pipelines/litigancia` | Lake/ops (SoT, schema, scrapers) |
| `projects/litigancia` | Front A research/writing |
| `projects/monitoramento` | Front B |
| `projects/macro` | Front C |
| `projects/garantias` | Front D |
| `docs/api-dump` | Dump premise |
| `docs/planos` | CTF Drive plans A–D |
| `docs/references` | Cross-front grounded refs (SoT) |

## Data
- **Dump:** Inteligência Fiscal research extract (not realtime) — see `docs/api-dump/00_premissa_dump.md`. Paths via `DUMP_ROOT` in `.env`.
- **Lake:** TJSP Delta under `LAKE_ROOT` — not in git.

## Archive

Historical lake repo (read-only): see [docs/ARCHIVE_A.md](docs/ARCHIVE_A.md).

## Figures (GitHub preview)

Static PNGs (Plotly + Kaleido) are under `projects/*/output/figures/` and listed in:

- [Monitoramento gallery](projects/monitoramento/docs/figures_gallery.md)
- [Litigância gallery](projects/litigancia/docs/figures_gallery.md)

Open the notebooks for interactive Plotly; GitHub renders the PNG galleries above.
