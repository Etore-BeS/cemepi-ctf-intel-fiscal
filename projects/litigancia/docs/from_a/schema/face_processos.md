# Schema — `face_processos_clean_delta`

**Delta path:** `$LAKE_ROOT/silver_layer/face_processos_clean_delta`
**Last updated:** 2026-06-25

---

## Overview

Enriched records scraped from the **FACE/TJSP** system (Ferramenta de Apoio às Certidões e Execuções). One row per process with financial, party, and sentence metadata.

Links to `processos_delta` via `cd_processo`.

**Scale (2026-06-04):**
- ~1,493,581 rows
- Covers processes from 2000 onwards (distribution date)

---

## Columns

| # | Column | Delta type | Polars type | Nullable | Description |
|---|---|---|---|---|---|
| 1 | `cd_processo` | `STRING` | `Utf8` | No | Official TJSP process code. Join key. |
| 2 | `distribuicao_data` | `DATE` | `Date` | Yes | Date the process was distributed (assigned to a court unit). |
| 3 | `valor_original` | `DOUBLE` | `Float64` | Yes | Original debt value in BRL at time of filing. |
| 4 | `valor_corrigido_atual` | `DOUBLE` | `Float64` | Yes | Current corrected debt value in BRL. |
| 5 | `tempo_tramitacao_meses` | `DOUBLE` | `Float64` | Yes | Time from distribution to sentence, in months. |
| 6 | `tipo_sentença` | `STRING` | `Utf8` | Yes | Sentence type(s). Stored as JSON array. |
| 7 | `partes` | `STRING` | `Utf8` | Yes | Parties (creditor/debtor). JSON blob. Heavy — select explicitly. |
| 8 | `movimentacoes_raw` | `STRING` | `Utf8` | Yes | Raw movement events. JSON blob. Heavy — select explicitly. |

> The full current schema may contain additional columns. Inspect with `pl.scan_delta(str(SILVER_FACE_CLEAN)).schema`.

---

## Column details

### `cd_processo`

Official TJSP process number. Use to join with `processos_delta`:

```python
processos = pl.scan_delta(str(SILVER_PROCESSOS))
face = pl.scan_delta(str(SILVER_FACE_CLEAN))

joined = processos.join(
    face.select(["cd_processo", "valor_corrigido_atual", "tipo_sentença"]),
    on="cd_processo",
    how="left"
)
```

### `distribuicao_data`

Date the process was distributed/assigned to a court unit. Stored as a native `Date` column (unlike `data_disponibilizacao` in `processos_delta`).

Coverage: ~2000–2025. Filter for sanity:
```python
lf.filter(pl.col("distribuicao_data").dt.year() >= 2000)
```

### `valor_corrigido_atual`

Current corrected debt value in BRL, with monetary correction (IPCA or SELIC) applied to the original debt. Descriptive statistics:

| Statistic | Value |
|---|---|
| Count (non-null) | ~1,493,581 |
| Mean | R$ 61.22 thousand |
| Std | R$ 59.15 thousand |
| Min | R$ -151.81 thousand |
| 25th percentile | R$ 18.07 thousand |
| Median | R$ 41.46 thousand |
| 75th percentile | R$ 90.18 thousand |
| Max | R$ 312.52 thousand |

Note: Negative values are data artefacts from TJSP. Apply `filter(pl.col("valor_corrigido_atual") > 0)` for financial analysis.

Common sanity cap used in notebooks:
```python
TETO_VALOR = 1_000_000_000  # R$ 1 billion
lf.filter(
    pl.col("valor_corrigido_atual").is_null()
    | (pl.col("valor_corrigido_atual") <= TETO_VALOR)
)
```

### `tempo_tramitacao_meses`

Time elapsed from `distribuicao_data` to the sentence date, in months. Descriptive statistics:

| Statistic | Value |
|---|---|
| Mean | 61.2 months (~5 years) |
| Std | 59.1 months |
| Min | -151.8 months (data artefact) |
| Median | 41.5 months (~3.5 years) |
| 75th percentile | 90.2 months |
| Max | 312.5 months (~26 years) |

Negative values indicate data quality issues in the source system.

### `tipo_sentença`

Sentence type(s) as classified by TJSP. Stored as a JSON-serialised list:

```python
# Parse the JSON list
lf.with_columns(
    pl.col("tipo_sentença").str.json_decode()
)
```

Top values:

| Sentence type | Count |
|---|---|
| Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação | 673,123 |
| Extinto o Processo sem Resolução do Mérito por Desistência | 104,541 |
| Extinto o Processo sem Resolução do Mérito por Ausência das Condições da Ação | 90,239 |
| Declarada Decadência ou Prescrição | 87,831 |
| Extinta a Punibilidade por Pagamento Integral do Débito | 64,735 |

### `partes`

Party information (plaintiff/defendant) scraped from FACE. Stored as a JSON blob. Structure:

```json
{
  "polo_ativo": ["Prefeitura de São Paulo"],
  "polo_passivo": ["Nome do Devedor"]
}
```

This is a heavy column. Only select it when party-level analysis is required.

### `movimentacoes_raw`

Raw movement events from FACE. Stored as JSON. Contains the same data as `movimentacoes_delta` but in unparsed form. Use `movimentacoes_delta` for structured analysis.

---

## Recommended base filter

```python
import polars as pl
from config.paths import SILVER_FACE_CLEAN

ANO_MIN = 2000
TETO_VALOR = 1_000_000_000

lf_face = (
    pl.scan_delta(str(SILVER_FACE_CLEAN))
    .filter(pl.col("distribuicao_data").dt.year() >= ANO_MIN)
    .filter(
        pl.col("valor_corrigido_atual").is_null()
        | (pl.col("valor_corrigido_atual") <= TETO_VALOR)
    )
)
```

---

## Data provenance

Source: FACE/TJSP system via `src/scrapers/face_tjsp.py`.

Bronze → silver pipeline: [`scripts/face/transforming/create_silver_face_layer.py`](../../scripts/face/transforming/create_silver_face_layer.py) (`config.scripts.CREATE_SILVER_FACE_LAYER`)
