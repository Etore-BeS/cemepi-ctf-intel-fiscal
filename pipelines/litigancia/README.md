# TJSP Fiscal Execution Dataset (CEMEPI)

A large-scale dataset of fiscal execution proceedings from the São Paulo State Court of Justice (TJSP), covering decisions, parties, case metadata, and financial records. Collected and curated for jurimetrics research under the CEMEPI project.

## Dataset at a glance

| Attribute | Value |
|---|---|
| Source | TJSP — Tribunal de Justiça do Estado de São Paulo |
| Scope | Execuções Fiscais (all fiscal execution classes) |
| Decisions (`processos_delta`) | ~6.1 M rows / ~5.8 M unique processes |
| Face records (`face_processos_clean_delta`) | ~1.5 M rows |
| Temporal coverage | 2000–2025 (scraping ongoing) |
| Geographic coverage | Estado de São Paulo — all *comarcas* |
| Format | Delta Lake (Parquet) |
| License | Code: MIT · Data: CC BY 4.0 (see [LICENSE](LICENSE)) |

## How to cite

> [Citation will be added after Zenodo DOI is issued]

BibTeX will be available in [CITATION.cff](CITATION.cff).

## Data access

The full dataset is deposited at HuggingFace (primary) with a Zenodo DOI:

- **HuggingFace:** [link TBD]
- **Zenodo DOI:** [link TBD]

To load from HuggingFace using Polars:

```python
# bash / fish: uv add polars deltalake
import polars as pl
# df = pl.read_delta("hf://datasets/<org>/tjsp-fiscal-execution/processos_delta")
```

## Repository structure

```
pipelines/litigancia/
├── notebooks/
│   ├── data_paper/          ← reproducible artefacts for the data paper manuscript
│   ├── articles/            ← one subdir per output (journal article, conference talk, poster)
│   │   └── eped-2026-analise-execucoes-fiscais/  ← XV EPED 2026 (comunicação oral)
│   └── playground/          ← exploratory notebooks (not for publication)
├── docs/
│   ├── data_dictionary.md   ← field-level codebook for all silver tables
│   ├── schema/              ← per-table schema docs
│   ├── normalization/       ← manual review CSV workflow
│   └── SOURCE_OF_TRUTH.md  ← operational pipeline runbook (canonical)
├── src/                     ← Python package (scrapers, config, utils)
│   └── config/scripts.py    ← canonical script paths
└── scripts/                 ← pipeline orchestration scripts
```

## Data documentation

- [Field-level data dictionary](docs/data_dictionary.md)
- [Schema: processos](docs/schema/processos.md)
- [Schema: face_processos](docs/schema/face_processos.md)
- [Schema: movimentacoes](docs/schema/movimentacoes.md)

## Pipeline overview

