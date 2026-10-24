import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
SRC = PKG / "src" if (PKG / "src" / "cnpj_pipeline").is_dir() else PKG

# Settings a developer may have exported (or loaded from .env) that must not leak into the synthetic runs.
_ISOLATE = ("CNPJ_PIPELINE_WORKDIR", "CNPJ_REF_RFB", "CNPJ_TEMP_DIR", "CNPJ_MEMORY_LIMIT", "CNPJ_THREADS",
            "CNPJ_MANIFEST_DIR", "CNPJ_ENV_FILE", "CNPJ_REGIME_BASE_URL", "CNPJ_REGIME_SHARE", "DATA_ROOT",
            "CEMEPI_DATA_ROOT", "CEMEPI_DUCKDB_MAIN", "CNPJ_HASH_SALT", "CNPJ_PIPELINE_FAULT")


def cli(workdir: Path, *args: str, env: dict | None = None, check: bool = True, memory: str = "256MB",
        threads: int = 2) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in _ISOLATE}
    e.update(env or {})
    e["CNPJ_PIPELINE_NO_DOTENV"] = "1"
    e["PYTHONPATH"] = os.pathsep.join([str(SRC), *filter(None, [os.environ.get("PYTHONPATH")])])
    cmd = [sys.executable, "-m", "cnpj_pipeline", args[0], "--workdir", str(workdir), "--memory-limit", memory,
           "--threads", str(threads), *args[1:]]
    p = subprocess.run(cmd, cwd=PKG, env=e, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"{cmd} failed ({p.returncode}):\n{p.stderr[-4000:]}")
    return p


def cli_env() -> dict:
    """Environment for direct `python -m cnpj_pipeline` subprocesses (same isolation as `cli`)."""
    e = {k: v for k, v in os.environ.items() if k not in _ISOLATE}
    e["CNPJ_PIPELINE_NO_DOTENV"] = "1"
    e["PYTHONPATH"] = os.pathsep.join([str(SRC), *filter(None, [os.environ.get("PYTHONPATH")])])
    return e


def duck_cli(db: Path, sql: str) -> str:
    """Query a database written by the duckdb CLI with that same CLI.

    The CLI (load_duckdb.sh) and the Python library may be different DuckDB versions (e.g. CLI 2.x, library 1.5),
    and an older library cannot open a newer storage file.
    """
    exe = shutil.which("duckdb")
    e = {k: v for k, v in os.environ.items() if k != "CURSOR_AGENT"}
    p = subprocess.run([exe, "-csv", "-noheader", str(db), "-c", sql], capture_output=True, text=True, env=e)
    if p.returncode != 0:
        raise AssertionError(p.stderr + p.stdout)
    return p.stdout.strip()


def sha256_check_cmd() -> list[str]:
    """`sha256sum -c` (Linux/coreutils) or `shasum -a 256 -c` (macOS without coreutils)."""
    return ["sha256sum", "-c", "SHA256SUMS"] if shutil.which("sha256sum") else ["shasum", "-a", "256", "-c", "SHA256SUMS"]
