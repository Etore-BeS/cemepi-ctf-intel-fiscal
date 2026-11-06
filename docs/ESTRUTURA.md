# ESTRUTURA — ontologia, workflows e regras do repositório

> Versão 1.1 — decisões D1–D11 aprovadas em **23/10/2026** pelo mantenedor (Étore Braga e Santos);
> D12 em **27/10/2026**.
> Destino: `docs/ESTRUTURA.md`. Este documento **substitui** a spec MECE local
> (`docs/superpowers/specs/2026-09-10-monorepo-mece-design.md`), cujo conteúdo está na §3.1.
> **[interp.]** marca uma decisão de projeto que a fonte não diz.
> Referências pelas siglas da §10 (ex.: [WIL17] = Wilson et al. 2017).

---

## 1. Princípios

1. **Alguém de fora tem que entender o que foi feito e por quê olhando só os arquivos.** Quase
   sempre esse alguém é você daqui a alguns meses. Tudo será refeito em algum momento. [NOB09]
2. **A análise é um grafo acíclico (DAG).** O dado bruto é imutável e cada passo tem entrada e
   saída explícitas. [CCDS] [GS14 cap. 4]
3. **Notebook serve para explorar, comunicar e, no artigo, gerar os resultados (D12); código
   reutilizado vira função testada em L3.** [CCDS] [RUL19 regras 4 e 7]
4. **Todo resultado publicado sai de código, sem passo manual:** figuras, tabelas e os números
   citados no texto. [SAN13 regras 1, 2 e 9] [AEA]
5. **Cada artigo é um compendium:** estrutura convencional; dados, métodos e saídas separados;
   ambiente especificado. [MAR18] [TTW]
6. **Monorepo.** As frentes compartilham mais da metade dos dados e do código; o código comum
   vira um pacote próprio. [WIL17 §4a]

---

## 2. Camadas (ontologia)

| # | Camada | O que é | Onde fica | No git? | Ref. |
|---|---|---|---|---|---|
| L0 | **Bruto** | Dump PGE/API, lake TJSP, séries BCB | HD externo (`DUMP_ROOT`, `LAKE_ROOT`, `DATA_ROOT`) | Não. Só o README de proveniência | [WIL17 §1] [CCDS] |
| L1 | **Validação** | Checagens do bruto: contagem por mês, hash, linhas quebradas, quarentena | `pipelines/divida_ativa/validation/` | Código + relatórios pequenos (md/json) | [SAN13 r5] [GS14 cap. 5] |
| L2 | **Derivados versionados** | Painéis limpos com versão fixa (`painel_icms/v2026-10-23`) | Parquet no HD em `$DATA_ROOT/derived/<painel>/<versao>/`; manifest em `data_manifests/` | Manifest sim; Parquet não (**D6**) | [GS14 cap. 4 "fixed revisions"] [WIL17] |
| L3 | **Pacote comum** | Leitura, de-para, painéis, filtros, tema de figuras | `pipelines/divida_ativa/src/divida_ativa/` (**D5**) | Sim, com testes | [CCDS] [RUL19 r4] [PIM19 bp3] |
| L4 | **Laboratório / EDA** | Caderno de laboratório datado, publicado no GitHub Pages | `lab/` (**D1**), Quarto com freeze (**D2**) | Fontes `.qmd` + `lab/_freeze/` | [NOB09] [RUL19 r1, r2] |
| L5 | **Análise por artigo** | Notebook que gera exatamente o que o artigo usa (`figures/`, `tables/`, `numeros.tex`); sem scripts `.py` (**D12**) | `projects/<frente>/notebooks/articles/<slug>.ipynb` | Sim, sem saídas (**D8**) | [RUL19 r7, r9] [AEA] |
| L6 | **Manuscrito** | LaTeX que só inclui `figures/`, `tables/` e `numeros.tex` gerados pelo notebook L5 | `projects/<frente>/manuscript/<slug>/` | Sim; PDF e auxiliares não | [WIL17] [SAN13 r9] |
| L7 | **Pacote de replicação** | Notebook L5 + L3 congelado + README SSDE + declaração de dados confidenciais | Tag git + depósito (Zenodo/openICPSR) | Sim (tag) | [AEA] [SSDE] [TIER] [MAR18] |

### 2.1 Direção das dependências

```
L0 → L1 → L2 → L3 ─┬─> L4  lab/  (Pages)
                   └─> L5  notebooks/articles/<slug>.ipynb → L6  manuscript/<slug>/ → L7  tag + depósito
```

