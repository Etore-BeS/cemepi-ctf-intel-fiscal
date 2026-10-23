"""Layout of the RFB open CNPJ files (metadata 'cnpj-metadados.pdf'): no header, ';' separator, '"' quote, Latin-1.

Privacy rule enforced here: only the columns in KEEP_* ever leave the CSV reader in `enrich`. Names (razao_social,
nome_fantasia), addresses, CEP, phones, fax and e-mail are never selected, so they never reach cnpj_enriquecido.
The Socios* files (partners, with names and masked CPF) are read ONLY by the separate `socios` stage, whose outputs stay
in <workdir>/socios/ (local layer, never exported with names or CPF). See README "Sigilo".
"""
from __future__ import annotations

EMPRESAS_COLS = ["cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
                 "capital_social", "porte", "ente_federativo"]
ESTAB_COLS = ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia", "situacao_cadastral",
              "data_situacao", "motivo_situacao", "cidade_exterior", "pais", "data_inicio_atividade",
              "cnae_principal", "cnae_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
              "bairro", "cep", "uf", "municipio", "ddd1", "telefone1", "ddd2", "telefone2", "ddd_fax", "fax",
              "email", "situacao_especial", "data_situacao_especial"]
SIMPLES_COLS = ["cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples",
                "opcao_mei", "data_opcao_mei", "data_exclusao_mei"]
CODE_COLS = ["codigo", "descricao"]

KEEP = {
    "empresas": ["cnpj_basico", "natureza_juridica", "qualificacao_responsavel", "capital_social", "porte"],
    "estabelecimentos": ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "situacao_cadastral",
                         "data_situacao", "motivo_situacao", "data_inicio_atividade", "cnae_principal",
                         "cnae_secundaria", "uf", "municipio", "situacao_especial", "data_situacao_especial"],
    "simples": SIMPLES_COLS,
}
COLS = {"empresas": EMPRESAS_COLS, "estabelecimentos": ESTAB_COLS, "simples": SIMPLES_COLS}
FORBIDDEN = {"razao_social", "nome_fantasia", "tipo_logradouro", "logradouro", "numero", "complemento", "bairro",
             "cep", "ddd1", "telefone1", "ddd2", "telefone2", "ddd_fax", "fax", "email", "cidade_exterior"}
assert not FORBIDDEN & {c for v in KEEP.values() for c in v}

# Files of one monthly release downloaded by default. Socios*.zip only with `download --socios` (natural persons).
BIG_FILES = ([f"Empresas{i}.zip" for i in range(10)] + [f"Estabelecimentos{i}.zip" for i in range(10)]
             + ["Simples.zip"])
CODE_FILES = {"Cnaes.zip": "cnaes", "Naturezas.zip": "naturezas", "Municipios.zip": "municipios",
              "Motivos.zip": "motivos", "Qualificacoes.zip": "qualificacoes", "Paises.zip": "paises"}
RELEASE_FILES = BIG_FILES + list(CODE_FILES)


def kind_of(zip_name: str) -> str:
    n = zip_name.lower()
    for k in ("empresas", "estabelecimentos", "simples"):
        if n.startswith(k):
            return k
    if zip_name in CODE_FILES:
        return "codes"
    raise ValueError(f"Unknown RFB file: {zip_name}")


# Fixed domains from the RFB layout (not shipped as code tables).
PORTE = {"00": "NAO INFORMADO", "01": "MICRO EMPRESA", "03": "EMPRESA DE PEQUENO PORTE", "05": "DEMAIS"}
SITUACAO = {"01": "NULA", "02": "ATIVA", "03": "SUSPENSA", "04": "INAPTA", "08": "BAIXADA"}
MATRIZ_FILIAL = {"1": "MATRIZ", "2": "FILIAL"}
# CNAE 2.x sections from the 2-digit division (IBGE/CONCLA).
_SECTIONS = [("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39), ("F", 41, 43),
             ("G", 45, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 63), ("K", 64, 66), ("L", 68, 68),
             ("M", 69, 75), ("N", 77, 82), ("O", 84, 84), ("P", 85, 85), ("Q", 86, 88), ("R", 90, 93),
             ("S", 94, 96), ("T", 97, 97), ("U", 99, 99)]
CNAE_SECTION = {f"{d:02d}": s for s, a, b in _SECTIONS for d in range(a, b + 1)}

# Legal natures that are a natural person acting as an entrepreneur (LGPD note in the export README):
# 2135 Empresário (Individual); 2305 Empresa Individual de Responsabilidade Limitada (extinct EIRELI);
# group 4xxx "Pessoas Físicas" (e.g. 4014 Empresa Individual Imobiliária, 4120 Produtor Rural Pessoa Física).
PF_NATUREZAS_SQL = "(natureza_juridica IN ('2135', '2305') OR starts_with(natureza_juridica, '4'))"


def sql_map(d: dict[str, str]) -> str:
    return "MAP {" + ", ".join(f"'{k}': '{v}'" for k, v in d.items()) + "}"


# ---------------------------------------------------------------------------------------------------- Socios
# Layout: cnpj-metadados.pdf (RFB), section "SÓCIOS"; checked against Socios9.zip of the 2026-03 release (11 fields).
SOCIOS_FILES = [f"Socios{i}.zip" for i in range(10)]
SOCIOS_COLS = ["cnpj_basico", "identificador_socio", "nome_socio", "cpf_cnpj_socio", "qualificacao_socio",
               "data_entrada_sociedade", "pais", "representante_legal", "nome_representante",
               "qualificacao_representante", "faixa_etaria"]
IDENTIFICADOR_SOCIO = {"1": "PESSOA JURIDICA", "2": "PESSOA FISICA", "3": "ESTRANGEIRO"}
FAIXA_ETARIA = {"0": "NAO SE APLICA", "1": "0 A 12 ANOS", "2": "13 A 20 ANOS", "3": "21 A 30 ANOS",
                "4": "31 A 40 ANOS", "5": "41 A 50 ANOS", "6": "51 A 60 ANOS", "7": "61 A 70 ANOS",
                "8": "71 A 80 ANOS", "9": "MAIOR DE 80 ANOS"}
# Columns that identify natural persons (or carry a firm name): never exported, never in cnpj_enriquecido.
SOCIOS_PERSONAL = {"nome_socio", "cpf_cnpj_socio", "representante_legal", "nome_representante"}

# ------------------------------------------------------------------------------------------ Regime tributário
# RFB "Regimes Tributários" (ECF), separate public share (not inside the monthly YYYY-MM folder). Verified 2026-10-23:
# CSV with header, ',' separator, columns below; one CSV per zip except Lucro Presumido (one CSV per year).
# Dictionaries: entidades-regime-tributario-dicionarios/entidades-*-dicionario.csv in the same share.
REGIME_FILES = ["entidades-lucro-real.zip", "entidades-lucro-presumido.zip", "entidades-lucro-arbitrado.zip",
                "entidades-imunes-e-isentas.zip"]
REGIME_COLS = ["ano", "cnpj", "cnpj_da_scp", "forma_de_tributacao", "quantidade_de_escrituracoes"]
REGIME_SIMPLES_DERIVADO = "SIMPLES (cadastro)"
