# Domain context — CTF monorepo

## Dump vs lake
- **Dump (API Inteligência Fiscal):** static research extract of dívida ativa / cobrança (ATTUS-like). Freeze `extracao=YYYY-MM`.
- **Lake (litigancia):** TJSP Delta tables under `LAKE_ROOT` (juscraper → bronze/silver). Join key: `AJUIZAMENTO.PEF` ↔ `cd_processo`.

## Grain
- Credit: `ID_DEBITO`
- Court case: `PEF` / `cd_processo`
- Debtor: CPF/CNPJ (PII — hash in shared artefacts)

## Layout (MECE)
- `pipelines/litigancia` — lake scrape/transform/ops (SoT lives here).
- `projects/{litigancia,monitoramento,macro,garantias}` — notebooks, manuscripts, front docs.
- `shared/cemepi_api` — dump HTTP client.
- `docs/` — portfolio only (mission, architecture, api-dump, planos, references).

## Archive
`habitual-tax-debtor-research` is historical only — do not write there.

## Non-goals
Realtime ops dashboards; full 800M row interactive scans on laptop.
