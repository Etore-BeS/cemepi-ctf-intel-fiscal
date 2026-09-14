# Garantias em execução fiscal

**Para:** Profa. Lívia e demais leitores não técnicos do CTF  
**Atualizado:** 14/09/2026 · dados congelados em **março/2026** (`extracao=2026-03`)

---

## O que este projeto pergunta?

Na cobrança da dívida ativa, o contribuinte pode oferecer **garantias** (por exemplo carta-fiança, seguro-garantia, imóvel ou depósito). A pergunta de pesquisa é: *o tipo de garantia ajuda a entender quem atrasa ou “empurra” o processo (risco protelatório) e quem acaba pagando?*

Para responder essa pergunta com rigor, o ideal é saber, para cada dívida, **qual garantia existe**, de que tipo, com que valor e por quanto tempo. Enquanto isso não estiver na base de pesquisa, o trabalho segue com **proxies honestos** de cobrança (parcelamento, protesto, ajuizamento, estoque e arrecadação) — úteis, mas **não** equivalentes ao tipo de garantia.

---

## O que temos — e o que não temos

### Não temos (lacuna central)

A base de pesquisa da Inteligência Fiscal (**API/dump**) **não traz nenhuma tabela de garantia**. Confirmamos isso de três formas:

1. a lista oficial de tabelas da API não inclui `garantia`;
2. pedir a tabela retorna “não encontrado”;
3. a documentação técnica da API não menciona o tema.

Sem essa tabela, **não é possível** montar um “score de tipo de garantia” honesto. Fazer de conta que temos o dado invalidaria o estudo.

### Temos (dados úteis como *proxies* de cobrança)

Já existem trilhas fortes de **como a cobrança caminha**:

| Base | Em linguagem simples | Ordem de grandeza |
|------|----------------------|-------------------|
| Parcelamento | Pedidos de parcelar a dívida, se foram pagos, seguem em andamento ou foram **rompidos** | ~2,7 milhões de pedidos |
| Protesto | Envio a cartório, status e valor protestado | ~19 milhões de registros |
| Ajuizamento | Dívidas que viraram processo (PEF), data e comarca | ~1,9 milhão |
| Débito (foto mar/2026) | Estoque inscrito: tipo, valor, se já foi ajuizado | ~8,8 milhões de dívidas na foto |
| Arrecadação (GARE) | Pagamentos registrados | ~23 milhões de pagamentos |

Esses dados descrevem **perfil do devedor e caminho da cobrança**. São insumos futuros para um modelo de garantia — mas **não substituem** o tipo de garantia.

---

## O que já dá para fazer agora

1. **Deixar documentada a lacuna** — para conversa com o time de dados / ATTUS, quando fizer sentido.  
2. **Mapear as trilhas de cobrança** (parcelamento rompido, protesto, ajuizamento, valores).  
3. **Preparar a pergunta científica** sem inventar a feature que falta.  
4. Se/quando a tabela de garantia existir: caracterizar por tipo, cruzar com recuperação (arrecadação) e só então falar em score/hipóteses do documento CTF.

Números ilustrativos já medidos (população na API):

- Parcelamentos **rompidos pelo contribuinte**: ~707 mil pedidos.  
- Na foto de estoque (mar/2026): ~2,14 milhões de débitos **já ajuizados**; ~6,68 milhões **liberados para ajuizar**.  
- Ticket médio de GARE é maior entre débitos ajuizados do que entre os ainda não ajuizados (recuperação como *resultado*, não como tipo de garantia).

Detalhe técnico e figuras: pasta `notebooks/playground/` e `output/figures/`.

---

## Estrutura de dados de cada tabela que vamos usar

Abaixo, em linguagem simples: o que **uma linha** representa, as colunas principais (nomes como na API), um **exemplo real anonimizado** (IDs/CPF/nome/CDA/PEF substituídos por hash) e a ordem de grandeza já medida. Freeze: **2026-03**. Exemplos vêm de amostras locais `n=2000` em `data/extracao=2026-03/samples/garantias_eda_*.parquet`.

### 1. Parcelamento (`parcelamento`)

**Uma linha =** um pedido de parcelamento ligado a um débito (pode haver vários pedidos por dívida).

| Coluna | Tipo (aprox.) | Significado |
|--------|---------------|-------------|
| `ID_SOLICITACAOPARCELAMENTO` | número | Identificador do pedido |
| `ID_DEBITO` | número | Débito a que o pedido se refere |
| `DATA_SOLICITACAO` | data | Quando o contribuinte pediu |
| `STATUS_PARCELAMENTO` | texto | Ex.: Pago, Em andamento, Rompido pelo contribuinte |
| `DATA_ROMPIMENTO` | data (pode ser vazia) | Preenchida quando o acordo foi rompido |
| `QTD_PARCELAS` | número | Quantas parcelas no pedido |
| `TIPO_PARCELAMENTO` | texto | Modalidade (ICMS Resolução, PPD, PEP, IPVA…) |
| `VIGENCIA_PARCELAMENTO` / `REGRA_PARCELAMENTO` | texto | Norma / regra aplicável |
| `DT_INSCRICAO` | data | Inscrição do débito (no dump) |

