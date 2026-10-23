# cnpj_enrich (`cnpj_pipeline`): enriquecimento resiliente dos CNPJs devedores com os Dados Abertos da RFB

Pipeline do monorepo em `pipelines/cnpj_enrich/` (docs/ESTRUTURA.md, D5). O código fica aqui; os dados ficam no HD, em `$CNPJ_PIPELINE_WORKDIR` (padrão `$DATA_ROOT/cnpj_enrich`), e nunca entram no git. Só o manifest do derivado vai para `data_manifests/cnpj_enrich/` (D6).

Este é um pacote Python pequeno, com uma CLI. Ele parte dos CNPJs de devedores PJ da tabela `DEBITO`, de **todos os tributos e todas as fotos**, e gera a tabela `cnpj_enriquecido`, com CNAE, natureza, porte, Simples/MEI, situação, município IBGE e **regime tributário** (ECF). No fim, monta um pacote para carregar na VPS da API.

Opcionalmente, traz também o **quadro de sócios** dos devedores (camada local, com nome e CPF mascarado, para análise de rede). Veja "Sigilo" abaixo.

Roda no Mac, com os dados no HD externo. Ele aguenta ser interrompido (kill, queda de rede, falta de energia) e retoma sem perder nem duplicar dados. A RAM fica limitada pelo `memory_limit` do DuckDB: nada é carregado inteiro com pandas.

Ele **não** usa a API nem o token, e **não** acessa a VPS.

## Etapas

| etapa | o que faz | unidade retomável | saída |
|---|---|---|---|
| `collect` | CNPJs distintos de `DEBITO` (ATTACH READ_ONLY de `cemepi.duckdb`, ou um glob Parquet do dump). Lê só identificador, tipo, mês e valor. CPF e nome nunca. | `year=YYYY`, depois `kb=K` (consolidação por bucket) | `collect/raw/year=*/kb=*/`, `collect/keys/kb=*/{keys,keys_tributo}.parquet` |
| `download` | Zips da RFB: Empresas, Estabelecimentos, Simples e as tabelas de código (CNAE, natureza, município, motivo, qualificação, país), mais a tabela TOM→IBGE. `Socios` e o regime tributário **só sob pedido**: `--socios` (Socios0..9.zip) e `--regime` (4 zips `entidades-*.zip`, de outro compartilhamento da RFB). | arquivo (retoma byte a byte) | `rfb/2026-03/*.zip`, `manifest.json` |
| `enrich` | Cada zip é extraído em streaming para o HD. O DuckDB lê o CSV, projeta só as colunas permitidas e faz semi-join pela raiz. | `arquivo#grupo` | `enrich/2026-03/{empresas,estabelecimentos,simples,codes}/` |
| `socios` | Sócios dos devedores, com **todas** as colunas do leiaute (nome, CPF/CNPJ como vem da RFB etc.). Entra a linha se a empresa é devedora **ou** se o sócio é PJ devedora (segundo grau). Gera também a lista de arestas para rede. Opcional: `--hash-salt-env VAR` gera a cópia pseudonimizada. | `SociosN.zip`, depois `socios.parquet` e `socios_arestas.parquet` | `socios/2026-03/` (**só local**) |
| `regime` | Regime tributário (Lucro Real, Presumido, Arbitrado, Imunes e Isentas) por CNPJ e ano, da base "Regimes Tributários" (ECF). | `entidades-*.zip`, depois `regime.parquet` | `regime_tributario/2026-03/regime.parquet` |
| `build` | Consolida 1 linha por CNPJ, com dedup determinística, as colunas-resumo do regime e relatório de cobertura por tributo. | `kb=K` | `build/2026-03/cnpj_enriquecido/kb=*.parquet`, `coverage.{md,csv}` |
| `export` | Pacote para a VPS: Parquet, CSV.gz, DDL DuckDB e Postgres, upsert idempotente, README com a nota LGPD. Sócios só com `--with-socios`, e mesmo assim **sem nome nem CPF**. | pacote | `export/cnpj_enriquecido_ref2026-03/` |

