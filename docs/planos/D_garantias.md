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


# Plano D — Garantias e risco protelatório

**Doc CTF:** `Projetos/Projeto_Garantia_e_Divida_Ativa.docx`  
**Time:** Rodello + Stanzani + **você** + Juvenal + Alexandre  

## Verdade incômoda
O dump **não contém** tipo de garantia (carta-fiança, seguro, imóvel, depósito). Sem isso o constructo central do projeto não fecha.

## Duas trilhas paralelas

### D1 — Desbloqueio de dados (obrigatório)
Pedido formal ao time ATTUS/API:
- tabela `garantia`: `ID_DEBITO`, tipo, data, valor, vigência, status
- ideal: join estável com PEF

Enquanto não vier: **não** treinar “score de garantia”.

### D2 — Trabalho científico possível agora (proxy, rotulado como tal)
Usar dump para **perfil do devedor e trilhas de cobrança** (insumo futuro do score):
- segmentação por valor/tipo/status ajuizamento
- parcelamento rompido + protesto + atraso relativo
- faturamento mascarado

Isso alimenta literatura de protelação **sem** afirmar causalidade via garantia.

## O que rodar agora
- [ ] 1 página: “Data gap — garantia ausente no dump de pesquisa”
- [ ] EDA proxy: notebook `garantia_proxy_cobranca_v0.ipynb` (sem usar a palavra score de garantia no título público)
- [ ] Checklist de campos mínimos quando a tabela chegar
- [ ] Alinhar com frente F (Segmentação) — mesmo bloqueio

## O que rodar quando `garantia` existir
1. Caracterizar por tipo (obj. 4.2.1)
2. Cluster + score protelatório (H1–H4 do doc)
3. Ligar recuperação via `arrecadacao`

## Trade-off
Honestidade metodológica > dashboard vazio. Seu tempo em Litigância/Monitoramento rende mais até o gap fechar.