- **L5 (o notebook do artigo) nunca importa nem lê nada de L4.** O que o lab descobrir e o artigo
  precisar sobe para L3 e o notebook importa de lá. [CCDS] [RUL19 r7] [interp.]
- **L4 importa de L3 e lê L2; nunca lê L0 direto.** A exceção são as páginas de validação, que são
  L1. [interp., a partir de GS14 cap. 4]
- **L3 não importa de L4 nem de L5.** [interp.]
- **L6 não roda código**: só inclui o que o notebook L5 gerou. [AEA "no manual modifications"] [SAN13 r2]
- **Cada notebook L5 fixa a versão de L2 na célula de parâmetros do topo**, para que um painel novo
  não altere um artigo já submetido. [GS14 cap. 4] [interp.: célula no lugar de `config.yaml`, D12]

### 2.2 Quem lê o quê

| Leitor | Lê | Não precisa ler |
|---|---|---|
| Orientadores | Site do lab (L4) e PDF do manuscrito (L6) | Código |
| Coautores e pareceristas | Notebook do artigo (L5), L6, L7 | Lab |
| Quem for replicar | L7 (README SSDE) | Outras frentes |
| Mantenedor e agentes | `AGENTS.md`, este documento, SoT dos pipelines | — |

[AEA "computational empathy"] [NOB09]

---

## 3. Árvore

```text
.
├── AGENTS.md  CONTRIBUTING.md  CONTEXT.md  README.md  CITATION.cff  LICENSE
├── .pre-commit-config.yaml          # nbstripout + pii_check + check_freeze (pre-push) + testes
├── .github/workflows/lab-pages.yml  # publica lab/ no Pages
├── docs/
│   ├── ESTRUTURA.md                 # este documento
│   ├── mission.md  architecture.md  # architecture.md aponta para cá
│   ├── api-dump/  references/
│   └── (planos/ e superpowers/: só locais, gitignored — D7)
├── data_manifests/                  # L2: um JSON por versão de derivado
├── scripts/                         # pii_check.py, check_freeze.py, pii_allowlist.toml, tests/
├── shared/cemepi_api/               # cliente HTTP do dump
├── pipelines/
│   ├── litigancia/                  # lake TJSP (SoT)
│   ├── divida_ativa/                # L1 + L2 + L3 (D5)
│   │   ├── src/divida_ativa/{io,depara,paineis,filtros,viz_theme}.py
│   │   ├── validation/              # raw_file_validator.py, loader_rules.py
│   │   ├── build.py                 # L0 → L2 (grava no HD + manifest)
│   │   └── tests/
│   └── cnpj_enrich/                 # cnpj_pipeline (CNAE, porte)
├── lab/                             # L4 (D1, D2, D3)
│   ├── _quarto.yml  _environment  index.qmd  README.md
│   ├── eda/AAAA-MM-DD_<tema>.qmd    # entradas datadas
│   ├── suplementos/<slug>/          # suplementos congelados por artigo (freeze: true)
│   └── _freeze/                     # resultados congelados, versionados
└── projects/<frente>/               # litigancia | monitoramento | macro | garantias
    ├── README.md  CONTEXT.md  docs/  references/
    ├── notebooks/playground/        # rascunhos (sem saídas no git)
    ├── notebooks/articles/<slug>.ipynb  # L5 (D12): análise do artigo; gera figures/, tables/, numeros.tex
    └── manuscript/<slug>/           # L6: main.tex, sections/, figures/, tables/, numeros.tex (gerados por L5)
```

O artigo declarado vs. autuado fica em `projects/macro/` (**D9**):
`notebooks/articles/icms_declarado_autuado.ipynb` + `manuscript/icms_declarado_autuado/`.

### 3.1 Os três andares de documentação (absorvidos da spec MECE, D7)

1. **`docs/` na raiz:** portfólio e regras transversais (missão, arquitetura, este documento,
   premissa do dump, referências).
2. **`pipelines/*/docs/`:** dados e operação (SoT, schema, normalização, recoleta).
3. **`projects/*/docs/`:** pesquisa de cada frente; não é SoT.

Precedência: em operação, vale o SoT do pipeline; em definição de termo, vale o `CONTEXT.md`
(regras atuais do `AGENTS.md`, mantidas). **Nenhum documento versionado aponta para arquivo
gitignored** (`docs/planos/`, `docs/superpowers/`, `.local/`). [NOB09] [GS14 cap. 7]

---

## 4. Workflow de pesquisa (ideia → artigo)

1. **Ideia:** uma linha em `projects/<frente>/docs/` ou numa issue, com a pergunta e os dados
   necessários. [WIL17 §3b]
