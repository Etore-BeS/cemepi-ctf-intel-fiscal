"""Run settings and the on-disk layout of the work directory (keep it on the external drive)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    workdir: Path
    ref_rfb: str = "2026-03"
    memory_limit: str = "2GB"
    threads: int = 2
    temp_dir: Path | None = None
    key_buckets: int = 16          # partitions of the debtor keys (by md5 of the 8-char root); fixed per workdir
    enrich_groups: int = 1         # key groups per RFB file in `enrich` (more groups = smaller hash table, more passes)

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir).expanduser().resolve()
        self.temp_dir = Path(self.temp_dir).expanduser().resolve() if self.temp_dir else self.workdir / "tmp"

    # layout
    @property
    def state_db(self) -> Path: return self.workdir / "state.sqlite"
    @property
    def log_dir(self) -> Path: return self.workdir / "logs"
    @property
    def collect_raw(self) -> Path: return self.workdir / "collect" / "raw"
    @property
    def collect_keys(self) -> Path: return self.workdir / "collect" / "keys"
    @property
    def collect_keys_tributo(self) -> Path: return self.workdir / "collect" / "keys_tributo"
    @property
    def rfb_dir(self) -> Path: return self.workdir / "rfb" / self.ref_rfb
    @property
    def manifest(self) -> Path: return self.rfb_dir / "manifest.json"
    @property
    def enrich_dir(self) -> Path: return self.workdir / "enrich" / self.ref_rfb
    @property
    def build_dir(self) -> Path: return self.workdir / "build" / self.ref_rfb
    @property
    def export_dir(self) -> Path: return self.workdir / "export"
    @property
    def socios_dir(self) -> Path: return self.workdir / "socios" / self.ref_rfb      # local only (names, masked CPF)
    @property
    def regime_dir(self) -> Path: return self.workdir / "regime_tributario" / self.ref_rfb

    def ensure(self) -> None:
        for p in (self.workdir, self.temp_dir, self.log_dir):
            p.mkdir(parents=True, exist_ok=True)


def env_default(name: str, fallback: str | None = None) -> str | None:
    return os.environ.get(name, fallback)


# --------------------------------------------------------------------------- .env (monorepo) and derived defaults
# Inside the cemepi-ctf-intel-fiscal monorepo the configuration comes from the repo-root `.env` (never committed),
# the same way pipelines/litigancia and shared/cemepi_api read it. Variables already set in the shell win
# (override=False). No absolute path lives in the code: everything is derived from DATA_ROOT / CNPJ_PIPELINE_WORKDIR.
REPO_NAME = "cemepi-ctf-intel-fiscal"
_ENV_FILE: Path | None = None


def _find_upwards(start: Path, name: str) -> Path | None:
    for d in (start, *start.parents):
        if (d / name).is_file():
            return d / name
    return None


def repo_root() -> Path | None:
    """Monorepo root (pyproject.toml with name = cemepi-ctf-intel-fiscal) above this package, if installed in it."""
    for d in Path(__file__).resolve().parents:
        pp = d / "pyproject.toml"
        if pp.is_file() and f'name = "{REPO_NAME}"' in pp.read_text(errors="ignore"):
            return d
    return None


def _parse_env(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader, used only when python-dotenv is not installed (standalone tarball)."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[k] = v
    return out


def load_env() -> Path | None:
    """Load the `.env` once: $CNPJ_ENV_FILE, else the monorepo root, else the first `.env` above the cwd.

    Disabled with CNPJ_PIPELINE_NO_DOTENV=1 (the tests use it). Values are never logged.
    """
    global _ENV_FILE
    if os.environ.get("CNPJ_PIPELINE_NO_DOTENV") == "1":
        return None
    if os.environ.get("CNPJ_ENV_FILE"):
        path: Path | None = Path(os.environ["CNPJ_ENV_FILE"]).expanduser()
    else:
        root = repo_root()
        path = (root / ".env") if root and (root / ".env").is_file() else _find_upwards(Path.cwd(), ".env")
    if not path or not path.is_file():
        return None
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except ImportError:
        for k, v in _parse_env(path).items():
            os.environ.setdefault(k, v)
    _ENV_FILE = path.resolve()
    return _ENV_FILE


def _abs(value: str) -> Path:
    """Relative paths in the .env are relative to the .env's folder (repo root), like COLLECT_ROOT in litigancia."""
    p = Path(value).expanduser()
    if not p.is_absolute() and _ENV_FILE is not None:
        p = _ENV_FILE.parent / p
    return p


def data_root() -> Path | None:
    v = os.environ.get("DATA_ROOT") or os.environ.get("CEMEPI_DATA_ROOT")
    return _abs(v) if v else None


def default_workdir() -> str | None:
    """CNPJ_PIPELINE_WORKDIR, else $DATA_ROOT/cnpj_enrich."""
    if os.environ.get("CNPJ_PIPELINE_WORKDIR"):
        return str(_abs(os.environ["CNPJ_PIPELINE_WORKDIR"]))
    root = data_root()
    return str(root / "cnpj_enrich") if root else None


def default_duckdb() -> str | None:
    """CEMEPI_DUCKDB_MAIN if absolute, else $DATA_ROOT/<CEMEPI_DUCKDB_MAIN or api_ctf/cemepi.duckdb>."""
    v = os.environ.get("CEMEPI_DUCKDB_MAIN")
    if v and Path(v).expanduser().is_absolute():
        return str(Path(v).expanduser())
    root = data_root()
    return str(root / (v or "api_ctf/cemepi.duckdb")) if root else None


def manifest_dir() -> Path | None:
    """Where `build` writes the git-tracked data manifest (D6): $CNPJ_MANIFEST_DIR, else <repo>/data_manifests/cnpj_enrich.

    Outside the monorepo (standalone tarball) or with CNPJ_PIPELINE_NO_DOTENV=1 nothing is written unless
    CNPJ_MANIFEST_DIR is set.
    """
    if os.environ.get("CNPJ_MANIFEST_DIR"):
        return _abs(os.environ["CNPJ_MANIFEST_DIR"])
    if os.environ.get("CNPJ_PIPELINE_NO_DOTENV") == "1":
        return None
    root = repo_root()
    return root / "data_manifests" / "cnpj_enrich" if root else None
