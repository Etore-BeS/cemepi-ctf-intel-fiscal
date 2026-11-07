"""Write the retained-hyperparameter rows for the benchmark appendix.

Reads ``state.json`` and ``metrics_test_ranked.csv`` for each series and grain.
Does not read forecasts, outcomes, or series files.
"""

from __future__ import annotations

import ast
import csv
import json
import math
import re
from pathlib import Path

ARTICLES = Path(__file__).resolve().parent
RESULTS = ARTICLES / "arrecadacao_forecast_v1_segmentado" / "results"
TABLE = (
    ARTICLES.parents[1]
    / "manuscript"
    / "arrecadacao_forecast_v1"
    / "tables"
    / "tab_hyperparams.tex"
)

SERIES = ("total", "declarado", "autuado")
GRAINS = (
    ("monthly", "Month"),
    ("weekly", "Week"),
    ("business_daily", "Mon to Fri"),
    ("daily", "Calendar day"),
)
MODELS = (
    ("M0", "M0 naive"),
    ("M1", "M1 ridge"),
    ("M2", "M2 SARIMAX"),
    ("M3", "M3 boosting"),
    ("M4", "M4 Prophet"),
    ("M5", "M5 MLP"),
    ("M6", "M6 forest"),
    ("M7", "M7 GP"),
    ("M8", "M8 kernel ridge"),
    ("M9", "M9 TimesFM"),
    ("E1", "E1 TEM"),
    ("E2", "E2 proxy"),
)
PREFIX = re.compile(r"^(?:BD_|D_|W_)")
FAMILIES = (
    "M0",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "E1",
    "E2",
    "E3",
    "E4",
)


def bare_name(modelo: str) -> str:
    return PREFIX.sub("", modelo)


def family_of(modelo: str) -> str:
    name = bare_name(modelo)
    if name.startswith("M1"):
        return "M1"
    for key in FAMILIES:
        if name.startswith(key):
            return key
    raise ValueError(f"unknown model name: {modelo}")


def fmt_num(value: float) -> str:
    return format(value, ".12g")


def finite_smape(row: dict) -> float | None:
    value = row.get("SMAPE")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def load_cell(series: str, grain: str) -> tuple[list[dict], dict]:
    folder = RESULTS / series / grain
    payload = json.loads((folder / "state.json").read_text())
    results = payload["results"]
    status = payload["status"]
    ranked: dict[str, str] = {}
    with (folder / "metrics_test_ranked.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            ranked[row["modelo"]] = row["notes"]
    _check_ranked(results, ranked)
    return results, status


def _check_ranked(results: list[dict], ranked: dict[str, str]) -> None:
    seen: set[str] = set()
    for row in results:
        if row.get("stage") != "test" or "_cand" in str(row["modelo"]):
            continue
        name = str(row["modelo"])
        seen.add(name)
        if name not in ranked:
            raise ValueError(f"test row missing from metrics_test_ranked.csv: {name}")
        if ranked[name] != row.get("notes"):
            raise ValueError(f"notes differ for {name}")
    extra = set(ranked) - seen
    if extra:
        raise ValueError(f"ranked models missing from state.json: {sorted(extra)}")


def val_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("stage") == "val"]


def test_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("stage") == "test" and "_cand" not in str(row["modelo"])
    ]


def argmin_rows(rows: list[dict]) -> list[dict]:
    scored = [(finite_smape(row), row) for row in rows]
    scored = [(score, row) for score, row in scored if score is not None]
    if not scored:
        raise ValueError("no validation sMAPE")
    best = min(score for score, _row in scored)
    return [row for score, row in scored if score == best]


def rows_for(results: list[dict], family: str) -> list[dict]:
    return [row for row in results if family_of(str(row["modelo"])) == family]


def one_test(rows: list[dict], family: str) -> dict:
    found = test_rows(rows)
    if len(found) != 1:
        raise ValueError(f"{family} has {len(found)} test rows")
    return found[0]


def ints_in(text: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in text.split(","))


