from utils.pf_redaction import anonymize_decision


def test_redacts_cpf_email_and_pf_name() -> None:
    decisao = (
        "Executado: João da Silva, CPF 123.456.789-09, "
        "contato joao@example.com. Exequente: Prefeitura Municipal."
    )
    result = anonymize_decision(
        decisao,
        autores_raw="Prefeitura Municipal",
        reus_raw="João da Silva",
    )
    assert "[CPF]" in result.decisao_anonimizada
    assert "[EMAIL]" in result.decisao_anonimizada
    assert "[PESSOA]" in result.decisao_anonimizada
    assert "123.456.789-09" not in result.decisao_anonimizada
    assert "joao@example.com" not in result.decisao_anonimizada


def test_preserves_pj_party_name() -> None:
    decisao = "Executado: Danfat Industria e Comercio Ltda CNPJ: 01.531.421/0001-01"
    result = anonymize_decision(
        decisao,
        autores_raw="Fazenda do Estado de São Paulo",
        reus_raw="Danfat Industria e Comercio Ltda",
    )
    assert "Danfat Industria e Comercio Ltda" in result.decisao_anonimizada


def test_does_not_redact_cnj_metadata_as_phone() -> None:
    decisao = "Processo nº 1502550-72.2021.8.26.0319\t2021 Classe Execução Fiscal"
    result = anonymize_decision(decisao, autores_raw="Prefeitura", reus_raw="João Silva")
    assert "[PHONE]" not in result.decisao_anonimizada
    assert "0319" in result.decisao_anonimizada


def test_redacts_labeled_phone() -> None:
    decisao = "Telefone: (11) 98765-4321 para contato."
    result = anonymize_decision(decisao, autores_raw="Prefeitura", reus_raw="João Silva")
    assert "[PHONE]" in result.decisao_anonimizada
    assert "98765" not in result.decisao_anonimizada


def test_skips_uniao_federal_as_pf_name() -> None:
    decisao = "Executado: União Federal - PRFN"
    result = anonymize_decision(
        decisao,
        autores_raw="Prefeitura",
        reus_raw="União Federal - PRFN",
    )
    assert "União Federal - PRFN" in result.decisao_anonimizada
    assert result.redaction_counts.get("pf_name", 0) == 0
