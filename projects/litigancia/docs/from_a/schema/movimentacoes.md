# Schema — `movimentacoes_delta`

**Delta path:** `$LAKE_ROOT/silver_layer/movimentacoes_delta`
**Last updated:** 2026-06-25

---

## Overview

Movement events for fiscal execution proceedings, parsed from the FACE/TJSP system. One row per movement event (*movimentação*) per process.

Links to `processos_delta` and `face_processos_clean_delta` via `cd_processo`.

> **Note:** This schema is preliminary. The pipeline for this table (`create_silver_movimentacoes.py`) is newer and has not been fully exercised. Inspect the live schema with:
>
> ```python
> import polars as pl
> from config.paths import SILVER_MOVIMENTACOES
> print(pl.scan_delta(str(SILVER_MOVIMENTACOES)).schema)
> ```

---

## Columns (preliminary)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `cd_processo` | `Utf8` | No | Official TJSP process code. Join key. |
| `data_movimentacao` | `Date` | Yes | Date of the movement event. |
| `descricao` | `Utf8` | Yes | Description of the movement (e.g., `"Conclusão para Sentença"`, `"Citação"`, `"Despacho"`). |

---

## Data provenance

Source: FACE/TJSP system (same scraper as `face_processos_clean_delta`). Movement events are parsed from `movimentacoes_raw` in `face_processos_clean_delta`.

Silver pipeline: [`scripts/movimentacoes/transforming/create_silver_movimentacoes.py`](../../scripts/movimentacoes/transforming/create_silver_movimentacoes.py) (`config.scripts.CREATE_SILVER_MOVIMENTACOES`)

---

## Usage example

```python
import polars as pl
from config.paths import SILVER_MOVIMENTACOES

lf_mov = pl.scan_delta(str(SILVER_MOVIMENTACOES))

# Top movement types
top_mov = (
    lf_mov
    .group_by("descricao")
    .agg(pl.len().alias("n"))
    .sort("n", descending=True)
    .head(20)
    .collect()
)
print(top_mov)
```
