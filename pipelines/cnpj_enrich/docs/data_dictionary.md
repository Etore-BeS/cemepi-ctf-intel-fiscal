# Dicionário de dados: `cnpj_enriquecido`, `regime`, `socios` e `socios_vinculos`

**Grão:** 1 linha por CNPJ de devedor PJ (14 caracteres) que aparece na tabela `DEBITO` do dump CEMEPI, em qualquer foto e qualquer tributo. **Chave primária:** `cnpj`. **Chave secundária:** `cnpj_raiz`, com índice.

**Referência RFB:** `ref_rfb` (padrão `2026-03`, o mesmo mês da extração `2026-03` do dump). CNAE, porte, situação etc. são os da data de referência, não os da inscrição da dívida.

## Fonte e licença

- **Fonte:** Receita Federal do Brasil, "Dados Abertos do CNPJ", liberação mensal.
  - Página: https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos/cadastros
  - Arquivos: compartilhamento público https://arquivos.receitafederal.gov.br (pasta `YYYY-MM`).
  - Leiaute: `cnpj-metadados.pdf`, no mesmo compartilhamento.
- **Regimes Tributários (ECF):** Receita Federal, compartilhamento público https://arquivos.receitafederal.gov.br/index.php/s/MPPfFit7g7zdA8C (arquivos `entidades-lucro-real.zip`, `entidades-lucro-presumido.zip`, `entidades-lucro-arbitrado.zip`, `entidades-imunes-e-isentas.zip`).
  - Leiaute: pasta `entidades-regime-tributario-dicionarios/` do mesmo compartilhamento (`entidades-*-dicionario.csv`). Conferido em 23/10/2026: CSV com cabeçalho, separador `,`, colunas `ano, cnpj, cnpj_da_scp, forma_de_tributacao, quantidade_de_escrituracoes`.
  - Cite como: "Receita Federal do Brasil. Dados Abertos, Regimes Tributários (ECF), versão de 13/03/2026."
- **Sócios:** arquivos `Socios0..9.zip` da liberação mensal; leiaute na seção "SÓCIOS" de [`cnpj-metadados.pdf`](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf) (11 campos, conferidos contra `Socios9.zip` de 2026-03).
- **Tabela TOM → IBGE de municípios:** Receita Federal, https://www.gov.br/receitafederal/dados/municipios.csv (metadados em `municipios-metadados.pdf`).
- **Licença:** são dados públicos, divulgados por força da Lei 12.527/2011 (LAI) e do Decreto 8.777/2016 (Política de Dados Abertos). O uso é livre, com citação da fonte. **Não encontrei uma licença nominal específica para o dump CNPJ.** O portal gov.br da RFB está sob CC BY-ND 3.0, mas isso cobre as páginas do portal, não necessariamente os dados.
  - Cite como: "Receita Federal do Brasil. Dados Abertos do CNPJ, liberação 2026-03."
  - Antes de redistribuir publicamente, confira os termos vigentes na página da RFB.
- **Lado CEMEPI:** a lista de CNPJs vem da `DEBITO` (dump PGE/SEFAZ-SP via API CEMEPI). **A combinação "este CNPJ é devedor" não é dado público.** Os termos de uso da API valem para esta tabela inteira.

## O que nunca entra

**Em `cnpj_enriquecido` e no pacote da API:** razão social, nome fantasia, logradouro, número, complemento, bairro, CEP, telefones, fax, e-mail, cidade no exterior, nome e CPF de sócios e de representantes legais. Também não entram CPF, nome do devedor nem os valores das dívidas.

**Sócios só na camada local.** Os arquivos `Socios*` só são baixados com `download --socios` e só são lidos pela etapa `socios`. As saídas (`socios.parquet`, `socios_arestas.parquet`, `socios_hashed.parquet`) ficam em `<workdir>/socios/<ref>/`, no HD, e **trazem nome e CPF mascarado** por pedido do pesquisador, para a análise de rede. Elas nunca vão para `cnpj_enriquecido`. O `export` só leva sócios com `--with-socios` (e o sal), e mesmo assim na tabela anonimizada `socios_vinculos`, **sem** `nome_socio`, `cpf_cnpj_socio` (PF vira id HMAC; o CNPJ de sócio PJ fica em `socio_id`), `representante_legal` e `nome_representante`.

