# Pacote `cnpj_enriquecido` para a API CEMEPI

Este pacote foi gerado por `cnpj_pipeline export`. Ele não foi enviado a nenhum servidor, e a carga é manual.

## Governança e fluxo de publicação

- A PGE disponibiliza os dados de devedores aos pesquisadores do grupo CEMEPI sob **NDA**. Só pesquisadores têm acesso à API (acesso autenticado). Este pacote herda essas regras: não republique abertamente.
- O sal do HMAC fica só com o Étore e com o mantenedor da API. Ele nunca vai para o pacote, o `MANIFEST.json`, o log, o `state.sqlite` ou o git.
- Fluxo: **pacote local → validação → homologação na API rodando localmente → VPS.** Este README cobre só as três primeiras etapas. A ida para a VPS é decisão e operação separadas, depois do checklist abaixo assinado.

## Conteúdo

- `data/cnpj_enriquecido.parquet`: para DuckDB, pandas ou polars.
- `data/cnpj_enriquecido.csv.gz`: o mesmo conteúdo, para o `\copy` do PostgreSQL.
- `sql/ddl_duckdb.sql` e `sql/ddl_postgres.sql`: `CREATE TABLE IF NOT EXISTS`, com PK `cnpj` e índice em `cnpj_raiz`.
- `sql/load_duckdb.sql` e `sql/load_postgres.sql`: upsert idempotente por `cnpj` numa única transação.
- `load_duckdb.sh` e `load_postgres.sh`: conferem o `SHA256SUMS` antes de carregar.
- `MANIFEST.json`: linhas, política PF, sha256 dos arquivos e dos zips RFB usados, versões, relatórios de risco.
- `data_dictionary.md` (fonte, licença, colunas) e `coverage.md` (cobertura por tributo).
- Só se o pacote foi gerado com `export --with-socios` (veja `with_socios` no `MANIFEST.json`): `data/socios_vinculos.parquet` e `.csv.gz`, tabela `socios_vinculos`, a rede sócio–empresa **anonimizada** (detalhes abaixo). Os scripts de carga já a incluem; a carga substitui as linhas do mesmo `ref_rfb`.
- No fim deste README, quando houver: os relatórios de risco de reidentificação, gerados pelo próprio export.

## Como gerar (no Mac, fish)

No monorepo (`pipelines/cnpj_enrich`), o sal fica em `CNPJ_HASH_SALT` no `.env` da raiz, que está no `.gitignore` e nunca vai para o git:

```fish
cd ~/Code/cemepi-ctf-intel-fiscal
uv run cnpj-pipeline export --pf-policy anonimizar --with-socios --hash-salt-env CNPJ_HASH_SALT
```

Com o pacote avulso (tarball), o sal entra só na sessão:

```fish
set -x CNPJ_HASH_SALT '<sal guardado pelo Étore>'     # nunca em arquivo versionado
python -m cnpj_pipeline export --pf-policy anonimizar --with-socios --hash-salt-env CNPJ_HASH_SALT
set -e CNPJ_HASH_SALT
```

Sem o sal na variável, `--pf-policy anonimizar` e `--with-socios` falham antes de escrever qualquer coisa.

## Checklist de homologação local (antes de qualquer VPS)

Tudo roda no computador local, contra uma cópia local da API/base. Nada aqui toca a VPS.

1. **Integridade:** `sha256sum -c SHA256SUMS` sem falhas.
2. **Contagens:** `rows` e `socios_rows` do `MANIFEST.json` batem com `SELECT COUNT(*)` nos Parquet e, depois da carga local, nas tabelas. `rows` bate com o `collect` (CNPJs distintos) quando a política não é `remover`.
3. **Unicidade das chaves:** `COUNT(*) = COUNT(DISTINCT cnpj)` em `cnpj_enriquecido`; nenhum `cnpj` nulo; ids HMAC com 64 caracteres hexadecimais e CNPJs em claro com 14 caracteres.
4. **PII check sem achados:** nenhuma coluna de `layouts.SOCIOS_PERSONAL` (`nome_socio`, `cpf_cnpj_socio`, `representante_legal`, `nome_representante`) nem de `layouts.FORBIDDEN`; nenhuma ocorrência de `***` (CPF mascarado) nos arquivos de `data/`; nenhum CNPJ em claro nas linhas `cnpj_anonimizado = true`; o valor do sal não aparece em nenhum arquivo do pacote nem do workdir (ex.: `grep -rF "$CNPJ_HASH_SALT" .` vazio).
5. **Carga local duas vezes:** `./load_duckdb.sh /caminho/copia_local.duckdb` duas vezes (ou `PGURI=postgresql://...localhost.../base ./load_postgres.sh`) e mesmas contagens: a carga é idempotente.
6. **Join de teste com a DEBITO:** na API local, aplique o HMAC ao `CNPJ_DEVEDOR` normalizado (abaixo) e confira que todo `cnpj` do pacote casa com algum devedor, e que a taxa de casamento por tributo bate com `coverage.md`.
7. **Relatórios k<5 revisados:** leia os relatórios no fim deste README e registre a decisão (aceitar, generalizar mais ou suprimir) para cada linha com k<5. Combinações raras não são listadas no pacote, só contadas.
8. **Registro:** anote no `/meta` da API local a versão do pacote (`pipeline_version`, `ref_rfb`, `pf_policy`, `with_socios`) e quem homologou.

