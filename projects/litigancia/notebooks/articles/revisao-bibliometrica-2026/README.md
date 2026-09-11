# Revisão Bibliométrica 2026 — Triagem assistida por dupla IA e Cohen's Kappa

Notebook de metodologia para a triagem do levantamento bibliométrico da dissertação (Plano Metodológico v2), com EDA dos corpora deduplicados, classificação por **duas passagens independentes** de Claude Sonnet 5 via **pydantic-ai** (com prompt caching) e aferição de concordância interavaliadores via Cohen's Kappa.

## Metadata

| Field | Value |
|---|---|
| **Title** | Triagem assistida por dupla IA com validação humana — Revisão Bibliométrica |
| **Status** | Em elaboração |
| **Output** | Artigo / capítulo metodológico da dissertação |

## Contents

| Path | Purpose |
|---|---|
| [`notebooks/triagem_llm_cohen_kappa.ipynb`](notebooks/triagem_llm_cohen_kappa.ipynb) | EDA, triagem LLM (pydantic-ai), Cohen's Kappa, fila de revisão humana |
| [`src/utils/bibliometric_screening.py`](../../../src/utils/bibliometric_screening.py) | Agentes, critérios EC v2, guardrails e cache |
| [`artifacts/`](artifacts/) | Planilhas de triagem e cache JSONL |

## Data sources

- `.tmp/Revisao Bibliométrica/Dados/Busca A/com_filtros/BuscaA_Unificado_Deduplicado.csv` — 990 registros
- `.tmp/Revisao Bibliométrica/Dados/Busca B/com_filtros/BuscaB_Unificado_Deduplicado.csv` — 31 registros

Documentos metodológicos de referência:

- `.tmp/Revisao Bibliométrica/Plano_Metodologico_Revbibliometrica_v2.docx`
- `.tmp/Revisao Bibliométrica/Sumario_Resumo_v2.docx`

## Reproduce

1. Configure `.env` na raiz do repositório com `ANTHROPIC_API_KEY`.
2. Instale dependências:

```bash
# bash / fish
uv sync
```

3. Execute o notebook em modo piloto primeiro (`RUN_MODE = "pilot"` na célula 1). Após revisar `artifacts/Piloto_Calibracao_BuscaA.csv`, defina `PILOT_APPROVED = True` e `RUN_MODE = "full"`.

```bash
# bash / fish
uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=7200 \
  notebooks/articles/revisao-bibliometrica-2026/notebooks/triagem_llm_cohen_kappa.ipynb
```

Saídas esperadas:

| Modo | Arquivo |
|------|---------|
| `RUN_MODE = "pilot"` | `artifacts/Piloto_Calibracao_BuscaA.csv` |
| `RUN_MODE = "full"` | `artifacts/cache/busca_*_ia{1,2}_claudesonnet5.jsonl` |
| `RUN_MODE = "full"` | `artifacts/progress/*.json` |
| `RUN_MODE = "full"` | `artifacts/BuscaA_Triagem_DuplaIA.xlsx`, `artifacts/BuscaB_Triagem_DuplaIA.xlsx` |
| `RUN_MODE = "full"` | `artifacts/BuscaA_Revisao_Humana.csv`, `artifacts/BuscaB_Revisao_Humana.csv` |

## Nota metodológica

O Plano Metodológico v2 (Quadro 5) prevê revisor-IA 1 em Claude e revisor-IA 2 em GPT. Esta implementação utiliza **duas passagens independentes** de `claude-sonnet-5`. O κ IA-IA mede estabilidade entre classificações do mesmo modelo; o **índice principal** permanece o κ entre os dois revisores humanos.

Registros já triados com `claude-sonnet-4-6` (ia1 parcial) são **migrados** para o cache Sonnet 5 sem reinvocar a API.

## Nota técnica — pydantic-ai + prompt caching

Toda chamada a LLM neste artigo usa **pydantic-ai** (`Agent` + `ScreeningDecision`) com `anthropic_cache_instructions=True`, conforme regra do repositório em [`docs/PROJECT_SOURCE_OF_TRUTH.md`](../../../docs/PROJECT_SOURCE_OF_TRUTH.md) §3.4. Não há uso direto dos SDKs `anthropic` ou `openai`.

Alternativa CLI para triagem completa (sem EDA):

```bash
# bash / fish
uv run python scripts/articles/run_bibliometric_screening_full.py
```
