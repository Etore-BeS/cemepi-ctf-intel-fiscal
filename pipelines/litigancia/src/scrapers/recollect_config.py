"""Load and expand recoleta campaign configs for juscraper drivers."""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

CollectionMode = Literal["pesquisa_livre", "assunto_tree"]

_DATE_FMT = "%d/%m/%Y"


@dataclass(frozen=True)
class DateSpan:
    start: str
    end: str
    granularity: str = "month"


@dataclass(frozen=True)
class FaceSettings:
    enabled: bool = True
    workers: int = 2
    delay_sec: float = 1.5
    fetch_batch: int = 500
    delta_batch: int = 200
    request_timeout: float = 15.0
    max_request_retries: int = 2


def parse_face_settings(raw: dict | None) -> FaceSettings:
    if not raw:
        return FaceSettings()
    return FaceSettings(
        enabled=bool(raw.get("enabled", True)),
        workers=int(raw.get("workers", 2)),
        delay_sec=float(raw.get("delay_sec", 1.5)),
        fetch_batch=int(raw.get("fetch_batch", 500)),
        delta_batch=int(raw.get("delta_batch", 200)),
        request_timeout=float(raw.get("request_timeout", 15.0)),
        max_request_retries=int(raw.get("max_request_retries", 2)),
    )


def apply_face_overrides(
    face: FaceSettings,
    *,
    workers: int | None = None,
    delay_sec: float | None = None,
    request_timeout: float | None = None,
    max_request_retries: int | None = None,
) -> FaceSettings:
    overrides: dict[str, int | float] = {}
    if workers is not None:
        overrides["workers"] = workers
    if delay_sec is not None:
        overrides["delay_sec"] = delay_sec
    if request_timeout is not None:
        overrides["request_timeout"] = request_timeout
    if max_request_retries is not None:
        overrides["max_request_retries"] = max_request_retries
    if not overrides:
        return face
    return replace(face, **overrides)


@dataclass(frozen=True)
class RecollectConfig:
    id: str
    execution: int
    mode: CollectionMode
    description: str
    folder_prefix: str
    tribunal: str
    classes: list[str]
    threshold: int
    max_workers: int
    max_retries: int
    state_file: str
    log_file: str
    pesquisa_terms: list[str]
    campaign_subdir: str | None = None
    assuntos_juridicos: list[str] | None = None
    assunto_tree_ids_ref: list[int] | None = None
    date_span: DateSpan | None = None
    ranges_file: str | None = None
    face: FaceSettings | None = None


def _parse_date(value: str) -> date:
    day, month, year = value.split("/")
    return date(int(year), int(month), int(day))


def _format_date(value: date) -> str:
    return value.strftime(_DATE_FMT)


def _month_end(value: date) -> date:
    last_day = calendar.monthrange(value.year, value.month)[1]
    return date(value.year, value.month, last_day)


def expand_monthly_ranges(start: str, end: str) -> list[tuple[str, str]]:
    """Expand inclusive calendar span into monthly [inicio, fim] pairs (DD/MM/YYYY)."""
    start_dt = _parse_date(start)
    end_dt = _parse_date(end)
    if start_dt > end_dt:
        raise ValueError(f"start must be <= end: {start} > {end}")

    ranges: list[tuple[str, str]] = []
    cursor = date(start_dt.year, start_dt.month, 1)
    while cursor <= end_dt:
        month_start = max(cursor, start_dt)
        month_end = min(_month_end(cursor), end_dt)
        ranges.append((_format_date(month_start), _format_date(month_end)))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return ranges


def build_folder_name(folder_prefix: str, date_range: tuple[str, str]) -> str:
    start, end = date_range
    return f"{folder_prefix}_{start.replace('/', '_')}_{end.replace('/', '_')}"


def build_date_key(config_id: str, date_range: tuple[str, str]) -> str:
    start, end = date_range
    return f"{config_id}_{start.replace('/', '_')}_{end.replace('/', '_')}"


def _data_dir_for_config(config_path: Path) -> Path:
    return config_path.resolve().parents[1]


def load_recollect_config(path: Path | str) -> RecollectConfig:
    config_path = Path(path)
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    date_span_raw = raw.get("date_span")
    date_span = None
    if date_span_raw is not None:
        date_span = DateSpan(
            start=date_span_raw["start"],
            end=date_span_raw["end"],
            granularity=date_span_raw.get("granularity", "month"),
        )

    mode = raw["mode"]
    if mode not in ("pesquisa_livre", "assunto_tree"):
        raise ValueError(f"Invalid mode: {mode}")

    return RecollectConfig(
        id=raw["id"],
        execution=int(raw["execution"]),
        mode=mode,
        description=raw["description"],
        folder_prefix=raw["folder_prefix"],
        tribunal=raw["tribunal"],
        classes=list(raw.get("classes") or []),
        threshold=int(raw.get("threshold", 1000)),
        max_workers=int(raw.get("max_workers", 4)),
        max_retries=int(raw.get("max_retries", 5)),
        state_file=raw["state_file"],
        log_file=raw["log_file"],
        pesquisa_terms=list(raw.get("pesquisa_terms") or []),
        campaign_subdir=raw.get("campaign_subdir"),
        assuntos_juridicos=list(raw["assuntos_juridicos"])
        if raw.get("assuntos_juridicos")
        else None,
        assunto_tree_ids_ref=list(raw["assunto_tree_ids_ref"])
        if raw.get("assunto_tree_ids_ref")
        else None,
        date_span=date_span,
        ranges_file=raw.get("ranges_file"),
        face=parse_face_settings(raw.get("face")),
    )


def resolve_ranges(
    config: RecollectConfig,
    *,
    data_dir: Path | None = None,
    config_path: Path | None = None,
) -> tuple[list[list[str]], int]:
    """Return ([[inicio, fim], ...], threshold) for the campaign."""
    if config.date_span is not None:
        if config.date_span.granularity != "month":
            raise ValueError(f"Unsupported granularity: {config.date_span.granularity}")
        monthly = expand_monthly_ranges(config.date_span.start, config.date_span.end)
        return [[start, end] for start, end in monthly], config.threshold

    if not config.ranges_file:
        raise ValueError(f"Config {config.id} has no date_span or ranges_file")

    base = data_dir
    if base is None and config_path is not None:
        base = _data_dir_for_config(config_path)
    if base is None:
        raise ValueError("data_dir or config_path required to load ranges_file")

    ranges_path = base / config.ranges_file
    with open(ranges_path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data, config.threshold
    if isinstance(data, dict):
        return data.get("ranges", []), int(data.get("threshold", config.threshold))
    raise ValueError(f"Invalid format in {ranges_path}")
