# Monitoramento e previsão de arrecadação

## Pergunta de pesquisa

Com o dump mensal de estoque + GARE (`extracao` congelada), qual horizonte e quais baselines permitem prever arrecadação de forma **reproduzível** (*walk-forward*)?

Notebooks: `notebooks/estoque_arrecadacao_eda_forecast_v0.ipynb` (EDA + M0–M5 walk-forward MAPE/SMAPE/RMSE); `notebooks/gare_janelas_mensais_v0.ipynb` (janelas GARE — Fase B).

## Escopo (frente B)

- Séries: estoque inscrito (`/v1/debito/estoque`) e arrecadação (`/v1/arrecadacao/serie`).
- Dump estático (não realtime). Análise no Parquet local.
- Fora de escopo neste artefato: litigância/PEF/lake join; macro BCB como tema principal; garantias.

## Plano / premissa

- Plano B: `docs/planos/B_monitoramento_arrecadacao.md` (cópia em `docs/`).
- Premissa dump: `docs/planos/00_premissa_dump.md` / `docs/api-dump/`.

## Shared env

Mono root `.env` + kernel `ctf-research`. Prefer `shared/cemepi_api` after `uv sync`.
