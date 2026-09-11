from utils.pf_pseudonym import pf_id_from_name, pseudonymize_face_parties


def test_pf_id_is_stable() -> None:
    a = pf_id_from_name("João da Silva", salt="test-salt")
    b = pf_id_from_name("João da Silva", salt="test-salt")
    assert a == b
    assert a.startswith("pf_")


def test_pf_id_differs_by_name() -> None:
    a = pf_id_from_name("João da Silva", salt="test-salt")
    b = pf_id_from_name("Maria Souza", salt="test-salt")
    assert a != b


def test_pseudonymize_only_pf_parties() -> None:
    records = pseudonymize_face_parties(
        autores_raw="Prefeitura Municipal",
        reus_raw="João da Silva, Danfat Industria e Comercio Ltda",
        salt="test-salt",
    )
    assert len(records) == 1
    assert records[0].pf_role == "reu"
    assert records[0].pf_id.startswith("pf_")
