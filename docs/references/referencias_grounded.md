# Referências grounded (SoT v0 — monorepo)

Escopo: só citações verificáveis (DOI / editora / RePEc / OTexts / Springer).  
**Não** inventar paper “GARE SP + SARIMA”, “execução fiscal SP + XGBoost”, nem DOI fantasma.  
SoT deste arquivo: `docs/references/referencias_grounded.md`. Frentes em `projects/*/references/` são **ponteiros** + extras locais.

Mapa: o que os notebooks **citam** × o que a literatura **sustenta** × lacunas honestas.

---

## Dados deste monorepo (fato empírico)

| Artefato | Origem | Observação |
|---|---|---|
| Dump Inteligência Fiscal | API research snapshot `extracao=2026-03` | Não é realtime |
| Samples Parquet | `$DUMP_ROOT/extracao=2026-03/samples/` | `SAMPLE_LIMIT` via `.env` |
| Selic/IPCA sample | BCB SGS via `python-bcb` | Externo ao dump |
| Lake TJSP (FACE) | `LAKE_ROOT` (litigância) | Delta no HD; fora do git |

### Notas empíricas (diagnósticos v0)

- **PEF ↔ FACE join:** chaves úteis `numero` / `num_processo_limpo` (exact + normalização CNJ). Cobertura descritiva baixa no toy (~2–4% no probe); soft-link por foro/data é **demasiado ruidoso** para identidade processual (bloqueio frouxo → muitos candidatos; sem pesos Fellegi–Sunter).
- **Monitoramento:** no painel mensal (`estoque` × `arrecadação`), **Prophet** foi o melhor no holdout walk-forward deste freeze (condicional a `extracao=2026-03` e ao protocolo do notebook) — ver `estoque_arrecadacao_eda_forecast_v0.ipynb`.
- **GARE:** janelas mensais expandidas no notebook `gare_janelas_mensais_v0.ipynb` (grain diário dentro do mês; naive / Ridge / Prophet).

---

## A — Litigância / contumaz

### Teoria / compliance (base)
- Allingham, M. G., & Sandmo, A. (1972). Income tax evasion: a theoretical analysis. *Journal of Public Economics*, 1(3–4), 323–338. https://doi.org/10.1016/0047-2727(72)90010-2  
  → modelo canônico auditoria/penalidade/risco; **IRPF**, não execução fiscal estadual.
- Andreoni, J., Erard, B., & Feinstein, J. (1998). Tax compliance. *Journal of Economic Literature*, 36(2), 818–860. https://www.jstor.org/stable/2565123  
  → survey; limitações do modelo puramente pecuniário.

### Analogia metodológica (não evidência SP)
- Savić, M., Atanasijević, J., Jakovetić, D., & Krejić, N. (2022). Tax evasion risk management using a Hybrid Unsupervised Outlier Detection method. *Expert Systems with Applications*, 193, 116409. https://doi.org/10.1016/j.eswa.2021.116409  
  → outlier detection em dados administrativos tributários (Sérvia); **analogia** unsupervised + validação interna.

### Record linkage / cobertura (citado nos toys PEF)
- Fellegi, I. P., & Sunter, A. B. (1969). A theory for record linkage. *Journal of the American Statistical Association*, 64(328), 1183–1210. https://doi.org/10.1080/01621459.1969.10501049  
  → framing exact vs probabilístico; nos toys: **só** exact/normalizado ou bloqueio determinístico — sem pesos $m$/$u$ nem EM.
- Christen, P. (2012). *Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection*. Springer. https://doi.org/10.1007/978-3-642-31164-2  
  → normalização e blocking antes do link (soft-link toy / coverage toy).

### Survival / duração processual
- Kaplan–Meier e riscos competitivos são **padrão** em empirical legal studies (duração até resolução / settlement timing). O plano A cita KM — apropriado para tempo-até-desfecho no **lake**, não para o dump sozinho.  
  **Não** há nesta SoT um paper “Bielen et al.” verificado para ancorar KM em execução fiscal SP; cite KM como método padrão ELS sem inventar referência.

### O que os toys A ainda **não** validaram
Score comportamental, process mining, BacenJud, rede de OABs, KM no subset matched. Soft-link ≠ prova de identidade.

---

## B — Monitoramento / forecast de arrecadação

