from utils.cnpj_extraction import (
    extract_mentions,
    format_cnpj,
    is_cda_number,
    is_valid_cnpj,
)


def test_valid_cnpj_check_digits() -> None:
    assert is_valid_cnpj("01.531.421/0001-01") is True


def test_cda_number_rejection() -> None:
    snippet = (
        "Executado: Yasutaka Fukui CDA/Outros nº: "
        "51214262022401, 51214262022402"
    )
    assert is_cda_number("51.214.262/0224-01", snippet) is True


def test_cda_number_rejection_from_process_metadata_list() -> None:
    snippet = (
        "50445452024402, 50445452024403, 50445452024404, 50445452024405, "
        "50445452024406, 50445452024407, 50445452024408, 50445452024409, "
        "50445452024410 - Valor da causa"
    )
    assert is_cda_number("50.445.452/0244-10", snippet) is True


def test_extract_mentions_rejects_pf_debtor() -> None:
    decisao = (
        "Exequente: PREFEITURA MUNICIPAL DE SÃO PAULO "
        "Executado: Yasutaka Fukui "
        "CDA/Outros nº: 51214262022401"
    )
    mentions = extract_mentions(
        cd_processo="X",
        id_processo="1",
        decisao=decisao,
        autores_raw="PREFEITURA MUNICIPAL DE SÃO PAULO",
        reus_raw="Yasutaka Fukui",
    )
    assert mentions
    assert mentions[0].cnpj is None
    assert mentions[0].rejected_reason in {"cda_number", "natural_person"}


def test_extract_mentions_finds_pj_debtor() -> None:
    decisao = (
        "Requerente: Fazenda do Estado de São Paulo "
        "Requerido: Danfat Industria e Comercio Ltda "
        "CNPJ: 01.531.421/0001-01"
    )
    mentions = extract_mentions(
        cd_processo="X",
        id_processo="1",
        decisao=decisao,
        autores_raw="Fazenda do Estado de São Paulo",
        reus_raw="Danfat Industria e Comercio Ltda",
    )
    assert len(mentions) == 1
    assert mentions[0].cnpj == format_cnpj("01.531.421/0001-01")
    assert mentions[0].confidence == "high"


def test_extract_mentions_keeps_cnpj_when_syntax_party_truncated() -> None:
    decisao = (
        "Requerido: Marfe Borrachas Especiais Ind Com Lt "
        "CNPJ: 43.012.640/0001-24"
    )
    mentions = extract_mentions(
        cd_processo="X",
        id_processo="1",
        decisao=decisao,
        autores_raw="Fazenda do Estado de Sao Paulo",
        reus_raw="Marfe Borrachas Especiais Ind Com Ltda",
    )
    assert len(mentions) == 1
    assert mentions[0].cnpj == format_cnpj("43.012.640/0001-24")
    assert mentions[0].rejected_reason is None


def test_extract_mentions_keeps_address_block_cnpj_with_co_mentioned_pf() -> None:
    decisao = (
        "Executado: HR DO BRASIL LTDA., CNPJ 03.013.938/0001-34, "
        "com endereço à Uruguai, 117, Jardim Sao Luis, CEP 06502-300, "
        "Santana do Parnaíba - SP e JOELICE DE SOUZA BATISTA, CPF 299.443.968-93, "
        "com endereço à Rua General Chagas Santos, 177, Vila da Saude, "
        "CEP 04146-050, São Paulo - SP CNPJ: 03.013.938/0001-34"
    )
    mentions = extract_mentions(
        cd_processo="X",
        id_processo="1",
        decisao=decisao,
        autores_raw="Prefeitura de Santana de Parnaíba",
        reus_raw="HR DO BRASIL LTDA., JOELICE DE SOUZA BATISTA",
    )
    cnpj_mentions = [m for m in mentions if m.cnpj == format_cnpj("03.013.938/0001-34")]
    assert cnpj_mentions
    assert cnpj_mentions[0].rejected_reason is None
