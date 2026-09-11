# Round 2 — Assunto jurídico ICMS

**Execution:** 2  
**Mode:** `assunto_tree`  
**Config:** [`scripts/data/recollect_configs/round2_assunto_icms.json`](../../scripts/data/recollect_configs/round2_assunto_icms.json)

## Purpose

Enrich the processos lake with TJSP fiscal executions filtered by **ICMS legal subjects** in the SAJ subject tree, independent of the round 1 PGE free-text search.

## Search criteria

- **Classe:** Execução Fiscal (`classeTreeSelection` → `8721`)
- **Assuntos jurídicos** (one unified query per month):

| SAJ id | Label |
|--------|-------|
| 5946 | ICMS/ Imposto sobre Circulação de Mercadorias |
| 7061 | Nao Cumulatividade |
| 5947 | ICMS/Importação |
| 10531 | ICMS / Incidência Sobre o Ativo Fixo |

- **Pesquisa livre:** empty
- **Varas:** all (no filter)

## Time range

Monthly slices from **01/01/2016** through **30/06/2026** (~126 months), generated at runtime from `date_span` in the config.

Example folder:

```text
COLLECT_ROOT/coleta_assunto_icms_01_01_2016_31_01_2016/
  collection_manifest.json
  20260707/                    # scrape batch date
    assunto_tree/assunto_tree.json
    process_grouped_all_assuntos.json
    process_grouped_all_assuntos.db
```

## Full pipeline (recommended)

Per month: CJPG → processos silver → FACE scrape → face silver.

```bash
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json
```

Resume FACE only for months already in processos silver:

```bash
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json \
  --face-only
```

## Operational notes

- State: `scripts/data/recollect_state_round2_icms.json` (keys include `config_id`).
- Volume threshold: **500** processos per month (configurable).
- FACE: `face` block in config (`workers`, `delay_sec`); loops until all cds scraped per month.
- `source_bronze_path` on processos silver points at `coleta_assunto_icms_*` JSON paths.

## Commands (legacy / partial)

```bash
# Scrape only
uv run python scripts/processos/scraping/run_recollect_juscraper.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json

# Batch processos ingest (without FACE)
uv run python scripts/processos/run_new_data_to_silver.py --skip-sqlite
```
