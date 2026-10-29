# Declared and assessed VAT (ICMS) debt — manuscript (AVD-02)

Draft for the AAA congress, in English, on the repo's current template
(NeurIPS 2025 style, the same as `projects/monitoramento/manuscript/arrecadacao_forecast_v1`).

## Layers (docs/ESTRUTURA.md v1.1)

- L5: `projects/macro/notebooks/articles/icms_declarado_autuado.ipynb` writes
  `figures/`, `tables/` and `numeros.tex`. Do not edit them by hand; re-run the notebook.
- L6: this folder. `main.tex` and `sections/` only include what L5 wrote.
  Every number in the text is a macro from `numeros.tex`.

## Placeholders

`\discutir{...}` prints a red **[DISCUTIR E ESCREVER: ...]**. Interpretation, discussion and conclusions are the authors'.

## Compile

```bash
cd projects/macro/manuscript/icms_declarado_autuado
latexmk -pdf -interaction=nonstopmode main.tex
```

`main.pdf` and the auxiliary files stay untracked (gitignored).