2. **Exploração:** numa entrada datada em `lab/eda/` ou em `notebooks/playground/`, registrando
   também os becos sem saída. [RUL19 r2] [NOB09]
3. **Promoção para L3:** quando o código aparece em dois lugares, entra no site ou vai ser usado por
   um artigo. Vira função testada em `divida_ativa` e o lab passa a importá-la. [CCDS] [RUL19 r4] [PIM19 bp3]
4. **Derivado novo:** o painel vai para `build.py` e gera a versão `vAAAA-MM-DD` com manifest.
   [WIL17 §1] [SAN13 r5]
5. **Artigo:** criar `notebooks/articles/<slug>.ipynb` e `manuscript/<slug>/`; o notebook fixa a
   versão de L2 (**D12**). [GS14] [AEA]
6. **Ligar cada afirmação do texto ao resultado**, nas notas e no manuscrito: caminho do arquivo ou
   chave de `numeros.tex`. [SAN13 r9]

---

## 5. Workflow de artigo

| Passo | Regra | Ref. |
|---|---|---|
| Ramo | **Trunk-based (D10):** trabalho na `main`, com commits pequenos e frequentes. Ramos curtos só para mudanças arriscadas | [WIL17 §5c] [interp.] |
| Execução | O notebook do artigo, rodado de cima a baixo num kernel limpo, apaga `figures/`, `tables/` e `numeros.tex` do manuscrito e regenera tudo. Rodar antes de cada commit que mexa em resultado (**D12**) | [GS14 cap. 2 e 3B] [TIER] |
| Números | `manuscript/<slug>/numeros.tex` com `\newcommand{\nCDAicms}{…}`, escrito pelo notebook. Nunca digitar número no texto | [SAN13 r9] [AEA] |
| Figuras | Plotly + Kaleido pelo notebook, CSV com os dados ao lado e nenhum retoque manual. **Uma cópia**, em `manuscript/<slug>/figures/` | [SAN13 r7] [RUL19 r2] [interp.] |
| Aleatoriedade | Seeds na célula de parâmetros do notebook (K-means, bootstrap) | [SAN13 r6] |
| Ambiente (D4) | **Hoje:** `pyproject.toml` único com base enxuta e dependency groups (`dev`, `lab`, `litigancia`, `forecast`, `nlp`), Python fixado em `.python-version`. **Na submissão:** o pacote de replicação (L7) leva um projeto uv próprio (`pyproject.toml` + `uv.lock`) que roda o notebook, com path dependency para `pipelines/divida_ativa` | [PIM19 bp4 e bp5] [SAN13 r3] [CCDS] [AEA] |
| Submissão (D10) | Tag `<slug>-submit-AAAAMMDD`; notebook rodado de cima a baixo num ambiente limpo; README SSDE preenchido | [AEA "re-run"] [SSDE] |
| Dados confidenciais | O pacote leva todo o código, a declaração de disponibilidade, a lista do que exige dados restritos e, se possível, uma amostra sintética | [AEA "confidential data"] |
| Suplemento (D3) | Na tag de submissão, o EDA do artigo é copiado para `lab/suplementos/<slug>/` com `freeze: true` e não muda mais | [MAR18] [TTW] [interp.] |

---

## 6. Workflow de laboratório (lab publicado)

Requisito: o EDA existe sempre e é publicado no GitHub Pages a cada atualização, para a discussão
de pesquisa.

1. **Onde (D1):** `lab/` no monorepo, publicado por `.github/workflows/lab-pages.yml` em
   `https://etore-bes.github.io/cemepi-ctf-intel-fiscal/`. O repositório `paper-eda-cemepi` fica
   **congelado como v1 (15/10/2026)** e listado em `lab/suplementos/`. Fonte e site saem do mesmo
   commit. [SAN13 r1, r4] [Docs GitHub Pages]
2. **Formato (D2):** Quarto website com `execute: freeze: auto`. Executa **só localmente**, com o HD;
   os resultados vão para `lab/_freeze/`, que é versionado. O CI monta o site a partir do freeze,
   sem dados nem Jupyter. [Docs Quarto "Freeze"]
   - Testado em 23/10/2026 com o Quarto 1.10.19:
     - com o freeze em dia, `quarto render lab` não executa nada;
     - com freeze desatualizado, o render falha (sem Jupyter no runner);
     - `--no-execute` **ignora** o freeze e gera páginas sem saídas, por isso não é usado;
     - o freeze guarda o MD5 da fonte, que é o que o `scripts/check_freeze.py` confere. [interp.]
