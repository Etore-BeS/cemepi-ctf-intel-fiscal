"""Triagem bibliométrica — critérios v2, guardrails e triagem via pydantic-ai."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModelSettings

DECISION_LABELS = ["incluir", "excluir", "dúvida"]
DEFAULT_SCREENING_MODEL = "claude-sonnet-5"

CRITERIA_BY_BUSCA = {
    "A": {"EC-A1", "EC-A2", "EC-A3", "EC-A4", "EC-A5", "EC-A6"},
    "B": {"EC-B1", "EC-B2", "EC-B3", "EC-B4"},
}

SYSTEM_PROMPT_A = """Você é revisor metodológico de uma revisão bibliométrica (scoping review, PRISMA-ScR).

Objeto da Busca A: administração tributária nas dimensões de cobrança, execução e litígio, e o comportamento de (in)adimplemento do contribuinte.

Dentro do escopo: enforcement e execução fiscal; recuperação de crédito e taxa de recuperação; litígio e disputa tributária (incluída litigância protelatória e abuso processual); dissuasão, fiscalização, auditoria, penalidades; (não-)conformidade e inadimplência do contribuinte com impacto na cobrança.

Fora do escopo: regulação bancária privada; contabilidade societária; planejamento tributário preventivo isolado; governança corporativa sem foco litigioso; estudos atitudinais genéricos; modelagem macroeconômica sem foco processual.

Critérios de EXCLUSÃO da Etapa 1 (aplique o primeiro satisfeito, em cascata):
- EC-A1: não trata do contexto tributário-estatal ou da administração pública.
- EC-A2: não aborda a dimensão coercitiva, executória ou contenciosa da cobrança tributária.
- EC-A3: não contempla o comportamento estratégico, a conformidade ou a conduta litigiosa do contribuinte.
- EC-A4: não está redigido em português, inglês ou espanhol.
- EC-A5: limita-se à percepção atitudinal do contribuinte sem conexão com a efetividade da cobrança ou do enforcement.
- EC-A6: trata a eficiência arrecadatória ou o enforcement exclusivamente em nível macroeconômico ou cross-country.

Etapa 2 (texto completo) reserva EC-A7 e EC-A8 — não aplicar nesta classificação por título/resumo.

Princípio conservador: se houver dúvida razoável, use decisao="dúvida".

REGRAS DE SAÍDA:
- Se a decisão for "incluir", o campo criterio DEVE ser null.
- NUNCA use códigos EC para justificar inclusão.
- Só preencha criterio com EC-A1 a EC-A6 se a decisão for "excluir" ou "dúvida"."""

SYSTEM_PROMPT_B = """Você é revisor metodológico de uma revisão bibliométrica (scoping review, PRISMA-ScR).

Objeto da Busca B: inadimplemento deliberado, estratégico ou habitual; figura do devedor contumaz, recorrente ou reincidente (wilful/strategic defaulter); critérios de enquadramento (valor, reiteração, injustificabilidade).

Dentro do escopo: inadimplemento deliberado/estratégico de obrigações; devedor contumaz ou recalcitrante; inadimplência habitual e NPA sob a ótica do comportamento do devedor.

Fora do escopo: modelagem puramente macroeconômica de risco de crédito sem inadimplemento deliberado; inadimplência incidental não intencional; temas fiduciários ou societários sem conexão com inadimplemento deliberado; programas genéricos de conformidade sem isolar o reincidente.

Critérios de EXCLUSÃO da Etapa 1 (aplique o primeiro satisfeito, em cascata):
- EC-B1: não aborda o inadimplemento tributário deliberado, estratégico ou contumaz.
- EC-B2: foca exclusivamente na persecução penal de crimes financeiros sem relação com recuperação de crédito ou contencioso tributário.
- EC-B3: trata apenas de programas genéricos de conformidade ou anistias fiscais sem isolar a figura do devedor reincidente ou recalcitrante.
- EC-B4: não está redigido em português, inglês ou espanhol.

Etapa 2 (texto completo) reserva EC-B5 e EC-B6 — não aplicar nesta classificação por título/resumo.

Princípio conservador: se houver dúvida razoável, use decisao="dúvida".

