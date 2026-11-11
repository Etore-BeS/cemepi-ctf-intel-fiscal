# AVD partition covariates (lab prep)

Prep for the declared-versus-assessed VAT (ICMS) paper. This folder does **not**
edit `projects/macro/manuscript/`. It does **not** choose a stock partition
method (Task 1). It does **not** fit the final recoverability logits (Task 3).

## What this run does

1. Writes an inventory of parcelamento and garantia from PR #9 helpers
   (`lab/eda/_impairment.py`, `2026-11-04_instalments_protest.qmd`,
   `2026-11-04_impairment_variables.qmd`).
2. Documents RFB porte codes and builds `porte_pequena` (codes 01 and 03).
3. Writes a methodology variable table from the code that already exists.
4. Records how `paid_flag` and municipality are treated in the current code.
5. On the Mac extract: `DESCRIBE`-style schema (names and types only), instalment
   flags reused from IMP-23, and aggregate counts (counts 1–4 blank).

## Command (Mac with the extract)

bash:

```bash
export CEMEPI_DUCKDB=/path/to/cemepi.duckdb
# optional if the enrichment is not next to the duckdb tree:
# export CNPJ_PIPELINE_WORKDIR=/path/to/cnpj_enrich
uv run python projects/macro/notebooks/articles/avd_partition_covariates/run.py
```

fish:

```fish
set -x CEMEPI_DUCKDB /path/to/cemepi.duckdb
# optional:
# set -x CNPJ_PIPELINE_WORKDIR /path/to/cnpj_enrich
uv run python projects/macro/notebooks/articles/avd_partition_covariates/run.py
```

Docs and inventory only (no DuckDB):

bash / fish:

```bash
uv run python projects/macro/notebooks/articles/avd_partition_covariates/run.py --docs-only
```

The DuckDB connection opens with `read_only=True`. The script never selects
`NOME_DEVEDOR` or `CPF_DEVEDOR`. Outputs never include CNPJ or CDA arrays.

## Outputs

| File | Content |
|---|---|
| `tables/tab_inventory_parcelamento_garantia.csv` | Parcelamento present; garantia absent |
| `tables/tab_porte_codes.csv` | RFB porte codes and `porte_pequena` |
| `tables/tab_variables_methodology.csv` / `.tex` | Name, definition, measurement, source |
| `code_facts_outcome_municipality.md` | How the current code treats `paid_flag` and municipality |
| `q1_q8_assumptions.txt` | IMP-23 Q1–Q8 text from `PgeParams` |
| `tables/tab_schema_describe.csv` | Names and types from the Mac run |
| `tables/tab_instalment_porte_counts.csv` | Aggregate flags by origin (Mac run) |
| `output/extended_frame_public.parquet` | Public columns only (local, Mac run) |

## Findings encoded from the repo

- **Parcelamento:** table `SOLICITACAOPARCELAMENTO` with request date, status,
  break date, instalment count, and debt id. Flags reuse
  `has_request`, `instalment_open_ids`, and break plausibility from `_impairment`.
- **Garantia:** no guarantee table. IMP-23 sets `guaranteed = False` and prints
  `GUARANTEE_WARNING`. The guaranteed class row is empty by construction.
- **Porte:** RFB codes 00 / 01 / 03 / 05. There is no porte level named
  “individual”. `porte_pequena` = 01 or 03.

## Task 3

See `task3/README.md`. Wait for Étore’s partition method before fitting.
