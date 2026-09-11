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


# Plano A — Litigância protelatória / inadimplência contumaz

**Doc CTF:** `Projetos/Projeto_Litigância_Protelatória_e_Inadimplência_Contumaz_...docx`  
**Time:** Gilson + você; Bertran + Rodello  
**Repo âncora:** `habitual-tax-debtor-research` (remote `fiscal-tax-research`)  
**Papel da API:** ATTUS-like (crédito/cobrança). **Papel do lake:** E-SAJ/TJSP (processo/sentença/movimentação).

## Pergunta operacional
Dado o dump + o lake, dá para construir um **score comportamental por CNPJ/CPF** que separe litigância estratégica de defesa legítima — sem fingir que a API é live.

## Camadas de dados (congelar)
| Camada | Fonte | Grain | O que extrair |
|---|---|---|---|
| Crédito | dump `debito` @ 2026-03 (+ histórico se precisar) | `ID_DEBITO` | tipo, valor, situação, status ajuizamento, datas |
| Judicial ATTUS | dump `ajuizamento` | `ID_DEBITO`↔`PEF` | comarca, data ajuizamento |
| Cobrança | dump protesto/parcelamento/arrecadacao | `ID_DEBITO` | status, rompimento, GARE |
| Capacidade | dump `faturamento` | CNPJ×mês | VL mascarado |
| Processo | lake silver FACE/processos/movimentações | `cd_processo`=`PEF` | desfecho, tempo, incidentes |

## Análise (ordem)
1. **Cobertura do join** PEF dump ↔ `cd_processo` (amostra 10k + full ajuizamento). Reportar % match / não-match.
2. **Unidade contribuinte:** agregar débitos por documento; Curva ABC valor.
3. **Features comportamentais (só dump):** qtd débitos, idade inscrição, taxa protesto, parcelamentos rompidos, gap GARE, razão valor/faturamento.
4. **Features processuais (lake):** time-to-sentence, densidade incidentes, desfecho normalizado (já no CONTEXT.md).
5. **Cluster + score:** não supervisionado → rótulos interpretáveis → logística/sobrevivência (Kaplan-Meier) no subset com sentença.
6. **Validação:** holdout temporal por ano de ajuizamento; SHAP só depois da base estável.

## O que rodar (checklist)
- [ ] Mergear PR do cliente API ou copiar helpers
- [ ] Export: `ajuizamento` full parquet; `debito` filtrado 2026-03 (+ ICMS se recorte EPED)
- [ ] Export: protesto/parcelamento/arrecadacao filtrados pelos `ID_DEBITO` do join
- [ ] Script cobertura PEF (DuckDB): match rate + amostra não-match sem PII
- [ ] Notebook `playground/litigancia_features_v0.ipynb`: tabela contribuinte + 5–8 features
- [ ] Cruzar 1k PEFs matched com FACE clean (desfecho)
- [ ] Memo 1 página: definição operacional de “contumaz” no dump (alinhada LC 225/2026, sem overclaim)

## Entregáveis próximos 2 semanas
1. Tabela de cobertura PEF + manifest do dump  
2. Dicionário de features v0  
3. Cluster exploratório (não é ainda o score final)

## Trade-off
Priorizar **join + features** antes de ML fancy. Sem cobertura PEF medida, process mining é teatro.