Detalhes de cada etapa:
- **`download`:** PROPFIND/HEAD, `Range` + `If-Range`, retries com backoff exponencial e jitter, conferência de tamanho, CRC de cada membro do zip e sha256.
  - O que já está `ok` no `manifest.json` (mesmo tamanho e ETag) nunca é baixado de novo. `download --socios` baixa só os Socios; `download --regime` só os 4 zips de regime; nenhum dos dois mexe nos arquivos padrão nem na tabela IBGE.
  - O `manifest.json` é regravado com trava e mesclagem: dois `download` no mesmo workdir não apagam a entrada um do outro.
  - Regime tributário: compartilhamento público próprio da RFB (`https://arquivos.receitafederal.gov.br/index.php/s/MPPfFit7g7zdA8C`, arquivos na raiz, sem pasta mensal). Conferido em 23/10/2026 (zips de 13/03/2026, anos 2016–2024). Se a RFB mudar o link: `--regime-share ID` e/ou `--regime-base-url URL` (ou `CNPJ_REGIME_SHARE` / `CNPJ_REGIME_BASE_URL`).
- **`socios` e `regime`:** cada zip é uma unidade atômica (como no `enrich`), lida em streaming com o `memory_limit`. Uma unidade é recalculada sozinha se o zip mudar (sha256 do manifest) ou se o `collect` for refeito. As tabelas finais são recalculadas quando alguma parte muda. O `build` refaz os buckets quando o `regime` muda.

**Resiliência.** Cada unidade escreve só num caminho `_tmp_*`. Depois faz `os.replace` para o nome final e só então grava `done` no `state.sqlite` (WAL, `synchronous=FULL`).
- Um kill antes do rename deixa só lixo temporário, que é apagado na próxima rodada.
- Um kill entre o rename e o commit faz a unidade ser recalculada e sobrescrita.
- Nenhuma saída é feita por append, então não há duplicatas. Rodar de novo uma etapa concluída não faz nada; use `--force` para recalcular.
- O `key_buckets` fica fixado no workdir: mudar o valor no meio do caminho dá erro, em vez de misturar partições.

**Memória.**
- `--memory-limit` (padrão 2GB), `--threads` (padrão 2) e `--temp-dir` (spill do DuckDB e CSV extraído, **no HD**).
- O CSV de Estabelecimentos0 tem uns 6 GB descompactado e nunca vai para a RAM.
- Se faltar memória, use `--groups N` no `enrich`. Cada arquivo é lido N vezes, com 1/N das chaves de cada vez.
- O mínimo prático é cerca de 128MB com 1 thread (testado). No Mac, recomendo 2GB e 2 threads.

## Configuração (`.env` da raiz do monorepo)

A CLI lê o `.env` da raiz do repositório (python-dotenv, como `pipelines/litigancia` e `shared/cemepi_api`). O que já estiver definido no shell tem prioridade. Caminhos relativos no `.env` são relativos à raiz do repo. Nenhum caminho absoluto fica no código.

| variável | uso | padrão |
|---|---|---|
| `DATA_ROOT` | raiz dos dados no HD | — |
| `CNPJ_PIPELINE_WORKDIR` | workdir (estado, zips, Parquet, export) | `$DATA_ROOT/cnpj_enrich` |
| `CNPJ_TEMP_DIR` | spill do DuckDB e CSV extraído (no HD) | `$CNPJ_PIPELINE_WORKDIR/tmp` |
| `CEMEPI_DUCKDB_MAIN` | base CEMEPI (absoluta, ou relativa ao `DATA_ROOT`) | `$DATA_ROOT/api_ctf/cemepi.duckdb` |
| `CNPJ_REF_RFB`, `CNPJ_MEMORY_LIMIT`, `CNPJ_THREADS` | liberação RFB, RAM e threads do DuckDB | `2026-03`, `2GB`, `2` |
| `CNPJ_REGIME_SHARE`, `CNPJ_REGIME_BASE_URL` | só se a RFB mudar o link do regime tributário | link oficial |
| `CNPJ_HASH_SALT` | sal do HMAC (export `anonimizar` / `--with-socios`). **Segredo:** só no `.env` local; nunca no git, no log, no manifest ou no pacote | — |
| `CNPJ_MANIFEST_DIR` | onde o `build` grava o manifest versionado | `data_manifests/cnpj_enrich` |
| `CNPJ_ENV_FILE`, `CNPJ_PIPELINE_NO_DOTENV=1` | outro `.env` / não ler `.env` (os testes usam) | — |

