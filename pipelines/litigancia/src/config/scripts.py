"""Canonical paths and dynamic loaders for repository scripts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from config.paths import PIPELINE_ROOT

SCRIPTS_DIR = PIPELINE_ROOT / "scripts"
DATA_DIR = SCRIPTS_DIR / "data"

# Domain roots
PROCESSOS_DIR = SCRIPTS_DIR / "processos"
FACE_DIR = SCRIPTS_DIR / "face"
MOVIMENTACOES_DIR = SCRIPTS_DIR / "movimentacoes"
MAINTENANCE_DIR = SCRIPTS_DIR / "maintenance"
OPS_DIR = SCRIPTS_DIR / "ops"

PROCESSOS_TRANSFORMING = PROCESSOS_DIR / "transforming"
PROCESSOS_SCRAPING = PROCESSOS_DIR / "scraping"
FACE_TRANSFORMING = FACE_DIR / "transforming"
FACE_SCRAPING = FACE_DIR / "scraping"
MOVIMENTACOES_TRANSFORMING = MOVIMENTACOES_DIR / "transforming"

# Processos
RUN_NEW_DATA_TO_SILVER = PROCESSOS_DIR / "run_new_data_to_silver.py"
RUN_ONE_JSON_TO_SILVER = PROCESSOS_DIR / "run_one_json_to_silver.py"
AUDIT_PROCESS_COUNTS = PROCESSOS_DIR / "audit_process_counts.py"
CREATE_BRONZE_LAYER = PROCESSOS_TRANSFORMING / "create_bronze_layer.py"
CREATE_SILVER_LAYER = PROCESSOS_TRANSFORMING / "create_silver_layer.py"
COMPACT_SILVER_PROCESSOS = PROCESSOS_TRANSFORMING / "compact_silver_processos.py"
BACKFILL_SILVER_SOURCE_PATHS = PROCESSOS_TRANSFORMING / "backfill_silver_source_paths.py"
RUN_RECOLLECT_JUSCRAPER = PROCESSOS_SCRAPING / "run_recollect_juscraper.py"
RUN_RECOLLECT_PIPELINE = PROCESSOS_SCRAPING / "run_recollect_pipeline.py"

# Face
SCRAPE_FACE_TO_BRONZE = FACE_SCRAPING / "scrape_face_to_bronze.py"
CREATE_SILVER_FACE_LAYER = FACE_TRANSFORMING / "create_silver_face_layer.py"

# Movimentações
CREATE_SILVER_MOVIMENTACOES = MOVIMENTACOES_TRANSFORMING / "create_silver_movimentacoes.py"
EXPORT_NORMALIZATION_REVIEW = MOVIMENTACOES_DIR / "export_normalization_review.py"

# Maintenance
OPTIMIZE_DELTA_LAKE = MAINTENANCE_DIR / "optimize_delta_lake.py"
EXPLORE_QUALITY = MAINTENANCE_DIR / "explore_quality.py"
EXPORT_VALIDATION_SAMPLES = MAINTENANCE_DIR / "export_validation_samples.py"
FIX_STATE = MAINTENANCE_DIR / "other" / "fix_state.py"

# Ops
OPS_COMMON_SH = OPS_DIR / "lib" / "common.sh"


def script_path(module_name: str, *, domain: str = "processos", layer: str = "transforming") -> Path:
    """Resolve a script file under the domain-first layout."""
    filename = module_name if module_name.endswith(".py") else f"{module_name}.py"
    if layer == "root":
        return SCRIPTS_DIR / domain / filename
    return SCRIPTS_DIR / domain / layer / filename


def format_command(script: Path, *args: str) -> str:
    """Human-readable ``uv run python …`` command for docs and error messages."""
    rel = script.relative_to(PIPELINE_ROOT) if script.is_absolute() else script
    suffix = f" {' '.join(args)}" if args else ""
    return f"uv run python {rel.as_posix()}{suffix}"


def _load_from_path(path: Path, module_name: str, *, register: bool = True) -> Any:
    if not path.is_file():
        raise ImportError(f"Script module not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load script module: {path}")
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_script_module(
    module_name: str,
    *,
    domain: str = "processos",
    layer: str = "transforming",
    register: bool = True,
) -> Any:
    """Load a script module from the domain-first layout."""
    path = script_path(module_name, domain=domain, layer=layer)
    return _load_from_path(path, module_name, register=register)


def load_transform_module(module_name: str) -> Any:
    """Load a module from ``scripts/processos/transforming/``."""
    return load_script_module(module_name, domain="processos", layer="transforming")
