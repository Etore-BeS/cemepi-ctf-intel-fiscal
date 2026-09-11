"""Generate PF redaction and CNPJ validation samples from the lake."""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from config.paths import PIPELINE_ROOT, SILVER_FACE_CLEAN, SILVER_PROCESSOS
from utils.cnpj_extraction import extract_mentions
from utils.pf_redaction import anonymize_decision, build_redaction_validation_rows

OUTPUT_DIR = PIPELINE_ROOT / "notebooks/playground/output"
CNPJ_LIKE = r"\d{2}[.]?\d{3}[.]?\d{3}/?\d{4}-?\d{2}"


def build_samples(*, sample_size: int = 500, seed: int = 42) -> tuple[pl.DataFrame, pl.DataFrame]:
    lf_processos = pl.scan_delta(str(SILVER_PROCESSOS))
    lf_face = pl.scan_delta(str(SILVER_FACE_CLEAN))

    df_pool = (
        lf_processos.filter(pl.col("decisao").str.contains(CNPJ_LIKE))
        .select("cd_processo", "id_processo", "decisao", "assunto", "comarca")
        .join(
            lf_face.select("cd_processo", "autores", "reus"),
            on="cd_processo",
            how="left",
        )
        .head(sample_size * 3)
        .collect()
    )
    df_sample = df_pool.sample(
        n=min(sample_size, len(df_pool)),
        shuffle=True,
        seed=seed,
    )

    cnpj_rows: list[dict] = []
    redaction_rows: list[dict] = []

    for row in df_sample.iter_rows(named=True):
        autores = row.get("autores")
        reus = row.get("reus")
        decisao = row["decisao"]
        cd_processo = row["cd_processo"]
        id_processo = str(row["id_processo"])

        for mention in extract_mentions(
            cd_processo=cd_processo,
            id_processo=id_processo,
            decisao=decisao,
            autores_raw=autores,
            reus_raw=reus,
        ):
            cnpj_rows.append(
                {
                    **mention.__dict__,
                    "assunto": row["assunto"],
                    "comarca": row["comarca"],
                    "pole_matches_syntax": (
                        mention.face_pole == mention.expected_face_pole
                        if mention.expected_face_pole and mention.face_pole
                        else None
                    ),
                    "manual_label": "",
                    "notes": "",
                }
            )

        redaction = anonymize_decision(
            decisao,
            autores_raw=autores,
            reus_raw=reus,
        )
        redaction_rows.extend(
            build_redaction_validation_rows(cd_processo, id_processo, redaction)
        )

    return pl.DataFrame(cnpj_rows), pl.DataFrame(redaction_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export CNPJ and PF redaction validation CSVs")
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_cnpj, df_redaction = build_samples(sample_size=args.sample_size, seed=args.seed)

    cnpj_path = OUTPUT_DIR / "cnpj_validation_sample.csv"
    redaction_path = OUTPUT_DIR / "pf_redaction_validation_sample.csv"
    df_cnpj.write_csv(cnpj_path)
    df_redaction.write_csv(redaction_path)
    print(f"CNPJ sample: {cnpj_path} ({len(df_cnpj):,} rows)")
    print(f"Redaction sample: {redaction_path} ({len(df_redaction):,} spans)")


if __name__ == "__main__":
    main()
