# CeMEPI CTF monorepo MECE migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate full `habitual-tax-debtor-research` (A) into `cemepi-ctf-intel-fiscal` under the approved MECE layout, freeze A as archive, and leave a single uv env with working imports and smokes.

**Architecture:** Portfolio docs and shared dump client at mono root; lake/ops code under `pipelines/litigancia/`; research writing under `projects/litigancia/`; thin skeletons for B/C/D. Slice copy (docs → code → notebooks → env merge), then fix `REPO_ROOT` in `paths.py` (parents depth changes after the move).

**Tech Stack:** Python ≥3.13, uv + hatchling, deltalake/duckdb/polars (lake), `shared.cemepi_api` (dump), pytest, Jupyter (`ctf-research` kernel on HD).

**Spec:** `docs/superpowers/specs/2026-09-10-monorepo-mece-design.md`

## Global Constraints

- Do **not** write to `/Users/etorebraga/Code/habitual-tax-debtor-research` (frozen archive).
- Do **not** copy `.git`, `.venv`, `logs/`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.DS_Store`.
- Lake/dumps stay on HD (`LAKE_ROOT`, `DUMP_ROOT`); no Delta in git.
- Commits only when the user explicitly asks (Étore policy); skip commit steps unless told.
- Machine paths: mono `/Users/etorebraga/Code/cemepi-ctf-intel-fiscal`; A `/Users/etorebraga/Code/habitual-tax-debtor-research`.
- Language of new portfolio docs: English for README/architecture; Portuguese OK in front CONTEXT if matching existing A voice.

---

### Task 1: Scaffold MECE tree + portfolio docs

**Files:**
- Create: `docs/mission.md`, `docs/architecture.md`, `docs/api-dump/00_premissa_dump.md`, `docs/references/referencias_grounded.md`, `AGENTS.md`, `CONTRIBUTING.md`, root `CONTEXT.md`, root `README.md` (rewrite)
- Create dirs: `pipelines/litigancia/{docs,src,scripts,tests,config}`, `projects/{litigancia,monitoramento,macro,garantias}/{docs,notebooks,manuscript,references,output}`
- Modify: move existing `docs/planos/` stays; move `docs/referencias_grounded.md` → `docs/references/`
- Remove later (Task 6): conflicting stub files under old flat `projects/*/notebooks/*_sample.ipynb` only after new tree is populated — in this task only create missing dirs

**Interfaces:**
- Consumes: approved tree in spec §1
- Produces: empty MECE directories + portfolio markdown stubs that link to pipelines/projects

- [ ] **Step 1: Create directory skeleton**

```bash
MONO=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal
cd "$MONO"
mkdir -p \
  docs/api-dump docs/references docs/planos \
  pipelines/litigancia/{docs/schema,docs/normalization,docs/recoleta,src,scripts,tests,config} \
  projects/litigancia/{docs,notebooks,manuscript,references,output} \
  projects/monitoramento/{docs,notebooks,references,output} \
  projects/macro/{docs,notebooks,references,output} \
  projects/garantias/{docs,notebooks,references,output}
touch projects/litigancia/output/.gitkeep \
  projects/monitoramento/output/.gitkeep \
  projects/macro/output/.gitkeep \
  projects/garantias/output/.gitkeep
```

- [ ] **Step 2: Relocate grounded refs**

```bash
MONO=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal
if [ -f "$MONO/docs/referencias_grounded.md" ]; then
  mv "$MONO/docs/referencias_grounded.md" "$MONO/docs/references/referencias_grounded.md"
fi
# if planos already under docs/planos, leave them
```

- [ ] **Step 3: Write `docs/mission.md`**

```markdown
# Mission

CeMEPI / PGE Contencioso Tributário Fiscal (CTF) research monorepo.

Working copy for fronts: litigância (A), monitoramento (B), macro (C), garantias (D).
Data: TJSP lake on external HD + Inteligência Fiscal **research dump** API (not realtime).

Historical archive (do not write): `habitual-tax-debtor-research`.
```

- [ ] **Step 4: Write `docs/architecture.md`** (short map pointing at the spec file; do not duplicate the full spec)

```markdown
# Architecture

- Spec: `docs/superpowers/specs/2026-09-10-monorepo-mece-design.md`
- `shared/cemepi_api` — dump HTTP client
- `pipelines/litigancia` — lake scrape/transform/ops
- `projects/*` — notebooks, manuscripts, front docs only
- HD: `LAKE_ROOT` gilson; `DUMP_ROOT` cemepi_api dumps; `.venv` → `envs/ctf-research`
```

- [ ] **Step 5: Write root `AGENTS.md`, `CONTEXT.md`, `CONTRIBUTING.md`, rewrite `README.md`**

`AGENTS.md` must include: never write to A; never invent metrics; SoT wins on ops; CONTEXT wins on term defs; attribution line for writeups; do not stage secrets.

- [ ] **Step 6: Verify scaffold**

```bash
find /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects -maxdepth 3 -type d | sort
test -f /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/docs/mission.md
test -f /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/docs/references/referencias_grounded.md
```

Expected: dirs exist; files exist.

- [ ] **Step 7: Commit only if user asked**

---

### Task 2: Copy pipeline docs + CONTEXT from A

**Files:**
- Create (copy): `pipelines/litigancia/docs/**`, `pipelines/litigancia/CONTEXT.md`, `pipelines/litigancia/README.md`
- Rename: `PROJECT_SOURCE_OF_TRUTH.md` → `SOURCE_OF_TRUTH.md`

**Interfaces:**
- Consumes: Task 1 dirs
- Produces: ops docs readable under pipeline; A untouched

- [ ] **Step 1: rsync docs (exclude manuscript — that goes to projects)**

```bash
A=/Users/etorebraga/Code/habitual-tax-debtor-research
P=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia
rsync -a --exclude 'manuscript/' "$A/docs/" "$P/docs/"
mv "$P/docs/PROJECT_SOURCE_OF_TRUTH.md" "$P/docs/SOURCE_OF_TRUTH.md"
cp "$A/CONTEXT.md" "$P/CONTEXT.md"
cp "$A/README.md" "$P/README.md"
```

- [ ] **Step 2: Patch path strings in SoT/README**

Replace occurrences of `habitual-tax-debtor-research/` with `pipelines/litigancia/` (and note mono root for `.env`) inside:

- `pipelines/litigancia/docs/SOURCE_OF_TRUTH.md`
- `pipelines/litigancia/README.md`

Keep `LAKE_ROOT` HD paths unchanged.

- [ ] **Step 3: Verify**

```bash
test -f /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia/docs/SOURCE_OF_TRUTH.md
test ! -d /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia/docs/manuscript
test -f /Users/etorebraga/Code/habitual-tax-debtor-research/docs/PROJECT_SOURCE_OF_TRUTH.md  # A still has original
```

- [ ] **Step 4: Commit only if user asked**

---

### Task 3: Copy src, scripts, tests, config

**Files:**
- Create (copy): `pipelines/litigancia/src/**`, `scripts/**`, `tests/**`, `config/**`

**Interfaces:**
- Consumes: Task 2
- Produces: code tree; imports may fail until Task 5 (`paths.py` parents)

- [ ] **Step 1: Copy code trees**

```bash
A=/Users/etorebraga/Code/habitual-tax-debtor-research
P=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$A/src/" "$P/src/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*.log' "$A/scripts/" "$P/scripts/"
rsync -a --exclude '__pycache__' "$A/tests/" "$P/tests/"
rsync -a "$A/config/" "$P/config/"
```

- [ ] **Step 2: Fix `REPO_ROOT` in paths.py (critical)**

File: `pipelines/litigancia/src/config/paths.py`

Current (A):

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
```

After move, `parents[2]` is `pipelines/litigancia`, but `.env` lives at mono root (`parents[4]`).

Replace with:

```python
PIPELINE_ROOT = Path(__file__).resolve().parents[2]  # .../pipelines/litigancia
REPO_ROOT = Path(__file__).resolve().parents[4]      # .../cemepi-ctf-intel-fiscal
load_dotenv(REPO_ROOT / ".env")
```

If any code assumed `REPO_ROOT` for pipeline-local files, use `PIPELINE_ROOT` for those; keep lake paths on `LAKE_ROOT`.

- [ ] **Step 3: Grep for hardcoded A paths in pipeline code**

```bash
rg -n "habitual-tax-debtor-research" /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia || true
```

Fix any hits to mono-relative paths.

- [ ] **Step 4: Verify file counts roughly match A (excluding caches)**

```bash
find /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia/src -type f | wc -l
find /Users/etorebraga/Code/habitual-tax-debtor-research/src -type f ! -path '*/__pycache__/*' | wc -l
```

Expected: equal or mono ≥ A (no missing modules).

- [ ] **Step 5: Commit only if user asked**

---

### Task 4: Copy notebooks + manuscript into projects/litigancia

**Files:**
- Create (copy): `projects/litigancia/notebooks/**`, `projects/litigancia/manuscript/**`
- Create: `projects/litigancia/CONTEXT.md`, `projects/litigancia/README.md`, `projects/litigancia/docs/` front notes
- Copy: `CITATION.cff` → mono root and `projects/litigancia/references/`; `LICENSE` → mono root

**Interfaces:**
- Consumes: Task 1 skeleton
- Produces: research writing tree; old sample notebooks may still coexist until Task 6

- [ ] **Step 1: Copy notebooks and manuscript**

```bash
A=/Users/etorebraga/Code/habitual-tax-debtor-research
PL=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia
rsync -a --exclude '.ipynb_checkpoints' "$A/notebooks/" "$PL/notebooks/"
rsync -a "$A/docs/manuscript/" "$PL/manuscript/"
cp "$A/CITATION.cff" /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/CITATION.cff
cp "$A/CITATION.cff" "$PL/references/CITATION.cff"
cp "$A/LICENSE" /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/LICENSE
```

- [ ] **Step 2: Write project CONTEXT/README**

`projects/litigancia/CONTEXT.md` — research question (litigância / contumaz / PEF↔lake), not lake glossary (that stays in pipeline CONTEXT).

`projects/litigancia/README.md` — how to open notebooks, kernel `ctf-research`, point to `pipelines/litigancia` for ops.

- [ ] **Step 3: Copy plan A into project docs**

```bash
cp /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/docs/planos/A_litigancia_protelatoria.md \
   /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia/docs/
```

- [ ] **Step 4: Grep notebooks for old repo path**

```bash
rg -n "habitual-tax-debtor-research" /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia || true
```

Replace with mono paths where needed (at least document in README if mass-edit is too large — prefer fixing cells that break import).

- [ ] **Step 5: Verify A notebooks still exist (archive intact)**

```bash
test -d /Users/etorebraga/Code/habitual-tax-debtor-research/notebooks/articles
test -d /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia/notebooks/articles
```

- [ ] **Step 6: Commit only if user asked**

---

### Task 5: Merge pyproject / .env and uv sync

**Files:**
- Modify: `pyproject.toml`, `.env`, `.env.example`, `.gitignore`
- Possibly create: `uv.lock` (regenerated)

**Interfaces:**
- Consumes: Task 3 code + existing `shared/cemepi_api`
- Produces: importable `config`, `scrapers`, `utils`, `cemepi_api`

- [ ] **Step 1: Write merged `pyproject.toml`**

Keep project name `cemepi-ctf-intel-fiscal`. Union dependencies from A and current mono. Include:

- A: altair, bs4, boto3, deltalake, duckdb, fake-useragent, juscraper git SSH, matplotlib, numpy, pandas, polars, pyarrow, python-bcb, python-dateutil, python-dotenv, requests, seaborn, tqdm, ijson, pydantic-ai-slim[anthropic], openpyxl, scikit-learn
- Mono extras if missing: statsmodels, ipykernel, nbformat, nbclient

Dev group from A: ipykernel, nbconvert, pytest, ruff, vl-convert-python  
nlp group from A: torch, transformers (optional; do not install by default)

Hatch:

```toml
[tool.hatch.build.targets.wheel]
packages = [
  "shared/cemepi_api",
  "pipelines/litigancia/src/config",
  "pipelines/litigancia/src/scrapers",
  "pipelines/litigancia/src/utils",
]

[tool.hatch.metadata]
allow-direct-references = true

[tool.uv]
package = true
override-dependencies = ["greenlet>=3.1.1"]
```

If hatch rejects nested paths, fall back to documented workaround: `tool.hatch.build.targets.wheel.force-include` or temporary `pyproject` packages path verification — **verify in Step 3**; do not leave broken lock.

- [ ] **Step 2: Merge `.env`**

Ensure keys present at mono root:

```bash
LAKE_ROOT=/Volumes/Meedi_Etore_HD1/CEMEPI/Coletas/gilson
COLLECT_ROOT=/Volumes/Meedi_Etore_HD1/CEMEPI/Coletas/gilson
DUMP_ROOT=/Volumes/Meedi_Etore_HD1/CEMEPI/dumps/cemepi_api
CEMEPI_API_BASE=http://143.107.158.76:8010
CEMEPI_API_TOKEN=   # keep existing token value; do not rotate
EXTRACAO_ANO=2026
EXTRACAO_MES=3
SAMPLE_LIMIT=100
```

`.env.example`: same keys, empty token.

- [ ] **Step 3: uv sync on HD venv**

```bash
cd /Users/etorebraga/Code/cemepi-ctf-intel-fiscal
# ensure .venv symlink still points to HD envs/ctf-research/.venv
uv sync
uv run python -c "import cemepi_api; import config.paths; import scrapers; print(config.paths.REPO_ROOT); print('OK')"
```

Expected: prints mono root path ending in `cemepi-ctf-intel-fiscal` and `OK`.

If juscraper SSH fails: stop and report; do not switch to HTTPS unless user asks.

- [ ] **Step 4: Commit only if user asked**

---

### Task 6: Thin projects B/C/D + remove conflicting stubs + dump samples

**Files:**
- Modify/create: `projects/{monitoramento,macro,garantias}/{README,CONTEXT}.md`
- Move or keep dump sample notebooks under each `projects/*/notebooks/`
- Delete: obsolete root-level duplicate notebooks under `notebooks/playground` if superseded; delete old stub-only litigancia samples that collide with real A notebooks **only if** names conflict

**Interfaces:**
- Consumes: Task 4–5
- Produces: uniform thin fronts; litigancia has real A content

- [ ] **Step 1: Ensure B/C/D CONTEXT/README exist (thin)**

Each states: research question placeholder, points to `docs/planos/B|C|D_*.md`, uses shared env.

- [ ] **Step 2: Place dump sample notebooks**

Keep existing working samples under:

- `projects/monitoramento/notebooks/`
- `projects/macro/notebooks/`
- `projects/garantias/notebooks/`
- `projects/litigancia/notebooks/dump_samples/` (move `01_dump_ping_sample.ipynb` etc. into a `dump_samples/` subfolder so they do not mix with A `articles/` / `data_paper/`)

```bash
PL=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia
mkdir -p "$PL/notebooks/dump_samples"
for f in 01_dump_ping_sample.ipynb 02_pef_sample.ipynb 03_features_sample.ipynb; do
  if [ -f "$PL/notebooks/$f" ]; then mv "$PL/notebooks/$f" "$PL/notebooks/dump_samples/"; fi