**Base da PGE:** o `collect` nunca lê `NOME_DEVEDOR`. `CPF_DEVEDOR` só é usado no filtro de pessoa jurídica (linhas sem CPF) e nunca é gravado.

O leitor CSV do `enrich` só projeta as colunas de `layouts.KEEP`. Testes conferem que nenhuma saída de `enrich`/`build`/`export` contém o conteúdo das colunas proibidas, e que o pacote com `--with-socios` não contém nome nem CPF.

## Colunas

| coluna | tipo (DuckDB / Postgres) | origem RFB | regra |
|---|---|---|---|
| cnpj | VARCHAR / char(14) | DEBITO.CNPJ_DEVEDOR | Maiúsculas e só `[0-9A-Z]`. Se tiver só dígitos e 12 ou 13 caracteres, recebe `lpad` com zeros até 14 (zeros à esquerda perdidos). Aceita o CNPJ alfanumérico. |
| cnpj_raiz | VARCHAR / char(8) | 8 primeiros caracteres | chave de Empresas e Simples |
| dv_valido | BOOLEAN | (calculado) | Módulo 11 da RFB, com valor `ord(c) - 48`, o que vale também para o alfanumérico. Se for `false`, o CNPJ está mascarado ou errado na origem. |
| cnpj_alfanumerico | BOOLEAN | (calculado) | Tem letra (IN RFB 2.229/2024, CNPJs novos a partir de jul/2026). |
| match_nivel | VARCHAR / text | (calculado) | `estabelecimento`: o CNPJ de 14 caracteres está em Estabelecimentos. `raiz`: só a raiz está em Empresas, então as colunas de estabelecimento ficam nulas. `nao_encontrado`: não está em nenhum dos dois. |
| matriz_filial | VARCHAR | Estabelecimentos.identificador_matriz_filial | 1 = MATRIZ, 2 = FILIAL |
| cnae_principal | VARCHAR / char(7) | Estabelecimentos.cnae_fiscal_principal | `lpad` 7 |
| cnae_principal_descricao | VARCHAR | Cnaes.zip | |
| cnae_divisao | VARCHAR / char(2) | (calculado) | 2 primeiros dígitos do CNAE |
| cnae_secao | VARCHAR / char(1) | (calculado) | seção A–U da CNAE 2.x (IBGE/CONCLA) |
| cnaes_secundarios | VARCHAR / text | Estabelecimentos.cnae_fiscal_secundaria | lista separada por vírgula, como vem na RFB |
| natureza_juridica | VARCHAR / char(4) | Empresas.natureza_juridica | |
| natureza_juridica_descricao | VARCHAR | Naturezas.zip | |
| qualificacao_responsavel | VARCHAR | Empresas.qualificacao_do_responsavel | Só o código do papel, como "49 Sócio-Administrador". A pessoa não é identificada. |
| qualificacao_responsavel_descricao | VARCHAR | Qualificacoes.zip | |
| porte | VARCHAR / char(2) | Empresas.porte | 00 NAO INFORMADO, 01 MICRO EMPRESA, 03 EMPRESA DE PEQUENO PORTE, 05 DEMAIS |
| porte_descricao | VARCHAR | leiaute | |
| capital_social | DECIMAL(18,2) / numeric(18,2) | Empresas.capital_social | Vírgula decimal convertida. É o valor declarado, sem atualização. |
| opcao_simples | BOOLEAN | Simples.opcao_pelo_simples | S = true, N = false. CNPJ (raiz) que não aparece no arquivo do Simples = false (não optante), inclusive quando não há cadastro na RFB; as datas ficam NULL |
| data_opcao_simples, data_exclusao_simples | DATE | Simples | `YYYYMMDD`. `00000000` vira NULL. |
| opcao_mei | BOOLEAN | Simples.opcao_mei | S = true, N = false; fora do arquivo do Simples = false (mesma regra de `opcao_simples`) |
| data_opcao_mei, data_exclusao_mei | DATE | Simples | |
| data_inicio_atividade | DATE | Estabelecimentos | |
| situacao_cadastral | VARCHAR / char(2) | Estabelecimentos | 01 NULA, 02 ATIVA, 03 SUSPENSA, 04 INAPTA, 08 BAIXADA |
| situacao_cadastral_descricao | VARCHAR | leiaute | |
| data_situacao_cadastral | DATE | Estabelecimentos.data_situacao_cadastral | |
| motivo_situacao_cadastral | VARCHAR | Estabelecimentos | |
| motivo_situacao_cadastral_descricao | VARCHAR | Motivos.zip | |
| situacao_especial, data_situacao_especial | VARCHAR, DATE | Estabelecimentos | por exemplo, recuperação judicial |
| uf | VARCHAR / char(2) | Estabelecimentos.uf | |
| municipio_rfb | VARCHAR / char(4) | Estabelecimentos.municipio | código TOM/SIAFI usado pela RFB |
| municipio_ibge | VARCHAR / char(7) | municipios.csv (RFB) | Correspondência TOM → IBGE. É NULL se o código não estiver na tabela. |
| municipio_nome | VARCHAR | municipios.csv, com Municipios.zip como alternativa | |
| n_estabelecimentos_rfb | INTEGER | (calculado) | estabelecimentos da raiz na RFB |
| n_estabelecimentos_ativos_rfb | INTEGER | (calculado) | estabelecimentos da raiz com situação 02 |
| pessoa_fisica_vinculada | BOOLEAN | (calculado) | MEI, ou natureza 2135 (empresário individual), 2305 (EIRELI) ou 4xxx (pessoa física). Ver a nota LGPD no README da exportação. |
| regime_ultimo | VARCHAR / text | Regimes Tributários (ECF), via `regime.parquet` | Forma de tributação do ano mais recente da **raiz** (a ECF é entregue pela empresa, em geral pela matriz), sem as linhas de SCP. Se houver mais de uma forma no mesmo ano, vêm juntas com ` + ` (ex.: `LUCRO ARBITRADO + LUCRO REAL`). Formas mistas da RFB ficam como vêm (`LUCRO PRESUMIDO/REAL`). Sem registro na ECF e com `opcao_simples = true`: `SIMPLES (cadastro)`, derivado. Sem ECF e sem Simples: NULL. NULL também se a etapa `regime` não rodou. |
| regime_ultimo_ano | INTEGER | ECF.ano | Ano da escrituração do `regime_ultimo`. NULL no derivado. |
| regimes_por_ano | VARCHAR / text | ECF | Lista `ano:forma` em ordem de ano, separada por `; ` (ex.: `2021:LUCRO PRESUMIDO; 2022:LUCRO REAL`). |
| regime_derivado | BOOLEAN | (calculado) | `true` quando `regime_ultimo = 'SIMPLES (cadastro)'` (não vem da ECF, vem do cadastro do Simples na data de referência); `false` quando vem da ECF; NULL quando não há informação. |
| ref_rfb | VARCHAR / char(7) | | liberação RFB usada |

