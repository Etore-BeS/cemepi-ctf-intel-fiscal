from utils.quality_exploration import _heuristic_redaction_label


def test_heuristic_flags_uniao_federal_as_incorrect_pf_name() -> None:
    label, reason = _heuristic_redaction_label(
        {
            "label": "pf_name",
            "source": "face_party",
            "original_text": "União Federal - PRFN",
        }
    )
    assert label == "incorrect"
    assert reason in {"org_keyword_in_pf_name", "pj_indicator_in_name"}


def test_heuristic_accepts_pf_name() -> None:
    label, _ = _heuristic_redaction_label(
        {
            "label": "pf_name",
            "source": "face_party",
            "original_text": "Yasutaka Fukui",
        }
    )
    assert label == "correct"
