# Monitoramento e previsão de arrecadação

Pergunta de pesquisa: com o dump mensal de estoque inscrito e arrecadação (GARE), qual horizonte e quais baselines permitem prever arrecadação de forma reproduzível (*walk-forward*)?

## Escopo
- Painel mensal estoque × arrecadação (`extracao` congelada)
- Baselines e modelos leves (naive, sazonal, OLS/Ridge, SARIMAX, HGB, Prophet, MLP-2)
- Fatia granular: janelas mensais de GARE agregadas a **diário** para treino/avaliação

## Layout
```text
# (archived) src/arrecadacao_forecast/ → .local/archive/arrecadacao_forecast_pkg/
# models+protocol inline in articles/arrecadacao_forecast_paper_v1.ipynb
notebooks/
  estoque_arrecadacao_eda_forecast_v0.ipynb
  articles/arrecadacao_forecast_paper_v1.ipynb
  gare_janelas_mensais_v0.ipynb
  *_sample.ipynb
manuscript/arrecadacao_forecast_v1/  # NeurIPS draft (sources + figures; build PDF gitignored)
output/figures/   # PNGs estáticos (Plotly + Kaleido) para preview no GitHub
docs/             # descrição da frente + galeria
references/
```

## Figuras
Ver [docs/figures_gallery.md](docs/figures_gallery.md).

## Descrição detalhada
[docs/B_monitoramento_arrecadacao.md](docs/B_monitoramento_arrecadacao.md)
