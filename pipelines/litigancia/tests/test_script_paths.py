"""Smoke tests for canonical script path registry."""

from __future__ import annotations

from config.scripts import (
    AUDIT_PROCESS_COUNTS,
    CREATE_SILVER_LAYER,
    EXPORT_NORMALIZATION_REVIEW,
    PROCESSOS_TRANSFORMING,
    RUN_NEW_DATA_TO_SILVER,
    SCRIPTS_DIR,
    format_command,
    load_transform_module,
    script_path,
)


def test_key_script_paths_exist() -> None:
    for path in (
        SCRIPTS_DIR,
        RUN_NEW_DATA_TO_SILVER,
        CREATE_SILVER_LAYER,
        AUDIT_PROCESS_COUNTS,
        EXPORT_NORMALIZATION_REVIEW,
    ):
        assert path.is_file() or path.is_dir(), path


def test_format_command_uses_repo_relative_path() -> None:
    cmd = format_command(CREATE_SILVER_LAYER, "--show-pending")
    assert cmd.startswith("uv run python scripts/processos/transforming/create_silver_layer.py")
    assert cmd.endswith("--show-pending")


def test_script_path_resolves_processos_transforming() -> None:
    assert script_path("create_bronze_layer") == PROCESSOS_TRANSFORMING / "create_bronze_layer.py"


def test_load_transform_module_create_silver_layer() -> None:
    module = load_transform_module("create_silver_layer")
    assert callable(module.get_pending_bronze_paths)


