# Agent rules — cemepi-ctf-intel-fiscal

## Hard paths
- **Working copy:** this monorepo only.
- **Never write** to the frozen historical archive `habitual-tax-debtor-research` (archive A; local clone path varies — do not develop there).
- Lake/dumps stay on the external HD (`LAKE_ROOT`, `DUMP_ROOT`); do not put Delta tables in git.
- Do not copy `.git`, `.venv`, `logs/`, caches, or `.DS_Store` from A.

## Evidence & metrics
- **Never invent metrics**, row counts, join rates, or experimental results.
- Cite dump freeze (`extracao=YYYY-MM`), notebook, or SoT section when stating numbers.
- If a figure is unknown, say so — do not fabricate placeholders as facts.

## Doc precedence
- Ops conflict (README vs SoT) → **SoT wins** (`pipelines/*/docs/SOURCE_OF_TRUTH.md`).
- Term definition conflict (CONTEXT vs long doc) → **CONTEXT wins**; fix the long doc.

## Three MECE floors
1. Root `docs/` — portfolio (mission, architecture, api-dump, planos, cross-front refs).
2. `pipelines/*/docs/` — data & ops (SoT, schema, normalization, recoleta).
3. `projects/*/docs/` — front research only (not SoT).

## Attribution (writeups)
- Author owns content, experiments, and conclusions.
- AI may assist formatting and drafting; do not claim AI authorship of research claims.

## Secrets & staging
- Do **not** stage `.env`, tokens, credentials, or PII dumps.
- Keep `CEMEPI_API_TOKEN` and similar only in `.env` (gitignored).

## Notebook hygiene (non-negotiable)
- `projects/*/notebooks/playground/` holds **only** `.ipynb` (explanatory prose lives in MD cells inside the notebook).
- Treat paper/playground notebooks as article drafts: one research front per notebook; no personal names or meeting framing.
- **First appearance** of any method, metric, or measure in that notebook: insert a clear **referenced** academic markdown cell explaining what it is and how it is used *here*, before the application code.
- **Before every figure**: markdown on how to interpret it (axes, comparison, what not to overclaim).

- MD must be **technical**: cite origin papers/books (use `references/referencias_grounded.md` — never invent citations); define every first-seen metric/method with **LaTeX formulas** and say how it is applied in this notebook.
- Plot-reading MD must name axes, encodings, comparison baseline, and overclaim risks — not vague "observe the trend".
- Use **Plotly for both** interactive figures (`fig.show`) and static exports (`fig.write_image` PNG/SVG via Kaleido) for publication; do not demote to matplotlib as primary.
- Toys stay in `playground/`; promote to `articles/` / `data_paper/` only when the question and protocol stabilize.
