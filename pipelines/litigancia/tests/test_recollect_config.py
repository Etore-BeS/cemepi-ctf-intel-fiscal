"""Tests for recoleta campaign config loading and monthly range expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrapers.recollect_config import (
    build_date_key,
    build_folder_name,
    expand_monthly_ranges,
    load_recollect_config,
    resolve_ranges,
)

CONFIGS_DIR = (
    Path(__file__).resolve().parents[1] / "scripts" / "data" / "recollect_configs"
)


def test_expand_monthly_ranges_first_and_last_month() -> None:
    ranges = expand_monthly_ranges("01/01/2016", "30/06/2026")
    assert ranges[0] == ("01/01/2016", "31/01/2016")
    assert ranges[-1] == ("01/06/2026", "30/06/2026")
    assert len(ranges) == 126


def test_expand_monthly_ranges_partial_start_month() -> None:
    ranges = expand_monthly_ranges("15/03/2016", "30/06/2016")
    assert ranges[0] == ("15/03/2016", "31/03/2016")
    assert ranges[-1] == ("01/06/2016", "30/06/2016")


def test_expand_monthly_ranges_single_month() -> None:
    ranges = expand_monthly_ranges("01/02/2016", "29/02/2016")
    assert ranges == [("01/02/2016", "29/02/2016")]


def test_expand_monthly_ranges_invalid_span() -> None:
    with pytest.raises(ValueError, match="start must be <= end"):
        expand_monthly_ranges("31/12/2025", "01/01/2025")


def test_build_folder_and_date_key() -> None:
    date_range = ("01/01/2016", "31/01/2016")
    assert build_folder_name("coleta_assunto_icms", date_range) == (
        "coleta_assunto_icms_01_01_2016_31_01_2016"
    )
    assert build_date_key("round2_assunto_icms", date_range) == (
        "round2_assunto_icms_01_01_2016_31_01_2016"
    )


def test_load_round2_config() -> None:
    config = load_recollect_config(CONFIGS_DIR / "round2_assunto_icms.json")
    assert config.id == "round2_assunto_icms"
    assert config.execution == 2
    assert config.mode == "assunto_tree"
    assert config.assuntos_juridicos is not None
    assert len(config.assuntos_juridicos) == 4
    assert config.assunto_tree_ids_ref == [5946, 7061, 5947, 10531]
    assert config.face is not None
    assert config.face.enabled is True
    assert config.face.workers == 2
    assert config.face.delay_sec == 1.5


def test_resolve_ranges_from_date_span() -> None:
    config = load_recollect_config(CONFIGS_DIR / "round2_assunto_icms.json")
    ranges, threshold = resolve_ranges(config)
    assert threshold == 500
    assert len(ranges) == 126
    assert ranges[0] == ["01/01/2016", "31/01/2016"]
    assert ranges[-1] == ["01/06/2026", "30/06/2026"]


def test_format_assunto_tree_ids_uses_raw_commas() -> None:
    from scrapers.juscraper_collect import format_assunto_tree_ids

    assert format_assunto_tree_ids([5946, 7061, 5947, 10531]) == (
        "5946,7061,5947,10531"
    )
    assert "%2C" not in format_assunto_tree_ids([5946, 7061])


def test_parse_face_settings_defaults() -> None:
    from scrapers.recollect_config import parse_face_settings

    face = parse_face_settings(None)
    assert face.enabled is True
    assert face.workers == 2