## Carga (na API local de homologação)

1. **Copiar o diretório inteiro** para a máquina da homologação e entrar nele.
2. **Conferir a integridade:** `sha256sum -c SHA256SUMS`. Os scripts também fazem isso.
3. **Fazer backup** da base local ou da tabela. Para DuckDB, copie o arquivo `.duckdb` com a API parada. Para Postgres, use `pg_dump -t cnpj_enriquecido`.
4. **Carregar.**
   - DuckDB (um escritor por vez; pare a API ou use uma cópia e troque o arquivo depois): `./load_duckdb.sh /caminho/api.duckdb`
   - PostgreSQL (a credencial vai em `PGURI` ou em `~/.pgpass`, nunca em arquivo do repo): `PGURI=postgresql://usuario@localhost:5432/base ./load_postgres.sh`
5. **Conferir:** a última consulta de cada script mostra as linhas por `ref_rfb`. Compare com `rows` do `MANIFEST.json`.
6. **Rodar de novo é seguro.** Repetir a carga do mesmo pacote não duplica nada: o upsert é por `cnpj`, e no Postgres as linhas iguais nem são reescritas. Um pacote de uma nova liberação RFB atualiza as linhas existentes e acrescenta as novas.
7. **Linhas antigas não são apagadas.** CNPJs que saíram da lista de devedores continuam com o `ref_rfb` antigo. Para remover, rode explicitamente `DELETE FROM cnpj_enriquecido WHERE ref_rfb < 'YYYY-MM';`. **Atenção:** trocar a política (por exemplo, de `suprimir` para `anonimizar`) muda a chave das linhas PF; nesse caso apague as linhas do `ref_rfb` antes de carregar, ou carregue numa tabela nova.

## Como a API e o pesquisador fazem o join

- Linhas com `cnpj_anonimizado = false`: `cnpj_enriquecido.cnpj` é o CNPJ em claro. Junção: `DEBITO.CNPJ_DEVEDOR` normalizado = `cnpj`.
- Linhas com `cnpj_anonimizado = true` (só na política `anonimizar`): `cnpj` = HMAC-SHA256(sal, CNPJ normalizado) e `cnpj_raiz` = HMAC-SHA256(sal, raiz de 8 caracteres), em hexadecimal minúsculo. O mesmo CNPJ sempre gera o mesmo id.
- **Normalização do CNPJ** (igual à do pipeline, `cnpj.normalize`): maiúsculas; só `[0-9A-Z]`; se sobrarem só dígitos e 12 ou 13 caracteres, `lpad` com zeros até 14.
- **Do lado da API** (que tem a `DEBITO` em claro e o sal), crie a coluna de junção uma vez:
  - PostgreSQL (`pgcrypto`): `encode(hmac(cnpj_norm, :'sal', 'sha256'), 'hex')`.
  - Python: `hmac.new(sal.encode(), cnpj_norm.encode(), hashlib.sha256).hexdigest()`.
  - Depois: `LEFT JOIN cnpj_enriquecido e ON e.cnpj = CASE WHEN <é PF-vinculado> THEN hmac_cnpj ELSE cnpj_norm END`, ou simplesmente tente as duas chaves (`e.cnpj IN (cnpj_norm, hmac_cnpj)`).
- **O pesquisador** não recebe o sal. Ele faz o join com a `DEBITO` servida pela API, que já expõe o mesmo id HMAC para esses CNPJs. Assim o join continua possível sem que o CNPJ do MEI circule em claro.