def select_m0(rows: list[dict]) -> str:
    lags = []
    for row in test_rows(rows):
        match = re.search(r"k=(\d+)", str(row.get("notes")))
        if not match:
            raise ValueError(f"M0 lag missing: {row.get('notes')}")
        lags.append(int(match.group(1)))
    if not lags:
        raise ValueError("M0 has no test lags")
    shown = ",".join(str(lag) for lag in sorted(set(lags)))
    return f"fixed $k={shown}$"


def _ridge_alpha(modelo: str, notes: str) -> float:
    match = re.search(r"α=([0-9.]+)", notes) or re.search(
        r"Ridge_a([0-9.]+)", bare_name(modelo)
    )
    if not match:
        raise ValueError(f"ridge penalty missing: {modelo} {notes}")
    return float(match.group(1))


def select_m1(rows: list[dict]) -> str:
    ridge_val = [
        row for row in val_rows(rows) if "Ridge" in bare_name(str(row["modelo"]))
    ]
    chosen_rows = argmin_rows(ridge_val)
    alphas = {
        _ridge_alpha(str(row["modelo"]), str(row.get("notes"))) for row in chosen_rows
    }
    test = [
        row
        for row in test_rows(rows)
        if bare_name(str(row["modelo"])).startswith("M1_Ridge")
    ]
    if len(test) != 1:
        raise ValueError(f"M1 ridge test rows: {len(test)}")
    kept = _ridge_alpha(str(test[0]["modelo"]), str(test[0].get("notes")))
    if kept not in alphas:
        raise ValueError(f"retained ridge penalty {kept} is not a validation minimum")
    text = f"penalty ${fmt_num(kept)}$"
    if len(alphas) > 1:
        return f"{text} (tie)"
    return text


def select_m2(rows: list[dict]) -> str:
    parsed = []
    for row in val_rows(rows):
        if finite_smape(row) is not None:
            raise ValueError("M2 validation row has an sMAPE; selection is AIC")
        match = re.search(
            r"order=\(([^)]+)\) seas=\(([^)]+)\); AIC=([0-9.]+)",
            str(row.get("notes")),
        )
        if not match:
            raise ValueError(f"M2 candidate note not parsed: {row.get('notes')}")
        order = (
            ints_in(match.group(1)),
            ints_in(match.group(2)),
            float(match.group(3)),
        )
        parsed.append(order)
    if not parsed:
        raise ValueError("M2 has no AIC candidates")
    best_aic = min(item[2] for item in parsed)
    winners = [item for item in parsed if item[2] == best_aic]
    test = one_test(rows, "M2")
    match = re.search(r"SARIMAX\(([^)]+)\)x\(([^)]+)\)", bare_name(str(test["modelo"])))
    if not match:
        raise ValueError(f"M2 test name not parsed: {test['modelo']}")
    kept = (ints_in(match.group(1)), ints_in(match.group(2)))
    if not any(item[0] == kept[0] and item[1] == kept[1] for item in winners):
        raise ValueError("retained SARIMAX order is not the train AIC minimum")
    nonseasonal, seasonal = kept
    period = seasonal[-1]
    left = ",".join(str(part) for part in nonseasonal)
    right = ",".join(str(part) for part in seasonal[:-1])
    text = f"$({left})\\times({right})_{{{period}}}$"
    if len(winners) > 1:
        return f"{text} (AIC, tie)"
    return f"{text} (AIC)"


def _hgb(notes: str) -> dict[str, float]:
    match = re.search(r"\{.*\}", notes)
    if not match:
        raise ValueError(f"boosting note not parsed: {notes}")
    payload = ast.literal_eval(match.group(0))
    return {
        "max_iter": int(payload["max_iter"]),
        "learning_rate": float(payload["learning_rate"]),
        "max_depth": int(payload["max_depth"]),
    }


def select_m3(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_hgb(str(row.get("notes"))) for row in chosen_rows]
    kept = _hgb(str(one_test(rows, "M3").get("notes")))
    if kept not in tied:
        raise ValueError("retained boosting setting is not a validation minimum")
    text = (
        f"trees ${fmt_num(float(kept['max_iter']))}$, "
        f"rate ${fmt_num(kept['learning_rate'])}$, "
        f"depth ${kept['max_depth']}$"
    )
    distinct = {tuple(sorted(item.items())) for item in tied}
    if len(distinct) == 1:
        return text
    same_rate = all(
        item["max_iter"] == kept["max_iter"]
        and item["learning_rate"] == kept["learning_rate"]
        for item in tied
    )
    others = sorted(
        {
            int(item["max_depth"])
            for item in tied
            if item["max_depth"] != kept["max_depth"]
        }
    )
    if same_rate and others:
        extra = ", ".join(f"${depth}$" for depth in others)
        return f"{text} (tie with depth {extra})"
    return f"{text} (tie)"