3. **O que publica (D3):** o lab vivo (`index.qmd` + `eda/`) e os suplementos congelados por artigo
   (`suplementos/<slug>/`, com `freeze: true` em `_metadata.yml`). [MAR18] [interp.]
4. **Entradas datadas:** `eda/AAAA-MM-DD_<tema>.qmd`, a partir de `eda/_modelo_entrada.qmd`. Cada
   uma diz a pergunta, a versão do painel e a `extracao`, o que foi visto e o que não concluir.
   [NOB09] [RUL19 r1] [AGENTS.md: markdown de leitura antes de cada figura]
5. **Questões para discussão (D11):** seção "Questões para discussão (AAAA-MM-DD)" em
   `index.qmd`, a mais recente no topo, sem nomes de pessoas e sem menção a reuniões. Nomes e notas
   pessoais só em `.local/`. Isso mantém a regra do `AGENTS.md` ("no personal names or meeting
   framing"). [RUL19 r1] [interp.]
6. **PII (gate):** `scripts/pii_check.py` roda no pre-commit e duas vezes no CI (fontes + freeze;
   depois o HTML gerado). Detecta:
   - CPF e CNPJ, formatados ou não, com dígito verificador válido;
   - e-mails;
   - as colunas `NOME_DEVEDOR`, `CPF_DEVEDOR`, `nome_socio`, `cpf_cnpj_socio` com valor ao lado.

   Falso positivo vai para a allowlist (`scripts/pii_allowlist.toml`), por arquivo e por `sha256`.
   Sites do Pages são públicos mesmo com repositório privado. [Docs GitHub Pages] [AGENTS.md] [interp.]
7. **Tamanho:** o plotly.js vem da CDN (`lab/_environment`: `PLOTLY_RENDERER=notebook_connected`).
   Uma página de teste ficou com cerca de 31 KB, contra 39,5 MB do HTML do paper EDA v1. Limites do
   Pages: 1 GB por site e 10 minutos por deploy. [Docs GitHub Pages limits]
8. **Promoção:** o que estabilizar no lab sobe para L3 e, se for de artigo, para L5. O lab nunca é
   fonte de figura de artigo. [CCDS] [RUL19 r7]

---

## 7. Higiene

- Dado bruto imutável e fora do git. [CCDS] [WIL17]
- **Notebooks sem saídas no git (D8):** `nbstripout` no pre-commit em todo o repositório
  (exceto `lab/_freeze/`). No lab, prefira `.qmd`; com `.ipynb`, rode `nbstripout` **antes** do
  `quarto render` (ver `lab/README.md`). [PIM19 §IV-F e bp8] [CCDS] [RUL19 r6]
- Rodar de cima a baixo antes do commit, sem células fora de ordem. [PIM19 bp8] [RUL19 r7]
- Caminhos vêm do `.env` ou da config, nunca de caminho fixo do HD no código. [PIM19 bp7] [TIER] [AEA]
- Testes para L3 e para `scripts/`; o pre-commit roda os testes de `scripts/`. [WIL14] [PIM19 bp3]
- Segredos só no `.env`. [CCDS] [AGENTS.md]
- Documento só existe se for mantido. Link quebrado é bug. [GS14 cap. 7]

---

## 8. Registro de decisões (D1–D11 em 23/10/2026; D12 em 27/10/2026)

| # | Decisão | Ref. |
|---|---|---|
| D1 | Lab em `lab/` no monorepo + GitHub Actions para o Pages; `paper-eda-cemepi` congelado como v1 | [SAN13 r1, r4] [Docs GitHub Pages] |
| D2 | Quarto com `freeze: auto`; execução só local | [Docs Quarto] [PIM19] [RUL19 r6] |
| D3 | Lab vivo + suplementos congelados por artigo | [MAR18] [AEA] |
| D4 | Dependency groups agora; projeto uv por artigo na submissão | [PIM19 bp4, bp5] [AEA] [Docs uv] |
| D5 | Pacote comum em `pipelines/divida_ativa/` | [WIL17 §4a] [CCDS] |
| D6 | Parquet no HD + manifest JSON no git | [GS14 cap. 4] [WIL17] [CCDS] |
| D7 | `docs/planos` e `docs/superpowers` só locais, sem links mortos; spec MECE absorvida (§3.1) | [NOB09] |
| D8 | `nbstripout` em tudo; lab com freeze | [PIM19] [CCDS] [RUL19 r6] |
| D9 | Artigo declarado vs. autuado em `projects/macro/` [interp.] | — |
| D10 | Trunk + tag por submissão | [WIL17 §5c] [AEA] |
| D11 | Seção datada "Questões para discussão"; nomes só em `.local/` | [RUL19 r1] [NOB09] |
| D12 | 27/10/2026: a análise de cada artigo fica só no notebook `projects/<frente>/notebooks/articles/<slug>.ipynb`, sem a camada `analysis/<slug>/` com `.py`; o notebook gera `figures/`, `tables/` e `numeros.tex` que `manuscript/<slug>/` inclui | [RUL19 r7, r9] [AEA] |

## 9. Manifest de derivado (formato, D6)

```json
{
  "nome": "painel_icms",
  "versao": "v2026-10-23",
  "caminho": "$DATA_ROOT/derived/painel_icms/v2026-10-23/",
  "build_commit": "<sha do git>",
  "fonte": {"extracao": "2026-03", "validation_report": "pipelines/divida_ativa/validation/reports/…/validation.json"},
  "arquivos": [{"arquivo": "part-0.parquet", "sha256": "…", "linhas": 0}],
  "linhas_por_mes": {"2016-01": 0},
  "filtros": ["ANO <= 2025", "PJ apenas"]
}
```

Os zeros são marcadores do formato, não números reais. [GS14 cap. 4] [SAN13 r5] [interp.]

---

## 10. Referências (verificadas em 23/10/2026)

- [WIL17] Wilson G, Bryan J, Cranston K, Kitzes J, Nederbragt L, Teal TK (2017). Good enough practices in scientific computing. *PLOS Comput Biol* 13(6): e1005510. https://doi.org/10.1371/journal.pcbi.1005510
- [WIL14] Wilson G et al. (2014). Best practices for scientific computing. *PLOS Biol* 12(1): e1001745. https://doi.org/10.1371/journal.pbio.1001745
- [NOB09] Noble WS (2009). A quick guide to organizing computational biology projects. *PLOS Comput Biol* 5(7): e1000424. https://doi.org/10.1371/journal.pcbi.1000424
- [SAN13] Sandve GK, Nekrutenko A, Taylor J, Hovig E (2013). Ten simple rules for reproducible computational research. *PLOS Comput Biol* 9(10): e1003285. https://doi.org/10.1371/journal.pcbi.1003285
- [RUL19] Rule A et al. (2019). Ten simple rules for writing and sharing computational analyses in Jupyter Notebooks. *PLOS Comput Biol* 15(7): e1007007. https://doi.org/10.1371/journal.pcbi.1007007
- [PIM19] Pimentel JF, Murta L, Braganholo V, Freire J (2019). A large-scale study about quality and reproducibility of Jupyter notebooks. *MSR 2019*, 507–517. https://doi.org/10.1109/MSR.2019.00077
- [GS14] Gentzkow M, Shapiro JM (2014). *Code and Data for the Social Sciences: A Practitioner's Guide*. https://web.stanford.edu/~gentzkow/research/CodeAndData.pdf
- [MAR18] Marwick B, Boettiger C, Mullen L (2018). Packaging data analytical work reproducibly using R (and friends). *The American Statistician* 72(1): 80–88. https://doi.org/10.1080/00031305.2017.1375986
- [CCDS] DrivenData. Cookiecutter Data Science — Opinions. https://cookiecutter-data-science.drivendata.org/opinions/
- [TTW] The Turing Way Community. Research Compendia. https://book.the-turing-way.org/reproducible-research/compendia/
- [TIER] Project TIER. TIER Protocol 4.0. https://www.projecttier.org/tier-protocol/protocol-4-0/
- [AEA] Office of the AEA Data Editor. Preparing your files for verification. https://aeadataeditor.github.io/aea-de-guidance/preparing-for-data-deposit
- [SSDE] Vilhuber L, Connolly M, Koren M, Llull J, Morrow P. A template README for social science replication packages. https://doi.org/10.5281/zenodo.7293838

**Documentação de ferramentas** (não são referências acadêmicas):

- [Docs GitHub Pages] https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site · limites: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
- [Docs Quarto] Freeze: https://quarto.org/docs/projects/code-execution.html · Pages: https://quarto.org/docs/publishing/github-pages.html
- [Docs uv] Workspaces e path dependencies: https://docs.astral.sh/uv/concepts/projects/workspaces/
- nbstripout: https://github.com/kynan/nbstripout · actions/deploy-pages: https://github.com/actions/deploy-pages · actions/upload-pages-artifact: https://github.com/actions/upload-pages-artifact
