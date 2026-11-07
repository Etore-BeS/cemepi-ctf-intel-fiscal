# VAT collection forecast

NeurIPS-style draft.
The scientific protocol is this paper.
The run that produced the freeze is the supplementary material.

## Layout

The entry point is `main.tex`.
`neurips.sty` is a copy of `neurips_2025.sty`.
Sections, figures, and tables sit beside `main.tex`.

## Numbers

Do not type new metrics into the prose.
Read them from the tables and figures in this folder.
E1 is selective TEM.
E2 is the disagreement-proxy ablation.
The official rank is raw test sMAPE.
`SS_sMAPE` is complementary.

Define a term at first use.
Use GARE, enrolled debt, grain A, grain B, sMAPE, the skill score, TEM, and abstention only after that definition.
Prefer a short sentence.
Put the claim first, then the number, then one sentence of interpretation.
In math, write `\lt` and `\gt` instead of bare inequality signs.

## Author

The author block is in `main.tex`.

## Overleaf

1. Zip this folder's files, not the parent folder.
2. Include `main.tex`, the style files, `refs.bib`, this file, and the `sections`, `figures`, and `tables` folders.
3. In Overleaf, choose New Project, then Upload Project, and select the zip.
4. Set the main document to `main.tex`.
5. Set the compiler to pdfLaTeX.
6. Recompile after the BibTeX pass if a citation shows as `?`.

The style line is `\usepackage[preprint]{neurips_2025}`.
Drop the option for an anonymized submission.

## Local compile

From this directory:

```bash
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

```fish
pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
```

Leave `main.pdf` and the LaTeX auxiliary files untracked.
They are gitignored.