## Uso analítico (ressalvas)

- **Foto da RFB, não do momento da inscrição.** A situação cadastral (INAPTA, BAIXADA) pode ser **consequência** de não pagar. Não use como variável explicativa nem no cluster, só para descrever.
- **Duplicatas na RFB.** Se o mesmo CNPJ aparecer em dois arquivos, fica a linha com a `data_situacao_cadastral` mais recente. O caso é raro, e o número de duplicatas não é perdido.
- **Linhas malformadas.** O CSV pode ter linhas malformadas. Elas são puladas e contadas no `state.sqlite` (campo `rejects` de cada unidade), sem registrar o conteúdo.
- **Regime tributário.**
  - A ECF é anual e sai com defasagem: na versão de 13/03/2026, vai até o ano-calendário 2024. Lucro Real e Arbitrado começam em 2016, Imunes e Isentas em 2018 e **Lucro Presumido só em 2019** (o zip atual traz um CSV por ano, de 2019 a 2024). Ausência num ano antes disso não quer dizer outro regime.
  - Quem não entrega ECF não aparece: optantes do Simples e MEI, inativas, órgãos públicos. O `SIMPLES (cadastro)` é só um preenchimento derivado, com a foto do Simples de 2026-03, e não diz o regime de anos passados. Uma empresa que saiu do Lucro Presumido para o Simples depois do último ano da ECF continua com o último regime da ECF.
  - Para usar o regime "na época da dívida", junte `regime.parquet` por `cnpj_raiz` e `ano`, em vez de usar `regime_ultimo`.

## Tabela local `regime_tributario/<ref>/regime.parquet`

Grão: 1 linha por (`cnpj`, `ano`, `forma_tributacao`, `cnpj_da_scp`, `quantidade_de_escrituracoes`), só para raízes devedoras. Fonte: Regimes Tributários (ECF), leiaute nos dicionários `entidades-*-dicionario.csv` (link acima).

