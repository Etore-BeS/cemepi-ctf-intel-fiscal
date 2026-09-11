from utils.party_identity import is_natural_person, split_parties


def test_is_natural_person_pf_name() -> None:
    assert is_natural_person("Yasutaka Fukui") is True


def test_is_natural_person_pj_name() -> None:
    assert is_natural_person("Danfat Industria e Comercio Ltda") is False
    assert is_natural_person("União Federal - PRFN") is False
    assert is_natural_person("Importacao e Exp") is False
    assert is_natural_person("Marfe Borrachas Especiais Ind Com Lt") is False
    assert is_natural_person("M.a.g & Associado Impressoes Digitais Lt") is False
    assert is_natural_person("Tunning Aliancas Estrat.mark.exec.s/c Lt") is False


def test_should_reject_as_natural_person() -> None:
    from utils.party_identity import should_reject_as_natural_person

    assert (
        should_reject_as_natural_person(
            party_syntax="Aol System",
            candidate="Aol System Consultoria e Assessoria Em Informatica Ltda",
            pattern_id="debtor_executado_cnpj_first",
        )
        is False
    )
    assert (
        should_reject_as_natural_person(
            party_syntax=None,
            candidate="Joelice de Souza Batista",
            pattern_id="debtor_address_block",
        )
        is False
    )
    assert (
        should_reject_as_natural_person(
            party_syntax="Yasutaka Fukui",
            candidate="Yasutaka Fukui",
            pattern_id="debtor_requerido",
        )
        is True
    )


def test_split_parties_skips_missing_marker() -> None:
    assert split_parties("Prefeitura, NÃO HÁ REGISTRO") == ["Prefeitura"]