done
```

- [ ] **Step 3: Copy planos B/C/D into project docs**

```bash
MONO=/Users/etorebraga/Code/cemepi-ctf-intel-fiscal
cp "$MONO/docs/planos/B_monitoramento_arrecadacao.md" "$MONO/projects/monitoramento/docs/"
cp "$MONO/docs/planos/C_divida_macro.md" "$MONO/projects/macro/docs/"
cp "$MONO/docs/planos/D_garantias.md" "$MONO/projects/garantias/docs/"
```

- [ ] **Step 4: Verify tree MECE**

```bash
ls /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia
ls /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/pipelines/litigancia
test ! -f /Users/etorebraga/Code/cemepi-ctf-intel-fiscal/projects/litigancia/docs/SOURCE_OF_TRUTH.md
```

Expected: SoT only under pipelines.

- [ ] **Step 5: Commit only if user asked**

---

### Task 7: Smoke tests (acceptance)

**Files:**
- Test: `pipelines/litigancia/tests/` (subset), dump sample notebook execute
- No production scrape runs

**Interfaces:**
- Consumes: Task 5 env
- Produces: pass/fail evidence for acceptance checklist

- [ ] **Step 1: Pytest subset (no network scrape)**

```bash
cd /Users/etorebraga/Code/cemepi-ctf-intel-fiscal
uv run pytest pipelines/litigancia/tests/test_script_paths.py pipelines/litigancia/tests/test_pf_redaction.py -q
```

Expected: PASS (or fix path assumptions in those tests if they hardcode A repo root — update to `REPO_ROOT` / `PIPELINE_ROOT`).

- [ ] **Step 2: Import + paths smoke**

```bash
uv run python - <<'PY'
from pathlib import Path
import config.paths as p
import cemepi_api
assert p.REPO_ROOT.name == "cemepi-ctf-intel-fiscal"
assert (p.REPO_ROOT / ".env").exists()
print("REPO_ROOT", p.REPO_ROOT)
print("LAKE_ROOT", p.LAKE_ROOT)
print("cemepi_api", cemepi_api)
PY
```

- [ ] **Step 3: Execute one dump sample notebook**

```bash
cd /Users/etorebraga/Code/cemepi-ctf-intel-fiscal
uv run python - <<'PY'
from pathlib import Path
import nbformat
from nbclient import NotebookClient
p = Path("projects/litigancia/notebooks/dump_samples/01_dump_ping_sample.ipynb")
nb = nbformat.read(p, as_version=4)
NotebookClient(nb, timeout=120, kernel_name="ctf-research",
               resources={"metadata": {"path": str(Path('.').resolve())}}).execute()
