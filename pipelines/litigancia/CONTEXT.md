# Domain glossary — habitual tax debtor research

## Movimentação

Evento de tramitação processual registrado no FACE/TJSP. Cada linha em `movimentacoes_delta` representa um evento por processo.

## Tipo de movimentação

Texto bruto do campo `tipo_movimentacao` em `movimentacoes_delta`. Corresponde ao rótulo TJSP da movimentação (primeira linha do evento no scrape).

## Chave de revisão (movimentação)

Primeira linha do `tipo_movimentacao`, usada como grain do CSV de conferência manual. Textos multilinha com corpo diferente colapsam no mesmo bucket.

## Tipo de movimentação normalizado

Rótulo canônico atribuído manualmente a um ou mais valores brutos de `tipo_movimentacao`, usado em análises agregadas.

## Sentença (metadado FACE)

Classificação de desfecho processual extraída do vocabulário TJSP e armazenada em `tipo_sentença` (lista JSON) em `face_processos_clean_delta`.

## Tipo de sentença normalizado

Rótulo canônico atribuído manualmente a um ou mais valores brutos de `tipo_sentença`.

## Resultado favorável

Desfecho jurídico favorável ao credor (FESP) na execução fiscal, independentemente de haver recuperação econômica do crédito.

## Resultado desfavorável

Desfecho jurídico desfavorável ao credor (FESP) na execução fiscal.

## Resultado inconclusivo

Desfecho ambíguo ou dependente de contexto adicional; não deve ser forçado em binário favorável/desfavorável.

## Crédito recuperado

Indica se o desfecho implica recuperação econômica do crédito tributário (`sim`, `nao`, `inconclusivo`). Dimensão independente de resultado favorável/desfavorável.

## Dicionário de normalização

Arquivo de mapeamento `valor_bruto → rótulo normalizado (+ classificações)` produzido após revisão manual dos CSVs de conferência.

## Conferência manual por frequência

Processo de revisão ordenado por contagem decrescente de ocorrências, priorizando termos que cobrem maior fração da base.

## Competência tributária

Esfera do ente credor do tributo ou taxa cobrada na execução fiscal: federal (União), estadual (FESP) ou municipal (prefeitura). Inconsistências entre `assunto` e `autores` indicam ruído de classificação ou coleta ampla demais.

## Polo autor FESP

Fazenda do Estado de São Paulo no polo ativo da execução fiscal. No scrape TJSP, `autores` é texto livre com variantes ("Fazenda Estadual", "Fesp", "Fazenda do Estado", etc.), não uma entidade normalizada.

## Assunto jurídico ICMS

Rótulo SAJ de execução fiscal vinculado ao ICMS estadual paulista. Na recoleta round2, os nós relevantes aparecem como três rótulos textuais distintos; não deve ser confundido com assuntos de "Importação" genérica (PIS, COFINS, II, taxas municipais).

## LLM / agentes

Toda interação com APIs de LLM neste repositório usa **pydantic-ai** (`Agent` + modelos Pydantic de saída). Regra canônica: `docs/SOURCE_OF_TRUTH.md` §3.4.
