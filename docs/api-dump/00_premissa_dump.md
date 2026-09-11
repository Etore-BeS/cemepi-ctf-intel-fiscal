# Dump premise — Inteligência Fiscal Data API

**Nature:** static research dump (not realtime, not PGE ops).

**Implication:** every paper/notebook freezes `extracao_ref = ANO_EXTRACAO + MES_EXTRACAO`, exports local Parquet, and does **not** depend on the API being up at review time.

## Hygiene
1. Token only in `.env` (`CEMEPI_API_TOKEN`); never commit secrets.
2. Versioned dump folder under `DUMP_ROOT` (symlink `data/` at mono root).
3. Manifest JSON: export date, endpoint, filters, row counts, Parquet hash.
4. Analyse on local Parquet (DuckDB/Polars); API only to re-extract when dump updates.
5. PII (CPF/name): do not print in shared notebooks; hash/anonymise public artefacts.

## Useful dump datasets
`debito`, `ajuizamento` (PEF), `arrecadacao`, `protesto`, `parcelamento`, `receita`, `faturamento`.

## Structural gaps (all fronts)
- No **garantia** table
- No unregistered debt
- No BacenJud / corporate network / judgment text
- Fragile typing (values/dates as strings in places)

See also project front docs under `projects/*/docs/`.
