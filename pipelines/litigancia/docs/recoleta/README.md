# Recoleta campaigns (juscraper)

The repo supports multiple **recoleta execution rounds**, each driven by a JSON config under [`scripts/data/recollect_configs/`](../../scripts/data/recollect_configs/).

## Round 1 vs round 2

| | Round 1 | Round 2 |
|---|---------|---------|
| **Config** | `round1_pesquisa_livre_pge.json` | `round2_assunto_icms.json` |
| **Execution** | 1 | 2 |
| **Mode** | `pesquisa_livre` | `assunto_tree` |
| **Filter** | Parte/agente via `pesquisaLivre` | Assunto jurídico via `assuntoTreeSelection` |
| **juscraper call** | `cjpg(pesquisa=..., classes=...)` | `cjpg(pesquisa="", assuntos=[...], classes=...)` |
| **Folder prefix** | `coleta_fazenda_*` | `coleta_assunto_icms_*` |
| **State file** | `recollect_state.json` | `recollect_state_round2_icms.json` |
| **Date queue** | `recollect_dates.json` (explicit ranges) | `date_span` expanded monthly at runtime |

Round 1 is unchanged in behaviour; round 2 **enriches** the lake with processes filtered by ICMS legal subjects without replacing round 1 data.

## Recommended: full pipeline (processos + FACE)

One command per campaign, **one month at a time** — all four stages before advancing:

```bash
# bash / fish
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json

# Dry-run: list months and planned phases
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json \
  --dry-run
```

### Monthly flow

```mermaid
flowchart LR
    cjpg[CJPG scrape] --> procIngest[Processos bronze+silver]
    procIngest --> faceScrape[FACE scrape por source_path]
    faceScrape --> faceSilver[Silver face]
```

| Step | Script / table |
|------|----------------|
| 1. Coleta | `juscraper_collect` → `coleta_*` JSON |
| 2. Processos | `run_one_json_to_silver.py` → `processos_delta` |
| 3. FACE scrape | `scrape_face_to_bronze.py --source-path <json>` → `face_processos_delta` |
| 4. FACE silver | `create_silver_face_layer.py` → `face_processos_clean_delta` |

FACE scrape loops until **pending=0** for that month's `source_bronze_path` (~1 HTTP request per process; expect tens of minutes per month).

### CLI flags

| Flag | Effect |
|------|--------|
| `--scrape-only` | CJPG only |
| `--ingest-only` | Processos bronze→silver only |
| `--face-only` | FACE scrape + silver for months with processos ingest done |
| `--skip-face` | Processos pipeline only (`full_pipeline` = processos ingest) |
| `--compact` | Delta `compact()` after silver steps |

### Failure / resume behaviour

| Failure | Next run |
|---------|----------|
| CJPG scrape fails | Re-scrape month |
| Processos ingest fails | Retry ingest only (no re-scrape) |
| FACE scrape fails | Retry FACE only (loop until pending=0) |
| FACE silver fails | Retry FACE silver only |

### State file (per campaign)

| Key | Meaning |
|-----|---------|
| `processed_ranges` | CJPG scrape accepted |
| `pipeline_completed_ranges` | Processos bronze+silver OK |
| `face_scrape_completed_ranges` | All FACE cds for month in bronze |
| `face_silver_completed_ranges` | FACE silver step ran successfully |
| `full_pipeline_completed_ranges` | Month 100% done |

**Legacy months** (e.g. jan/2016 with `pipeline_completed` but no face) resume at `face_scrape` on the next run.

### Config `face` block

```json
"face": {
  "enabled": true,
  "workers": 2,
  "delay_sec": 1.5,
  "fetch_batch": 500,
  "delta_batch": 200,
  "request_timeout": 15,
  "max_request_retries": 2
}
```

Set `"enabled": false` or pass `--skip-face` to skip FACE.

### FACE throughput tuning

Throughput is dominated by `workers × (delay_sec + HTTP latency)`. Current defaults (~1 proc/s) are conservative for residential IP.

| Profile | workers | delay_sec | ~rate | ~time for 5k proc |
|---------|---------|-----------|-------|-------------------|
| Safe (default) | 2 | 1.5 | ~1/s | ~80 min |
| Balanced | 4 | 0.5 | ~3/s | ~28 min |
| Fast | 6 | 0.3 | ~5/s | ~17 min (watch for timeouts) |

Override without editing JSON:

```bash
# bash / fish
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json \
  --face-only \
  --face-workers 4 \
  --face-delay-sec 0.5
```

Or edit `face` in the campaign config. If TJSP starts returning many timeouts, reduce workers or increase `delay_sec`.

## SAJ payload reference (round 2)

| Field | Example |
|-------|---------|
| `classeTreeSelection.values` | `8721` (Execução Fiscal) |
| `assuntoTreeSelection.values` | `5946,7061,5947,10531` |
| `dadosConsulta.pesquisaLivre` | *(empty)* |
| `dadosConsulta.dtInicio` / `dtFim` | monthly slice |

Recoleta uses `assunto_tree_ids_ref` as raw comma-separated SAJ ids (not URL-encoded).

## Scrape-only (legacy driver)

```bash
uv run python scripts/processos/scraping/run_recollect_juscraper.py \
  --config scripts/data/recollect_configs/round2_assunto_icms.json
```

## Run round 1 (original PGE / pesquisa livre)

```bash
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/round1_pesquisa_livre_pge.json
```

## Batch ingest (optional)

If you scraped without the pipeline script:

```bash
uv run python scripts/processos/run_new_data_to_silver.py --skip-sqlite
```

## Add round 3+

1. Copy a config template from [`scripts/data/recollect_configs/README.md`](../../scripts/data/recollect_configs/README.md).
2. Set unique `id`, `folder_prefix`, `state_file`, `log_file`.
3. Document the campaign here (e.g. `round3_*.md`).
4. Run with `--config`.
