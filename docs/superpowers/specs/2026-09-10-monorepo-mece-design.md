# Design: CeMEPI CTF monorepo MECE layout

**Date:** 2026-09-10  
**Repo:** `/Users/etorebraga/Code/cemepi-ctf-intel-fiscal`  
**Source archive (read-only after cutover):** `/Users/etorebraga/Code/habitual-tax-debtor-research`  
**Status:** Approved in chat (§1–§4). Awaiting user review of this file before implementation plan.

## 1. Goal

Turn `cemepi-ctf-intel-fiscal` into a research monorepo that:

- Hosts all CTF fronts Étore works on (litigância, monitoramento, macro, garantias).
- Absorbs the full content of repo A (`habitual-tax-debtor-research`) into the correct places — not a stub.
- Follows UNIX conventions, MECE separation, context engineering, and academic-writing practice.
- Keeps heavy data on the external HD; git stays code + docs + notebooks.

Non-goals for this cutover: new ML models, rotating API tokens, rewriting scrapers, publishing papers.

## 2. Decisions locked

| # | Decision |
|---|---|
| D1 | **Copy** full A into the mono; leave `habitual-tax-debtor-research` **frozen as archive** (not working copy). |
| D2 | Lift shareable pieces to mono root; `projects/litigancia` is **research/writing only**. |
| D3 | Lake/pipeline code lives in **`pipelines/litigancia/`** (src + scripts + tests + ops docs). |
| D4 | Docs are **three MECE floors**: root `docs/` = portfolio; `projects/*/docs/` = front research; `pipelines/*/docs/` = SoT/schema/ops. |
| D5 | Migration = **scaffold empty MECE tree**, then copy **slices** (docs → pipelines → notebooks → shared), fix imports/SoT, smoke tests. |
| D6 | Single root `pyproject`/`uv.lock`/`.venv` (HD `ctf-research`); keep Hatch packages `config`/`scrapers`/`utils` from A paths under `pipelines/litigancia/src/` to avoid mass import rewrite. |

## 3. Target tree (§1)

```text
cemepi-ctf-intel-fiscal/
├── README.md
├── CONTEXT.md                 # short cross-front glossary / constraints
├── AGENTS.md                  # agent rules (paths, no invented data, frozen A)
├── CONTRIBUTING.md
├── CITATION.cff               # from A (portfolio-level cite)
├── LICENSE                    # from A
├── pyproject.toml             # merge A ∪ dump deps
├── uv.lock
├── .env / .env.example
├── shared/
│   └── cemepi_api/            # Inteligência Fiscal dump client
├── pipelines/
│   └── litigancia/
│       ├── README.md          # ops / lake how-to
│       ├── CONTEXT.md         # lake glossary (A's CONTEXT.md)
│       ├── docs/              # SoT, schema, normalization, recoleta, dictionaries
│       ├── src/{config,scrapers,utils}/
│       ├── scripts/
│       ├── tests/
│       └── config/            # e.g. oxylabs.json.example
├── projects/
│   ├── litigancia/
│   │   ├── README.md
│   │   ├── CONTEXT.md         # research question of the front
│   │   ├── docs/              # front-only notes / plan A (not SoT)
│   │   ├── notebooks/         # articles, data_paper, dump samples, playground
│   │   ├── manuscript/        # LaTeX data descriptor
│   │   ├── references/
│   │   └── output/            # gitignored except curated figures
│   ├── monitoramento/
│   ├── macro/
│   └── garantias/             # same thin skeleton
├── docs/                      # PORTFOLIO ONLY
│   ├── mission.md
│   ├── architecture.md        # mono ↔ HD map (summary of this design)
│   ├── api-dump/              # dump premise, API notes
│   ├── planos/                # A–D from CTF Drive
│   ├── references/            # cross-front grounded refs
│   └── superpowers/
│       ├── specs/             # this file
│       └── plans/             # implementation plan (next)
├── data → /Volumes/.../dumps/cemepi_api
└── .venv → /Volumes/.../envs/ctf-research/.venv
```

**Data stays on HD:** `LAKE_ROOT` / `COLLECT_ROOT` = gilson lake; dumps under `DUMP_ROOT`. No Delta tables in git.

## 4. A → mono mapping (§2)

| From A | To mono |
|---|---|
| `src/` | `pipelines/litigancia/src/` |
| `scripts/` | `pipelines/litigancia/scripts/` |
| `tests/` | `pipelines/litigancia/tests/` |
| `config/` | `pipelines/litigancia/config/` |
| `docs/PROJECT_SOURCE_OF_TRUTH.md` | `pipelines/litigancia/docs/SOURCE_OF_TRUTH.md` |
| `docs/schema|normalization|recoleta/` | `pipelines/litigancia/docs/...` |
| `docs/data_dictionary.md`, `pf_publication_schema.md` | `pipelines/litigancia/docs/` |
| `CONTEXT.md` | `pipelines/litigancia/CONTEXT.md` |
| `README.md` (ops parts) | `pipelines/litigancia/README.md` |
| `notebooks/**` | `projects/litigancia/notebooks/` |
| `docs/manuscript/` | `projects/litigancia/manuscript/` |
| `CITATION.cff`, `LICENSE` | mono root (+ optional copy under `projects/litigancia/references/`) |
| `pyproject.toml` / `uv.lock` | **merge** into mono root |
| `.env` keys | **merge** into mono root `.env` / `.env.example` |
| `.git`, `.venv`, `logs/`, caches, `.DS_Store` | **do not copy** |

