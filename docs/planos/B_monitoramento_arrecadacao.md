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


# Plano B — Monitoramento e previsão de arrecadação

**Doc CTF:** `Projetos/Projeto_Monitoramento_CeMEPI_PGE-SP.docx`  
**Time:** Bertini (UNICAMP) + Juvenal/Jackson/**você** + ICs  
**Âncora:** pode viver em notebook no repo fiscal **ou** pasta CTF; método = série temporal no dump.

## Pergunta operacional
Com o dump mensal de estoque + GARE, qual horizonte dá para prever arrecadação de forma **reproduzível** (walk-forward), e o ajuizamento melhora o forecast?

## Camadas
| Série | Endpoint/tabela dump | Frequência |
|---|---|---|
| Estoque inscrito | `/v1/debito/estoque` ou aggregate `debito` por ANO/MES | mensal 2016-01→2026-03 |
| Arrecadação | `/v1/arrecadacao/serie` + `arrecadacao` | mensal |
| Mix por tipo | aggregate `debito`/`arrecadacao` por `TIPO_DEBITO` | mensal |
| Composição | `receita` / analytics composicao | conforme cobertura |
| Controles | status ajuizamento, qtd ajuizamentos (`cobranca/panorama`) | mensal |

## Análise (ordem)
1. Congelar painel mensal único (CSV/Parquet) com colunas alinhadas ao schema da **Proposta de storage CTF**.
2. EDA: estacionariedade, sazonalidade, quebras (ex. Res. PGE 9/2024 se visível no dump).
3. Baseline: naive → ARIMA/SARIMA → Prophet/XGBoost.
4. Features exógenas **dentro do dump:** estoque defasado, mix ICMS/IPVA, taxa ajuizado, parcelamentos ativos.
5. Walk-forward (nunca shuffle aleatório).
6. Só então LSTM/Transformer — se baseline não bastar (**trade-off:** interpretabilidade pro Bertini/PGE > SOTA frágil).

## O que rodar
- [ ] Export séries estoque + arrecadação (todas as 119 extrações via endpoints de negócio, não full 800M)
- [ ] Notebook `monitoramento_serie_v0.ipynb`: painel + plots + ADF/KPSS
- [ ] Baseline SARIMA e métricas (MAPE/RMSE) em horizonte 1/3/6 meses
- [ ] Ablation: com vs sem status ajuizamento
- [ ] Mini-dashboard estático (Quarto/HTML) — não app live

## Entregáveis 2 semanas
1. Painel mensal versionado + manifesto  
2. Relatório de baseline (1–2 páginas)  
3. Decisão: deep learning sim/não com evidência

## Trade-off
Este projeto **é o melhor fit** do dump. Não gastar ciclos pedindo realtime; gastar em qualidade da série e protocolo experimental.