REGRAS DE SAÍDA:
- Se a decisão for "incluir", o campo criterio DEVE ser null.
- NUNCA use códigos EC para justificar inclusão.
- Só preencha criterio com EC-B1 a EC-B4 se a decisão for "excluir" ou "dúvida"."""


class ScreeningDecision(BaseModel):
    decisao: Literal["incluir", "excluir", "dúvida"]
    criterio: (
        Literal[
            "EC-A1",
            "EC-A2",
            "EC-A3",
            "EC-A4",
            "EC-A5",
            "EC-A6",
            "EC-B1",
            "EC-B2",
            "EC-B3",
            "EC-B4",
        ]
        | None
    ) = None
    justificativa: str = Field(min_length=1)


def build_screening_agent(
    system_prompt: str,
    model: str,
    *,
    cache_instructions: bool = True,
) -> Agent[None, ScreeningDecision]:
    model_settings: AnthropicModelSettings | None = None
    if cache_instructions:
        model_settings = AnthropicModelSettings(anthropic_cache_instructions=True)
    return Agent(
        f"anthropic:{model}",
        instructions=system_prompt,
        output_type=ScreeningDecision,
        retries=2,
        model_settings=model_settings,
    )


def build_user_prompt(row: pd.Series) -> str:
    return (
        f"id_seq: {row['id_seq']}\n"
        f"titulo: {row['titulo']}\n"
        f"resumo: {row['resumo']}\n"
        f"palavras_chave: {row['palavras_chave']}\n"
        f"idioma: {row['idioma']}"
    )


def apply_json_guardrail(
    decisao: str,
    criterio: str | None,
    allowed_criteria: set[str],
) -> tuple[str | None, bool, str | None, str | None]:
    inconsistencia = False
    motivo: str | None = None
    acao: str | None = None
    normalized = criterio
    if normalized in {"null", "None", ""}:
        normalized = None

    if decisao == "incluir":
        if normalized is not None:
            inconsistencia = True
            motivo = "incluir_com_criterio_ec"
            acao = "criterio_forcado_null"
            normalized = None
    elif decisao in {"excluir", "dúvida"}:
        if normalized is None:
            inconsistencia = True
            motivo = f"{decisao}_sem_criterio_ec"
        elif normalized not in allowed_criteria:
            inconsistencia = True
            motivo = "criterio_ec_invalido"
            acao = "criterio_mantido_para_auditoria"
    return normalized, inconsistencia, motivo, acao


def decision_to_record(
    decision: ScreeningDecision,
    busca: str,
    *,
    reviewer: str,
    model: str,
    id_seq: int,
) -> dict[str, str | bool | int | None]:
    criterio, inconsistencia_json, motivo_inconsistencia, acao_guardrail = (
        apply_json_guardrail(
            decision.decisao,
            decision.criterio,
            CRITERIA_BY_BUSCA[busca],
        )
    )
    return {
        "id_seq": id_seq,
        "reviewer": reviewer,
        "model": model,
        "busca": busca,
        "decisao": decision.decisao,
        "criterio": criterio,
        "justificativa": decision.justificativa.strip(),
        "inconsistencia_json": inconsistencia_json,
        "motivo_inconsistencia": motivo_inconsistencia,
        "acao_guardrail": acao_guardrail,
    }


def model_slug(model: str) -> str:
    return model.replace("-", "").replace(".", "")


def archive_legacy_artifacts(artifacts_dir: Path) -> list[str]:
    moved: list[str] = []
    archive_root = artifacts_dir / "archive_pre_v2"
    for sub in ("cache", "progress", "batch"):
        src = artifacts_dir / sub
        if not src.exists():
            continue
        for path in src.glob("*gpt5*"):
            dst = archive_root / sub / path.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            path.rename(dst)
            moved.append(str(dst))
    return moved


def merge_legacy_cache(
    cache_file: Path,
    legacy_files: list[Path],
    *,
    target_model: str,
) -> int:
    """Seed *cache_file* with records from older model caches (same reviewer/busca)."""
    cached = load_cache(cache_file)
    added = 0
    for legacy in legacy_files:
        if not legacy.exists():
            continue
        for record in load_cache(legacy).values():
            record_id = int(record["id_seq"])
            if record_id in cached:
                continue
            migrated = {
                **record,
                "model": target_model,
                "migrated_from_model": record.get("model"),
            }
            append_cache(cache_file, migrated)
            cached[record_id] = migrated
            added += 1
    return added


def load_cache(path: Path) -> dict[int, dict]:
    cached: dict[int, dict] = {}
    if not path.exists():
        return cached
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cached[int(record["id_seq"])] = record
    return cached


def append_cache(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def cache_path(cache_dir: Path, busca: str, reviewer: str, model: str) -> Path:
    slug = model_slug(model)
    return cache_dir / f"busca_{busca.lower()}_{reviewer}_{slug}.jsonl"


def progress_path(progress_dir: Path, busca: str, reviewer: str, model: str) -> Path:
    slug = model_slug(model)
    return progress_dir / f"busca_{busca.lower()}_{reviewer}_{slug}.json"


def write_progress(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def classify_record(
    agent: Agent[None, ScreeningDecision],
    row: pd.Series,
    busca: str,
    reviewer: str,
    model: str,
) -> dict[str, str | bool | int | None]:
    result = await agent.run(build_user_prompt(row))
    return decision_to_record(
        result.output,
        busca,
        reviewer=reviewer,
        model=model,
        id_seq=int(row["id_seq"]),
    )


async def run_screening(
    agent: Agent[None, ScreeningDecision] | None,
    df: pd.DataFrame,
    busca: str,
    reviewer: str,
    model: str,
    cache_dir: Path,
    progress_dir: Path,
    *,
    max_concurrent: int = 10,
    require_api: bool = True,
    legacy_cache_files: list[Path] | None = None,
) -> pd.DataFrame:
    cache_file = cache_path(cache_dir, busca, reviewer, model)
    progress_file = progress_path(progress_dir, busca, reviewer, model)

    if legacy_cache_files:
        n_migrated = merge_legacy_cache(
            cache_file, legacy_cache_files, target_model=model
        )
        if n_migrated:
            print(
                f"Busca {busca} | {reviewer}: {n_migrated} registros migrados "
                f"de cache legado → {cache_file.name}"
            )

    cached = load_cache(cache_file)
    total = len(df)
    pending: list[tuple[int, pd.Series]] = []
    for _, row in df.iterrows():
        record_id = int(row["id_seq"])
        if record_id not in cached:
            pending.append((record_id, row))

    n_cache_hits = total - len(pending)

    def emit_progress(extra: dict) -> None:
        current_done = len(cached)
        write_progress(
            progress_file,
            {
                "busca": busca,
                "reviewer": reviewer,
                "model": model,
                "done": current_done,
                "total": total,
                "cache_hits": n_cache_hits,
                "pending": total - current_done,
                "max_concurrent": max_concurrent,
                **extra,
            },
        )

    if pending:
        if agent is None:
            if require_api:
                raise RuntimeError(
                    f"Sem cache para id_seq={pending[0][0]} e ANTHROPIC_API_KEY ausente."
                )
        else:
            semaphore = asyncio.Semaphore(max_concurrent)
            lock = asyncio.Lock()
            errors: list[tuple[int, str]] = []

            async def process_one(record_id: int, row: pd.Series) -> None:
                try:
                    async with semaphore:
                        payload = await classify_record(
                            agent, row, busca, reviewer, model
                        )
                except Exception as exc:
                    async with lock:
                        errors.append((record_id, str(exc)))
                        emit_progress(
                            {
                                "status": "running",
                                "percent": round(len(cached) / total * 100, 2),
                                "last_error_id_seq": record_id,
                                "last_error": str(exc),
                                "error_count": len(errors),
                            }
                        )
                    return
                async with lock:
                    append_cache(cache_file, payload)
                    cached[record_id] = payload
                    emit_progress(
                        {
                            "status": "running",
                            "percent": round(len(cached) / total * 100, 2),
                            "last_id_seq": record_id,
                            "last_decisao": payload.get("decisao"),
                        }
                    )

            print(
                f"Triagem pydantic-ai | Busca {busca} | {reviewer} | "
                f"{len(pending)} pendentes (concorrência={max_concurrent})"
            )
            await asyncio.gather(
                *(process_one(record_id, row) for record_id, row in pending)
            )
            ingested = len(cached) - n_cache_hits
            if ingested:
                print(f"Ingeridos {ingested} registros no cache")

            still_pending = total - len(cached)
            if still_pending:
                emit_progress(
                    {
                        "status": "partial",
                        "percent": round(len(cached) / total * 100, 2),
                        "pending": still_pending,
                        "error_count": len(errors),
                    }
                )
                sample = errors[0][1] if errors else "erro desconhecido"
                raise RuntimeError(
                    f"Triagem incompleta ({busca}/{reviewer}): "
                    f"{still_pending} pendentes, {len(errors)} erros. Ex.: {sample}"
                )
    else:
        print(f"Busca {busca} | {reviewer}: {total} registros já em cache")

    emit_progress({"status": "completed", "percent": 100.0, "pending": 0})

    results = [cached[int(row["id_seq"])] for _, row in df.iterrows()]
    out = pd.DataFrame(results).set_index("id_seq")
    rename_map = {
        "decisao": f"decisao_{reviewer}",
        "criterio": f"criterio_{reviewer}",
        "justificativa": f"justificativa_{reviewer}",
        "inconsistencia_json": f"inconsistencia_json_{reviewer}",
        "motivo_inconsistencia": f"motivo_inconsistencia_{reviewer}",
        "acao_guardrail": f"acao_guardrail_{reviewer}",
    }
    out = out.rename(columns=rename_map)
    keep = [col for col in rename_map.values() if col in out.columns]
    return out[keep]


def run_screening_sync(
    agent: Agent[None, ScreeningDecision] | None,
    df: pd.DataFrame,
    busca: str,
    reviewer: str,
    model: str,
    cache_dir: Path,
    progress_dir: Path,
    *,
    max_concurrent: int = 10,
    require_api: bool = True,
    legacy_cache_files: list[Path] | None = None,
) -> pd.DataFrame:
    return asyncio.run(
        run_screening(
            agent,
            df,
            busca,
            reviewer,
            model,
            cache_dir,
            progress_dir,
            max_concurrent=max_concurrent,
            require_api=require_api,
            legacy_cache_files=legacy_cache_files,
        )
    )
