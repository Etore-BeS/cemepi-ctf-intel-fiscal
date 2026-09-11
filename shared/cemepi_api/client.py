from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv


@dataclass
class Settings:
    base: str
    token: str
    dump_root: Path
    lake_root: Path
    ano: int
    mes: int
    sample_limit: int


def load_settings(env_path: str | Path | None = None) -> Settings:
    # mono root .env
    mono = Path(__file__).resolve().parents[2]
    load_dotenv(mono / ".env")
    if env_path:
        load_dotenv(env_path, override=True)
    token = os.getenv("CEMEPI_API_TOKEN", "")
    if not token:
        raise RuntimeError("CEMEPI_API_TOKEN missing in .env")
    dump_root = os.getenv("DUMP_ROOT")
    lake_root = os.getenv("LAKE_ROOT")
    if not dump_root:
        raise RuntimeError("DUMP_ROOT missing in .env (see .env.example)")
    if not lake_root:
        raise RuntimeError("LAKE_ROOT missing in .env (see .env.example)")
    return Settings(
        base=os.getenv("CEMEPI_API_BASE", "http://143.107.158.76:8010").rstrip("/"),
        token=token,
        dump_root=Path(dump_root),
        lake_root=Path(lake_root),
        ano=int(os.getenv("EXTRACAO_ANO", "2026")),
        mes=int(os.getenv("EXTRACAO_MES", "3")),
        sample_limit=int(os.getenv("SAMPLE_LIMIT", "100")),
    )


class CemepiClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or load_settings()
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.s.token}"})

    def get_json(self, path: str, params: dict[str, Any] | None = None, timeout: int = 60) -> Any:
        url = f"{self.s.base}{path}"
        r = self.session.get(url, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def sample_dataset(
        self,
        dataset: str,
        *,
        limit: int | None = None,
        select: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"limit": limit or self.s.sample_limit}
        if select:
            params["select"] = select
        if extra:
            params.update(extra)
        payload = self.get_json(f"/v1/data/{dataset}", params=params)
        rows = payload.get("dados") or payload.get("data") or []
        return pd.DataFrame(rows)

    def sample_dir(self) -> Path:
        p = self.s.dump_root / f"extracao={self.s.ano:04d}-{self.s.mes:02d}" / "samples"
        p.mkdir(parents=True, exist_ok=True)
        return p
