# Dívida ativa × macroeconomia

Pergunta de pesquisa: como a dinâmica do **estoque inscrito** e da arrecadação se relaciona com séries macro (ex.: Selic, IPCA), depois de alinhado o painel mensal do dump — sem reivindicar causalidade prematura nem cobrir dívida não inscrita (ausente do dump).

## Escopo
- Reutilizar o painel mensal da frente de monitoramento
- Incorporar séries BCB (SGS) como controles / co-movimento
- Métodos econométricos clássicos (cointegração / VAR) só após o painel estável

## Layout
```text
notebooks/   # samples painel + BCB + correlação
docs/
references/
```

## Descrição detalhada
[docs/C_divida_macro.md](docs/C_divida_macro.md)

## Modelo de dados (API / dump)

Notebook de playground com inventário `/meta`, argumento fact vs satélite e diagrama ER (Plotly + Mermaid/HTML + PNG):

[`notebooks/playground/modelo_dados_api_er_v0.ipynb`](notebooks/playground/modelo_dados_api_er_v0.ipynb)

Figuras estáticas: `output/figures/macro_er_*.png` e `macro_er_mermaid_v0.html`.

**Leitura rápida:** fact de estoque = `debito` (grão `ID_DEBITO` × foto `ANO_EXTRACAO`/`MES_EXTRACAO`); fact de pagamento = `arrecadacao`; satélites em `ID_DEBITO`; `faturamento` em `CNPJ`; BCB/IBGE fora do DB. Freeze `extracao=2026-03`.

## EDA completo (PGE / Profa. Lívia)

Notebook único, etapa a etapa: [`notebooks/EDA_completo.ipynb`](notebooks/EDA_completo.ipynb).

Documentação em linguagem tributária (LEIA-ME, glossário de variáveis, resultados):

[`docs/livia_eda/LEIA-ME.md`](docs/livia_eda/LEIA-ME.md)