O log registra só se o `.env` foi lido, nunca os valores.

## Como rodar (Mac, fish)

Uma etapa por vez. Interrompeu? Rode o mesmo comando de novo: ele continua de onde parou. O log fica em `$CNPJ_PIPELINE_WORKDIR/logs/pipeline.log`.

```fish
cd ~/Code/cemepi-ctf-intel-fiscal          # raiz do monorepo
uv sync --group cnpj                       # uma vez (base + dev + grupo cnpj)

uv run cnpj-pipeline collect --memory-limit 2GB --threads 2      # --duckdb CAMINHO se não usar o padrão do .env
uv run cnpj-pipeline download                    # ~7,4 GB (liberação 2026-03, sem Socios)
uv run cnpj-pipeline download --socios           # +0,67 GB (Socios0..9.zip)
uv run cnpj-pipeline download --regime           # +0,06 GB (4 zips entidades-*.zip)
uv run cnpj-pipeline enrich --memory-limit 2GB --threads 2      # --groups 4 se faltar RAM
uv run cnpj-pipeline socios --memory-limit 2GB --threads 2
uv run cnpj-pipeline regime --memory-limit 2GB --threads 2
uv run cnpj-pipeline build                       # também grava data_manifests/cnpj_enrich/cnpj_enriquecido_<ref>.json
uv run cnpj-pipeline status                      # unidades done/failed por etapa
```

Export (recomendado: `anonimizar`; o sal vem de `CNPJ_HASH_SALT` no `.env`; sem o sal, falha):

```fish
uv run cnpj-pipeline export --pf-policy anonimizar --with-socios --hash-salt-env CNPJ_HASH_SALT
```

Sem sal, ainda dá para gerar o pacote antigo, sem sócios: `uv run cnpj-pipeline export --pf-policy suprimir`. O pacote sai em `$CNPJ_PIPELINE_WORKDIR/export/` e **não é enviado a lugar nenhum**: siga o checklist de homologação local do `docs/EXPORT_README.md`.

Pseudonimização opcional dos sócios (o sal fica só no ambiente/`.env` local):

```fish
set -x CEMEPI_SOCIOS_SALT (uv run python -c 'import secrets; print(secrets.token_hex(32))')
uv run cnpj-pipeline socios --hash-salt-env CEMEPI_SOCIOS_SALT    # gera socios/2026-03/socios_hashed.parquet
```

A etapa `all` encadeia tudo: `uv run cnpj-pipeline all --socios --regime`. Sem `--socios`/`--regime`, ela faz só o fluxo antigo. `--with-socios` e `--pf-policy anonimizar` também valem no `all` e leem o sal de `CNPJ_HASH_SALT` (ou de `--hash-salt-env VAR`). `uv run python -m cnpj_pipeline ...` é equivalente a `uv run cnpj-pipeline ...`.

## Manifest do derivado (D6)

Ao fim de cada `build`, `data_manifests/cnpj_enrich/cnpj_enriquecido_<ref>.json` é regravado com nome, versão (`rfb-<ref>`), caminho simbólico (`$DATA_ROOT/...`), commit do código (`-dirty` se houver mudança local em `pipelines/cnpj_enrich`), sha256 dos zips RFB e do `regime.parquet`, e sha256 + linhas de cada Parquet. Não contém CNPJ, nome, valor, caminho local nem o sal: é o único artefato de dados que vai para o git. Fora do monorepo (tarball), nada é gravado, a menos que `CNPJ_MANIFEST_DIR` esteja definido.

**Antes do `enrich`, olhe o log do `collect`.** Ele mostra a % de CNPJs com dígito verificador válido. Se ficar abaixo de 90%, o identificador provavelmente está mascarado na origem e o join não faz sentido. Pare aí.