**Exemplo (anonimizado):**

| Campo | Valor |
|-------|-------|
| ID do pedido / do débito | `[ID_HASH_…]` |
| Data da solicitação | 2021-09-17 |
| Status | Rompido pelo contribuinte |
| Data do rompimento | 2022-09-28 |
| Qtd. parcelas | 43 |
| Tipo | ICMS Resolução SF/PGE- 01 |
| Vigência / regra | ICMS_SF_01 · Regra Geral ICMS Resolução SF/PGE- 01 2018 |

**Ordem de grandeza:** ~2,72 milhões de pedidos. Na população: Pago ~847 mil; Rompido pelo contribuinte ~707 mil; Não celebrado ~651 mil; Em andamento ~384 mil.

### 2. Protesto (`protesto`)

**Uma linha =** um registro de trâmite de protesto em cartório para um débito.

| Coluna | Tipo (aprox.) | Significado |
|--------|---------------|-------------|
| `ID_DEBITO` | número | Débito protestado / em trâmite |
| `DT_INSCRICAO` | data | Inscrição do débito |
| `STATUS_PROTESTO` | texto | Situação no cartório / fila |
| `DATA_PROTESTO` | data (pode ser vazia) | Data do protesto (~12% nulo na amostra) |
| `VALOR_PROTESTADO` | número (R$) | Valor enviado ao cartório |

**Exemplo (anonimizado):**

| Campo | Valor |
|-------|-------|
| ID do débito | `[ID_HASH_…]` |
| Data de inscrição | 2017-07-15 |
| Status | Aguardando pagamento dos emolumentos e cancelamento do protesto |
| Data do protesto | 2020-07-16 |
| Valor protestado | R$ 446,08 |

**Ordem de grandeza:** ~18,9 milhões de registros. Top status (população): aguardando emolumentos/cancelamento ~6,5 M; cartório protestou ~5,7 M; protesto cancelado ~2,3 M; pago no cartório ~1,5 M.

### 3. Ajuizamento (`ajuizamento`)

**Uma linha =** um débito que virou processo de execução fiscal (PEF), com data e comarca.

| Coluna | Tipo (aprox.) | Significado |
|--------|---------------|-------------|
| `ID_DEBITO` | número | Débito ajuizado |
| `DT_INSCRICAO` | data | Inscrição do débito |
| `PEF` | texto | Identificador do processo (aqui só hash) |
| `DT_AJUIZAMENTO` | data | Quando ajuizou |
| `NOMECOMARCA` | texto | Comarca / foro |

**Exemplo (anonimizado):**

| Campo | Valor |
|-------|-------|
| ID do débito | `[ID_HASH_…]` |
| Data de inscrição | 2016-01-04 |
| PEF | `[PEF_HASH_…]` |
| Data de ajuizamento | 2016-09-28 |
| Comarca | Comarca de Nova Odessa |

**Ordem de grandeza:** ~1,85 milhão de linhas. Maior volume nas comarcas/foros de São Paulo (execuções fiscais estaduais).

### 4. Débito — foto de estoque (`debito`, extracao=2026-03)

**Uma linha =** uma dívida inscrita **nessa foto mensal** (o dump completo de `debito` é histórico; usamos o filtro março/2026).

| Coluna | Tipo (aprox.) | Significado |
|--------|---------------|-------------|
| `ANO_EXTRACAO` / `MES_EXTRACAO` | número | Mês da foto (aqui 2026 / 3) |
| `ID_DEBITO` | número | Identificador da dívida |
| `CDA_COMPLETA` | texto | CDA (aqui só hash) |
| `DATA_INSCRICAO` | data | Quando foi inscrita |
| `SITUACAO_DEBITO` | texto | Ex.: Inscrito, Suspenso |
| `TIPO_DEBITO` | texto | IPVA, ICMS Declarado, Taxa Judiciária… |
| `STATUS_AJUIZAMENTO_DEBITO` | texto | Liberado / Ajuizado / … |
| `VALOR_SEM_HONORARIOS` | número (R$) | Valor principal (visão sem honorários) |
| `NOME_DEVEDOR` / `CPF_DEVEDOR` / `CNPJ_DEVEDOR` | texto | **PII — nunca publicar em claro** |

**Exemplo (anonimizado):**

