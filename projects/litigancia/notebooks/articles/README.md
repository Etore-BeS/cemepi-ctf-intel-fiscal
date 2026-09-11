# Articles

One subdirectory per **substantive research output** based on this dataset.

This includes journal articles, working papers, conference talks, posters, and seminar presentations — anything that presents analysis beyond the data paper itself.

| Folder | Purpose | Publishable as |
|---|---|---|
| `notebooks/data_paper/` | Dataset description and validation | Scientific Data Data Descriptor |
| `notebooks/articles/<slug>/` | Analysis for a specific output | Journal article, conference talk, poster, etc. |
| `notebooks/playground/` | Exploratory work | Not for publication |

## Recommended layout per article

```
notebooks/articles/<slug>/
├── README.md           # title, venue, date, status, how to reproduce
├── presentation/       # slides (PDF, Beamer .tex, PPTX)
├── notebooks/          # analysis notebooks for this output
└── figures/            # exported figures used in slides/paper
```

Rename `<slug>` to something stable (e.g. `eped-2026-analise-execucoes-fiscais`, `cemepi-2026-sp`).

## Current articles

| Slug | Output |
|---|---|
| [`eped-2026-analise-execucoes-fiscais/`](eped-2026-analise-execucoes-fiscais/) | XV EPED 2026 — comunicação oral (FESP execuções fiscais 2016–2025) |
| [`revisao-bibliometrica-2026/`](revisao-bibliometrica-2026/) | Triagem assistida por dupla IA — revisão bibliométrica (Cohen's Kappa) |

## vs data paper

- **Data paper** describes the dataset itself (what it is, how it was built, how to use it).
- **Articles** use the dataset to answer a research question or present findings at an event.

The same exploratory notebook can start in `playground/` and move here once it targets a specific output.
