# Premissa comum — Inteligência Fiscal Data API

**Natureza:** dump estático para pesquisa (não realtime, não operação PGE).
**Implicação:** todo paper/notebook congela `extracao_ref = ANO_EXTRACAO + MES_EXTRACAO` (hoje o mais recente no dump é 2026-03), exporta Parquet local, e **não** depende da API estar no ar na hora da revisão.

## Higiene do dump
1. Token só em `.env` (`CEMEPI_API_TOKEN`); rotacionar o que vazou em chat.
2. Uma pasta versionada: `$LAKE_ROOT/external/cemepi_api/extracao=2026-03/` (ou `/Volumes/.../CEMEPI/dumps/`).
3. Manifest JSON: data do export, endpoint, filtros, contagem de linhas, hash do Parquet.
4. Análise sempre no Parquet local (DuckDB/Polars). API só para extrair de novo se o dump for atualizado.
5. PII (CPF/nome): não imprimir em notebook compartilhado; hash/anonimizar em artefatos públicos.

## Datasets úteis no dump
`debito` (fotos mensais), `ajuizamento` (PEF), `arrecadacao`, `protesto`, `parcelamento`, `receita`, `faturamento`.

## Lacunas estruturais do dump (todas as frentes)
- Sem tabela de **garantia**
- Sem dívida **não inscrita**
- Sem BacenJud / rede societária / texto de sentença
- Tipagem frágil (valores/datas como string em alguns campos)

