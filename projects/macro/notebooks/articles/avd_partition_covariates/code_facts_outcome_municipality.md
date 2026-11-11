# Code facts: outcome and municipality (no new method choice)

This note records how the repository code treats the dependent variable and
municipality today. It does not propose a new specification.

## Dependent variable in the origin logit (`avd_origin_models.py`)

- Name in the frame: `paid_flag`.
- Construction: for each certificate in the December 2025 legal-entity ICMS
  snapshot, sum `ARRECADACAO.VALOR_RECEITA` with
  `DATA_ARRECADACAO_GARE` in `[2016-01-01, 2026-01-01)`.
- Coding: `1` if the sum is greater than zero, else `0`.
- The manuscript text describes this as any GARE payment through 31/12/2025
  for the payment window used by the script (`PAY_END = 2026-01-01`).
- The firm key (`root`) is used only for clustered covariance. It is never written.

## Municipality in the current stack

- `avd_origin_models.py` (tables that feed the manuscript origin AME / within /
  recovery contrasts): the design has **no** municipality term. Controls are
  log stock, debt age, firm age, missing-opening, enrolment year, CNAE section
  and porte.
- Lab section 4.5 / 4.7 (`lab/eda/2026-11-04_diagnostico_linearidade` and
  `lab/eda/2026-11-04_inferencia_avd`): `municipality` is a required column of
  the estimation frame. For **declared** debt the selected complement is a
  binomial mixed logit with a **random intercept by municipality** on a
  cluster sample of municipalities. For **assessed** debt the printed SPEC
  sets `random_intercept` to `None`. The moment ICC screen that selected that
  complement is stored in `infer.SPEC` and is not re-estimated here.
- Enrichment source for a municipality field when loaded: RFB
  `cnpj_enriquecido` (`municipio_ibge` / name / UF), register `2026-03`.

## Q1–Q8 (IMP-23)

The open-point defaults live in `lab/eda/_impairment.PgeParams` and are printed
by `assumptions_text`. Guaranteed class: always empty
(no guarantee table in the data; collectable share is understated).

## Task 3

Final logits by the partition Étore chooses are not run in this module.
See `task3/README.md`.