def _prophet_val(notes: str) -> tuple[float, float]:
    payload = ast.literal_eval(notes)
    return (
        float(payload["changepoint_prior_scale"]),
        float(payload["seasonality_prior_scale"]),
    )


def _prophet_test(notes: str) -> tuple[float, float]:
    match = re.search(r"cps=([0-9.]+),sps=([0-9.]+)", notes)
    if not match:
        raise ValueError(f"Prophet test note not parsed: {notes}")
    return (float(match.group(1)), float(match.group(2)))


def select_m4(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_prophet_val(str(row.get("notes"))) for row in chosen_rows]
    kept = _prophet_test(str(one_test(rows, "M4").get("notes")))
    if kept not in tied:
        raise ValueError("retained Prophet scales are not a validation minimum")
    text = f"trend ${fmt_num(kept[0])}$, season ${fmt_num(kept[1])}$"
    if len(set(tied)) > 1:
        return f"{text} (tie)"
    return text


def _mlp(notes: str) -> tuple[int, int, int, float]:
    match = re.search(r"lag=(\d+),h=\((\d+),(\d+)\),lr=([0-9.eE+-]+)", notes)
    if not match:
        raise ValueError(f"MLP note not parsed: {notes}")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        float(match.group(4)),
    )


def select_m5(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_mlp(str(row.get("notes"))) for row in chosen_rows]
    kept = _mlp(str(one_test(rows, "M5").get("notes")))
    if kept not in tied:
        raise ValueError("retained MLP setting is not a validation minimum")
    text = f"lag ${kept[0]}$, width $({kept[1]},{kept[2]})$, rate ${fmt_num(kept[3])}$"
    if len(set(tied)) > 1:
        return f"{text} (tie)"
    return text


def _forest(notes: str) -> tuple[int, float]:
    match = re.search(r"n=(\d+),mf=([0-9.]+)", notes)
    if not match:
        raise ValueError(f"forest note not parsed: {notes}")
    return (int(match.group(1)), float(match.group(2)))


def select_m6(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_forest(str(row.get("notes"))) for row in chosen_rows]
    kept = _forest(str(one_test(rows, "M6").get("notes")))
    if kept not in tied:
        raise ValueError("retained forest setting is not a validation minimum")
    text = f"trees ${kept[0]}$, subsample ${fmt_num(kept[1])}$"
    if len(set(tied)) > 1:
        return f"{text} (tie)"
    return text


def _gp_val(notes: str) -> tuple[str, str]:
    cap = re.fullmatch(r"(RBF|Matern|RQ)\+cap(\d+)", notes.strip())
    if cap:
        return (cap.group(1), f"cap{cap.group(2)}")
    match = re.fullmatch(r"(RBF|Matern|RQ),alpha=([0-9.]+)", notes.strip())
    if not match:
        raise ValueError(f"GP note not parsed: {notes}")
    return (match.group(1), fmt_num(float(match.group(2))))


def _gp_test(modelo: str, notes: str) -> tuple[str, str]:
    match = re.search(r"M7_GPR_(RBF|Matern|RQ)", bare_name(modelo))
    if not match:
        raise ValueError(f"GP test name not parsed: {modelo}")
    kernel = match.group(1)
    if "cap=200" in notes or "cap200" in bare_name(modelo):
        return (kernel, "cap200")
    alpha = re.search(r"alpha=([0-9.]+)", notes)
    if not alpha:
        raise ValueError(f"GP test note not parsed: {notes}")
    return (kernel, fmt_num(float(alpha.group(1))))


