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


# Plano C — Dívida ativa × fatores macroeconômicos

**Doc CTF:** `Projetos/Projeto_Dívida Ativa e Fatores Macroeconômicos_...docx`  
**Time:** Stanzani + **você** + Heloísa  

## Pergunta operacional
O estoque/arrecadação no dump cointegra com PIB/Selic/IPCA/desemprego? Eventos (REFIS) mudam o nível?

## Dependência
Só começa o modelo macro **depois** do painel mensal do Plano B (mesma série PGE). Evita duas limpezas.

## Camadas
| Lado | Fonte |
|---|---|
| PGE (Y) | painel B: estoque, inscrição líquida, arrecadação, por setor/`TIPO_DEBITO` |
| Macro (X) | BCB/IBGE (Sidra/SGS) — **fora da API** |
| Eventos | calendário manual REFIS/transação/Res. 9/2024 (+ flags se `parcelamento` ajudar) |
| Empresa | `faturamento` agregado por mês (proxy setorial frágil) |

## Análise (ordem)
1. Reusar painel B; escolher 2–3 `TIPO_DEBITO` (ex. ICMS Declarado, ICMS Autuação, IPVA).
2. Baixar séries macro alinhadas 2016–2026-03.
3. Correlação / cointegração / VAR ou ARDL — conforme estacionariedade.
4. Dummies de evento (H2–H4 do doc).
5. **Não esperar** dívida não inscrita no dump atual — declarar lacuna e, se crítico, pedir ao time **ou** proxy (fluxo de novas CDAs entre extrações).

## O que rodar
- [ ] Script pull macro (BCB SGS: Selic, IPCA; IBGE PIB) → Parquet
- [ ] Notebook `macro_cointegracao_v0.ipynb` em cima do painel B
- [ ] Tabela de hipóteses H1–H5 com status (testável / bloqueada)
- [ ] Parágrafo de limitações (sem dívida não inscrita; faturamento mascarado)

## Entregáveis 3–4 semanas (após B)
1. Painel PGE+macro  
2. Resultados H1/H5 (macro/empresa)  
3. Nota de pedido de dados (dívida não inscrita)

## Trade-off
Sem dívida não inscrita, o paper fala de **estoque inscrito**, não de inadimplência corrente. Seja explícito no framing.
