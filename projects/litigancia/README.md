# Litigância protelatória / inadimplência contumaz

Research writing for Plano A (CEMEPI / CTF). Lake/ops code is in `pipelines/litigancia/`.

## Layout

```
projects/litigancia/
├── notebooks/
│   ├── articles/          ← EPED 2026, revisão bibliométrica, …
│   ├── data_paper/        ← artefatos do data descriptor
│   ├── playground/        ← exploração
│   └── 0*_sample.ipynb    ← dump samples (Task 6 → dump_samples/)
├── manuscript/            ← data_descriptor (LaTeX/PDF)
├── docs/                  ← plano A + notas / material from A
├── references/            ← CITATION.cff + grounded refs
└── output/
```

## Open notebooks

1. From mono root: `uv run jupyter lab` (or VS Code / Cursor).
2. Select kernel **`ctf-research`** (HD env: `envs/ctf-research`).
3. If missing: `uv run python -m ipykernel install --user --name ctf-research --display-name "ctf-research (HD)"`.

Lake tables need `LAKE_ROOT` in mono `.env`. Dump samples need `DUMP_ROOT` / CEMEPÍ token as in root docs.

## Imports / paths

- Prefer `from config.paths import REPO_ROOT, …` (Hatch packages under `pipelines/litigancia/src/` — Task 5 `uv sync`).
- Notebook artefact paths use `REPO_ROOT / "projects/litigancia/notebooks/..."`.
- Pipeline scripts live under `pipelines/litigancia/scripts/` (not mono `scripts/`).

## Path migration notes

Grep still finds `habitual-tax-debtor-research` mainly in:

- **Stored notebook outputs** (print paths from old runs) — left as historical; re-run cells to refresh.
- **Manuscript** GitHub URLs in `manuscript/data_descriptor.tex` — citation of the source dataset repo; kept on purpose.
- **Presentation HTML** under articles — generated artefact; regenerate from notebook if needed.

Source cells that resolve `notebooks/` or `scripts/` relative to the old A repo root were updated to mono paths where they would break writes/imports.

## Ops

Pipeline runbook: `pipelines/litigancia/docs/` and `pipelines/litigancia/CONTEXT.md` (lake glossary).
Research question: `CONTEXT.md` here. Plan: `docs/A_litigancia_protelatoria.md`.