def select_m7(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_gp_val(str(row.get("notes"))) for row in chosen_rows]
    test = one_test(rows, "M7")
    kept = _gp_test(str(test["modelo"]), str(test.get("notes")))
    if kept not in tied:
        raise ValueError("retained GP setting is not a validation minimum")
    if kept[1].startswith("cap"):
        text = f"{kept[0]}, cap ${kept[1].removeprefix('cap')}$"
    else:
        text = f"{kept[0]}, noise ${kept[1]}$"
    if len(set(tied)) > 1:
        return f"{text} (tie)"
    return text


def _krr_val(notes: str) -> tuple[str, float, float]:
    match = re.fullmatch(
        r"(rbf|laplacian)_a([0-9.]+)_g([0-9.]+)\+scaler",
        notes.strip(),
    )
    if not match:
        raise ValueError(f"kernel-ridge note not parsed: {notes}")
    return (match.group(1), float(match.group(2)), float(match.group(3)))


def _krr_test(modelo: str, notes: str) -> tuple[str, float, float]:
    name = bare_name(modelo)
    if "laplacian" in name:
        kernel = "laplacian"
    elif "rbf" in name:
        kernel = "rbf"
    else:
        raise ValueError(f"kernel-ridge name not parsed: {modelo}")
    match = re.search(r"α=([0-9.]+),γ=([0-9.]+)\+scaler", notes)
    if not match:
        raise ValueError(f"kernel-ridge test note not parsed: {notes}")
    return (kernel, float(match.group(1)), float(match.group(2)))


def select_m8(rows: list[dict]) -> str:
    chosen_rows = argmin_rows(val_rows(rows))
    tied = [_krr_val(str(row.get("notes"))) for row in chosen_rows]
    test = one_test(rows, "M8")
    kept = _krr_test(str(test["modelo"]), str(test.get("notes")))
    if kept not in tied:
        raise ValueError("retained kernel ridge setting is not a validation minimum")
    label = "Laplacian" if kept[0] == "laplacian" else "RBF"
    text = f"{label}, penalty ${fmt_num(kept[1])}$, width ${fmt_num(kept[2])}$"
    if len(set(tied)) > 1:
        return f"{text} (tie)"
    return text


def select_m9(rows: list[dict]) -> str:
    if val_rows(rows):
        raise ValueError("M9 has validation rows; weights were expected to stay fixed")
    notes = str(one_test(rows, "M9").get("notes"))
    if "soft-fail" in notes.lower() or "timesfm" not in notes.lower():
        raise ValueError("M9 test note is not a frozen TimesFM forecast")
    return "not updated"


_E1_NOTE = re.compile(
    r"TimesNet\+Exp_Main_Energy; "
    r"cands=(?P<cands>\[[^\]]+\]); "
    r"thr_val=(?P<thr>[-+0-9.eE]+); "
    r"val_sMAPE=(?P<val>[0-9.]+); "
    r"REAL TEM; "
    r"seq=(?P<seq>\d+); "
    r"epochs_bb=(?P<bb>\d+); "
    r"epochs_ebm=(?P<ebm>\d+)$"
)


def _e1_config(notes: str) -> dict[str, float | int | str]:
    match = _E1_NOTE.fullmatch(notes.strip())
    if not match:
        raise ValueError(f"E1 note is not a TimesNet configuration: {notes}")
    candidates = ast.literal_eval(match.group("cands"))
    if not isinstance(candidates, list) or any(
        not isinstance(item, str) for item in candidates
    ):
        raise ValueError(f"E1 candidates not parsed: {notes}")
    seasonal = [item for item in candidates if item.startswith("seas")]
    if len(candidates) != 3 or len(seasonal) != 1:
        raise ValueError(f"E1 candidate set is not three names: {candidates}")
    if "hgb" not in candidates or "timesnet" not in candidates:
        raise ValueError(
            f"E1 candidates are missing boosting or TimesNet: {candidates}"
        )
    return {
        "thr": float(match.group("thr")),
        "val_note": match.group("val"),
        "seq": int(match.group("seq")),
        "bb": int(match.group("bb")),
        "ebm": int(match.group("ebm")),
    }


