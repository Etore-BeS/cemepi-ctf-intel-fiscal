# Manuscript — Scientific Data Data Descriptor

The manuscript is written in **LaTeX** from the start, per [Scientific Data submission guidelines](https://www.nature.com/sdata/publish/submission-guidelines#latex):

- No official journal template — mandated section headings only
- **Single standalone `.tex` file** for submission (references embedded, no `.bib`)
- Computer Modern / standard fonts recommended

## Files

| File | Purpose |
|---|---|
| [`data_descriptor.tex`](data_descriptor.tex) | **Source of truth** — full manuscript draft |
| [`data_descriptor_outline.md`](data_descriptor_outline.md) | Deprecated scaffold; kept for checklist notes only |
| [`Makefile`](Makefile) | Local PDF build |

## Build PDF locally

**`pdflatex` is not a Python package.** Do not run `uv add pdflatex` — that installs an unrelated PyPI package and conflicts with `juscraper`.

You need a **system TeX engine**. Pick one:

### Option A — BasicTeX (recommended, smaller)

```bash
# bash
brew install --cask basictex
export PATH="/Library/TeX/texbin:$PATH"
make -C docs/manuscript
```

```fish
# fish
brew install --cask basictex
fish_add_path /Library/TeX/texbin
make -C docs/manuscript
```

After installing BasicTeX, open a **new terminal** so `pdflatex` is on your `PATH`.

### Option B — Tectonic (single binary, no full TeX Live)

```bash
# bash / fish
brew install tectonic
make -C docs/manuscript
```

The Makefile uses `pdflatex` when available, otherwise falls back to `tectonic`.

Output: `docs/manuscript/data_descriptor.pdf`

## Before submission

1. Run data paper notebooks to generate figures under `notebooks/data_paper/figures/`
2. Update author affiliations, email, funding, acknowledgements in `data_descriptor.tex`
3. Add HuggingFace URL and Zenodo DOI to Data Availability
4. Verify the `.tex` compiles **standalone** (no missing files except figures)
5. Upload `data_descriptor.tex` as the **Article** file in the submission system

## Section mapping

| LaTeX section | Notebook artefact |
|---|---|
| Background \& Summary | `01_dataset_overview.ipynb` |
| Methods | `03_pipeline_figures.ipynb` |
| Data Records | `docs/data_dictionary.md`, `docs/schema/` |
| Technical Validation | `02_quality_validation.ipynb` |
| Usage Notes | README + data dictionary |

## Submission checklist

- [ ] Dataset on HuggingFace with dataset card
- [ ] Zenodo DOI in `data_descriptor.tex` and `CITATION.cff`
- [ ] All three `notebooks/data_paper/` notebooks executed
- [ ] `data_descriptor.tex` compiles standalone to PDF
- [ ] Co-authors and affiliations confirmed
- [ ] Cover letter drafted
