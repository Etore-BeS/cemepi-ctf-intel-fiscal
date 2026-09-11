# Dívida ativa × fatores macroeconômicos (Plano C)

Thin research front for CTF Plano C. Shared mono env / kernel (`ctf-research`).

## Layout

```
projects/macro/
├── notebooks/     ← dump samples (01_painel_from_B, 02_macro_bcb, 03_corr)
├── docs/          ← copy of docs/planos/C_divida_macro.md
├── references/
└── output/
```

## Plano

Canonical plan: [`docs/planos/C_divida_macro.md`](../../docs/planos/C_divida_macro.md) (copy in `docs/`).

## Open notebooks

1. From mono root: `uv run jupyter lab` (or VS Code / Cursor).
2. Kernel **`ctf-research`**.
3. Dump + BCB/IBGE pulls as documented in the plan; freeze `extracao` for dump panels.