def select_e1(rows: list[dict], status: dict) -> str:
    test = one_test(rows, "E1")
    validation = val_rows(rows)
    if len(validation) != 1:
        raise ValueError(f"E1 validation rows: {len(validation)}")
    test_score = finite_smape(test)
    val_score = finite_smape(validation[0])
    if test_score is None or val_score is None:
        raise ValueError("E1 is missing a validation or test sMAPE")
    if str(test.get("notes")) != str(validation[0].get("notes")):
        raise ValueError("E1 validation and test notes differ")
    info = status.get("E1") or {}
    if info.get("ok") not in (1, True) or info.get("backend") != (
        "TimesNet+Exp_Main_Energy"
    ):
        raise ValueError("E1 backend is not TimesNet+Exp_Main_Energy")
    config = _e1_config(str(test.get("notes")))
    timesnet = info.get("timesnet") or {}
    if (
        timesnet.get("ok") not in (1, True)
        or int(timesnet.get("seq_len")) != config["seq"]
        or int(timesnet.get("train_epochs")) != config["bb"]
        or int(timesnet.get("ebm_epochs")) != config["ebm"]
    ):
        raise ValueError("E1 TimesNet status does not match the saved note")
    if f"{val_score:.2f}" != config["val_note"]:
        raise ValueError("E1 note val_sMAPE does not match the validation row")
    return (
        f"TimesNet, seq ${config['seq']}$, "
        f"forecast ${config['bb']}$, energy ${config['ebm']}$, "
        f"$\\tau={fmt_num(float(config['thr']))}$, "
        f"val ${val_score:.2f}$, test ${test_score:.2f}$"
    )


def _tau(notes: str) -> str:
    match = re.search(r"thr_val=([0-9]*\.?[0-9]+)e\+0*([0-9]+)", notes)
    if not match:
        raise ValueError(f"E2 threshold missing: {notes}")
    exponent = str(int(match.group(2)))
    return f"$\\tau={match.group(1)}\\times 10^{{{exponent}}}$"


def select_e2(rows: list[dict]) -> str:
    validation = val_rows(rows)
    test = one_test(rows, "E2")
    if len(validation) != 1:
        raise ValueError(f"E2 validation rows: {len(validation)}")
    if finite_smape(validation[0]) is None:
        raise ValueError("E2 validation row has no sMAPE")
    kept = _tau(str(test.get("notes")))
    if kept != _tau(str(validation[0].get("notes"))):
        raise ValueError("E2 validation and test thresholds differ")
    return kept


SELECTORS = {
    "M0": select_m0,
    "M1": select_m1,
    "M2": select_m2,
    "M3": select_m3,
    "M4": select_m4,
    "M5": select_m5,
    "M6": select_m6,
    "M7": select_m7,
    "M8": select_m8,
    "M9": select_m9,
}


def cell_text(family: str, results: list[dict], status: dict) -> str:
    rows = rows_for(results, family)
    if family == "E1":
        return select_e1(rows, status)
    if family == "E2":
        return select_e2(rows)
    return SELECTORS[family](rows)


def build_rows() -> list[str]:
    loaded = {
        (series, grain): load_cell(series, grain)
        for series in SERIES
        for grain, _label in GRAINS
    }
    lines = ["% Generated by make_hyperparams_table.py. Do not edit."]
    last_grain = len(GRAINS) - 1
    last_model = len(MODELS) - 1
    for index, (grain, label) in enumerate(GRAINS):
        lines.append(rf"\emph{{{label}}} & & & \\")
        for model_index, (family, name) in enumerate(MODELS):
            cells = [
                cell_text(
                    family, loaded[(series, grain)][0], loaded[(series, grain)][1]
                )
                for series in SERIES
            ]
            ending = (
                r"\\[4pt]"
                if model_index == last_model and index < last_grain
                else r"\\"
            )
            lines.append(f"{name} & {cells[0]} & {cells[1]} & {cells[2]} {ending}")
    _reject_forbidden("\n".join(lines))
    return lines


def _reject_forbidden(text: str) -> None:
    lowered = text.lower()
    for token in ("/users", "importerror", "http", "github", "cemepi", ".local"):
        if token in lowered:
            raise ValueError(f"generated table contains a forbidden token: {token}")


def main() -> None:
    if not RESULTS.is_dir():
        raise SystemExit(f"results directory not found: {RESULTS}")
    lines = build_rows()
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.write_text("\n".join(lines) + "\n")
    print(f"wrote {TABLE}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