Raw court data is scraped via [juscraper](https://github.com/Etore-BeS/juscraper), landed in **bronze** Delta tables (metadata + content hash), and transformed into **silver** analytical tables using DuckDB and PyArrow. See [docs/SOURCE_OF_TRUTH.md](docs/SOURCE_OF_TRUTH.md) for full operational details.

```
juscraper → COLLECT_ROOT (JSON/SQLite)
          → bronze coletas_delta (hash + path)
          → silver processos_delta (~6.1M decisions)

FACE scraper → bronze face_processos_delta
             → silver face_processos_clean_delta (~1.5M records)
```

## Setup (for pipeline operators)

```bash
# bash
uv sync
cp .env.example .env
# .env lives at monorepo root (cemepi-ctf-intel-fiscal/), not under pipelines/litigancia/
# Edit .env: set LAKE_ROOT and COLLECT_ROOT to your volume path
```

```fish
# fish
uv sync
cp .env.example .env
# .env lives at monorepo root (cemepi-ctf-intel-fiscal/), not under pipelines/litigancia/
# Edit .env: set LAKE_ROOT and COLLECT_ROOT to your volume path
```

Install Playwright (required for juscraper):

```bash
# bash / fish
uv run playwright install
```

Use the `.venv` as your Jupyter kernel so `config` and `scrapers` imports resolve.

Optional: copy `config/oxylabs.json.example` to `config/oxylabs.json` for Comunica proxy scraping.

## Pipeline run order

**Full runbook, flags, overnight jobs, and troubleshooting:** [docs/SOURCE_OF_TRUTH.md](docs/SOURCE_OF_TRUTH.md) (§6 Orchestration & CLI).

Quick sequence:

1. **Recoleta** — see [docs/recoleta/](docs/recoleta/) for round 1 vs round 2 configs:
   - Round 2 (ICMS assunto tree, default): `uv run python scripts/processos/scraping/run_recollect_juscraper.py --config scripts/data/recollect_configs/round2_assunto_icms.json`
   - Round 1 (PGE pesquisa livre): `uv run python scripts/processos/scraping/run_recollect_juscraper.py --config scripts/data/recollect_configs/round1_pesquisa_livre_pge.json`
2. **Bronze → silver (processos)** — `uv run python scripts/processos/run_new_data_to_silver.py --skip-sqlite`
3. **Audit counts** — `uv run python scripts/processos/audit_process_counts.py`
4. **Face** — scrape then `create_silver_face_layer.py` (see [SOURCE_OF_TRUTH §6](docs/SOURCE_OF_TRUTH.md#6-orchestration--cli))
5. **Movimentações** — `uv run python scripts/movimentacoes/transforming/create_silver_movimentacoes.py`

Overnight wrappers: `./scripts/ops/start_overnight_*.sh` (ingest, face, face silver, movimentações, compact, optimize).

**Normalization dictionaries:** `uv run python scripts/movimentacoes/export_normalization_review.py` — see [docs/normalization/](docs/normalization/) and [SOURCE_OF_TRUTH §6](docs/SOURCE_OF_TRUTH.md).

## Optional collectors

- Juscraper CLI: `uv run python -m scrapers.juscraper_collect`
- Comunica (alternate JSON shape): `uv run python -m scrapers.comunica_full`

## Paths

Centralized in `src/config/paths.py` and `src/config/scripts.py`, driven by `.env`:

| Variable | Purpose |
|----------|---------|
| `LAKE_ROOT` | Delta bronze/silver tables, `aggregated_database.db` |
| `COLLECT_ROOT` | `coleta_fazenda_*` / `coleta_assunto_*` collector folders (JSON rglob for bronze) |

## Aborting a long silver run safely

See [docs/SOURCE_OF_TRUTH.md](docs/SOURCE_OF_TRUTH.md) §6 (resume) and §9 (known challenges). Delta Lake is versioned — stop with `Ctrl+C` once; checkpoint updates only after a successful publish.

```bash
# bash / fish
uv run python scripts/processos/run_new_data_to_silver.py --dry-run --skip-sqlite
uv run python scripts/processos/transforming/create_silver_layer.py --show-pending
```

Optional global dedupe overnight: `./scripts/ops/start_overnight_compact.sh --buckets 128 --compact`

## Data paper

Reproducible notebooks for the Scientific Data manuscript:

| Notebook | Manuscript section | Output |
|---|---|---|
| [01_dataset_overview.ipynb](notebooks/data_paper/01_dataset_overview.ipynb) | Background & Summary · Data Records | Tables + temporal figures |
| [02_quality_validation.ipynb](notebooks/data_paper/02_quality_validation.ipynb) | Technical Validation | Null/duplicate/join checks |
| [03_pipeline_figures.ipynb](notebooks/data_paper/03_pipeline_figures.ipynb) | Methods | Pipeline flowchart |

Manuscript (LaTeX): [docs/manuscript/data_descriptor.tex](docs/manuscript/data_descriptor.tex) — build with `make -C docs/manuscript` (requires [TeX](docs/manuscript/README.md#build-pdf-locally), not `uv`)

Generated artefacts are written to `notebooks/data_paper/figures/` when notebooks are executed.

```bash
# bash / fish — dev deps include nbconvert + vl-convert-python (Altair PNG export)
uv sync --group dev
uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 \
  notebooks/data_paper/01_dataset_overview.ipynb
```

## Notebooks

- `notebooks/data_paper/` — reproducible artefacts for the data paper manuscript
- `notebooks/articles/eped-2026-analise-execucoes-fiscais/` — [XV EPED 2026](notebooks/articles/eped-2026-analise-execucoes-fiscais/) comunicação oral (FESP)
- `notebooks/playground/eda_gilson.ipynb` — exploratory analysis
- `notebooks/playground/extract_base_sent_victor.ipynb` — extraction playground
