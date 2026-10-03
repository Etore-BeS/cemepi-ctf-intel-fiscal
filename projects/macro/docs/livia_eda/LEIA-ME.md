# EDA da dívida inscrita (PGE) — guia para leitura

**Para:** Profa. Lívia Maria Lopes Stanzani  
**Por:** Étore Braga e Santos (PPGT/FT-Unicamp · CEMEPI)  
**Notebook:** [`../../notebooks/EDA_completo.ipynb`](../../notebooks/EDA_completo.ipynb)  
**Objetivo:** entender, com a base completa da dívida inscrita, **o que há no estoque**, **como ICMS / IPVA / taxas se diferenciam**, e **o que a arrecadação (GARE) faz em relação ao estoque no ICMS** — sem afirmar causalidade de política.

Leia nesta ordem:

1. Este LEIA-ME (mapa do notebook)
2. [`GLOSSARIO_variaveis.md`](GLOSSARIO_variaveis.md) (o que cada coluna/conceito significa no mundo tributário)
3. [`RESULTADOS.md`](RESULTADOS.md) (o que os números dizem em linguagem de cobrança)
4. Os CSV em [`dados/`](dados/) (tabelas leves; os arquivos grandes de parquet ficam só na máquina de análise)

---

## O que este trabalho **não** é

- Não é previsão oficial de receita nem meta de cobrança.
- Não substitui relatório contábil / extrato da CDA.
- Não prova que “idade causa baixa arrecadação” — só descreve associações na base.

---

## Como o notebook está organizado

| Seção | Pergunta em linguagem tributária | Saída principal |
|------|-----------------------------------|-----------------|
| **0. Setup** | A base está acessível? | Confirmação de pasta dos bancos |
| **1. Inventário** | O que existe na base (tabelas, volumes, buracos)? | Resumo de tamanhos e nulos |
| **2. De-para de tipo** | Cada `TIPO_DEBITO` é ICMS, IPVA ou TAXAS? | `tipo_debito_buckets.csv` |
| **3. Descritiva** | Em quantidade vs em valor, quem pesa? CDAs mais velhas em qual grupo? | % qtd / % valor; idade média/mediana |
| **4. Painel ICMS** | Para cada débito ICMS em cada ano: estoque, o que entrou em GARE, taxa | Painel anual (arquivo grande local) |
| **5. Correlações e lags** | A arrecadação relativa “se repete” de um ano para o outro? | Correlações + persistência |
| **6. Três modelos** | Chance de pagar algo; tamanho da taxa; visão da carteira agregada | Coeficientes + série anual |

---

## Conceitos-chave (resumo de uma tela)

- **Dívida inscrita / CDA:** crédito tributário (e correlatos) já inscrito em dívida ativa, com identificação (`ID_DEBITO`).
- **Estoque:** valor do débito na “foto” da base naquele ano de extração (sem honorários, no recorte usado).
- **Arrecadação (GARE):** valores efetivamente pagos/registrados como receita ligada ao débito naquele ano.
- **Taxa (neste estudo):** `arrecadação do ano ÷ estoque médio do ano` naquele débito.  
  - Taxa **0** = naquele ano não houve arrecadação ligada ao débito (ou estoque inválido).  
  - Taxa **não** é alíquota de ICMS nem percentual de parcelamento oficial.
- **Bucket ICMS / IPVA / TAXAS:** agrupamento dos textos de `TIPO_DEBITO` para comparar famílias de crédito (ver glossário).

---

## Como abrir o notebook no GitHub

1. Abra o repositório e vá em `projects/macro/notebooks/EDA_completo.ipynb`.
2. No GitHub você **vê o código e os textos**; para **reexecutar** precisa da base DuckDB no disco (Meedi) — isso não vai no GitHub (volume grande e dado sensível).
3. As tabelas-resumo que cabem no Git estão em `projects/macro/docs/livia_eda/dados/`.

---

## Arquivos deste pacote

| Arquivo | Conteúdo |
|---------|----------|
| `LEIA-ME.md` | Este guia |
| `GLOSSARIO_variaveis.md` | Variáveis e tabelas, uma a uma |
| `RESULTADOS.md` | Interpretação tributária dos achados |
| `dados/*.csv` | Números leves das etapas 1–6 |

Parquets (`painel_icms_*.parquet`) e HTMLs de gráfico **não** sobem ao GitHub (pesados). Os gráficos principais podem ser regenerados rodando o notebook localmente.
