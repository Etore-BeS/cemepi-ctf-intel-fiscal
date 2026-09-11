#!/usr/bin/env python3
"""Triagem bibliométrica completa — Sonnet 5 + prompt caching."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parents[2]  # .../pipelines/litigancia
REPO_ROOT = Path(__file__).resolve().parents[4]      # .../cemepi-ctf-intel-fiscal
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from utils.bibliometric_screening import (  # noqa: E402
    DEFAULT_SCREENING_MODEL,
    SYSTEM_PROMPT_A,
    SYSTEM_PROMPT_B,
    build_screening_agent,
    cache_path,
    run_screening,
)

ARTICLE_ROOT = PIPELINE_ROOT / "notebooks/articles/revisao-bibliometrica-2026"
DATA_ROOT = PIPELINE_ROOT / ".tmp/Revisao Bibliométrica/Dados"
CACHE_DIR = ARTICLE_ROOT / "artifacts/cache"
PROGRESS_DIR = ARTICLE_ROOT / "artifacts/progress"
LEGACY_MODEL = "claude-sonnet-4-6"
MAX_CONCURRENT = 10


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY ausente no .env")

    for directory in (CACHE_DIR, PROGRESS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    df_a = pd.read_csv(
        DATA_ROOT / "Busca A/com_filtros/BuscaA_Unificado_Deduplicado.csv",
        dtype=str,
    ).fillna("")
    df_b = pd.read_csv(
        DATA_ROOT / "Busca B/com_filtros/BuscaB_Unificado_Deduplicado.csv",
        dtype=str,
    ).fillna("")

    model = DEFAULT_SCREENING_MODEL
    legacy_ia1_a = cache_path(CACHE_DIR, "A", "ia1", LEGACY_MODEL)

    agent_a = build_screening_agent(SYSTEM_PROMPT_A, model)
    agent_b = build_screening_agent(SYSTEM_PROMPT_B, model)

    print(
        f"Modelo: {model} | prompt caching: instructions | concorrência: {MAX_CONCURRENT}"
    )

    phases = [
        ("A", "ia1", df_a, agent_a, [legacy_ia1_a]),
        ("A", "ia2", df_a, agent_a, None),
        ("B", "ia1", df_b, agent_b, None),
        ("B", "ia2", df_b, agent_b, None),
    ]

    for busca, reviewer, frame, agent, legacy in phases:
        try:
            await run_screening(
                agent,
                frame,
                busca,
                reviewer,
                model,
                CACHE_DIR,
                PROGRESS_DIR,
                max_concurrent=MAX_CONCURRENT,
                legacy_cache_files=legacy,
            )
        except RuntimeError as exc:
            print(f"Parado em Busca {busca} / {reviewer}: {exc}")
            print(
                "Recarregue créditos e reexecute — o cache idempotente retoma do ponto."
            )
            raise SystemExit(1) from exc

    print("Triagem completa. Reexecute o notebook (células 5–7) para κ e exportação.")


if __name__ == "__main__":
    asyncio.run(main())
