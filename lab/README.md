# lab/ — laboratório (EDA vivo) publicado no GitHub Pages

Camada L4 de `docs/ESTRUTURA.md`. Aqui se explora; as figuras de artigo **não** saem daqui.
Site: `https://etore-bes.github.io/cemepi-ctf-intel-fiscal/` (depois do primeiro deploy).

## Como funciona

1. Você escreve/edita uma entrada em `lab/eda/AAAA-MM-DD_<tema>.qmd` (modelo: `eda/_modelo_entrada.qmd`).
2. **Renderiza localmente**, com o HD montado: `uv run --no-sync quarto render lab/eda/<entrada>.qmd --profile partes`
   executa o código e grava os resultados em `lab/_freeze/`. Depois, `uv run --no-sync python
   lab/paper_eda/_pagina_unica.py` põe a entrada na página única (seção "L. Análises do laboratório"; inclua a
   entrada nova em `LAB_PAGES` de `_pagina_unica.py`). Desde 26/10/2026 (CTF-03) as entradas **não são páginas
   do site**: todo o conteúdo fica num HTML só, com navegação pelo sumário lateral.
3. Faz commit da fonte **e** de `lab/_freeze/`, e push na `main`.
4. O workflow `.github/workflows/lab-pages.yml` confere o freeze, roda o PII check, monta o site **sem executar
   código** e publica no Pages.

O CI não tem acesso aos dados nem tem Jupyter. Se você esquecer o passo 2, o CI falha com
`check_freeze: … _freeze desatualizado` — nada é publicado pela metade.

## Pré-requisitos (uma vez, no Mac)

```fish
# Quarto: use a MESMA versão do CI (QUARTO_VERSION em .github/workflows/lab-pages.yml)
# Baixe o .pkg de https://github.com/quarto-dev/quarto-cli/releases/tag/v1.10.19
quarto --version                       # deve mostrar 1.10.19

cd ~/Code/cemepi-ctf-intel-fiscal
uv sync --group lab                    # base + dev + motor Jupyter mínimo para o Quarto
uv run quarto check jupyter            # deve listar o Python de .venv e o kernel python3
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Se `quarto check jupyter` mostrar outro Python (por exemplo o 3.14 do Homebrew), aponte para o venv:

```fish
set -x QUARTO_PYTHON (pwd)/.venv/bin/python
```

## Renderizar e publicar (fish + uv)

```fish
cd ~/Code/cemepi-ctf-intel-fiscal
ls /Volumes/Meedi_Etore_HD1/CEMEPI/api_ctf/      # HD montado?
# DATA_ROOT e demais caminhos vêm do .env (nunca no código)