Dependências: grupo `cnpj` do `pyproject.toml` da raiz (`duckdb`, `requests`, `python-dotenv`, `pglast` para validar a DDL Postgres nos testes); `pytest` vem do grupo `dev`. Os testes de carga usam opcionalmente a CLI `duckdb` e `psql`/`initdb`.

## Sigilo (sócios)

- **Fonte:** arquivos `Socios*.zip` dos Dados Abertos do CNPJ (RFB), leiaute em [`cnpj-metadados.pdf`](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), seção "SÓCIOS". A RFB já publica o CPF descaracterizado: `***123456**` (somem os 3 primeiros dígitos e os 2 verificadores, art. 129 § 2º da Lei 13.473/2017).
- **Chave de pessoa física é aproximada.** Como o CPF vem mascarado, a chave de PF é `nome + CPF mascarado`. Homônimos com o mesmo miolo de CPF viram um nó só, e grafias diferentes da mesma pessoa viram dois. O log da etapa `socios` avisa isso. O CNPJ de sócio PJ vem completo, e a ligação PJ–PJ é exata.
- **Onde ficam:** só em `$CNPJ_PIPELINE_WORKDIR/socios/<ref>/`, no HD. Não vão para `cnpj_enriquecido`, nem para o pacote da API.
- **Export:** por padrão, nada de sócios. Com `export --with-socios --hash-salt-env CNPJ_HASH_SALT` entra a tabela `socios_vinculos`, a rede anonimizada: empresa em claro (chave da API), sócio PJ com o CNPJ em claro, sócio PF/estrangeiro com `socio_id` = HMAC-SHA256(sal, nome normalizado | miolo do CPF), o mesmo em todas as empresas; ficam tipo, qualificação, ano de entrada e faixa etária; saem nome, CPF mascarado, representante, país. O export escreve no README do pacote um relatório de risco de reidentificação (k<5 em faixa etária × município × CNAE e variações) e grava o resumo no `MANIFEST.json`.
- **MEI e empresário individual:** `export --pf-policy anonimizar` (recomendada) troca `cnpj`/`cnpj_raiz` dessas linhas por HMAC com o mesmo sal, trunca as datas ao ano, generaliza o município para UF quando a célula município × divisão CNAE tem k<5 e troca o capital social por faixa. Como a API aplica o mesmo HMAC ao `CNPJ_DEVEDOR`, o join com a `DEBITO` continua possível. Detalhes e o checklist de homologação local em `docs/EXPORT_README.md`.
- **O sal:** só em variável de ambiente (nunca em arquivo, no pacote, no manifest, no `state.sqlite`, no log ou no git). Sem ele, o export com sócios ou com `anonimizar` falha. Fica com você e com o mantenedor da API; se vazar, troque o sal e regenere o pacote (todos os ids mudam).
- **Pseudonimização:** `socios --hash-salt-env VAR` gera `socios_hashed.parquet` com HMAC-SHA256 de `NOME|documento`, com sal lido da variável `VAR` (nunca no código, nunca no log, mínimo 16 caracteres). O mesmo sal gera os mesmos ids entre rodadas; guarde-o com o mesmo cuidado de uma senha. É só uma opção: a anonimização definitiva fica a cargo do pesquisador.
- A base da PGE continua sendo lida só no `collect`, sem `NOME_DEVEDOR` e com `CPF_DEVEDOR` apenas no filtro de PJ.

## Testes (só dados sintéticos)

```fish
uv run pytest pipelines/cnpj_enrich        # da raiz do monorepo
```

Os testes nunca leem o `.env` (`CNPJ_PIPELINE_NO_DOTENV=1`) e isolam as variáveis `CNPJ_*`/`DATA_ROOT` do shell.

