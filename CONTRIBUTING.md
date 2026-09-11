# Contributing

## Setup
- Clone this monorepo; use the HD-backed `.venv` → `envs/ctf-research` (kernel `ctf-research`).
- Copy `.env.example` → `.env`; never commit secrets.
- `uv sync` from repo root after dependency changes.

## Where to put work
| Kind | Location |
|---|---|
| Lake/ops code, SoT, schema | `pipelines/litigancia/` |
| Front research, notebooks, manuscript | `projects/<front>/` |
| Dump client | `shared/cemepi_api/` |
| Cross-front portfolio docs | `docs/` |

## Docs rules
- Three floors (portfolio / pipeline ops / project research) — see `AGENTS.md` and `docs/architecture.md`.
- SoT wins on ops; CONTEXT wins on term definitions.
- Keep planos under `docs/planos/` as received from CTF Drive.

## Git hygiene
- Do not stage `.env`, tokens, PII, or lake/dump data.
- Commits only when the maintainer asks.
- Do not write to the frozen archive `habitual-tax-debtor-research`.

## Attribution
Author owns experiments and conclusions; AI assist for formatting is fine when disclosed per `AGENTS.md`.
