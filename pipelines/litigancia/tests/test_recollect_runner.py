"""Tests for recoleta runner state and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scrapers.recollect_runner import (
    DEFAULT_STATE,
    find_dedup_json,
    load_state,
    planned_phases,
    process_one_month_face_scrape,
    save_state,
)
from scrapers.recollect_config import FaceSettings, load_recollect_config

CONFIGS_DIR = (
    Path(__file__).resolve().parents[1] / "scripts" / "data" / "recollect_configs"
)


def test_load_state_legacy_without_pipeline_keys(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "processed_ranges": ["round2_assunto_icms_01_01_2016_31_01_2016"],
                "pipeline_completed_ranges": [
                    "round2_assunto_icms_01_01_2016_31_01_2016"
                ],
                "attempt_tracker": {"round2_assunto_icms_01_01_2016_31_01_2016": 1},
                "volume_history": {"round2_assunto_icms_01_01_2016_31_01_2016": [1170]},
            }
        ),
        encoding="utf-8",
    )

    state = load_state(state_path)
    assert state["processed_ranges"] == ["round2_assunto_icms_01_01_2016_31_01_2016"]
    assert state["pipeline_completed_ranges"] == [
        "round2_assunto_icms_01_01_2016_31_01_2016"
    ]
    assert state["face_scrape_completed_ranges"] == []
    assert state["face_silver_completed_ranges"] == []
    assert state["full_pipeline_completed_ranges"] == []
    assert state["face_scrape_attempt_tracker"] == {}
    assert state["face_history"] == {}


def test_save_state_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = dict(DEFAULT_STATE)
    state["full_pipeline_completed_ranges"] = ["month_a"]
    save_state(state_path, state)
    loaded = load_state(state_path)
    assert loaded["full_pipeline_completed_ranges"] == ["month_a"]


def test_find_dedup_json_nested(tmp_path: Path) -> None:
    month_dir = tmp_path / "coleta_assunto_icms_01_01_2016_31_01_2016" / "20260707"
    month_dir.mkdir(parents=True)
    json_path = month_dir / "process_grouped_all_assuntos.json"
    json_path.write_text('{"count": 1, "items": []}', encoding="utf-8")

    found = find_dedup_json(tmp_path / "coleta_assunto_icms_01_01_2016_31_01_2016")
    assert found == json_path


def test_find_dedup_json_missing(tmp_path: Path) -> None:
    assert find_dedup_json(tmp_path) is None


@pytest.mark.parametrize(
    ("state", "scrape_only", "ingest_only", "face_on", "expected"),
    [
        (
            {"processed_ranges": [], "pipeline_completed_ranges": []},
            False,
            False,
            True,
            ["scrape", "ingest"],
        ),
        (
            {"processed_ranges": ["month_a"], "pipeline_completed_ranges": []},
            False,
            False,
            True,
            ["ingest"],
        ),
        (
            {
                "processed_ranges": ["month_a"],
                "pipeline_completed_ranges": ["month_a"],
            },
            False,
            False,
            True,
            ["face_scrape", "face_silver"],
        ),
        (
            {
                "processed_ranges": ["month_a"],
                "pipeline_completed_ranges": ["month_a"],
                "full_pipeline_completed_ranges": ["month_a"],
            },
            False,
            False,
            True,
            ["skip"],
        ),
        (
            {"processed_ranges": [], "pipeline_completed_ranges": []},
            True,
            False,
            True,
            ["scrape"],
        ),
        (
            {"processed_ranges": ["month_a"], "pipeline_completed_ranges": []},
            False,
            True,
            True,
            ["ingest"],
        ),
        (
            {
                "processed_ranges": ["month_a"],
                "pipeline_completed_ranges": ["month_a"],
            },
            False,
            False,
            False,
            ["skip"],
        ),
    ],
)
def test_planned_phases(
    state: dict,
    scrape_only: bool,
    ingest_only: bool,
    face_on: bool,
    expected: list[str],
) -> None:
    assert (
        planned_phases(
            "month_a",
            state,
            scrape_only=scrape_only,
            ingest_only=ingest_only,
            face_on=face_on,
        )
        == expected
    )


def test_planned_phases_legacy_month_needs_face() -> None:
    state = {
        "processed_ranges": ["round2_assunto_icms_01_01_2016_31_01_2016"],
        "pipeline_completed_ranges": ["round2_assunto_icms_01_01_2016_31_01_2016"],
        "full_pipeline_completed_ranges": [],
    }
    assert planned_phases(
        "round2_assunto_icms_01_01_2016_31_01_2016",
        state,
    ) == ["face_scrape", "face_silver"]


def test_planned_phases_face_only() -> None:
    state = {
        "processed_ranges": ["month_a"],
        "pipeline_completed_ranges": ["month_a"],
        "face_scrape_completed_ranges": ["month_a"],
    }
    assert planned_phases("month_a", state, face_only=True) == ["face_silver"]


def test_planned_phases_skip_face() -> None:
    state = {
        "processed_ranges": ["month_a"],
        "pipeline_completed_ranges": ["month_a"],
    }
    assert planned_phases("month_a", state, skip_face=True, face_on=False) == ["skip"]


@patch("scrapers.recollect_runner.count_face_pending", return_value=0)
@patch("scrapers.recollect_runner.run_face_scrape_subprocess")
def test_face_scrape_marks_complete_when_no_pending(
    _mock_subprocess,
    _mock_pending,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_recollect_config(CONFIGS_DIR / "round2_assunto_icms.json")
    month_dir = tmp_path / "coleta_assunto_icms_01_01_2016_31_01_2016" / "20260707"
    month_dir.mkdir(parents=True)
    (month_dir / "process_grouped_all_assuntos.json").write_text(
        '{"count": 1, "items": []}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "scrapers.recollect_runner.COLLECT_ROOT",
        tmp_path,
    )

    face_scrape_completed: list[str] = []
    ok = process_one_month_face_scrape(
        config,
        date_range=["01/01/2016", "31/01/2016"],
        date_key="round2_assunto_icms_01_01_2016_31_01_2016",
        face=FaceSettings(),
        face_scrape_completed=face_scrape_completed,
        face_scrape_attempt_tracker={},
        face_history={},
        dates_to_scrape=[],
        compact=False,
    )
    assert ok is True
    assert face_scrape_completed == ["round2_assunto_icms_01_01_2016_31_01_2016"]
    _mock_subprocess.assert_not_called()