- **`test_cnpj`:** normalização, DV numérico e alfanumérico (exemplo oficial `12ABC34501DE35`), e equivalência entre as macros SQL e o Python.
- **`test_download`:**
  - servidor HTTP local com PROPFIND, Range e If-Range;
  - conexões cortadas no meio, com a retomada por `Range` conferida;
  - 503 e 502 com retry;
  - servidor que ignora o Range, o que exige recomeçar e não fazer append;
  - `.part` corrompido, detectado pelo CRC e removido;
  - **processo morto (exit 137) no meio do arquivo**, depois retomado;
  - segunda rodada sem nenhum GET.
- **`test_pipeline`:** ponta a ponta contra o gabarito.
  - CNPJs coletados = verdade; CPF fora; formatação normalizada.
  - `match_nivel` de cada caso; MEI/2135 marcado; IBGE resolvido; CNPJ alfanumérico preservado.
  - Duplicata da RFB e linha malformada tratadas.
  - **Nenhum valor das colunas proibidas** (sentinela) em nenhum Parquet, CSV, SQL, MD ou log.
  - Mesmo resultado com `--groups 3`; rodar de novo não faz nada.
- **`test_resume`:**
  - kill injetado antes e depois do rename em `enrich`, `collect` e `build`;
  - um **loop de SIGKILL em instantes aleatórios**;
  - em todos, depois de retomar, o resultado é idêntico ao da rodada limpa, sem duplicatas e sem `_tmp_` sobrando.
- **`test_memory`:** 272 MB de CSV (900 mil linhas) com `memory_limit=128MB` e 1 thread. O pico de RSS do processo fica abaixo de 400 MB (medido: cerca de 250 MB).
- **`test_export_load`:**
  - DDL Postgres validada pelo parser (pglast);
  - carga real **duas vezes** no DuckDB CLI e num PostgreSQL local descartável, para provar a idempotência: mesma contagem e uma linha alterada é atualizada.
- **`test_socios_regime`:**
  - `download --socios` e `--regime` (servidor mock com os dois compartilhamentos): só baixam os arquivos pedidos, sobrevivem a conexão cortada e 503, e a segunda rodada não faz nenhum GET; dois downloaders com manifest desatualizado não apagam entradas um do outro;
  - `socios`: vínculos = gabarito, inclusive o segundo grau (empresa não devedora cujo sócio PJ é devedor) e as exclusões; flags, descrições de qualificação/país/faixa, linha duplicada da RFB mantida uma vez, homônimos colapsando no mesmo nó da rede;
  - kill injetado antes e depois do rename nas partes e nas tabelas finais; depois de retomar, resultado idêntico, sem `_tmp_`;
  - `--hash-salt-env`: HMAC igual ao do Python, nenhum nome ou CPF na saída, sal fora do log; sem a variável, erro;
  - `regime`: só raízes devedoras, forma mista deduplicada entre arquivos, linha de SCP separada, CRLF tratado; `build` refaz os buckets e preenche último regime, ano, lista por ano e o derivado `SIMPLES (cadastro)`; kill e retomada;
  - `export`: sem sócios por padrão; `--with-socios` sem sal falha; com sal, nenhum nome, CPF ou razão social em nenhum arquivo de dados, `socio_id` igual ao HMAC calculado em Python, a mesma pessoa com o mesmo id em duas empresas, PJ com CNPJ em claro, ano de entrada, relatório de risco no README, e o valor do sal em nenhum arquivo do pacote ou do workdir; a carga duas vezes no DuckDB não duplica;
  - `--pf-policy anonimizar`: sem sal falha; ids HMAC para todas as linhas PF (e só para elas), conferidos contra o HMAC da `DEBITO` normalizada (join de teste); datas no 1º de janeiro; capital social só em faixa; regra k≥5 do município testada nos dois ramos (mantém e generaliza); `socios_vinculos` com o mesmo id HMAC para empresas PF; carga duas vezes no DuckDB.
- **`test_config_env`:** `.env` com caminhos relativos, shell tem prioridade sobre o `.env`, leitor reserva sem python-dotenv, `CNPJ_PIPELINE_NO_DOTENV`, CLI achando o workdir em `$DATA_ROOT/cnpj_enrich` sem o sal em log ou arquivo, e o manifest do `build` (D6) sem CNPJ nem caminho local.