uv run nbstripout lab/eda/*.ipynb 2>/dev/null   # só se houver .ipynb no lab (ver abaixo)
uv run quarto render lab                        # projeto inteiro: respeita o freeze
uv run python scripts/check_freeze.py --project lab
uv run python scripts/pii_check.py lab          # fontes, _freeze e _site

uv run quarto preview lab                       # opcional: ver no navegador antes do push

git add lab/ && git commit -m "lab: <tema> (AAAA-MM-DD)" && git push
```

Atenção:

- Renderize **o projeto** (`quarto render lab`) para montar o site. Para executar uma entrada ou parte e
  atualizar o freeze dela, use `quarto render <arquivo> --profile partes` (sem o perfil, o arquivo fica fora do
  projeto e o freeze não muda). Apague depois o HTML solto que esse comando deixa em `lab/_site/`.
- **Não** use `--no-execute`: no Quarto 1.10 ele ignora o `_freeze/` e gera páginas sem figuras
  (testado em 23/10/2026).
- Dados de entrada mudaram (novo painel, nova extração)? Apague o freeze da entrada
  (`rm -r lab/_freeze/eda/<entrada>`) ou o `_freeze/` inteiro e renderize de novo.
  O `freeze: auto` só olha a fonte, não os dados.
- Suplementos de artigos (`lab/suplementos/`) usam `freeze: true`: nunca são reexecutados.

## EDA do paper (`lab/paper_eda/`): uma página só

`/paper_eda/` é **uma página só**, `lab/paper_eda/index.qmd`, com sumário lateral e quadro de novidades. Ela não
executa código: é montada por `_pagina_unica.py` a partir do resultado já executado das 8 partes
(`lab/_freeze/paper_eda/<parte>/`). As partes (`01_dados.qmd` … `08_parte_e.qmd`) continuam no repositório porque
o freeze delas é a fonte da página única, mas **não são páginas do site**: ficam fora do `render:` de `_quarto.yml`
(não use `"*.qmd"` lá: sem `/`, o padrão casa em subpastas e traz as partes de volta).

**Abrir sem servidor (um arquivo só).** Para ler o EDA do paper com duplo clique, sem preview nem servidor:

```fish
cd ~/Code/cemepi-lab; and uv run --no-sync python lab/paper_eda/_html_unico.py
open ~/Desktop/EDA_paper_CEMEPI.html     # ou lab/_site/paper_eda/EDA_paper_CEMEPI.html
```

O `_html_unico.py` remonta a página do `_freeze`, renderiza só ela (não tem código, nada é executado) e embute
CSS, JS, plotly.js, MathJax, fontes e as 4 figuras pesadas num único HTML (cerca de 22 MB) que abre via `file://`
sem internet. Na primeira vez ele baixa os arquivos da CDN e guarda em `~/.cache/cemepi-lab/cdn/`. Esse arquivo não
é publicado (o CI não roda este script).

**Assinatura do `macro.duckdb` (desde 26/10/2026).** A chave do cache inclui tamanho e data do `cemepi.duckdb` e do
`macro.duckdb`. A série SGS 4189 entrou no `macro.duckdb` em 26/10/2026 sem mudar nenhuma das outras 16 séries
(conferido por EXCEPT ALL contra o backup), e o lab não lê a 4189 do banco. Para reaproveitar o cache, exporte antes de
executar qualquer parte ou entrada (o CI não precisa disso, porque não executa):

```fish
set -x CEMEPI_MACRO_FP_PIN 798720:1790442383   # assinatura do macro.duckdb anterior à 4189
```

Reexecutar uma parte (só quando a fonte dela mudar; o código roda e usa o cache do HD):

```fish
cd ~/Code/cemepi-lab
uv run --no-sync python lab/paper_eda/_gerar_paginas.py        # regera as partes a partir do notebook (só leitura)
uv run --no-sync quarto render lab/paper_eda/04_parte_b2.qmd --profile partes   # executa a parte e atualiza lab/_freeze/paper_eda/04_parte_b2/
# (sem --profile partes a parte é renderizada como arquivo solto, fora do projeto, e o _freeze NÃO muda)
uv run --no-sync python lab/paper_eda/_pagina_unica.py         # remonta lab/paper_eda/index.qmd (avisa se um freeze estiver velho)
rm -f lab/_site/paper_eda/0*.html                              # o render solto da parte deixa um HTML que não é página do site
uv run --no-sync quarto render lab                             # site inteiro, sem reexecutar nada
uv run --no-sync python scripts/check_freeze.py --project lab
```

Ver localmente: `uv run --no-sync quarto preview lab --no-render` e abra `http://localhost:<porta>/paper_eda/`.

## Converter o `EDA_completo.ipynb` atual

**Opção A — recomendada: virar `.qmd`.** Texto puro: diff legível, sem saídas no git, freeze estável.

```fish
quarto convert projects/macro/notebooks/EDA_completo.ipynb --output lab/eda/2026-10-23_eda_completo.qmd
```

Depois, no `.qmd` gerado:

1. Cabeçalho YAML: troque o título (tire "(base …)" e qualquer nome de pessoa), acrescente
   `description:` e `date: 2026-10-23`.
2. Troque o caminho fixo do HD (`DEFAULT_DATA_ROOT = Path("/Volumes/…")`) por leitura do `.env`
   (`DATA_ROOT`). O site é público e não deve expor caminhos locais.
3. Remova ou passe para `.local/` os trechos de bastidor ("com você supervisionando" etc.).
4. `uv run quarto render lab` e confira o resultado.

O `.ipynb` original continua em `projects/macro/notebooks/` até o passo 5 do `MIGRACAO.md`,
sem saídas (nbstripout). Não mantenha as duas versões editáveis: depois de convertido, o lab é a
fonte e o `.ipynb` é apagado.

**Opção B: manter `.ipynb` dentro do Quarto.** Funciona, com dois cuidados:

1. Por padrão o Quarto **não executa** `.ipynb` e usa as saídas gravadas, que o nbstripout apaga.
   Ponha na primeira célula (tipo *Raw*):
   ```yaml
   ---
   title: "EDA completo — ICMS"
   date: 2026-10-23
   execute:
     enabled: true
   ---
   ```
2. O freeze guarda o MD5 do arquivo. Por isso, **rode `nbstripout` antes de `quarto render`**.
   Se rodar depois, o arquivo muda, o freeze fica desatualizado e o CI falha.
   O `nbstripout` é idempotente: rodar de novo não muda nada.

## Regras de conteúdo (resumo de docs/ESTRUTURA.md §6)

- Só agregados. Nunca linhas com `NOME_DEVEDOR`, `CPF_DEVEDOR`, `nome_socio`, `cpf_cnpj_socio`,
  nem CPF/CNPJ em claro. Raiz de CNPJ só como hash com sal (o sal fica fora do repositório).
- Cada entrada diz a versão do painel e a `extracao`.
- "Questões para discussão (AAAA-MM-DD)" ficam em `index.qmd`, sem nomes de pessoas nem menção a
  reuniões. Notas pessoais vão para `.local/`.
- Falso positivo do PII check: registre em `scripts/pii_allowlist.toml` (`[[allow]]` com `path`,
  `rules` e `sha256` do valor) e explique em `reason`.
