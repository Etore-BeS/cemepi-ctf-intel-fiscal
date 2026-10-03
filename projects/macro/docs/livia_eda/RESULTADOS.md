# Resultados em linguagem tributária / de cobrança

Síntese do `EDA_completo.ipynb` para conversa com a Profa. Lívia.  
Números de apoio estão em [`dados/`](dados/).

---

## 1. O que a base mostra em volume

- A tabela de **débito** é da ordem de **centenas de milhões** de linhas (débito × fotos de extração).
- **IPVA** concentra **quantidade** de débitos; **ICMS** concentra **valor** (padrão clássico: muitas CDAs menores vs menos CDAs mais “pesadas”).
- Idade mediana aproximada (anos desde inscrição): **ICMS ~10,5 anos**, **IPVA ~7,8**, **TAXAS ~6,7** — o estoque ICMS inscrito, em mediana, é mais antigo.

*(Detalhe por tipo bruto: `dados/tipo_debito_buckets.csv`; idade: `dados/idade_cda_por_bucket.csv`.)*

---

## 2. Painel ICMS (unidade de cobrança ao longo do tempo)

- Montamos um painel **débito ICMS × ano** (cerca de **23 milhões** de linhas, ~**5 milhões** de débitos distintos, anos **2016–2026**).
- Em cada ano medimos: estoque na foto, arrecadação GARE daquele ano, e a **taxa de realização** `arrecadação ÷ estoque`.
- Débitos **sem** pagamento no ano ficam com taxa **0** (não foram excluídos). Isso importa: a carteira “parada” entra na foto.

---

## 3. Persistência: quem arrecada relativo ao estoque tende a repetir?

Leitura da etapa 5 (`dados/corr_lags_icms.csv`):

| Comparação | O que significa na cobrança | Ordem de grandeza |
|------------|-----------------------------|-------------------|
| Taxa hoje × taxa ano passado | Há **memória de curto prazo**: débitos que realizaram melhor no ano anterior tendem a realizar melhor de novo | Pearson ~**0,54**; Spearman ~**0,67** |
| Taxa hoje × 2 ou 3 anos atrás | A ligação **linear** enfraquece; a **ordem** (bons vs ruins) ainda se parece um pouco | Pearson baixo; Spearman ainda moderado |
| Estoque hoje × estoque ano passado | Estoque inscrito é **quase estável** ano a ano | ~**0,99** |
| Taxa × tamanho do estoque / idade | Quase **não** há reta simples “estoque maior → taxa maior”; em postos, idade um pouco associada a **menor** taxa | Efeito fraco / descritivo |

**Mensagem para política de cobrança (descritiva):** o melhor preditor simples da realização relativa no ICMS, entre o que medimos, é o **histórico recente do próprio débito** (`taxa` defasada), não o tamanho do estoque sozinho.

---

## 4. Três modelos (etapa 6) — o que levar para a reunião

Arquivos: `modelo1_logit_coefs.csv`, `modelo2_taxa_ols_coefs.csv`, `modelo3_ar1_taxa_carteira.csv`, `serie_agregada_icms_ano.csv`.

### Modelo 1 — Chance de ter **algum** pagamento no ano

- Controlando ano, estoque (em log) e idade, a taxa do ano anterior entra com efeito **positivo porém pequeno** no logit (a decisão 0/1 “pagou algo?” é mais bruta que o *quanto* pagou).
- **Estoque maior** (log) e **idade maior** associam-se a **menor** chance de pagamento no ano na amostra — coerente com carteira antiga/pesada mais difícil de “despertar”, mas **não** prova causa.

### Modelo 2 — **Quanto** se realiza (taxa)

- Coeficiente de `taxa_l1` na ordem de **~0,15**: em média linear na amostra, parte da realização relativa do ano passado **reaparece** no ano seguinte (eco da correlação da seção 5; o número exato depende da amostra de 1 M linhas e dos controles).
- Log do estoque negativo: débitos maiores tendem a taxas menores na amostra (realização relativa mais difícil quando o denominador é enorme).

### Modelo 3 — Carteira agregada

- Série anual de estoque somado, arrecadação somada, taxa da carteira e % de débitos com algum pagamento (`serie_agregada_icms_ano.csv`).
- **2026** aparece com arrecadação e share de pagantes bem mais baixos — trate como **ano incompleto** na extração, não como veredito de desempenho.
- O AR(1) só na taxa agregada tem **poucos pontos** (≈11 anos): use só como ilustração visual, não como modelo de previsão.

---

## 5. Frases prontas (tom sóbrio, reunião CEMEPI/PGE)

1. “No ICMS inscrito, a realização relativa ao estoque **persiste** de um ano para o outro no mesmo débito.”  
2. “IPVA concentra **quantidade**; ICMS concentra **valor** e, em mediana, CDAs mais **antigas**.”  
3. “Estoque do débito quase não muda ano a ano; o que varia mais é **se e quanto** entra em GARE.”  
4. “Parcelamento, protesto e ajuizamento ainda **não** estão nos regressores — próximo passo natural se a pergunta for efetividade de instrumento.”  
5. “Associação na base ≠ efeito causal de uma política.”

---

## 6. Próximos passos sugeridos (se a Lívia pedir)

- Incluir dummies / flags de **parcelamento**, **protesto** e **ajuizamento** no painel.  
- Recorte **trimestral** a partir da data da arrecadação (hoje o painel é anual).  
- Separar ICMS declarado vs autuação (já existem tipos brutos no de-para).  
- Tratar explicitamente o ano da última extração como parcial.
