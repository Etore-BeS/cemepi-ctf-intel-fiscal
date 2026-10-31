"""Skill, Diebold-Mariano-HLN, and model confidence set from saved forecasts.

Reads ``results/<series>/<grain>/state.json``. Does not refit models.
The loss is the sMAPE summand. The DM horizon is h=1.
The model confidence set uses alpha=0.10 and a circular block bootstrap.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from forecast_compare import diebold_mariano_hln, model_confidence_set, smape_loss

ALPHA = 0.10
BOOTSTRAP = 999


def article_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "notebooks"
        / "articles"
        / "arrecadacao_forecast_v1_segmentado"
    )


def _finite_forecast(values, n_obs: int) -> np.ndarray | None:
    if values is None or len(values) != n_obs:
        return None
    array = np.asarray([np.nan if value is None else value for value in values], float)
    if not np.isfinite(array).all():
        return None
    return array


def official_rank(state_path: Path) -> pd.DataFrame:
    path = state_path.parent / "metrics_test_ranked.csv"
    frame = pd.read_csv(path)
    return frame.sort_values("SMAPE", ascending=True)


def score_state(state: dict, ranked: pd.DataFrame) -> dict:
    meta = state["meta"]
    actual = np.asarray(meta["y_test"], float)
    losses = {}
    for name, values in state["preds"].items():
        forecast = _finite_forecast(values, len(actual))
        if forecast is None:
            continue
        loss = smape_loss(actual, forecast)
        if np.isfinite(loss).all():
            losses[name] = loss
    seasonal = meta["seasonal_model"]
    if seasonal not in losses:
        raise RuntimeError(f"missing seasonal naive {seasonal}")
    best_name = str(ranked.iloc[0]["modelo"])
    best_smape = float(ranked.iloc[0]["SMAPE"])
    ref = float(ranked.loc[ranked["modelo"].eq(seasonal), "SMAPE"].iloc[0])
    skill = float(ranked.iloc[0]["SS_SMAPE"])
    if best_name not in losses:
        raise RuntimeError(f"best model {best_name} has no finite test forecast")
    dm = diebold_mariano_hln(losses[best_name], losses[seasonal], h=1)
    mcs = model_confidence_set(losses, alpha=ALPHA, bootstrap=BOOTSTRAP, seed=42)
    return {
        "series": meta["series"],
        "grain": meta["grain"],
        "n_test": len(actual),
        "test_start": meta["dates_test"][0],
        "test_end": meta["dates_test"][-1],
        "best": best_name,
        "smape": best_smape,
        "seasonal": seasonal,
        "seasonal_smape": ref,
        "skill": skill,
        "dm": dm.dm,
        "dm_hln": dm.dm_hln,
        "dm_p": dm.p_value,
        "dm_better": dm.better,
        "mcs_block": mcs.block,
        "mcs_included": mcs.included,
        "best_in_mcs": best_name in mcs.included,
        "mcs_p": mcs.p_values,
        "rank": [
            {"model": str(row.modelo), "smape": float(row.SMAPE)}
            for row in ranked.itertuples(index=False)
        ],
    }


def main() -> None:
    root = article_dir() / "results"
    rows = []
    details = []
    for state_path in sorted(root.glob("*/*/state.json")):
        ranked_path = state_path.parent / "metrics_test_ranked.csv"
        if not ranked_path.exists():
            continue
        state = json.loads(state_path.read_text())
        if not state.get("preds"):
            continue
        scored = score_state(state, official_rank(state_path))
        details.append(scored)
        rows.append(
            {
                key: scored[key]
                for key in (
                    "series",
                    "grain",
                    "n_test",
                    "test_start",
                    "test_end",
                    "best",
                    "smape",
                    "seasonal_smape",
                    "skill",
                    "dm_hln",
                    "dm_p",
                    "dm_better",
                    "best_in_mcs",
                )
            }
        )
        rows[-1]["mcs_included"] = "; ".join(scored["mcs_included"])
        print(
            f"{scored['series']} {scored['grain']}: {scored['best']} "
            f"sMAPE={scored['smape']:.3f} skill={scored['skill']:.3f} "
            f"HLN p={scored['dm_p']:.4f} MCS={scored['mcs_included']}",
            flush=True,
        )
    out = article_dir() / "results" / "comparison.json"
    out.write_text(json.dumps(details, indent=2))
    pd.DataFrame(rows).to_csv(article_dir() / "results" / "comparison.csv", index=False)
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