## Nota LGPD

**Base legal e natureza dos dados**
- Os dados cadastrais de pessoa jurídica da RFB são públicos (LAI; Decreto 8.777/2016).
- A tabela, porém, é indexada pelos CNPJs que são **devedores inscritos em dívida ativa**. Essa combinação vem do dump da PGE, cedido sob NDA, e herda as regras de acesso da API: acesso autenticado, uso para pesquisa.
- A finalidade é estudo por órgão de pesquisa (LGPD art. 7º, IV, e art. 13). Não republique a tabela abertamente.

**O que foi excluído por desenho**
- Ficam de fora: nomes (razão social e fantasia), endereço, CEP, telefone e e-mail.
- Também ficam de fora CPF e nome de devedores pessoa física: o `collect` só lê linhas com CNPJ e sem CPF.

**Caso MEI e empresário individual (`pessoa_fisica_vinculada = true`)**
- O CNPJ de MEI, empresário individual (2135), EIRELI (2305) ou de naturezas 4xxx identifica, na prática, uma **pessoa natural**. Na base pública da RFB, a razão social do MEI traz o nome e, muitas vezes, o CPF da pessoa. Por isso essas linhas são dado pessoal (LGPD art. 5º, I).
- A política aplicada está em `MANIFEST.json` → `pf_policy`:
  - `anonimizar` (**recomendada**; exige o sal): `cnpj` e `cnpj_raiz` viram ids HMAC; ficam CNAE, porte, Simples/MEI, natureza, situação e UF; todas as datas são truncadas ao ano (`AAAA-01-01`); o município só fica se a célula município × divisão CNAE (entre as linhas PF) tiver k≥5, senão fica NULL e `municipio_generalizado = true` (sobra a UF); `capital_social` vira NULL e só fica `capital_social_faixa`. O relatório de risco dessas linhas está no fim deste README.
  - `suprimir` (padrão da CLI, para não exigir o sal): a linha fica com o CNPJ em claro, mas `capital_social`, `municipio_rfb`, `municipio_ibge` e `municipio_nome` ficam em NULL.
  - `remover`: as linhas PF-vinculadas saem do pacote.
  - `manter`: tudo é mantido. Use só com aval da PGE ou do encarregado (DPO).
- Saídas publicadas (papers, figuras) só devem trazer agregados, nunca o CNPJ de MEI.

**Sócios (`socios_vinculos`, só com `--with-socios`)**
- Fica: CNPJ da empresa (`cnpj_basico`, a raiz) em claro, porque já é a chave da API; tipo do sócio; qualificação; **década** de entrada (`decada_entrada_sociedade`); faixa etária; flags de devedor.
- Duas versões (`--socios-detail`): `api` (padrão) é a única que pode ser carregada na API, com a entrada só por década. `local` mantém o **ano** exato (`ano_entrada_sociedade`), sai na pasta `..._LOCAL_NAO_SUBIR` com `api_upload_allowed: false` no `MANIFEST.json` e fica só no disco do pesquisador, sob o NDA da PGE.
- Município e CNAE da empresa não estão em `socios_vinculos`, mas qualquer um chega a eles pelo CNPJ (em `cnpj_enriquecido` ou na RFB). Por isso o relatório de risco mede também faixa × município × CNAE: a generalização para UF × seção do CNAE só vale se a API não expuser município e CNAE fino da empresa junto com os sócios.
- Sócio PJ: `socio_id` = CNPJ em claro. Sócio PF ou estrangeiro: `socio_id` = HMAC-SHA256(sal, nome normalizado + `|` + miolo do CPF), com o nome sem acento, em maiúsculas e com espaços simples. A mesma pessoa tem o mesmo id em todas as empresas, e a rede continua conectável.
- Sai: nome, CPF mascarado, representante legal (CPF e nome), país, razão social do sócio PJ.
- Na política `anonimizar`, empresas PF-vinculadas (MEI etc.) aparecem em `cnpj_basico` / `socio_id` com o mesmo id HMAC de `cnpj_enriquecido`.
- **Limite importante:** como o CNPJ da empresa está em claro e o quadro de sócios da RFB é público (com nome), quem cruzar o pacote com a RFB pode recuperar o nome do sócio. O HMAC impede reverter o id sem o sal, mas não impede essa ligação. Trate `socios_vinculos` como **dado pessoal pseudonimizado**, não como anônimo. O relatório de risco mede isso na linha "dentro da empresa".
