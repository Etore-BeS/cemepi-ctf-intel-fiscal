# EPED 2026 — Análise empírica das execuções fiscais estaduais paulistas (2016–2025)

Comunicação oral aprovada no **XV Encontro Nacional de Pesquisa Empírica em Direito (EPED)**, REED.

## Metadata

| Field | Value |
|---|---|
| **Title** | ANÁLISE EMPÍRICA DAS EXECUÇÕES FISCAIS ESTADUAIS PAULISTAS ENTRE 2016 E 2025 |
| **Submission ID** | 1540866 |
| **Submitted** | 2026-04-25 |
| **Status** | Aprovado |
| **Format** | Resumo expandido — Comunicação oral |
| **Thematic area** | GT68: Direito, tecnologia e inteligência artificial: métodos, teorias e regulação |
| **Event** | XV EPED 2026 |
| **Dates** | 2026-08-10 — 2026-08-14 (GMT-3) |
| **Venue** | ICJ — Instituto de Ciências Jurídicas, UFPA, Belém/PA |
| **Authors** | Gilson Oliveira da Silva Tardim; Étore Braga e Santos; Ildeberto Aparecido Rodello; Maria Paula Bertran |

## Contents

| Path | Purpose |
|---|---|
| [`notebooks/fesp_execucao_fiscal_analysis.ipynb`](notebooks/fesp_execucao_fiscal_analysis.ipynb) | Análise principal (adaptada de `generate_plots_pge.ipynb`) |
| [`notebooks/eped_2026_presentation.ipynb`](notebooks/eped_2026_presentation.ipynb) | Entrypoint: narrativa curta para apresentação oral |
| [`presentation/`](presentation/) | Slides (PDF, Beamer, PPTX) |
| [`figures/`](figures/) | Figuras exportadas pelos notebooks |
| [`artifacts/`](artifacts/) | Tabelas-resumo e stats para slides |

## Data source

- Silver tables: `face_processos_clean_delta` (`SILVER_FACE_CLEAN`) ∩ `processos_delta` (`SILVER_PROCESSOS`)
- Coleta: round2 ICMS (`coleta_assunto_icms_*`); ver [`docs/recoleta/round2_assunto_icms.md`](../../../docs/recoleta/round2_assunto_icms.md)
- Assuntos (allowlist): `ICMS/ Imposto sobre Circulação de Mercadorias`, `ICMS/Importação`, `ICMS / Incidência Sobre o Ativo Fixo`
- Polo autor: FESP e variantes textuais no scrape (`fazenda do estado`, `fazenda estadual`, `fesp`, `estado de são paulo`, etc.)
- Export filtrado: [`artifacts/fesp_execucao_fiscal_icms_assuntos_filtered.csv`](artifacts/fesp_execucao_fiscal_icms_assuntos_filtered.csv)
- See [docs/data_dictionary.md](../../../docs/data_dictionary.md) and [docs/schema/face_processos.md](../../../docs/schema/face_processos.md)

## Provenance

| Artifact | Location |
|---|---|
| Original notebook | `medplus-scraper/scripts/generate_plots_pge.ipynb` |
| Migration plan | `.cursor/plans/gilson_data_paper_migration_7942be22.plan.md` (local) |

## Reproduce

1. Configure `.env` with `LAKE_ROOT` pointing to the lake volume.
2. Install dev dependencies (includes `nbconvert`):

```bash
# bash / fish
uv sync --group dev
```

3. Run analysis notebook, then presentation entrypoint (from repo root). The analysis notebook is heavy (~290k processos no recorte ICMS+FESP, várias figuras) and may take **15–30+ minutes**; nbconvert prints little until it finishes.

```bash
# bash / fish
uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 \
  notebooks/articles/eped-2026-analise-execucoes-fiscais/notebooks/fesp_execucao_fiscal_analysis.ipynb
uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 \
  notebooks/articles/eped-2026-analise-execucoes-fiscais/notebooks/eped_2026_presentation.ipynb
```

Monitor progress with `ls notebooks/articles/eped-2026-analise-execucoes-fiscais/figures/`.

Figures are written to `figures/`; summary tables to `artifacts/`.

## vs data paper

- **Data paper** (`notebooks/data_paper/`, `docs/manuscript/`): describes the full TJSP dataset.
- **This article**: substantive FESP-focused empirical analysis for EPED 2026.