| Campo | Valor |
|-------|-------|
| Foto | 2026-03 |
| ID do débito / CDA | `[ID_HASH_…]` / `[CDA_HASH_…]` |
| Data de inscrição | 2021-08-18 |
| Situação | Inscrito |
| Tipo | IPVA |
| Status ajuizamento | Liberado para ajuizamento |
| Valor sem honorários | R$ 684,30 |
| Devedor / CPF | `[NOME_HASH_…]` / `[CPF_HASH_…]` |

**Ordem de grandeza (foto 2026-03):** ~8,82 milhões de débitos. Liberado para ajuizamento ~6,68 M; Ajuizado ~2,14 M. Tipos mais frequentes: IPVA (~5,7 M), ICMS Declarado (~2,3 M), Taxa Judiciária (~0,6 M).

### 5. Arrecadação / GARE (`arrecadacao`)

**Uma linha =** um pagamento (GARE) associado a um débito.

| Coluna | Tipo (aprox.) | Significado |
|--------|---------------|-------------|
| `ANO` / `MES` | número | Competência do registro |
| `ID_DEBITO` | número | Débito pago (parcial ou total) |
| `CDA_COMPLETA` | texto | CDA (aqui só hash) |
| `STATUS_AJUIZAMENTO_DEBITO` | texto | Situação de ajuizamento no momento do dado |
| `ID_GARE_SEFAZ` | número | Identificador do pagamento |
| `DATA_ARRECADACAO_GARE` | data | Data do pagamento |
| `VALOR_TOTAL_GARE` | número (R$) | Total pago |
| `VALOR_RECEITA` / `JUROS_MORA` / `MULTA_MORA` / `VALOR_ACRESCIMO_FINANCEIRO` | número | Componentes |
| `HONORARIOS_*` | número (pode ser vazio) | Honorários |

**Exemplo (anonimizado):**

| Campo | Valor |
|-------|-------|
| Ano/mês | 2016 / 12 |
| ID do débito / GARE / CDA | `[ID_HASH_…]` |
| Status ajuizamento | Ajuizado |
| Data da arrecadação | 2016-12-26 |
| Valor total GARE | R$ 457,68 |
| Receita / juros / multa / acréscimo | 186,85 / 88,17 / 18,68 / 143,46 |

**Ordem de grandeza:** ~23,2 milhões de pagamentos. Ticket médio (população) ≈ R$ 1,3 mil entre débitos ainda “liberados”; ≈ R$ 3,6 mil entre já ajuizados — isso mede **recuperação**, não tipo de garantia.

> `faturamento` / `receita` existem na API e podem ajudar no perfil do contribuinte, mas **não** são o foco desta rodada de EDA de proxies de cobrança.

---

## O que destravaria a pergunta original (colaboração)

O trabalho **já segue** com os proxies acima. Se, no futuro, o time de dados / ATTUS puder disponibilizar uma tabela de garantia (o nome pode variar), isso **destravaria** a pergunta científica original — tipo, valor, vigência e ligação com o débito/processo.

Campos que seriam especialmente úteis, **se/quando** disponíveis:

- identificação da dívida (`ID_DEBITO`);
- **tipo** de garantia;
- valor e datas (constituição / vigência);
- status (ativa, vencida, etc.);
- ligação estável com o processo (PEF), se houver.

Isso não é um pré-requisito para continuar o EDA de cobrança; é uma **oportunidade de colaboração** para fechar o constructo de garantia.

---

## Status (resumo executivo)

| Tema | Situação |
|------|----------|
| Tabela de garantia | **Ausente** |
| Trabalho com proxies de cobrança | **Viável agora** |
| Score / modelo de tipo de garantia | **Pendente** do dado de garantia |
| Avaliação geral da frente | **Seguir com lacunas** (*go-with-gaps*) |

Em português claro: **não estamos parados**, e também **não fingimos** ter o dado central. O caminho responsável é análise honesta das trilhas de cobrança + diálogo colaborativo se/quando a tabela de garantia existir.

---

## Próximos passos

1. Continuar o EDA de proxies (parcelamento × protesto × ajuizamento × arrecadação), sempre rotulado como *proxy*, nunca como “score de garantia”.  
2. Aprofundar cruzamentos possíveis sem inventar tipo de garantia.  
3. Alinhar com outras frentes que compartilham a mesma lacuna (ex.: segmentação).  
4. Se/quando `garantia` existir no dump: nova rodada de caracterização por tipo e só então modelagem.

---

## Onde achar mais detalhe

- Descrição técnica da frente: [docs/D_garantias.md](docs/D_garantias.md)  
- Contexto curto: [CONTEXT.md](CONTEXT.md)  
- Notebook de EDA (playground): `notebooks/playground/proxy_cobranca_eda_v0.ipynb`  
- Figuras: `output/figures/`

> Texto voltado a leitores não técnicos. Números vêm da API Inteligência Fiscal com freeze `2026-03`; não inventamos métricas. Exemplos de linha são reais, com PII e identificadores sensíveis substituídos por hash.
