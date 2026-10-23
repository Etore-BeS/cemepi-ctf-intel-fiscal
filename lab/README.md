# lab/ — laboratório (EDA vivo) publicado no GitHub Pages

Camada L4 de `docs/ESTRUTURA.md`. Aqui se explora; as figuras de artigo **não** saem daqui.
Site: `https://etore-bes.github.io/cemepi-ctf-intel-fiscal/` (depois do primeiro deploy).

## Como funciona

1. Você escreve/edita uma entrada em `lab/eda/AAAA-MM-DD_<tema>.qmd` (modelo: `eda/_modelo_entrada.qmd`).
2. **Renderiza localmente**, com o HD montado: o Quarto executa o código e grava os resultados em `lab/_freeze/`.
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

- Renderize **o projeto** (`quarto render lab`). Renderizar um arquivo solto ou uma subpasta
  (`quarto render lab/eda/x.qmd`) sempre reexecuta o código desse arquivo. Isso também atualiza
  o freeze dele, mas não vale como render do site.
- **Não** use `--no-execute`: no Quarto 1.10 ele ignora o `_freeze/` e gera páginas sem figuras
  (testado em 23/10/2026).
- Dados de entrada mudaram (novo painel, nova extração)? Apague o freeze da entrada
  (`rm -r lab/_freeze/eda/<entrada>`) ou o `_freeze/` inteiro e renderize de novo.
  O `freeze: auto` só olha a fonte, não os dados.
- Suplementos de artigos (`lab/suplementos/`) usam `freeze: true`: nunca são reexecutados.

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
