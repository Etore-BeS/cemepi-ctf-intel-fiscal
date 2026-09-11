# Litigância protelatória / inadimplência contumaz

## Pergunta de pesquisa

Dado o dump CEMEPÍ (ATTUS-like: crédito/cobrança) + o lake TJSP/FACE (processo/sentença/movimentação), dá para construir um **score comportamental por CNPJ/CPF** que separe litigância estratégica de defesa legítima — sem fingir que a API é live?

## Escopo deste projeto (`projects/litigancia`)

Pesquisa e escrita: notebooks, manuscript, referências, memos. Operação do lake (scrapers, silver, scripts) vive em `pipelines/litigancia/`.

## Join âncora

- Dump `ajuizamento.PEF` ↔ lake `cd_processo` (cobertura a medir antes de features processuais).
- Unidade de análise: contribuinte (documento), com débitos agregados; features de cobrança (dump) + tempo/incidentes/desfecho (lake).

## Definições operacionais (rascunho)

- **Contumaz / habitual:** padrão reiterado de inadimplência + uso do processo, alinhado à LC 225/2026 sem overclaim — memo em `docs/`.
- **Litigância protelatória (hipótese):** densidade de incidentes, time-to-sentence anômalo, desfecho sem recuperação, cruzado com capacidade (`faturamento`) e histórico de protesto/parcelamento.

## O que *não* fica aqui

Glossário de tabelas silver / normalização de movimentação → `pipelines/litigancia/CONTEXT.md`.
Premissa do dump estático → `docs/planos/A_litigancia_protelatoria.md` (cópia em `docs/`).
