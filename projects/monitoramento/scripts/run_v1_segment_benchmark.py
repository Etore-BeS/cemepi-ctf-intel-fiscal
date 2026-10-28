"""Run the closed v1 M0–M9 / E1–E4 protocol on one ICMS series and grain.

Model bodies are the code cells of
``notebooks/articles/arrecadacao_forecast_paper_v1.ipynb``.
They are executed unchanged. Hyperparameters are selected again on the
validation block of the series being scored.

One process handles one series and one grain. Results are written after
every model, so a later model can be resumed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MODEL_ORDER = [
    "M0",
    "M1",
    "M2",
    "M3",
    "M4",
    "M6",
    "M8",
    "M7",
    "M5",
    "E2",
    "E4",
    "E3",
    "E1",
    "M9",
]
TORCH_MODELS = {"M5", "E1", "E3", "E4"}
HEAVY_MODELS = {"M9"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def article_dir() -> Path:
    return (
        repo_root()
        / "projects"
        / "monitoramento"
        / "notebooks"
        / "articles"
        / "arrecadacao_forecast_v1_segmentado"
    )


def memory_free_percent() -> int | None:
    try:
        text = subprocess.check_output(
            ["memory_pressure"], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in text.splitlines():
        if "free percentage" in line:
            return int(line.split(":")[-1].strip().rstrip("%"))
    return None


def load_protocol() -> dict:
    path = Path(__file__).resolve().with_name("v1_protocol_extract.py")
    spec = importlib.util.spec_from_file_location("v1_protocol_extract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.__dict__


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if value is None or isinstance(value, (str, bool)):
        return value
    return str(value)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"results": [], "preds": {}, "status": {}, "meta": {}}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(state), indent=2))


def read_series(series: str, grain: str) -> pd.DataFrame:
    frame = pd.read_csv(
        article_dir() / "data" / f"{series}_{grain}.csv", parse_dates=["data"]
    )
    frame = frame.sort_values("data").reset_index(drop=True)
    if frame["data"].max() > pd.Timestamp("2025-12-31"):
        raise RuntimeError(f"{series} {grain} extends past 2025-12-31")
    return frame


def make_ctx(ns: dict, frame: pd.DataFrame, grain: str):
    if grain == "monthly":
        y = frame["valor_total"].astype(float).reset_index(drop=True)
        feat = (
            frame[["valor_total", "valor_sem_honorarios"]]
            .astype(float)
            .copy()
            .reset_index(drop=True)
        )
        feat = feat.rename(columns={"valor_total": "y"})
        for lag in (1, 3, 12):
            feat[f"arrec_l{lag}"] = feat["y"].shift(lag)
            feat[f"estoque_l{lag}"] = feat["valor_sem_honorarios"].shift(lag)
        xcols = [
            col for col in feat.columns if col.startswith(("arrec_l", "estoque_l"))
        ]
        split = ns["SplitConfig"](
            val_h=12,
            test_h=12,
            min_train=24,
            wf_cap=None,
            seasonal_lag=12,
            y_col="y",
            grain="monthly",
            id_prefix="",
        )
    elif grain == "daily":
        y = frame["valor_sum"].astype(float).reset_index(drop=True)
        feat = (
            frame[["valor_sum", "n_guias"]].astype(float).copy().reset_index(drop=True)
        )
        feat = feat.rename(columns={"valor_sum": "y"})
        for lag in (1, 7, 14, 28):
            feat[f"y_l{lag}"] = feat["y"].shift(lag)
            feat[f"g_l{lag}"] = feat["n_guias"].shift(lag)
        feat["dow"] = frame["data"].dt.dayofweek.astype(float)
        feat["month"] = frame["data"].dt.month.astype(float)
        xcols = [col for col in feat.columns if col.startswith(("y_l", "g_l"))] + [
            "dow",
            "month",
        ]
        split = ns["SplitConfig"](
            val_h=90,
            test_h=90,
            min_train=365,
            wf_cap=730,
            seasonal_lag=7,
            y_col="y",
            grain="daily",
            id_prefix="D_",
        )
    elif grain == "business_daily":
        y = frame["valor_sum"].astype(float).reset_index(drop=True)
        feat = (
            frame[["valor_sum", "n_guias"]].astype(float).copy().reset_index(drop=True)
        )
        feat = feat.rename(columns={"valor_sum": "y"})
        for lag in (1, 5, 10, 20):
            feat[f"y_l{lag}"] = feat["y"].shift(lag)
            feat[f"g_l{lag}"] = feat["n_guias"].shift(lag)
        feat["dow"] = frame["data"].dt.dayofweek.astype(float)
        feat["month"] = frame["data"].dt.month.astype(float)
        xcols = [col for col in feat.columns if col.startswith(("y_l", "g_l"))] + [
            "dow",
            "month",
        ]
        split = ns["SplitConfig"](
            val_h=90,
            test_h=90,
            min_train=365,
            wf_cap=730,
            seasonal_lag=5,
            y_col="y",
            grain="business_daily",
            id_prefix="BD_",
        )
    elif grain == "weekly":
        y = frame["valor_sum"].astype(float).reset_index(drop=True)
        feat = (
            frame[["valor_sum", "n_guias"]].astype(float).copy().reset_index(drop=True)
        )
        feat = feat.rename(columns={"valor_sum": "y"})
        for lag in (1, 4, 13, 52):
            feat[f"y_l{lag}"] = feat["y"].shift(lag)
            feat[f"g_l{lag}"] = feat["n_guias"].shift(lag)
        feat["weekofyear"] = frame["data"].dt.isocalendar().week.astype(float)
        feat["month"] = frame["data"].dt.month.astype(float)
        xcols = [col for col in feat.columns if col.startswith(("y_l", "g_l"))] + [
            "weekofyear",
            "month",
        ]
        seasonal = 52 if len(y) >= 52 + 13 + 13 + 52 else 4
        split = ns["SplitConfig"](
            val_h=13,
            test_h=13,
            min_train=104,
            wf_cap=260,
            seasonal_lag=seasonal,
            y_col="y",
            grain="weekly",
            id_prefix="W_",
        )
    else:
        raise ValueError(grain)
    dates = frame["data"].reset_index(drop=True)
    ctx = ns["make_context"](
        y,
        feat,
        xcols,
        split,
        seed=42,
        mono=repo_root(),
        dates=dates,
        extras={"dates": dates},
    )
    return ctx


def seasonal_name(ctx) -> str:
    return ctx.mid(f"M0_naive_sazonal_lag{ctx.split.seasonal_lag}")


def write_ranked(state: dict, dest: Path) -> None:
    frame = pd.DataFrame(state["results"])
    if frame.empty:
        return
    test = frame[frame["stage"].astype(str).eq("test")].copy()
    notes = test["notes"].astype(str) if "notes" in test.columns else ""
    keep = ~notes.str.contains(
        "exclude_from_ranking|degenerate|soft-fail", case=False, na=False
    )
    keep &= ~test["modelo"].astype(str).str.contains("_cand")
    ranked = test.loc[keep].copy()
    ranked = ranked[pd.to_numeric(ranked["SMAPE"], errors="coerce").notna()]
    ranked = ranked.sort_values("SMAPE", ascending=True)
    ref_name = state["meta"]["seasonal_model"]
    ref_rows = ranked[ranked["modelo"].astype(str).eq(ref_name)]
    if not ref_rows.empty:
        ref = float(ref_rows.iloc[0]["SMAPE"])
        ranked["SS_SMAPE"] = 1.0 - ranked["SMAPE"].astype(float) / ref
    ranked.to_csv(dest, index=False)


def run(series: str, grain: str, only: list[str] | None) -> None:
    free = memory_free_percent()
    print(f"memory_free_pct={free}", flush=True)
    ns = load_protocol()
    frame = read_series(series, grain)
    ctx = make_ctx(ns, frame, grain)
    state_path = article_dir() / "results" / series / grain / "state.json"
    state = load_state(state_path)
    state["meta"] = {
        "series": series,
        "grain": grain,
        "n": len(frame),
        "train_end": int(ctx.train_end),
        "val_end": int(ctx.val_end),
        "test_end": int(ctx.test_end),
        "seasonal_lag": int(ctx.split.seasonal_lag),
        "seasonal_model": seasonal_name(ctx),
        "date_min": str(frame["data"].min().date()),
        "date_max": str(frame["data"].max().date()),
        "dates_test": [
            str(pd.Timestamp(value).date()) for value in ctx.dates_test.tolist()
        ],
        "y_test": [float(value) for value in ctx.y_test],
        "target": "valor_total" if grain == "monthly" else "valor_sum",
        "value_field": "VALOR_TOTAL_GARE",
        "deflated": False,
    }
    print(
        f"{series} {grain} n={len(frame)} "
        f"train=[0,{ctx.train_end}) val=[{ctx.train_end},{ctx.val_end}) "
        f"test=[{ctx.val_end},{ctx.test_end}) "
        f"test_dates={state['meta']['dates_test'][0]}..{state['meta']['dates_test'][-1]}",
        flush=True,
    )
    if state["meta"]["dates_test"][-1] > "2025-12-31":
        raise RuntimeError("test window includes a date after 2025-12-31")
    wanted = only or MODEL_ORDER
    for model_id in wanted:
        if model_id in state["status"]:
            print(f"skip {model_id} (checkpoint)", flush=True)
            continue
        free = memory_free_percent()
        print(f"==> {model_id} memory_free_pct={free}", flush=True)
        if model_id in HEAVY_MODELS and free is not None and free < 22:
            row = ns["soft_fail_row"](
                ctx.mid("M9_TimesFM3"),
                f"memory_pressure free {free}% < 22; weights not loaded",
            )
            state["results"].append(row)
            state["status"][model_id] = {"ok": False, "reason": row["notes"]}
            save_state(state_path, state)
            print(f"    soft-fail {row['notes']}", flush=True)
            continue
        if model_id in TORCH_MODELS and free is not None and free < 18:
            row = ns["soft_fail_row"](
                ctx.mid(model_id),
                f"memory_pressure free {free}% < 18; torch model not started",
            )
            state["results"].append(row)
            state["status"][model_id] = {"ok": False, "reason": row["notes"]}
            save_state(state_path, state)
            print(f"    soft-fail {row['notes']}", flush=True)
            continue
        output = ns["MODEL_REGISTRY"][model_id](ctx)
        if model_id in {"E1", "E2", "E3", "E4"}:
            rows, preds, status = output
        else:
            rows, preds = output
            failed = rows and all(
                "soft-fail" in str(row.get("notes", ""))
                or "exclude_from_ranking" in str(row.get("notes", ""))
                for row in rows
                if row.get("stage") == "test"
            )
            status = {
                "ok": not failed,
                "reason": None if not failed else rows[-1].get("notes"),
            }
        state["results"].extend(rows)
        for name, values in preds.items():
            state["preds"][name] = [
                None if not np.isfinite(value) else float(value)
                for value in np.asarray(values, float)
            ]
        state["status"][model_id] = status
        save_state(state_path, state)
        test_rows = [
            row for row in rows if row.get("stage") == "test" and "SMAPE" in row
        ]
        if test_rows:
            best = min(
                test_rows,
                key=lambda row: row["SMAPE"] if row["SMAPE"] == row["SMAPE"] else 1e18,
            )
            print(f"    {best['modelo']} test sMAPE={best['SMAPE']}", flush=True)
        else:
            print(f"    status={status}", flush=True)
    write_ranked(state, state_path.parent / "metrics_test_ranked.csv")
    print("wrote", state_path, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--series", required=True, choices=["total", "declarado", "autuado"]
    )
    parser.add_argument(
        "--grain",
        required=True,
        choices=["monthly", "daily", "business_daily", "weekly"],
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated registry ids. Empty runs the full ladder.",
    )
    args = parser.parse_args()
    only = [item for item in args.models.split(",") if item] or None
    run(args.series, args.grain, only)


if __name__ == "__main__":
    main()
