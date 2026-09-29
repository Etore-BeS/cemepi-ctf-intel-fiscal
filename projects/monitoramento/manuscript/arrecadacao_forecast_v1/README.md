# Arrecadação forecast paper v1

NeurIPS-style draft of the enrolled-debt collection forecast.

The scientific protocol is the paper.
The run that produced the freeze is the notebook
`projects/monitoramento/notebooks/articles/arrecadacao_forecast_paper_v1.ipynb`.

## Layout

```
arrecadacao_forecast_v1/
  README.md
  main.tex
  neurips_2025.sty
  neurips.sty
  refs.bib
  sections/
  figures/
  tables/
```

`neurips.sty` is a copy of `neurips_2025.sty`.
The entry point is `main.tex`.

## Numbers

Do not type new metrics into the prose.
Read them from the freeze:

- Ranked tables and predictions:
  `projects/monitoramento/output/articles/arrecadacao_forecast_paper_v1/`
- Figure files:
  `projects/monitoramento/output/figures/articles/arrecadacao_forecast_paper_v1/`
- Copies used by the manuscript: `figures/` in this folder.

E1 is selective TEM.
E2 is the disagreement-proxy ablation.
The official rank is raw test sMAPE.
`SS_sMAPE` is complementary.
In math, write `\lt` and `\gt` instead of bare inequality signs.

## Author

Étore Braga e Santos, FT/Unicamp, CEMEPI.
The email in `main.tex` is still a placeholder.
No address in the repo notes was available to fill it.

## Overleaf

1. Zip the project files, not the parent folder.

```bash
cd projects/monitoramento/manuscript/arrecadacao_forecast_v1
zip -r ~/Desktop/arrecadacao_forecast_v1_overleaf.zip \
  main.tex neurips_2025.sty neurips.sty refs.bib \
  sections figures tables README.md
```

```fish
cd projects/monitoramento/manuscript/arrecadacao_forecast_v1
zip -r ~/Desktop/arrecadacao_forecast_v1_overleaf.zip \
  main.tex neurips_2025.sty neurips.sty refs.bib \
  sections figures tables README.md
```

2. In Overleaf, choose New Project, then Upload Project, and select the zip.
3. Set the main document to `main.tex`.
4. Set the compiler to pdfLaTeX.
5. Recompile after the BibTeX pass if a citation shows as `?`.

The style line is `\usepackage[preprint]{neurips_2025}`.
Drop the option for an anonymized submission.

## Local compile

```bash
cd projects/monitoramento/manuscript/arrecadacao_forecast_v1
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

```fish
cd projects/monitoramento/manuscript/arrecadacao_forecast_v1
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

Leave `main.pdf` and the LaTeX auxiliary files untracked.
They are gitignored.