nbformat.write(nb, p)
print("notebook OK")
PY
```

If kernel missing: `uv run python -m ipykernel install --user --name ctf-research --display-name "ctf-research (HD)"`.

- [ ] **Step 4: Confirm A frozen untouched**

```bash
test -d /Users/etorebraga/Code/habitual-tax-debtor-research/.git
test ! -L /Users/etorebraga/Code/habitual-tax-debtor-research
# optional: git -C A status should be clean if we never wrote
git -C /Users/etorebraga/Code/habitual-tax-debtor-research status --short | head
```

Expected: no modifications from this migration.

- [ ] **Step 5: Tick acceptance boxes in the spec file** (edit checkboxes to `[x]` where proven)

- [ ] **Step 6: Commit only if user asked**

---

### Task 8: Archive notice on A (read-only documentation only)

**Files:**
- Create (optional, **only if user confirms writing a single README note into A**): otherwise create notice **only in mono**: `docs/ARCHIVE_A.md`

**Default (no write to A):**

- [ ] **Step 1: Write `docs/ARCHIVE_A.md`**

```markdown
# Archive: habitual-tax-debtor-research

Frozen source tree at `/Users/etorebraga/Code/habitual-tax-debtor-research`.
Working copy: this monorepo (`pipelines/litigancia` + `projects/litigancia`).
Do not continue development in the archive.
```

- [ ] **Step 2: Link from root README**

One line under Setup pointing to `docs/ARCHIVE_A.md`.

- [ ] **Step 3: Done announcement to user** with path checklist

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|---|---|
| §1 tree | T1, T6 |
| §2 mapping | T2–T4 |
| §3 docs floors | T1, T2, T4, T6 |
| §4 env/imports/paths parents | T3 Step 2, T5, T7 |
| Freeze A | T7 Step 4, T8 (no write default) |
| Slice order docs→pipeline→notebooks→shared | T2→T3→T4→T5 |
| Smoke pytest + notebook | T7 |
| No placeholders / exact commands | filled above |
| Hatch path verify | T5 Step 3 |

**Gap closed in plan:** `paths.py` `parents[2]` → `parents[4]` for mono `.env` (not explicit enough in spec; required for acceptance).