After cutover, do not write to A. README/CONTEXT at mono root state that A is historical archive only.

## 5. Docs & context engineering (§3)

### Three floors (MECE)

1. **Root `docs/`** — portfolio: mission, architecture, API dump premise, CTF plans, cross-front references, CONTRIBUTING.
2. **`pipelines/*/docs/`** — data & ops: SoT, schemas, normalization, recoleta, quality gates.
3. **`projects/*/docs/`** — front research only: question, hypotheses, analysis notes, front plan (not SoT).

### Precedence

- Ops conflict README vs SoT → **SoT wins**.
- Term definition conflict CONTEXT vs long doc → **CONTEXT wins**; fix the long doc.

### File roles

| File | Where | Role |
|---|---|---|
| `CONTEXT.md` | root, each pipeline, each project | ≤ ~80 lines; agent-facing |
| `SOURCE_OF_TRUTH.md` | pipelines only | long; architecture & ops state |
| `AGENTS.md` | root | agent rules |
| `README.md` | each level | human onboarding |

### Academic writing

- Manuscript under `projects/litigancia/manuscript/`.
- Per-project `references/`; cross-front refs only in `docs/references/`.
- Notebook outputs under `projects/*/output/` (gitignore; keep citable figures if needed).
- Attribution on writeups: author owns content/experiments/conclusions; AI may assist formatting.

## 6. Env, imports, packaging (§4)

### Environment

- One editable install at mono root via `uv`.
- Merge dependency sets:
  - From A: deltalake, duckdb, polars, juscraper (git), pydantic-ai-slim[anthropic], ijson, boto3, altair, …
  - From dump mono: requests, python-bcb, statsmodels (if still needed), ipykernel, nbclient, …
- Hatch wheel packages (paths relative to `pipelines/litigancia`):

```toml
[tool.hatch.build.targets.wheel]
packages = [
  "shared/cemepi_api",
  "pipelines/litigancia/src/config",
  "pipelines/litigancia/src/scrapers",
  "pipelines/litigancia/src/utils",
]
```

Exact Hatch path form must be verified during implementation (`uv sync` + import smoke). Prefer preserving import names `config`, `scrapers`, `utils` over introducing `pipelines.litigancia.*` in this cutover.

- Kernel name: `ctf-research`.
- Scripts: `uv run` from mono root; document `cwd` in pipeline SoT.

### Slice order

1. Scaffold MECE empty dirs + portfolio docs stubs.
2. Copy ops docs → `pipelines/litigancia/docs` + CONTEXT.
3. Copy `src` / `scripts` / `tests` / `config` → pipeline.
4. Copy notebooks + manuscript → `projects/litigancia`.
5. Merge pyproject / uv.lock / `.env`; `uv sync`.
6. Patch SoT / README / path strings; remove conflicting stubs.
7. Smoke: import `cemepi_api` + one `scrapers` module; subset `pytest`; one litigância notebook.

## 7. Acceptance criteria

- [x] Tree matches §1; duplicate stub layouts removed.
- [x] A content mapped per §2; no `.git`/`.venv`/logs copied.
- [x] Docs on three floors per §3; grounded refs under `docs/references/`.
- [x] `uv sync` succeeds; imports work.
- [x] Smoke pytest + one notebook pass.
- [x] A remains intact and is documented as non-working-copy.
- [x] This spec reviewed by user; implementation plan written under `docs/superpowers/plans/`.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Hatch package paths break after move | Verify with `uv sync` + `python -c "import scrapers"` early in slice 5. |
| Notebooks hardcode old repo path | Grep for `habitual-tax-debtor-research` and rewrite to mono paths. |
| juscraper SSH git dep fails on sync | Document SSH key requirement; keep same A dep URL. |
| Accidental writes to A | AGENTS.md + README; no scripts target A path. |
| 249MB notebooks | Copy once; do not duplicate into `data/`; large outputs stay gitignored. |

## 9. Out of scope (explicit)

- Deleting or chmodding A (optional later).
- Git history rewrite / subtree merge from A.
- Force-push or remote setup for the mono (only if user asks).
- Token rotation for CEMEPI API.
- Building garantia dataset or full PEF↔lake join ML.

---

**Self-review (2026-09-10):** No TBD placeholders for locked decisions; §1–§4 consistent with chat approvals; scope limited to cutover; hatch path form flagged for verify-at-implement (not a design hole).
