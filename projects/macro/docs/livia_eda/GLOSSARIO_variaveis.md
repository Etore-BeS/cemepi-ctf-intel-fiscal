# Glossário de variáveis — ponto de vista tributário / cobrança

Texto pensado para leitura de **procuradora / analista de dívida ativa**, não de engenharia de banco.  
Quando um nome técnico aparece (ex.: `ID_DEBITO`), a coluna da direita traduz o papel jurídico-operacional.

---

## 1. Universo da base

| Nome na base / no notebook | O que é, em linguagem tributária |
|----------------------------|----------------------------------|
| **Dívida inscrita** | Créditos já encaminhados à inscrição em dívida ativa do Estado (recorte deste dump PGE/CEMEPI). |
| **CDA** | Certidão de Dívida Ativa — documento que formaliza o crédito inscrito. Neste EDA usamos o **débito** (`ID_DEBITO`) como unidade analítica ligada a essa lógica. |
| **Extração / foto** | Momento em que a base foi “fotografada” (`ANO_EXTRACAO`). O mesmo débito pode aparecer em vários anos de foto com estoque atualizado. |
| **GARE** | Guia de arrecadação / registro de pagamento na frente de **arrecadação** da base — o que **entrou** ligado ao débito, não o que “deveria” entrar. |

---

## 2. Tabelas principais (o que cada uma responde)

| Tabela | Pergunta que ela responde | Atenção |
|--------|---------------------------|---------|
| **DEBITO** | Qual o estoque inscrito, de que tipo, de quem, desde quando? | É a tabela gigante (~800 milhões de linhas: débito × fotos). |
| **ARRECADACAO** | Quanto foi arrecadado / creditado ao débito, e quando? | Uma linha costuma ser um evento de receita; há muitos débitos **sem** linha aqui no ano. |
| **AJUIZAMENTO** | O crédito foi ajuizado? Em que comarca / status? | Satélite processual do débito. |
| **DEBITO_RECEITA** | Ligação débito ↔ receita / classificação de receita | Apoio; não é o painel principal deste EDA. |
| **SOLICITACAOPARCELAMENTO** | Houve pedido / regra de parcelamento? | Importante para política de parcelamento; **não** modelado na etapa 6 ainda. |
| **STATUS_PROTESTO** | Situação de protesto | Satélite; não entra nos três modelos atuais. |
| **FATURAMENTO_MASCARADO** | Sinal econômico do contribuinte (CNPJ), mascarado | Não entra no painel ICMS desta versão. |
| **MACRO** (macro.duckdb) | Séries macro (ex. ambiente econômico) | Frente “dívida × macro”; não é o núcleo deste EDA para a reunião. |

---

## 3. Identificadores e datas do débito

| Variável | Significado tributário |
|----------|------------------------|
| **`ID_DEBITO`** | Chave do débito inscrito. É a “pessoa” do painel: acompanhamos o **mesmo crédito** ao longo dos anos. |
| **`TIPO_DEBITO`** | Rótulo cadastral do crédito (ex.: “ICMS Declarado”, “IPVA”, “Taxa Judiciária”). Texto livre-ish da origem — por isso fazemos o **de-para** para ICMS / IPVA / TAXAS. |
| **`DATA_INSCRICAO`** | Data de inscrição em dívida ativa (quando válida). Serve para medir **idade da CDA/débito**. |
| **`ANO_EXTRACAO` / `ano` no painel** | Ano da foto do estoque. No painel ICMS, cada linha é **um débito em um ano**. |
| **`VALOR_SEM_HONORARIOS`** | Valor do débito **sem** honorários advocatícios/administrativos no recorte usado. Usado como estoque “de principal/encargos tipicamente de crédito”, conforme a coluna da base. |
| **`CNPJ_DEVEDOR` / `CPF_DEVEDOR`** | Identificação do sujeito passivo (quando preenchida). Muitos nulos — comum em bases mistas PF/PJ. |
| **`SITUACAO_DEBITO`**, **`STATUS_AJUIZAMENTO_DEBITO`** | Situação cadastral / processual do débito. Úteis em EDA futura; não são o eixo da taxa GARE÷estoque nesta entrega. |

---

## 4. Buckets da etapa 2 (agrupamento tributário)

| Bucket | Regra usada neste notebook | Leitura |
|--------|----------------------------|---------|
| **ICMS** | `TIPO_DEBITO` contém a palavra ICMS (ex.: declarado, autuação) | Família do imposto sobre circulação — foco do painel e dos modelos. |
| **IPVA** | Contém IPVA | Tributo sobre propriedade de veículo — em geral **muitas** CDAs de valor unitário menor. |
| **TAXAS** | Nem ICMS nem IPVA no texto | Taxas e outros créditos (ex. taxa judiciária) — grupo residual deste de-para. |

Colunas do CSV `tipo_debito_buckets.csv`:

| Coluna | Significado |
|--------|-------------|
| `tipo_bruto` | Texto original de `TIPO_DEBITO`. |
| `bucket` | ICMS, IPVA ou TAXAS. |
| `regra` | Por que caiu naquele bucket. |
| `n_linhas` | Quantas linhas (débito×foto) têm aquele tipo. |
| `n_id` | Quantos **débitos distintos** (`ID_DEBITO`). |
| `soma_valor` | Soma dos valores de estoque nas linhas (cuidado: soma ao longo de fotos — **não** é “estoque único atual” sem consolidar). |

