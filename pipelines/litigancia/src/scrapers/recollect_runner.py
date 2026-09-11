"""Shared recoleta orchestration: state, monthly scrape, and bronze→silver ingest."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import traceback
from pathlib import Path

from config.paths import COLLECT_ROOT, PIPELINE_ROOT
from config.scripts import (
    CREATE_SILVER_FACE_LAYER,
    RUN_ONE_JSON_TO_SILVER,
    SCRAPE_FACE_TO_BRONZE,
    load_script_module,
)
from scrapers.juscraper_collect import (
    config_meta_from_recollect,
    main as juscraper_main,
)
from scrapers.recollect_config import (
    FaceSettings,
    RecollectConfig,
    apply_face_overrides,
    build_date_key,
    build_folder_name,
    load_recollect_config,
    resolve_ranges,
)

INGEST_ERROR = "INGEST_ERROR"
FACE_SCRAPE_ERROR = "FACE_SCRAPE_ERROR"
FACE_SILVER_ERROR = "FACE_SILVER_ERROR"

DEFAULT_STATE: dict = {
    "processed_ranges": [],
    "pipeline_completed_ranges": [],
    "face_scrape_completed_ranges": [],
    "face_silver_completed_ranges": [],
    "full_pipeline_completed_ranges": [],
    "attempt_tracker": {},
    "ingest_attempt_tracker": {},
    "face_scrape_attempt_tracker": {},
    "face_silver_attempt_tracker": {},
    "volume_history": {},
    "ingest_history": {},
    "face_history": {},
}


def configure_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="a", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            with open(state_path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            logging.error("Error loading state file: %s", e)
            raw = {}
    else:
        raw = {}

    state = dict(DEFAULT_STATE)
    state.update(raw)
    state.setdefault("processed_ranges", [])
    state.setdefault("pipeline_completed_ranges", [])
    state.setdefault("face_scrape_completed_ranges", [])
    state.setdefault("face_silver_completed_ranges", [])
    state.setdefault("full_pipeline_completed_ranges", [])
    state.setdefault("attempt_tracker", {})
    state.setdefault("ingest_attempt_tracker", {})
    state.setdefault("face_scrape_attempt_tracker", {})
    state.setdefault("face_silver_attempt_tracker", {})
    state.setdefault("volume_history", {})
    state.setdefault("ingest_history", {})
    state.setdefault("face_history", {})
    return state


def save_state(state_path: Path, state: dict) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)


def should_unlock_scrape_range(
    key: str, volume_history: dict, recollect_threshold: int
) -> bool:
    history = volume_history.get(key, [])
    if not history:
        return False
    last = history[-1]
    if last == "ERROR" or last == 0:
        logging.info("Unlocking %s for rerun (last state was %s)", key, last)
        return True
    if isinstance(last, (int, float)) and last < recollect_threshold:
        logging.info(
            "Unlocking %s for rerun (volume %s < threshold %s)",
            key,
            last,
            recollect_threshold,
        )
        return True
    return False


def reconcile_scrape_state(state: dict, recollect_threshold: int) -> None:
    original_processed = state.get("processed_ranges", [])
    volume_history = state.get("volume_history", {})

    actual_success = []
    for key in original_processed:
        if should_unlock_scrape_range(key, volume_history, recollect_threshold):
            if key in state.get("attempt_tracker", {}):
                state["attempt_tracker"][key] = 0
        else:
            actual_success.append(key)

    state["processed_ranges"] = actual_success


def find_dedup_json(folder: Path) -> Path | None:
    matches = sorted(folder.rglob("process_grouped_all_assuntos.json"))
    if not matches:
        return None
    return matches[-1]


def count_volume_from_folder(base_output_dir: Path) -> int:
    total_volume = 0
    for j_file in base_output_dir.rglob("process_grouped_all_assuntos.json"):
        try:
            with open(j_file, encoding="utf-8") as jf:
                data = json.load(jf)
                total_volume += data.get("count", 0)
        except Exception as e:
            logging.error("Error reading JSON %s: %s", j_file, e)
    return total_volume


def run_month_scrape(
    config: RecollectConfig,
    *,
    date_range: list[str],
    base_output_dir: str,
    config_meta: dict,
) -> None:
    juscraper_main(
        BASE_OUTPUT_DIR=base_output_dir,
        collection_mode=config.mode,
        tribunal=config.tribunal,
        date_range=tuple(date_range),
        classes=config.classes,
        varas=None,
        max_workers=config.max_workers,
        pesquisa_terms=config.pesquisa_terms,
        campaign_subdir=config.campaign_subdir,
        assuntos_juridicos=config.assuntos_juridicos,
        assunto_tree_ids=config.assunto_tree_ids_ref,
        config_meta=config_meta,
    )


def face_settings_for(config: RecollectConfig) -> FaceSettings:
    return config.face if config.face is not None else FaceSettings()


def face_enabled_for_run(config: RecollectConfig, *, skip_face: bool) -> bool:
    if skip_face:
        return False
    return face_settings_for(config).enabled


def count_face_pending(json_path: str) -> int:
    mod = load_script_module("scrape_face_to_bronze", domain="face", layer="scraping")
    return mod.count_pending(source_path=json_path)


def run_face_scrape_subprocess(
    json_path: Path,
    face: FaceSettings,
    *,
    compact: bool = False,
) -> int:
    cmd = [
        sys.executable,
        str(SCRAPE_FACE_TO_BRONZE),
        "--source-path",
        str(json_path),
        "--workers",
        str(face.workers),
        "--delay-sec",
        str(face.delay_sec),
        "--fetch-batch",
        str(face.fetch_batch),
        "--delta-batch",
        str(face.delta_batch),
        "--request-timeout",
        str(face.request_timeout),
        "--max-request-retries",
        str(face.max_request_retries),
        "--skip-end-count",
    ]
    if compact:
        cmd.append("--compact")
    logging.info("FACE scrape subprocess: %s", " ".join(cmd[1:]))
    result = subprocess.run(cmd, cwd=str(PIPELINE_ROOT))
    return result.returncode


def run_face_silver_subprocess(*, compact: bool = False) -> int:
    cmd = [sys.executable, str(CREATE_SILVER_FACE_LAYER), "--skip-backfill"]
    if compact:
        cmd.append("--compact")
    logging.info("FACE silver subprocess: %s", " ".join(cmd[1:]))
    result = subprocess.run(cmd, cwd=str(PIPELINE_ROOT))
    return result.returncode


def _mark_face_and_full_complete(
    date_key: str,
    *,
    face_scrape_completed: list[str],
    face_silver_completed: list[str],
    full_pipeline_completed: list[str],
) -> None:
    for bucket in (
        face_scrape_completed,
        face_silver_completed,
        full_pipeline_completed,
    ):
        if date_key not in bucket:
            bucket.append(date_key)


def maybe_mark_full_pipeline_without_face(
    date_key: str,
    *,
    pipeline_completed: list[str],
    full_pipeline_completed: list[str],
    face_on: bool,
) -> None:
    if face_on:
        return
    if date_key in pipeline_completed and date_key not in full_pipeline_completed:
        full_pipeline_completed.append(date_key)


def run_month_ingest(json_path: Path, *, compact: bool = False) -> int:
    cmd = [sys.executable, str(RUN_ONE_JSON_TO_SILVER), str(json_path)]
    if compact:
        cmd.append("--compact")
    logging.info("Ingest subprocess: %s", " ".join(cmd[1:]))
    result = subprocess.run(cmd, cwd=str(PIPELINE_ROOT))
    return result.returncode


def _accept_scrape(
    *,
    date_key: str,
    total_volume: int,
    current_attempt: int,
    max_retries: int,
    recollect_threshold: int,
    processed_keys: list[str],
    dates_to_scrape: list[list[str]],
    date_range: list[str],
) -> bool:
    """Return True if scrape is accepted (no requeue)."""
    if total_volume >= recollect_threshold:
        logging.info(
            "Volume meets recollect threshold! Successfully completed scrape for %s",
            date_key,
        )
        processed_keys.append(date_key)
        return True

    if current_attempt >= max_retries:
        logging.warning(
            "Volume (%s) still below recollect threshold (%s) after %s attempts. "
            "Accepting as is.",
            total_volume,
            recollect_threshold,
            max_retries,
        )
        processed_keys.append(date_key)
        return True

    logging.warning(
        "Volume (%s) is below recollect threshold (%s). Sending to the end of the line.",
        total_volume,
        recollect_threshold,
    )
    dates_to_scrape.append(date_range)
    logging.info("Taking a 10-second cooldown before moving to the next month...")
    time.sleep(10)
    return False


def _handle_scrape_exception(
    *,
    date_key: str,
    date_range: list[str],
    current_attempt: int,
    max_retries: int,
    volume_history: dict,
    processed_keys: list[str],
    dates_to_scrape: list[list[str]],
    exc: Exception,
) -> None:
    logging.error("CRITICAL ERROR processing %s: %s", date_key, str(exc))
    logging.error(traceback.format_exc())

    volume_history[date_key].append("ERROR")

    if current_attempt < max_retries:
        logging.warning("Sending failed job to the end of the line.")
        dates_to_scrape.append(date_range)
    else:
        logging.error(
            "Skipping %s permanently due to consistent critical errors.",
            date_key,
        )
        processed_keys.append(date_key)

    logging.info("Taking a 30-second cooldown to let the proxy/server recover...")
    time.sleep(30)


def process_one_month_scrape(
    config: RecollectConfig,
    *,
    date_range: list[str],
    date_key: str,
    recollect_threshold: int,
    config_meta: dict,
    processed_keys: list[str],
    attempt_tracker: dict,
    volume_history: dict,
    dates_to_scrape: list[list[str]],
) -> bool:
    """Run scrape for one month. Returns True if scrape accepted this round."""
    if date_key in processed_keys:
        logging.info("Skipping scrape for %s (already processed)", date_key)
        return True

    attempt_tracker[date_key] = attempt_tracker.get(date_key, 0) + 1
    current_attempt = attempt_tracker[date_key]

    if date_key not in volume_history:
        volume_history[date_key] = []

    logging.info(
        "\n%s\nStarting scrape: %s (Attempt %s/%s)\n%s",
        "=" * 50,
        date_key,
        current_attempt,
        config.max_retries,
        "=" * 50,
    )

    folder_name = build_folder_name(config.folder_prefix, tuple(date_range))
    base_output_dir = COLLECT_ROOT / folder_name

    try:
        run_month_scrape(
            config,
            date_range=date_range,
            base_output_dir=str(base_output_dir),
            config_meta=config_meta,
        )
        total_volume = count_volume_from_folder(base_output_dir)
        volume_history[date_key].append(total_volume)
        history_str = " -> ".join(map(str, volume_history[date_key]))

        logging.info("Volume collected this round: %s processos", total_volume)
        logging.info("Volume history across attempts: [%s]", history_str)

        return _accept_scrape(
            date_key=date_key,
            total_volume=total_volume,
            current_attempt=current_attempt,
            max_retries=config.max_retries,
            recollect_threshold=recollect_threshold,
            processed_keys=processed_keys,
            dates_to_scrape=dates_to_scrape,
            date_range=date_range,
        )
    except Exception as e:
        _handle_scrape_exception(
            date_key=date_key,
            date_range=date_range,
            current_attempt=current_attempt,
            max_retries=config.max_retries,
            volume_history=volume_history,
            processed_keys=processed_keys,
            dates_to_scrape=dates_to_scrape,
            exc=e,
        )
        return date_key in processed_keys


def process_one_month_ingest(
    config: RecollectConfig,
    *,
    date_range: list[str],
    date_key: str,
    pipeline_completed: list[str],
    ingest_attempt_tracker: dict,
    ingest_history: dict,
    dates_to_scrape: list[list[str]],
    compact: bool,
) -> bool:
    """Run bronze→silver for one month. Returns True if pipeline step completed."""
    if date_key in pipeline_completed:
        return True

    folder_name = build_folder_name(config.folder_prefix, tuple(date_range))
    base_output_dir = COLLECT_ROOT / folder_name
    json_path = find_dedup_json(base_output_dir)

    if json_path is None:
        logging.info(
            "No dedup JSON for %s (empty month). Marking pipeline complete.",
            date_key,
        )
        pipeline_completed.append(date_key)
        return True

    ingest_attempt_tracker[date_key] = ingest_attempt_tracker.get(date_key, 0) + 1
    current_attempt = ingest_attempt_tracker[date_key]
    if date_key not in ingest_history:
        ingest_history[date_key] = []

    logging.info(
        "\n%s\nStarting ingest: %s (Attempt %s/%s)\n%s",
        "=" * 50,
        date_key,
        current_attempt,
        config.max_retries,
        "=" * 50,
    )
    logging.info("JSON: %s", json_path)

    code = run_month_ingest(json_path, compact=compact)
    if code == 0:
        logging.info("Ingest completed for %s", date_key)
        pipeline_completed.append(date_key)
        ingest_history[date_key].append("OK")
        return True

    ingest_history[date_key].append(INGEST_ERROR)
    logging.error("Ingest failed for %s (exit %s)", date_key, code)

    if current_attempt >= config.max_retries:
        logging.warning(
            "Ingest for %s failed after %s attempts. Marking pipeline complete anyway.",
            date_key,
            config.max_retries,
        )
        pipeline_completed.append(date_key)
        return True

    logging.warning("Requeueing %s for ingest retry (scrape will not rerun).", date_key)
    dates_to_scrape.append(date_range)
    time.sleep(10)
    return False


def process_one_month_face_scrape(
    config: RecollectConfig,
    *,
    date_range: list[str],
    date_key: str,
    face: FaceSettings,
    face_scrape_completed: list[str],
    face_scrape_attempt_tracker: dict,
    face_history: dict,
    dates_to_scrape: list[list[str]],
    compact: bool,
) -> bool:
    """Scrape FACE for all cd_processo from this month. Returns True when complete."""
    if date_key in face_scrape_completed:
        return True

    folder_name = build_folder_name(config.folder_prefix, tuple(date_range))
    json_path = find_dedup_json(COLLECT_ROOT / folder_name)

    if json_path is None:
        logging.info(
            "No dedup JSON for %s — skipping FACE scrape (empty month).",
            date_key,
        )
        return True

    if date_key not in face_history:
        face_history[date_key] = []

    json_path_str = str(json_path)
    pending = count_face_pending(json_path_str)
    logging.info("FACE pending for %s: %s cd_processo", date_key, pending)

    if pending == 0:
        logging.info("FACE scrape already complete for %s", date_key)
        face_scrape_completed.append(date_key)
        return True

    logging.info(
        "\n%s\nStarting FACE scrape: %s\n%s",
        "=" * 50,
        date_key,
        "=" * 50,
    )

    while count_face_pending(json_path_str) > 0:
        face_scrape_attempt_tracker[date_key] = (
            face_scrape_attempt_tracker.get(date_key, 0) + 1
        )
        current_attempt = face_scrape_attempt_tracker[date_key]

        if current_attempt > config.max_retries:
            face_history[date_key].append(FACE_SCRAPE_ERROR)
            logging.error(
                "FACE scrape for %s failed after %s attempts. Requeueing month.",
                date_key,
                config.max_retries,
            )
            dates_to_scrape.append(date_range)
            time.sleep(10)
            return False

        logging.info(
            "FACE scrape wave %s/%s for %s (pending=%s)",
            current_attempt,
            config.max_retries,
            date_key,
            count_face_pending(json_path_str),
        )
        code = run_face_scrape_subprocess(json_path, face, compact=compact)
        pending_after = count_face_pending(json_path_str)
        logging.info(
            "FACE scrape wave finished (exit=%s, pending=%s)",
            code,
            pending_after,
        )

        if pending_after == 0:
            break

        if code != 0:
            face_history[date_key].append(FACE_SCRAPE_ERROR)
            logging.warning(
                "FACE scrape made no progress for %s (exit %s). Retrying...",
                date_key,
                code,
            )

    if count_face_pending(json_path_str) == 0:
        logging.info("FACE scrape completed for %s", date_key)
        face_scrape_completed.append(date_key)
        face_history[date_key].append("FACE_SCRAPE_OK")
        return True

    dates_to_scrape.append(date_range)
    time.sleep(10)
    return False


def process_one_month_face_silver(
    config: RecollectConfig,
    *,
    date_range: list[str],
    date_key: str,
    face_silver_completed: list[str],
    full_pipeline_completed: list[str],
    face_silver_attempt_tracker: dict,
    face_history: dict,
    dates_to_scrape: list[list[str]],
    compact: bool,
) -> bool:
    """Run bronze face → silver face for this month. Returns True when complete."""
    if date_key in face_silver_completed:
        if date_key not in full_pipeline_completed:
            full_pipeline_completed.append(date_key)
        return True

    if date_key not in face_history:
        face_history[date_key] = []

    face_silver_attempt_tracker[date_key] = (
        face_silver_attempt_tracker.get(date_key, 0) + 1
    )
    current_attempt = face_silver_attempt_tracker[date_key]

    logging.info(
        "\n%s\nStarting FACE silver: %s (Attempt %s/%s)\n%s",
        "=" * 50,
        date_key,
        current_attempt,
        config.max_retries,
        "=" * 50,
    )

    code = run_face_silver_subprocess(compact=compact)
    if code == 0:
        logging.info("FACE silver completed for %s", date_key)
        face_silver_completed.append(date_key)
        full_pipeline_completed.append(date_key)
        face_history[date_key].append("FACE_SILVER_OK")
        return True

    face_history[date_key].append(FACE_SILVER_ERROR)
    logging.error("FACE silver failed for %s (exit %s)", date_key, code)

    if current_attempt >= config.max_retries:
        logging.warning(
            "FACE silver for %s failed after %s attempts. Marking full pipeline anyway.",
            date_key,
            config.max_retries,
        )
        face_silver_completed.append(date_key)
        full_pipeline_completed.append(date_key)
        return True

    logging.warning(
        "Requeueing %s for FACE silver retry (scrape will not rerun).",
        date_key,
    )
    dates_to_scrape.append(date_range)
    time.sleep(10)
    return False


def _face_phase_list(
    date_key: str,
    state: dict,
    *,
    face_on: bool,
    processos_ingest_done: bool,
) -> list[str]:
    if not face_on or not processos_ingest_done:
        return []
    phases: list[str] = []
    if date_key not in state.get("face_scrape_completed_ranges", []):
        phases.append("face_scrape")
    if date_key not in state.get("face_silver_completed_ranges", []):
        phases.append("face_silver")
    return phases


def planned_phases(
    date_key: str,
    state: dict,
    *,
    scrape_only: bool = False,
    ingest_only: bool = False,
    face_only: bool = False,
    skip_face: bool = False,
    face_on: bool = True,
) -> list[str]:
    if date_key in state.get("full_pipeline_completed_ranges", []):
        return ["skip"]

    scrape_done = date_key in state.get("processed_ranges", [])
    processos_ingest_done = date_key in state.get("pipeline_completed_ranges", [])

    if face_only:
        if not processos_ingest_done:
            return ["skip"]
        return _face_phase_list(
            date_key, state, face_on=face_on, processos_ingest_done=True
        ) or ["skip"]

    phases: list[str] = []

    if ingest_only:
        if scrape_done and not processos_ingest_done:
            phases.append("ingest")
        elif not scrape_done:
            return ["skip"]
        elif processos_ingest_done:
            phases.extend(
                _face_phase_list(
                    date_key,
                    state,
                    face_on=face_on,
                    processos_ingest_done=True,
                )
            )
        return phases or ["skip"]

    if not scrape_done:
        phases.append("scrape")
    if not scrape_only and (scrape_done or "scrape" in phases):
        if not processos_ingest_done:
            phases.append("ingest")
        elif face_on:
            phases.extend(
                _face_phase_list(
                    date_key,
                    state,
                    face_on=face_on,
                    processos_ingest_done=True,
                )
            )
    return phases or ["skip"]


def log_campaign_header(
    config: RecollectConfig,
    dates_count: int,
    recollect_threshold: int,
    processed_keys: list[str],
    pipeline_completed: list[str] | None = None,
    full_pipeline_completed: list[str] | None = None,
    *,
    face_on: bool = True,
) -> None:
    logging.info(
        "Campaign: %s (execution %s, mode=%s)",
        config.id,
        config.execution,
        config.mode,
    )
    logging.info("Description: %s", config.description)
    logging.info("Loaded %s date ranges.", dates_count)
    logging.info("Recollect threshold: %s processos", recollect_threshold)
    logging.info("Scrape completed: %s ranges.", len(processed_keys))
    if pipeline_completed is not None:
        logging.info("Processos ingest completed: %s ranges.", len(pipeline_completed))
    if full_pipeline_completed is not None:
        logging.info(
            "Full pipeline completed: %s ranges (face=%s).",
            len(full_pipeline_completed),
            "on" if face_on else "off",
        )


def run_recollection(
    config_path: Path,
    *,
    data_dir: Path,
    dry_run: bool = False,
) -> None:
    config = load_recollect_config(config_path)
    dates_to_scrape, recollect_threshold = resolve_ranges(
        config,
        data_dir=data_dir,
        config_path=config_path,
    )

    state_path = data_dir / config.state_file
    log_path = data_dir / config.log_file
    configure_logging(log_path)

    state = load_state(state_path)
    reconcile_scrape_state(state, recollect_threshold)
    save_state(state_path, state)

    processed_keys = state.get("processed_ranges", [])
    attempt_tracker = state.get("attempt_tracker", {})
    volume_history = state.get("volume_history", {})

    log_campaign_header(
        config, len(dates_to_scrape), recollect_threshold, processed_keys
    )

    if dry_run:
        for i, date_range in enumerate(dates_to_scrape, 1):
            date_key = build_date_key(config.id, tuple(date_range))
            folder = build_folder_name(config.folder_prefix, tuple(date_range))
            logging.info(
                "[%s/%s] %s -> %s/%s",
                i,
                len(dates_to_scrape),
                date_key,
                COLLECT_ROOT,
                folder,
            )
        return

    config_meta = config_meta_from_recollect(config)
    queue = list(dates_to_scrape)

    while queue:
        date_range = queue.pop(0)
        date_key = build_date_key(config.id, tuple(date_range))

        process_one_month_scrape(
            config,
            date_range=date_range,
            date_key=date_key,
            recollect_threshold=recollect_threshold,
            config_meta=config_meta,
            processed_keys=processed_keys,
            attempt_tracker=attempt_tracker,
            volume_history=volume_history,
            dates_to_scrape=queue,
        )

        state["processed_ranges"] = processed_keys
        state["attempt_tracker"] = attempt_tracker
        state["volume_history"] = volume_history
        save_state(state_path, state)

    logging.info("\nAll date ranges have been processed for campaign %s!", config.id)


def run_pipeline(
    config_path: Path,
    *,
    data_dir: Path,
    dry_run: bool = False,
    scrape_only: bool = False,
    ingest_only: bool = False,
    face_only: bool = False,
    skip_face: bool = False,
    compact: bool = False,
    face_workers: int | None = None,
    face_delay_sec: float | None = None,
) -> None:
    if scrape_only and ingest_only:
        raise ValueError("Cannot use --scrape-only and --ingest-only together")
    if face_only and (scrape_only or ingest_only):
        raise ValueError(
            "Cannot combine --face-only with --scrape-only or --ingest-only"
        )

    config = load_recollect_config(config_path)
    dates_to_scrape, recollect_threshold = resolve_ranges(
        config,
        data_dir=data_dir,
        config_path=config_path,
    )

    state_path = data_dir / config.state_file
    log_path = data_dir / config.log_file
    configure_logging(log_path)

    state = load_state(state_path)
    reconcile_scrape_state(state, recollect_threshold)
    save_state(state_path, state)

    processed_keys = state.get("processed_ranges", [])
    pipeline_completed = state.get("pipeline_completed_ranges", [])
    face_scrape_completed = state.get("face_scrape_completed_ranges", [])
    face_silver_completed = state.get("face_silver_completed_ranges", [])
    full_pipeline_completed = state.get("full_pipeline_completed_ranges", [])
    attempt_tracker = state.get("attempt_tracker", {})
    ingest_attempt_tracker = state.get("ingest_attempt_tracker", {})
    face_scrape_attempt_tracker = state.get("face_scrape_attempt_tracker", {})
    face_silver_attempt_tracker = state.get("face_silver_attempt_tracker", {})
    volume_history = state.get("volume_history", {})
    ingest_history = state.get("ingest_history", {})
    face_history = state.get("face_history", {})

    face_on = face_enabled_for_run(config, skip_face=skip_face)
    face = apply_face_overrides(
        face_settings_for(config),
        workers=face_workers,
        delay_sec=face_delay_sec,
    )
    if face_workers is not None or face_delay_sec is not None:
        logging.info(
            "FACE tuning override: workers=%s delay_sec=%s",
            face.workers,
            face.delay_sec,
        )

    log_campaign_header(
        config,
        len(dates_to_scrape),
        recollect_threshold,
        processed_keys,
        pipeline_completed,
        full_pipeline_completed,
        face_on=face_on,
    )

    total_months = len(dates_to_scrape)

    if dry_run:
        for i, date_range in enumerate(dates_to_scrape, 1):
            date_key = build_date_key(config.id, tuple(date_range))
            phases = planned_phases(
                date_key,
                state,
                scrape_only=scrape_only,
                ingest_only=ingest_only,
                face_only=face_only,
                skip_face=skip_face,
                face_on=face_on,
            )
            folder = build_folder_name(config.folder_prefix, tuple(date_range))
            logging.info(
                "[%s/%s] %s phases=%s -> %s/%s",
                i,
                total_months,
                date_key,
                "+".join(phases),
                COLLECT_ROOT,
                folder,
            )
        return

    config_meta = config_meta_from_recollect(config)
    queue = list(dates_to_scrape)
    month_index = 0

    def persist_state() -> None:
        state["processed_ranges"] = processed_keys
        state["pipeline_completed_ranges"] = pipeline_completed
        state["face_scrape_completed_ranges"] = face_scrape_completed
        state["face_silver_completed_ranges"] = face_silver_completed
        state["full_pipeline_completed_ranges"] = full_pipeline_completed
        state["attempt_tracker"] = attempt_tracker
        state["ingest_attempt_tracker"] = ingest_attempt_tracker
        state["face_scrape_attempt_tracker"] = face_scrape_attempt_tracker
        state["face_silver_attempt_tracker"] = face_silver_attempt_tracker
        state["volume_history"] = volume_history
        state["ingest_history"] = ingest_history
        state["face_history"] = face_history
        save_state(state_path, state)

    while queue:
        date_range = queue.pop(0)
        date_key = build_date_key(config.id, tuple(date_range))
        month_index += 1

        if date_key in full_pipeline_completed:
            logging.info(
                "[%s/%s] Skipping %s (full pipeline already complete)",
                month_index,
                total_months,
                date_key,
            )
            continue

        scrape_done = date_key in processed_keys
        processos_ingest_done = date_key in pipeline_completed

        if face_only and not processos_ingest_done:
            logging.info(
                "[%s/%s] Skipping %s (processos ingest not done, face-only mode)",
                month_index,
                total_months,
                date_key,
            )
            continue

        if ingest_only and not scrape_done:
            logging.info(
                "[%s/%s] Skipping %s (scrape not done, ingest-only mode)",
                month_index,
                total_months,
                date_key,
            )
            continue

        logging.info(
            "\n%s\n[%s/%s] Month: %s\n%s",
            "=" * 50,
            month_index,
            total_months,
            date_key,
            "=" * 50,
        )

        if not face_only and not scrape_done and not ingest_only:
            scrape_accepted = process_one_month_scrape(
                config,
                date_range=date_range,
                date_key=date_key,
                recollect_threshold=recollect_threshold,
                config_meta=config_meta,
                processed_keys=processed_keys,
                attempt_tracker=attempt_tracker,
                volume_history=volume_history,
                dates_to_scrape=queue,
            )
            scrape_done = date_key in processed_keys
            persist_state()

            if not scrape_accepted and not scrape_done:
                continue

        if scrape_only:
            continue

        if not face_only and scrape_done and not processos_ingest_done:
            process_one_month_ingest(
                config,
                date_range=date_range,
                date_key=date_key,
                pipeline_completed=pipeline_completed,
                ingest_attempt_tracker=ingest_attempt_tracker,
                ingest_history=ingest_history,
                dates_to_scrape=queue,
                compact=compact,
            )
            processos_ingest_done = date_key in pipeline_completed
            maybe_mark_full_pipeline_without_face(
                date_key,
                pipeline_completed=pipeline_completed,
                full_pipeline_completed=full_pipeline_completed,
                face_on=face_on,
            )
            persist_state()

            if not processos_ingest_done:
                continue

        if face_on and processos_ingest_done:
            json_path = find_dedup_json(
                COLLECT_ROOT
                / build_folder_name(config.folder_prefix, tuple(date_range))
            )
            if json_path is None:
                _mark_face_and_full_complete(
                    date_key,
                    face_scrape_completed=face_scrape_completed,
                    face_silver_completed=face_silver_completed,
                    full_pipeline_completed=full_pipeline_completed,
                )
                persist_state()
                continue

            if date_key not in face_scrape_completed:
                face_ok = process_one_month_face_scrape(
                    config,
                    date_range=date_range,
                    date_key=date_key,
                    face=face,
                    face_scrape_completed=face_scrape_completed,
                    face_scrape_attempt_tracker=face_scrape_attempt_tracker,
                    face_history=face_history,
                    dates_to_scrape=queue,
                    compact=compact,
                )
                persist_state()
                if not face_ok:
                    continue

            if date_key not in face_silver_completed:
                process_one_month_face_silver(
                    config,
                    date_range=date_range,
                    date_key=date_key,
                    face_silver_completed=face_silver_completed,
                    full_pipeline_completed=full_pipeline_completed,
                    face_silver_attempt_tracker=face_silver_attempt_tracker,
                    face_history=face_history,
                    dates_to_scrape=queue,
                    compact=compact,
                )
                persist_state()

    logging.info(
        "\nAll date ranges processed for campaign %s! Full pipeline: %s/%s months.",
        config.id,
        len(full_pipeline_completed),
        total_months,
    )