| coluna | tipo | origem | regra |
|---|---|---|---|
| cnpj | VARCHAR(14) | ECF.cnpj | CNPJ do declarante, normalizado (sem pontuação) |
| cnpj_raiz | VARCHAR(8) | (calculado) | 8 primeiros caracteres; é a chave do join com os devedores |
| ano | INTEGER | ECF.ano | ano da escrituração |
| forma_tributacao | VARCHAR | ECF.forma_de_tributacao | maiúsculas, como vem (`LUCRO REAL`, `LUCRO PRESUMIDO`, `LUCRO ARBITRADO`, `LUCRO PRESUMIDO/REAL`, `IMUNE DE IRPJ`, `ISENTO DO IRPJ`…) |
| cnpj_da_scp | VARCHAR(14) | ECF.cnpj_da_scp | CNPJ da Sociedade em Conta de Participação; `0` na fonte vira NULL (a própria empresa) |
| quantidade_de_escrituracoes | INTEGER | ECF | ECFs entregues no ano com essa forma |
| arquivos | VARCHAR[] | (calculado) | zips em que a linha aparece (as formas mistas aparecem em mais de um) |
| cnpj_devedor | BOOLEAN | (calculado) | o CNPJ de 14 caracteres do declarante está na lista de devedores (senão, casou só pela raiz) |
| kb, ref_rfb | | | bucket da raiz e liberação |

## Tabela local `socios/<ref>/socios.parquet` (só no HD)

Grão: 1 linha por vínculo sócio–empresa (linhas idênticas da RFB contam uma vez). Entra a linha se `cnpj_basico` é uma raiz devedora **ou** se o sócio é PJ e a raiz do CNPJ dele é devedora (segundo grau). Fonte: `Socios*.zip`, leiaute em [`cnpj-metadados.pdf`](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf).

| coluna | tipo | origem RFB | regra |
|---|---|---|---|
| cnpj_basico | VARCHAR(8) | CNPJ BÁSICO | raiz da empresa |
| identificador_socio | VARCHAR(1) | IDENTIFICADOR DE SÓCIO | 1 PJ, 2 PF, 3 estrangeiro |
| identificador_socio_descricao | VARCHAR | leiaute | |
| nome_socio | VARCHAR | NOME DO SÓCIO / RAZÃO SOCIAL | **dado pessoal** (PF) |
| cpf_cnpj_socio | VARCHAR | CNPJ/CPF DO SÓCIO | como vem: CPF mascarado `***xxxxxx**`, CNPJ completo para PJ, vazio (NULL) para estrangeiro |
| socio_cnpj, socio_cnpj_raiz | VARCHAR(14), VARCHAR(8) | (calculado) | só para PJ: CNPJ normalizado e a raiz dele |
| qualificacao_socio, qualificacao_socio_descricao | VARCHAR(2), VARCHAR | QUALIFICAÇÃO DO SÓCIO + Qualificacoes.zip | |
| data_entrada_sociedade | DATE | DATA DE ENTRADA SOCIEDADE | `YYYYMMDD`; `00000000` vira NULL |
| pais, pais_descricao | VARCHAR(3), VARCHAR | PAIS + Paises.zip | só para estrangeiro |
| representante_legal | VARCHAR | REPRESENTANTE LEGAL | CPF mascarado; `***000000**` = sem representante |
| nome_representante | VARCHAR | NOME DO REPRESENTANTE | **dado pessoal** |
| qualificacao_representante, qualificacao_representante_descricao | VARCHAR(2), VARCHAR | QUALIFICAÇÃO DO REPRESENTANTE LEGAL + Qualificacoes.zip | |
| faixa_etaria, faixa_etaria_descricao | VARCHAR(1), VARCHAR | FAIXA ETÁRIA | 0 não se aplica, 1 0–12, 2 13–20, 3 21–30, 4 31–40, 5 41–50, 6 51–60, 7 61–70, 8 71–80, 9 mais de 80 |
| empresa_devedora | BOOLEAN | (calculado) | `cnpj_basico` é raiz devedora |
| socio_pj_devedor | BOOLEAN | (calculado) | sócio PJ cuja raiz é devedora |
| kb, arquivo_origem, ref_rfb | | | bucket, zip de origem, liberação |

**Chave de PF aproximada:** o CPF vem mascarado, então `nome_socio + cpf_cnpj_socio` é só uma chave aproximada. Homônimos com o mesmo miolo de CPF se fundem, e grafias diferentes da mesma pessoa se separam.

