"""Quality exploration for PF redaction and CNPJ extraction."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from config.paths import PIPELINE_ROOT
from config.scripts import EXPORT_VALIDATION_SAMPLES, format_command
from utils.cnpj_extraction import CNPJ_CANDIDATE_RE, format_cnpj, is_cda_number, is_valid_cnpj
from utils.party_identity import is_natural_person

OUTPUT_DIR = PIPELINE_ROOT / "notebooks/playground/output"

PJ_MISCLASS_PATTERNS = re.compile(
    r"(?i)\b("
    r"uni[aã]o federal|fazenda|prefeitura|munic[ií]pio|prfn|"
    r"importacao|importa[cç][aã]o|ltda|s\.?a\.?|s/a|companhia|banco|"
    r"cnpj|incorporadora|empreendimento"
    r")\b"
)

PROMOTION_GATES = {
    "pf_name_precision": 0.95,
    "regex_precision": 0.98,
    "cda_rejection_precision": 0.99,
    "natural_person_rejection_precision": 0.95,
}


@dataclass
class StratumMetric:
    stratum: str
    n: int
    n_labeled: int
    precision: float | None
    n_incorrect: int
    n_ambiguous: int


@dataclass
class GateResult:
    gate: str
    threshold: float
    observed: float | None
    passed: bool
    notes: str


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_valid_cpf_digits(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    nums = [int(c) for c in digits]
    s1 = sum(n * w for n, w in zip(nums[:9], range(10, 1, -1), strict=True))
    r1 = (s1 * 10) % 11
    r1 = 0 if r1 == 10 else r1
    if nums[9] != r1:
        return False
    s2 = sum(n * w for n, w in zip(nums[:10], range(11, 1, -1), strict=True))
    r2 = (s2 * 10) % 11
    r2 = 0 if r2 == 10 else r2
    return nums[10] == r2


def _heuristic_redaction_label(row: dict) -> tuple[str, str]:
    label = row["label"]
    source = row["source"]
    text = str(row.get("original_text") or "")

    if label == "pf_name" and source == "face_party":
        if not is_natural_person(text):
            return "incorrect", "pj_indicator_in_name"
        if PJ_MISCLASS_PATTERNS.search(text):
            return "incorrect", "org_keyword_in_pf_name"
        if len(text.strip()) < 5:
            return "ambiguous", "short_name"
        return "correct", "pf_face_party"

    if label == "cpf":
        digits = _only_digits(text)
        if len(digits) == 11 and _is_valid_cpf_digits(digits):
            return "correct", "valid_cpf_checksum"
        if "cpf" in text.lower():
            return "correct", "cpf_context"
        return "incorrect", "invalid_cpf_or_false_match"

    if label in {"rg", "oab"}:
        prefix = label.upper()
        if text.upper().startswith(prefix):
            return "correct", f"{label}_prefix_present"
        return "ambiguous", f"missing_{label}_prefix"

    if label == "phone":
        digits = _only_digits(text)
        if "\t" in text:
            return "incorrect", "tab_separator_metadata"
        if re.search(r"(?i)(?:tel|fone|cel|whatsapp|contato)", text):
            if 10 <= len(digits) <= 13:
                return "correct", "phone_with_context"
            return "incorrect", "invalid_phone_length"
        if re.search(r"\(\d{2}\)\s+\d", text) or re.search(r"\b\d{2}\s+\d{4}[- ]\d{4}", text):
            if 10 <= len(digits) <= 13:
                return "correct", "phone_with_ddd"
            return "incorrect", "invalid_phone_length"
        return "incorrect", "missing_phone_context"

    if label == "email" and "@" in text:
        return "correct", "email_format"

    return "ambiguous", "unhandled_label"


def _heuristic_cnpj_label(row: dict) -> tuple[str, str]:
    rejected = row.get("rejected_reason")
    cnpj = row.get("cnpj")
    snippet = str(row.get("snippet") or "")
    confidence = row.get("confidence")
    pole_match = row.get("pole_matches_syntax")

    if rejected == "cda_number":
        for match in CNPJ_CANDIDATE_RE.finditer(snippet):
            candidate = match.group(0)
            if is_valid_cnpj(candidate) and is_cda_number(format_cnpj(candidate), snippet):
                return "correct", "cda_number_in_list"
        if "CDA/Outros" in snippet:
            return "correct", "cda_header_context"
        return "ambiguous", "cda_context_unclear"

    if rejected == "natural_person":
        party = row.get("party_from_syntax") or row.get("candidate_party")
        if party and is_natural_person(str(party)):
            return "correct", "pf_party_rejection"
        return "incorrect", "pj_misrejected_as_pf"

    if cnpj and is_valid_cnpj(str(cnpj)):
        if confidence == "high" and pole_match is True:
            return "correct", "high_confidence_pole_match"
        if confidence in {"high", "medium"}:
            return "correct", "syntax_match"
        return "ambiguous", "low_or_unmatched"

    return "ambiguous", "unhandled_cnpj_row"


def _extract_cnpj_candidate_from_snippet(snippet: str | None) -> str | None:
    if not snippet:
        return None
    for match in CNPJ_CANDIDATE_RE.finditer(snippet):
        candidate = match.group(0)
        if is_valid_cnpj(candidate):
            return format_cnpj(candidate)
    return None


def _merge_manual_labels(
    df: pl.DataFrame,
    path: Path,
    *,
    key_cols: list[str],
) -> pl.DataFrame:
    if not path.exists():
        return df
    existing = pl.read_csv(path)
    if "manual_label" not in existing.columns:
        return df
    merge_keys = [col for col in key_cols if col in df.columns and col in existing.columns]
    if not merge_keys:
        return df
    saved = existing.select(
        merge_keys + [col for col in ("manual_label", "notes") if col in existing.columns]
    )
    merged = df.drop(["manual_label", "notes"], strict=False).join(saved, on=merge_keys, how="left")
    return merged.with_columns(
        pl.col("manual_label").fill_null(""),
        pl.col("notes").fill_null(""),
    )


def _stratified_sample(df: pl.DataFrame, group_cols: list[str], per_stratum: int, seed: int) -> pl.DataFrame:
    if df.is_empty():
        return df
    parts: list[pl.DataFrame] = []
    for key, group in df.group_by(group_cols, maintain_order=True):
        parts.append(group.sample(n=min(per_stratum, len(group)), shuffle=True, seed=seed))
    if not parts:
        return df.head(0)
    return pl.concat(parts)


def _apply_heuristic_labels(df: pl.DataFrame, kind: str) -> pl.DataFrame:
    rows = []
    fn = _heuristic_redaction_label if kind == "redaction" else _heuristic_cnpj_label
    for row in df.iter_rows(named=True):
        auto_label, reason = fn(row)
        manual = str(row.get("manual_label") or "").strip().lower()
        final_label = manual if manual else auto_label
        rows.append({**row, "auto_label": auto_label, "auto_label_reason": reason, "review_label": final_label})
    return pl.DataFrame(rows)


def _precision_metrics(df: pl.DataFrame, stratum_col: str) -> list[StratumMetric]:
    metrics: list[StratumMetric] = []
    for stratum in df[stratum_col].unique().sort().to_list():
        subset = df.filter(pl.col(stratum_col) == stratum)
        labeled = subset.filter(pl.col("review_label").is_in(["correct", "incorrect", "ambiguous"]))
        correct = labeled.filter(pl.col("review_label") == "correct")
        incorrect = labeled.filter(pl.col("review_label") == "incorrect")
        ambiguous = labeled.filter(pl.col("review_label") == "ambiguous")
        evaluable = labeled.filter(pl.col("review_label").is_in(["correct", "incorrect"]))
        precision = (
            correct.height / evaluable.height if evaluable.height else None
        )
        metrics.append(
            StratumMetric(
                stratum=str(stratum),
                n=subset.height,
                n_labeled=labeled.height,
                precision=round(precision, 4) if precision is not None else None,
                n_incorrect=incorrect.height,
                n_ambiguous=ambiguous.height,
            )
        )
    return metrics


def _error_taxonomy(df: pl.DataFrame) -> list[dict]:
    wrong = df.filter(pl.col("review_label") == "incorrect")
    if wrong.is_empty():
        return []
    return (
        wrong.group_by("auto_label_reason")
        .agg(pl.len().alias("n"), pl.col("original_text").head(3).alias("examples"))
        .sort("n", descending=True)
        .to_dicts()
    )


def _evaluate_gates(redaction: pl.DataFrame, cnpj: pl.DataFrame) -> list[GateResult]:
    results: list[GateResult] = []

    pf = redaction.filter((pl.col("label") == "pf_name") & (pl.col("source") == "face_party"))
    pf_eval = pf.filter(pl.col("review_label").is_in(["correct", "incorrect"]))
    pf_prec = (
        pf.filter(pl.col("review_label") == "correct").height / pf_eval.height
        if pf_eval.height
        else None
    )
    results.append(
        GateResult(
            gate="pf_name_precision",
            threshold=PROMOTION_GATES["pf_name_precision"],
            observed=round(pf_prec, 4) if pf_prec is not None else None,
            passed=pf_prec is not None and pf_prec >= PROMOTION_GATES["pf_name_precision"],
            notes=f"evaluated {pf_eval.height} pf_name spans (heuristic labels)",
        )
    )

    regex_labels = ["cpf", "email", "phone"]
    regex_df = redaction.filter(pl.col("label").is_in(regex_labels))
    regex_eval = regex_df.filter(pl.col("review_label").is_in(["correct", "incorrect"]))
    regex_prec = (
        regex_df.filter(pl.col("review_label") == "correct").height / regex_eval.height
        if regex_eval.height
        else None
    )
    results.append(
        GateResult(
            gate="regex_precision",
            threshold=PROMOTION_GATES["regex_precision"],
            observed=round(regex_prec, 4) if regex_prec is not None else None,
            passed=regex_prec is not None and regex_prec >= PROMOTION_GATES["regex_precision"],
            notes=f"labels={regex_labels}; evaluated {regex_eval.height} spans",
        )
    )

    cda = cnpj.filter(pl.col("rejected_reason") == "cda_number")
    cda_eval = cda.filter(pl.col("review_label").is_in(["correct", "incorrect"]))
    cda_prec = (
        cda.filter(pl.col("review_label") == "correct").height / cda_eval.height
        if cda_eval.height
        else None
    )
    results.append(
        GateResult(
            gate="cda_rejection_precision",
            threshold=PROMOTION_GATES["cda_rejection_precision"],
            observed=round(cda_prec, 4) if cda_prec is not None else None,
            passed=cda_prec is not None and cda_prec >= PROMOTION_GATES["cda_rejection_precision"],
            notes=f"evaluated {cda_eval.height} cda rejections",
        )
    )

    np_rej = cnpj.filter(pl.col("rejected_reason") == "natural_person")
    np_eval = np_rej.filter(pl.col("review_label").is_in(["correct", "incorrect"]))
    np_prec = (
        np_rej.filter(pl.col("review_label") == "correct").height / np_eval.height
        if np_eval.height
        else None
    )
    np_passed = np_eval.height == 0 or (
        np_prec is not None and np_prec >= PROMOTION_GATES["natural_person_rejection_precision"]
    )
    np_notes = (
        "no natural_person rejections in stratified sample"
        if np_eval.height == 0
        else f"evaluated {np_eval.height} natural_person rejections"
    )
    results.append(
        GateResult(
            gate="natural_person_rejection_precision",
            threshold=PROMOTION_GATES["natural_person_rejection_precision"],
            observed=round(np_prec, 4) if np_prec is not None else None,
            passed=np_passed,
            notes=np_notes,
        )
    )

    leakage = pf.filter(pl.col("review_label") == "incorrect").height
    results.append(
        GateResult(
            gate="critical_leakage_spotcheck",
            threshold=0.0,
            observed=float(leakage),
            passed=leakage == 0,
            notes="proxy: count of incorrect pf_name redactions (PJ/org misclassified as PF)",
        )
    )

    return results


def _rule_recommendations(gates: list[GateResult], redaction_errors: list[dict]) -> list[str]:
    recs: list[str] = []
    by_gate = {g.gate: g for g in gates}
    if not by_gate.get("regex_precision", GateResult("", 0, 0, True, "")).passed:
        recs.append(
            "Tighten PHONE regex: require contextual cues (Tel./Telefone) or minimum digit patterns "
            "to avoid matching process metadata fragments."
        )
    if not by_gate.get("pf_name_precision", GateResult("", 0, 0, True, "")).passed:
        recs.append(
            "Extend PJ indicators in `party_identity.py` (e.g. PRFN, União Federal, Importacao) "
            "before redacting FACE party names."
        )
        recs.append(
            "Only redact FACE names when `is_natural_person(name)` is true; skip PJ-like FACE rows."
        )
    if redaction_errors:
        top = redaction_errors[0].get("auto_label_reason")
        if top == "org_keyword_in_pf_name":
            recs.append(
                "Review FACE `reus`/`autores` rows where PJ strings were classified as PF names."
            )
    if not by_gate.get("critical_leakage_spotcheck", GateResult("", 0, 0, True, "")).passed:
        recs.append(
            "Block pipeline promotion until incorrect pf_name redactions are zero in manual review."
        )
    if not recs:
        recs.append("Heuristic gates passed; run manual label confirmation on stratified CSVs.")
    return recs


def _write_markdown_report(
    path: Path,
    *,
    redaction_metrics: list[StratumMetric],
    cnpj_metrics: list[StratumMetric],
    redaction_errors: list[dict],
    cnpj_errors: list[dict],
    gates: list[GateResult],
    go_no_go: str,
    recommendations: list[str],
) -> None:
    lines = [
        "# Quality Exploration Report",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Promotion decision",
        "",
        f"**{go_no_go}**",
        "",
        "## Gate results",
        "",
        "| Gate | Threshold | Observed | Pass | Notes |",
        "|------|-----------|----------|------|-------|",
    ]
    for gate in gates:
        obs = "n/a" if gate.observed is None else str(gate.observed)
        lines.append(
            f"| {gate.gate} | {gate.threshold} | {obs} | {'yes' if gate.passed else 'no'} | {gate.notes} |"
        )

    lines.extend(["", "## Redaction precision by stratum", ""])
    for metric in redaction_metrics:
        lines.append(
            f"- `{metric.stratum}`: n={metric.n}, precision={metric.precision}, "
            f"incorrect={metric.n_incorrect}, ambiguous={metric.n_ambiguous}"
        )

    lines.extend(["", "## CNPJ precision by stratum", ""])
    for metric in cnpj_metrics:
        lines.append(
            f"- `{metric.stratum}`: n={metric.n}, precision={metric.precision}, "
            f"incorrect={metric.n_incorrect}, ambiguous={metric.n_ambiguous}"
        )

    if redaction_errors:
        lines.extend(["", "## Redaction error taxonomy", ""])
        for item in redaction_errors:
            examples = item.get("examples") or []
            lines.append(f"- `{item['auto_label_reason']}` (n={item['n']}): {examples}")

    if cnpj_errors:
        lines.extend(["", "## CNPJ error taxonomy", ""])
        for item in cnpj_errors:
            examples = item.get("examples") or []
            lines.append(f"- `{item['auto_label_reason']}` (n={item['n']}): {examples}")

    lines.extend(["", "## Recommended rule tweaks (ranked)", ""])
    for idx, rec in enumerate(recommendations, start=1):
        lines.append(f"{idx}. {rec}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_exploration(*, per_stratum: int = 15, seed: int = 42) -> dict:
    redaction_path = OUTPUT_DIR / "pf_redaction_validation_sample.csv"
    cnpj_path = OUTPUT_DIR / "cnpj_validation_sample.csv"
    if not redaction_path.exists() or not cnpj_path.exists():
        msg = f"Run {format_command(EXPORT_VALIDATION_SAMPLES)} first"
        raise FileNotFoundError(msg)

    df_redaction_raw = pl.read_csv(redaction_path)
    df_cnpj_raw = pl.read_csv(cnpj_path)

    df_redaction_strat = _stratified_sample(
        df_redaction_raw, ["label", "source"], per_stratum, seed
    )
    df_cnpj_strat = _stratified_sample(
        df_cnpj_raw.with_columns(
            pl.concat_str(
                [
                    pl.col("syntax_pattern").fill_null(""),
                    pl.lit("|"),
                    pl.col("rejected_reason").fill_null(""),
                    pl.lit("|"),
                    pl.col("confidence").fill_null(""),
                ]
            ).alias("cnpj_stratum")
        ),
        ["cnpj_stratum"],
        per_stratum,
        seed,
    )

    manual_redaction_path = OUTPUT_DIR / "manual_review_redaction.csv"
    manual_cnpj_path = OUTPUT_DIR / "manual_review_cnpj.csv"

    df_redaction = _merge_manual_labels(
        df_redaction_strat,
        manual_redaction_path,
        key_cols=["cd_processo", "id_processo", "label", "source", "original_text"],
    )
    df_cnpj = _merge_manual_labels(
        df_cnpj_strat,
        manual_cnpj_path,
        key_cols=["cd_processo", "id_processo", "syntax_pattern", "rejected_reason", "snippet"],
    )
    df_cnpj = df_cnpj.with_columns(
        pl.when(pl.col("cnpj").is_null() | (pl.col("cnpj") == ""))
        .then(pl.col("snippet").map_elements(_extract_cnpj_candidate_from_snippet, return_dtype=pl.Utf8))
        .otherwise(None)
        .alias("cnpj_candidate")
    )

    df_redaction = _apply_heuristic_labels(df_redaction, "redaction")
    df_cnpj = _apply_heuristic_labels(df_cnpj, "cnpj")

    df_redaction = df_redaction.with_columns(
        pl.concat_str([pl.col("label"), pl.lit("|"), pl.col("source")]).alias("stratum")
    )
    if "cnpj_stratum" not in df_cnpj.columns:
        df_cnpj = df_cnpj.with_columns(
            pl.concat_str(
                [
                    pl.col("syntax_pattern").fill_null(""),
                    pl.lit("|"),
                    pl.col("rejected_reason").fill_null(""),
                    pl.lit("|"),
                    pl.col("confidence").fill_null(""),
                ]
            ).alias("cnpj_stratum")
        )
    df_cnpj = df_cnpj.with_columns(pl.col("cnpj_stratum").alias("stratum"))

    redaction_metrics = _precision_metrics(df_redaction, "stratum")
    cnpj_metrics = _precision_metrics(df_cnpj, "stratum")

    redaction_errors = _error_taxonomy(df_redaction)
    cnpj_errors = _error_taxonomy(df_cnpj)
    gates = _evaluate_gates(df_redaction, df_cnpj)

    all_passed = all(g.passed for g in gates)
    go_no_go = "GO (heuristic proxy; confirm with manual labels)" if all_passed else "NO-GO (fix failing gates first)"

    recommendations = _rule_recommendations(gates, redaction_errors)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_json_path = OUTPUT_DIR / "quality_exploration_report.json"
    report_md_path = OUTPUT_DIR / "quality_exploration_report.md"

    export_redaction_cols = [
        c
        for c in [
            "cd_processo",
            "id_processo",
            "label",
            "source",
            "original_text",
            "replacement",
            "auto_label",
            "auto_label_reason",
            "review_label",
            "manual_label",
            "notes",
            "stratum",
        ]
        if c in df_redaction.columns
    ]
    export_cnpj_cols = [
        c
        for c in [
            "cd_processo",
            "id_processo",
            "syntax_pattern",
            "rejected_reason",
            "confidence",
            "stratum",
            "cnpj",
            "cnpj_candidate",
            "candidate_party",
            "party_from_syntax",
            "snippet",
            "manual_label",
            "notes",
            "auto_label",
            "auto_label_reason",
            "review_label",
        ]
        if c in df_cnpj.columns
    ]

    df_redaction.select(export_redaction_cols).write_csv(manual_redaction_path)
    df_cnpj.select(export_cnpj_cols).write_csv(manual_cnpj_path)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "promotion_decision": go_no_go,
        "redaction_metrics": [asdict(m) for m in redaction_metrics],
        "cnpj_metrics": [asdict(m) for m in cnpj_metrics],
        "redaction_error_taxonomy": redaction_errors,
        "cnpj_error_taxonomy": cnpj_errors,
        "promotion_gates": [asdict(g) for g in gates],
        "recommendations": recommendations,
        "outputs": {
            "manual_review_redaction": str(manual_redaction_path),
            "manual_review_cnpj": str(manual_cnpj_path),
        },
    }
    report_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_markdown_report(
        report_md_path,
        redaction_metrics=redaction_metrics,
        cnpj_metrics=cnpj_metrics,
        redaction_errors=redaction_errors,
        cnpj_errors=cnpj_errors,
        gates=gates,
        go_no_go=go_no_go,
        recommendations=recommendations,
    )

    print(f"Manual review (redaction): {manual_redaction_path} ({len(df_redaction):,} rows)")
    print(f"Manual review (cnpj): {manual_cnpj_path} ({len(df_cnpj):,} rows)")
    print(f"Report: {report_md_path}")
    print(f"Decision: {go_no_go}")
    return payload