---

## 5. Descritiva (etapa 3)

| Ideia / coluna | Significado tributário |
|----------------|------------------------|
| **% quantidade (`pct_qtd`)** | Participação do bucket no **número** de débitos (ou linhas, conforme a tabela). Responde: “onde está o volume de CDAs?” |
| **% valor (`pct_valor`)** | Participação do bucket no **valor** somado. Responde: “onde está o dinheiro?” |
| **Contraste IPVA alto em qtd / baixo em valor** | Muitas guias/CDAs “baratas”; ICMS costuma ser o inverso (menos linhas, mais valor). |
| **`idade_media_anos` / `idade_mediana_anos`** | Anos desde `DATA_INSCRICAO` até a foto. CDA **mais velha** ≠ automaticamente “pior”, mas é proxy de tempo na cobrança. |
| **`n_com_data_valida`** | Quantas linhas tinham data de inscrição aproveitável (datas inválidas foram ignoradas com conversão segura). |

---

## 6. Painel ICMS (etapa 4) — variáveis do núcleo analítico

Cada linha = **um `ID_DEBITO` ICMS × um `ano`**.

| Variável | Fórmula / origem | Leitura tributária |
|----------|------------------|--------------------|
| **`estoque_medio`** | Média de `VALOR_SEM_HONORARIOS` do débito naquele ano de extração | Quanto a CDA “pesa” na foto daquele ano. |
| **`data_inscricao`** | Máxima data de inscrição válida vista naquele agrupamento | Âncora para idade. |
| **`arrecadado`** | Soma de `VALOR_RECEITA` (e correlatos usados no SQL) em `ARRECADACAO` no mesmo débito e ano; **0** se não houver GARE | O que **entrou** naquele ano para aquele débito. |
| **`taxa`** | `arrecadado / estoque_medio` se estoque > 0; senão nulo; com arrecadação zero → taxa 0 | **Índice de realização no ano**: quanto do estoque da foto foi coberto por arrecadação registrada. **Não** é alíquota. |
| **Zeros de taxa** | Mantidos de propósito | Débito sem pagamento no ano continua na análise (carteira “parada” no ano). |

---

## 7. Idade e lags (etapa 5)

| Variável | Significado |
|----------|-------------|
| **`idade_anos`** | `ano − ano(data_inscricao)`. Tempo desde a inscrição até a foto. |
| **`taxa_l1` / `taxa_l2` / `taxa_l3`** | Taxa do **mesmo débito** 1, 2 ou 3 anos antes. Mede se quem arrecadou (relativo ao estoque) no passado tende a repetir. |
| **`estoque_l1`** | Estoque do mesmo débito no ano anterior. Em dívida inscrita costuma ser **muito estável**. |
| **Pearson** | Associação **linear** (−1 a +1). |
| **Spearman** | Associação pela **ordem** (postos). Útil quando há outliers de taxa (poucos débitos com taxa absurda por estoque pequeno). |

Atenção: correlação alta entre **`taxa` e `arrecadado`** é em parte **mecânica** (a taxa é feita com a arrecadação). Não interprete como “achado” de política.

---

## 8. Modelos (etapa 6)

| Nome | O que estima | Variáveis do lado direito (nesta versão) |
|------|--------------|------------------------------------------|
| **Modelo 1 — Logit** | Probabilidade de **haver alguma arrecadação** no ano (`y_pago = 1` se `arrecadado > 0`) | `taxa_l1`, `log_estoque`, `idade_anos`, dummies de ano |
| **Modelo 2 — MQO da taxa** | Valor esperado da **taxa** (realização relativa) | As mesmas |
| **Modelo 3 — Série agregada** | Soma de estoque e arrecadação **da carteira ICMS** por ano; AR(1) ilustrativo na taxa da carteira | Só o tempo (poucos anos — leitura qualitativa) |

Variáveis auxiliares:

| Variável | Significado |
|----------|-------------|
| **`y_pago`** | Indicador 0/1 de pagamento no ano (qualquer valor > 0). |
| **`log_estoque`** | `ln(1 + estoque)`. Controla escala: débitos de R$ 1 mil vs R$ 10 milhões. |
| **`C(ano)`** | Efeitos fixos de ano: absorve choques comuns (calendário, pandemia, mudanças operacionais). |
| **`taxa_carteira`** | `soma(arrecadação) / soma(estoque)` no ano — realização da **carteira**, não do débito médio. |
| **`share_pago`** | Fração de débitos com alguma arrecadação no ano. |
| **`taxa_media_id`** | Média simples das taxas por débito (pode ser puxada por outliers). |

**Amostra dos modelos 1–2:** 1 milhão de linhas com lag disponível (semente de amostragem no DuckDB). Serve para caber na memória; a série agregada usa **todos** os anos do painel.

---

## 9. Limitações que importam na reunião

1. **Ano 2026** na série tende a estar **incompleto** (extração parcial) — não compare “queda” de 2026 como resultado de gestão sem checar a data da foto.  
2. Soma de estoque ao longo de tipos/fotos **não** é automaticamente o estoque contábil único da PGE.  
3. Parcelamento, protesto, ajuizamento e faturamento **ainda não** entram como regressores nesta versão.  
4. Associação ≠ causalidade (ex.: idade mais alta e menor taxa).
