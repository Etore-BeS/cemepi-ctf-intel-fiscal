# Data Dictionary — TJSP Fiscal Execution Dataset

**Last updated:** 2026-06-25
**Scope:** Silver-layer analytical tables. Bronze tables are intermediate and not intended for direct analysis.

All tables are stored as Delta Lake (Parquet) under `LAKE_ROOT/silver_layer/`.

---

## Tables

| Table | Delta path | Rows (approx.) | Description |
|---|---|---|---|
| `processos_delta` | `silver_layer/processos_delta` | ~6.1 M | One row per court *decisão* (decision event). Core table. |
| `face_processos_clean_delta` | `silver_layer/face_processos_clean_delta` | ~1.5 M | Party, financial, and sentence metadata scraped from FACE/TJSP. |
| `movimentacoes_delta` | `silver_layer/movimentacoes_delta` | TBD | Case movement events (tramitação). |

---

## `processos_delta`

Primary analytical table. One row per **decision event** (`decisao`) within a fiscal execution proceeding.

See full schema: [schema/processos.md](schema/processos.md)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id_processo` | `Int64` | No | Internal integer identifier for the process. Not the official court number. |
| `cd_processo` | `Utf8` | No | Official TJSP process code (CNJ format or legacy format). |
| `classe` | `Utf8` | No | Process class. All rows are `"Execução Fiscal"`. |
| `assunto` | `Utf8` | No | Subject of the fiscal execution (e.g., `"IPTU/ Imposto Predial e Territorial Urbano"`, `"Dívida Ativa"`). |
| `comarca` | `Utf8` | No | Municipality of the court (*comarca*). |
| `foro` | `Utf8` | No | Court district name (e.g., `"Foro de Guarulhos"`). Typically one per *comarca*. |
| `vara` | `Utf8` | No | Specific court unit within the *foro* (e.g., `"Vara da Fazenda Pública"`, `"SAF - Serviço de Anexo Fiscal"`). |
| `magistrado` | `Utf8` | Yes | Judge name as listed in the decision. ~0.05% null. |
| `data_disponibilizacao` | `Utf8` | No | Publication date of the decision. Stored as text in `DD/MM/YYYY` format. |
| `decisao` | `Utf8` | No | Full text of the decision. |
| `tipo_ato` | `Utf8` | Yes | Act type extracted from `decisao` (e.g., `"SENTENÇA P"`, `"CONCLUSÃO E"`). Derived field. |
| `source_bronze_path` | `Utf8` | Yes | Path to the source bronze file that originated this row. Used for incremental updates; nullable on legacy rows. |

### Notes

- **`id_processo` vs `cd_processo`:** `id_processo` is an internal integer used for deduplication; `cd_processo` is the human-readable court identifier.
- **`data_disponibilizacao`:** Stored as `DD/MM/YYYY` text, not as a date type. Parse with `pl.col("data_disponibilizacao").str.to_date("%d/%m/%Y")` before date operations.
- **`tipo_ato`:** Derived by extracting the first line of `decisao`. Contains normalisation noise (e.g., `"C O N C L U S Ã O E"` and `"CONCLUSÃO E"` refer to the same type).
- **Deduplication:** The pipeline deduplicates on `(id_processo, decisao)` per source file. Global deduplication (`compact_silver_processos.py`) is recommended after bulk ingests.

---

## `face_processos_clean_delta`

Enriched records scraped from the FACE/TJSP system. One row per process with party, financial, and sentence metadata. Links to `processos_delta` via `cd_processo`.

See full schema: [schema/face_processos.md](schema/face_processos.md)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `cd_processo` | `Utf8` | No | Official TJSP process code. Join key to `processos_delta`. |
| `distribuicao_data` | `Date` | Yes | Distribution date (when the process was assigned to a court unit). |
| `valor_original` | `Float64` | Yes | Original debt value in BRL at time of filing. |
| `valor_corrigido_atual` | `Float64` | Yes | Current corrected debt value in BRL (monetary correction applied). |
| `tempo_tramitacao_meses` | `Float64` | Yes | Time elapsed from distribution to sentence, in months. |
| `tipo_sentença` | `Utf8` | Yes | Sentence type (e.g., `"Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação"`). Stored as a JSON-serialised list. |
| `partes` | `Utf8` | Yes | Parties to the case (creditor/debtor). Stored as JSON. Heavy column — drop in memory-constrained analyses. |
| `movimentacoes_raw` | `Utf8` | Yes | Raw movement events from FACE. Stored as JSON. Heavy column. |

### Notes

- **`valor_corrigido_atual`:** Contains a small number of negative values (data artefacts from TJSP). Apply `filter(pl.col("valor_corrigido_atual") > 0)` for financial analyses.
- **`tipo_sentença`:** Despite the column name, values are JSON arrays (e.g., `["Extinta a Execução/Cumprimento da Sentença pela Satisfação da Obrigação"]`). Parse with `pl.col("tipo_sentença").str.json_decode()` if needed.
- **Sanity filter used in notebooks:** `distribuicao_data.dt.year() >= 2000` and `valor_corrigido_atual <= 1_000_000_000` (R$ 1B cap).
- **Heavy columns:** `partes` and `movimentacoes_raw` contain large JSON blobs. Select them explicitly only when needed.

---

## `movimentacoes_delta`

Movement events for each process, extracted from the FACE system. One row per movement event.

See full schema: [schema/movimentacoes.md](schema/movimentacoes.md)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `cd_processo` | `Utf8` | No | Official TJSP process code. Join key. |
| `data_movimentacao` | `Date` | Yes | Date of the movement event. |
| `descricao` | `Utf8` | Yes | Description of the movement (e.g., `"Conclusão para Sentença"`, `"Citação"`). |

> Note: Schema details for `movimentacoes_delta` are preliminary. Run `pl.scan_delta(SILVER_MOVIMENTACOES).schema` to inspect the current schema.

---

## Key identifiers

| Identifier | Tables | Notes |
|---|---|---|
| `cd_processo` | `processos_delta`, `face_processos_clean_delta`, `movimentacoes_delta` | Primary join key across tables. |
| `id_processo` | `processos_delta` only | Internal integer; do not use as join key with FACE tables. |

---

## Coverage statistics (measured 2026-06-04)

| Metric | Value |
|---|---|
| Total decisions (`processos_delta`) | 6,100,561 |
| Unique processes (`id_processo`) | 5,812,126 |
| Unique process codes (`cd_processo`) | 5,812,127 |
| Mean decisions per process | 1.05 |
| Null `magistrado` | 2,996 (~0.05%) |
| Null `foro` / `vara` | 20 each |
| Face records | ~1,493,581 |
| Mean `tempo_tramitacao_meses` (face) | 61.2 months |
| Median `tempo_tramitacao_meses` (face) | 41.5 months |
