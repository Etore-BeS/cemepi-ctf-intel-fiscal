# Recollect campaign configs

Each JSON file describes one **recoleta campaign** (execution round). Drivers:

- [`run_recollect_pipeline.py`](../../processos/scraping/run_recollect_pipeline.py) — **recommended**: scrape + bronze + silver per month (`--config`)
- [`run_recollect_juscraper.py`](../../processos/scraping/run_recollect_juscraper.py) — scrape only

## Campaigns

| File | Execution | Mode | Folder prefix |
|------|-----------|------|---------------|
| `round1_pesquisa_livre_pge.json` | 1 | `pesquisa_livre` | `coleta_fazenda_*` |
| `round2_assunto_icms.json` | 2 | `assunto_tree` | `coleta_assunto_icms_*` |

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable campaign id (used in state keys) |
| `execution` | yes | Human-readable round number (1, 2, …) |
| `mode` | yes | `pesquisa_livre` or `assunto_tree` |
| `description` | yes | Short note for logs and docs |
| `folder_prefix` | yes | Output folder under `COLLECT_ROOT` |
| `tribunal` | yes | juscraper tribunal sigla (e.g. `tjsp`) |
| `classes` | yes | Classe jurídica labels for `classeTreeSelection` |
| `pesquisa_terms` | round 1 | Free-text search terms (`pesquisaLivre`) |
| `campaign_subdir` | round 1 | Subfolder under each month (e.g. `pge`) |
| `assuntos_juridicos` | round 2 | Labels for `assuntoTreeSelection` (exact SAJ text) |
| `assunto_tree_ids_ref` | optional | SAJ node ids for documentation only |
| `date_span` | round 2 | `{ start, end, granularity: "month" }` — expands to monthly ranges |
| `ranges_file` | round 1 | JSON under `scripts/data/` with explicit `ranges` list |
| `threshold` | yes | Min process count to accept a month |
| `max_workers` | yes | Parallel workers (pesquisa_livre only) |
| `max_retries` | yes | Retries per month before giving up |
| `state_file` | yes | Runtime state filename under `scripts/data/` |
| `log_file` | yes | Runtime log filename under `scripts/data/` |
| `face` | optional | FACE scrape settings: `enabled`, `workers`, `delay_sec`, `fetch_batch`, `delta_batch` |

### `face` block (full pipeline)

```json
"face": {
  "enabled": true,
  "workers": 2,
  "delay_sec": 1.5,
  "fetch_batch": 500,
  "delta_batch": 200
}
```

When `enabled` is true, [`run_recollect_pipeline.py`](../../processos/scraping/run_recollect_pipeline.py) runs FACE scrape + silver face after processos ingest for each month. Use `--skip-face` to disable at runtime.

## Adding round 3+

1. Copy `round2_assunto_icms.json` or `round1_pesquisa_livre_pge.json` as a template.
2. Set a new `id`, `execution`, `folder_prefix`, `state_file`, and `log_file`.
3. Document the campaign in [`docs/recoleta/`](../../../docs/recoleta/).
4. Run:

```bash
uv run python scripts/processos/scraping/run_recollect_pipeline.py \
  --config scripts/data/recollect_configs/your_campaign.json
```

Legacy `assuntos_dict` / `assuntos_keys` in `juscraper_collect.py` remain for round 1 reference; new campaigns should use config JSON only.
