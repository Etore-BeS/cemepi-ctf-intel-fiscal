import pytest

from utils.legalbert_ner import legalbert_available


@pytest.mark.skipif(not legalbert_available(), reason="nlp dependency group not installed")
def test_legalbert_find_pessoa_spans_smoke() -> None:
    from utils.legalbert_ner import find_pessoa_spans

    text = "O executado João da Silva compareceu à audiência."
    spans = find_pessoa_spans(text)
    assert isinstance(spans, list)