## Tabela local `socios/<ref>/socios_arestas.parquet` (só no HD)

Uma aresta por linha de `socios.parquet`, pronta para NetworkX/igraph.

| coluna | regra |
|---|---|
| source | `cnpj_basico` (raiz da empresa) |
| target | PJ: CNPJ de 14 caracteres do sócio. PF e estrangeiro: `cpf_cnpj_socio|NOME` (chave aproximada) |
| target_tipo | `PJ`, `PF`, `ESTRANGEIRO` |
| target_chave_aproximada | `true` quando o nó não é um CNPJ |
| target_raiz | raiz do sócio PJ (use para ligar ao `source` de outra empresa) |
| target_documento, target_nome | documento e nome como vêm da RFB |
| tipo_vinculo, tipo_vinculo_descricao | qualificação do sócio |
| data_entrada_sociedade | |
| source_devedora, target_devedor | flags de devedor |

## Tabela local opcional `socios/<ref>/socios_hashed.parquet`

Gerada por `socios --hash-salt-env VAR`. Tem as mesmas colunas de `socios.parquet`, **menos** `nome_socio`, `cpf_cnpj_socio`, `representante_legal` e `nome_representante`, e mais:
- `socio_id` = HMAC-SHA256(sal, `upper(trim(nome_socio)) + '|' + cpf_cnpj_socio`), em hex;
- `representante_id`: o mesmo cálculo com nome e CPF do representante, ou NULL quando não há representante;
- `target_id`: `socio_cnpj` para PJ, `socio_id` para os demais.

## Colunas só do pacote de export

| coluna | tipo | regra |
|---|---|---|
| capital_social_faixa | VARCHAR | faixa do capital social: `0`, `ate 1 mil`, `1 mil a 10 mil`, `10 mil a 50 mil`, `50 mil a 100 mil`, `100 mil a 500 mil`, `500 mil a 1 milhao`, `1 milhao a 10 milhoes`, `mais de 10 milhoes` |
| cnpj_anonimizado | BOOLEAN | `true` quando `cnpj`/`cnpj_raiz` são ids HMAC-SHA256 (política `anonimizar`, linhas `pessoa_fisica_vinculada`) |
| municipio_generalizado | BOOLEAN | `true` quando o município foi retirado (política `anonimizar` com k<5 na célula município × divisão CNAE, ou política `suprimir`); a UF fica |

No Postgres, `cnpj` e `cnpj_raiz` são `varchar(64)` no pacote, para caber o id HMAC.

## Tabela exportável opcional `socios_vinculos` (`export --with-socios --hash-salt-env VAR`)

| coluna | regra |
|---|---|
| cnpj_basico | raiz da empresa, em claro (chave da API); na política `anonimizar`, empresas PF-vinculadas recebem o HMAC da raiz, igual ao `cnpj_raiz` de `cnpj_enriquecido` |
| socio_id | PJ: CNPJ do sócio em claro (ou o HMAC dele, se for PF-vinculado na política `anonimizar`). PF e estrangeiro: HMAC-SHA256(sal, `NOME_NORMALIZADO|miolo do CPF`), com nome sem acento, maiúsculo, espaços simples. Mesmo sócio, mesmo id em todas as empresas |
| socio_tipo, identificador_socio | `PJ`/`PF`/`ESTRANGEIRO` e o código 1/2/3 |
| socio_cnpj_raiz | só PJ |
| qualificacao_socio, qualificacao_socio_descricao | |
| decada_entrada_sociedade | versão API (padrão, `--socios-detail api`): só a década de entrada (ex.: 2010 = 2010–2019) |
| ano_entrada_sociedade | só no pacote local (`--socios-detail local`, pasta `..._LOCAL_NAO_SUBIR`): o ano exato. Esse pacote nunca vai para a API |
| faixa_etaria, faixa_etaria_descricao | |
| empresa_devedora, socio_pj_devedor, ref_rfb | |

**Não entram:** nome, CPF mascarado, representante legal (CPF e nome), qualificação do representante, país, razão social do sócio PJ, data completa de entrada (e, na versão API, o ano exato). A carga apaga e regrava as linhas do mesmo `ref_rfb` numa transação, então é idempotente. O README do pacote traz o relatório de risco de reidentificação (contagens de combinações com k<5).