### Âncora normativa de forecast
- Hyndman, R. J., & Athanasopoulos, G. (2021). *Forecasting: principles and practice* (3rd ed.). OTexts. https://otexts.com/fpp3/  
  → baselines, walk-forward / TSCV, combinações, intervalos; âncora antes de deep learning.

### Métodos citados em `estoque_arrecadacao_eda_forecast_v0.ipynb` / `gare_janelas_mensais_v0.ipynb`
- Box, G. E. P., & Jenkins, G. M. (1970). *Time Series Analysis: Forecasting and Control*. Holden-Day. (tradição ARIMA/SARIMA; edições posteriores Box–Jenkins–Reinsel).
- Hoerl, A. E., & Kennard, R. W. (1970). Ridge regression: Biased estimation for nonorthogonal problems. *Technometrics*, 12(1), 55–67. https://doi.org/10.1080/00401706.1970.10488634
- Friedman, J. H. (2001). Greedy function approximation: A gradient boosting machine. *Annals of Statistics*, 29(5), 1189–1232. https://doi.org/10.1214/aos/1013203451  
  → base GBM; neste repo: `HistGradientBoostingRegressor` (scikit-learn).
- Taylor, S. J., & Letham, B. (2018). Forecasting at scale. *The American Statistician*, 72(1), 37–45. https://doi.org/10.1080/00031305.2017.1380080  
  → Prophet.
- Tukey, J. W. (1977). *Exploratory Data Analysis*. Addison-Wesley. (regra IQR 1.5).
- Armstrong, J. S., & Collopy, F. (1992). Error measures for generalizing about forecasting methods: Empirical comparisons. *International Journal of Forecasting*, 8(1), 69–80. https://doi.org/10.1016/0169-2070(92)90008-W
- Makridakis, S., Spiliotis, E., & Assimakopoulos, V. (2020). The M4 Competition: 100,000 time series and 61 forecasting methods. *International Journal of Forecasting*, 36(1), 54–74. https://doi.org/10.1016/j.ijforecast.2019.04.014  
  → uso de sMAPE / práticas de competição M.
- Makridakis, S. (1993). Accuracy measures: theoretical and practical concerns. *International Journal of Forecasting*, 9(4), 527–529. https://doi.org/10.1016/0169-2070(93)90079-3  
  → citado no notebook GARE (medidas percentuais / zeros).

### Honestidade
Não há paper canônico verificável **“GARE mensal PGE-SP + SARIMA”** (nem template publicado SP equivalente) para citar como evidência local. Desenho = **métodos gerais de forecast + dados administrativos do dump**, não plágio de caso inexistente.

---

## C — Macro × dívida ativa

### Âncoras
- Séries macro: BCB SGS (Selic, IPCA) — fonte primária oficial.
- Cointegração / VAR: literatura econométrica padrão (Engle–Granger, Johansen) — **depois** do painel mensal alinhado; samples C só puxaram macro e leram o painel B.

### Lacuna de dados
Dump **não** traz dívida não inscrita. Framing: dinâmica do **estoque inscrito** / arrecadação, não inadimplência corrente completa.

---

## D — Garantias

### Fato do sample
Meta da API: **sem dataset `garantia`**. Keyword search no `/meta/tabelas` negativo.

### Literatura auxiliar (interpretabilidade / direito positivo)
- Lundberg, S. M., & Lee, S.-I. (2017). A unified approach to interpreting model predictions. *NeurIPS*. (SHAP — interpretabilidade; **não** substitui feature de garantia.)
- LEF / CTN: Lei 6.830/1980; CTN — direito positivo, não ML.

### Conclusão metodológica
Até existir tabela de garantia, qualquer “score de garantia” seria **inválido**. Proxy de cobrança (parcelamento/protesto) é outra pergunta.

---

## Síntese para os 4 projetos

| Frente | Seguro agora | Evitar reivindicar |
|---|---|---|
| A | Join PEF+FACE por `numero`/`num_processo_limpo`; framing Fellegi–Sunter/Christen; KM padrão ELS no lake | Soft-link = identidade; contumaz legal = score do dump; paper Bielen inventado |
| B | FPP3 baselines + Ridge/SARIMAX/HGB/Prophet no painel mensal; Prophet melhor *neste* holdout | SOTA deep learning sem baseline; paper “GARE SP + SARIMA” |
| C | Painel B + BCB; cointegração depois | Efeitos causais sem desenho |
| D | Documentar gap; proxy cobrança | Score de tipo de garantia |

