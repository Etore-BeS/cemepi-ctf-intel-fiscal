# Monitoramento e previsão de arrecadação (Plano B)

Thin research front for CTF Plano B. Shared mono env / kernel (`ctf-research`); lake/ops stay in `pipelines/litigancia/`.

## Layout

```
projects/monitoramento/
├── notebooks/     ← dump samples (01_ping_series, 02_painel_mensal, 03_baseline_naive)
├── docs/          ← copy of docs/planos/B_monitoramento_arrecadacao.md
├── references/
└── output/
```

## Plano

Canonical plan: [`docs/planos/B_monitoramento_arrecadacao.md`](../../docs/planos/B_monitoramento_arrecadacao.md) (copy in `docs/`).

## Open notebooks

1. From mono root: `uv run jupyter lab` (or VS Code / Cursor).
2. Kernel **`ctf-research`** (HD env via root `.venv`).
3. Dump paths / token: root `.env` (`DUMP_ROOT`, `CEMEPI_API_TOKEN`, `EXTRACAO_*`).
