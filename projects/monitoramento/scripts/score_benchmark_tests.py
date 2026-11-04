"""Skill, Diebold-Mariano-HLN, Wilcoxon signed-rank, and the model confidence set.

Reads ``results/<series>/<grain>/state.json``. Does not refit models.
The loss is the sMAPE summand. The DM horizon is h=1.
The Wilcoxon procedure is the lab page
``lab/forecast/2026-11-04_wilcoxon_signed_rank.qmd``.
The leader-versus-naive signed-rank p-value is not adjusted.
Every pair inside the cell is adjusted with Benjamini-Hochberg.
The model confidence set uses alpha=0.10 and a circular block bootstrap.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from forecast_compare import (
    PAIRWISE_COLUMNS,
    diebold_mariano_hln,
    model_confidence_set,
    pairwise_wilcoxon,
    smape_loss,
    wilcoxon_paired,
)

ALPHA = 0.10
BOOTSTRAP = 999
_WSR_BETTER = {"model": "model", "reference": "seasonal_naive", "tie": "tie"}


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
    wsr = wilcoxon_paired(losses[best_name], losses[seasonal])
    pairs = [pair.as_dict() for pair in pairwise_wilcoxon(losses)]
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
        "wsr_stat": wsr.statistic,
        "wsr_p": wsr.p_value,
        "wsr_better": _WSR_BETTER[wsr.better],
        "pairwise": pairs,
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
    pair_rows = []
    for state_path in sorted(root.glob("*/*/state.json")):
        ranked_path = state_path.parent / "metrics_test_ranked.csv"
        if not ranked_path.exists():
            continue
        state = json.loads(state_path.read_text())
        if not state.get("preds"):
            continue
        scored = score_state(state, official_rank(state_path))
        details.append(scored)
        for pair in scored["pairwise"]:
            pair_rows.append(
                {"series": scored["series"], "grain": scored["grain"], **pair}
            )
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
                    "wsr_stat",
                    "wsr_p",
                    "wsr_better",
                    "best_in_mcs",
                )
            }
        )
        rows[-1]["mcs_included"] = "; ".join(scored["mcs_included"])
        print(
            f"{scored['series']} {scored['grain']}: {scored['best']} "
            f"sMAPE={scored['smape']:.3f} skill={scored['skill']:.3f} "
            f"HLN p={scored['dm_p']:.4f} WSR p={scored['wsr_p']:.4f} "
            f"MCS={scored['mcs_included']}",
            flush=True,
        )
    out = article_dir() / "results" / "comparison.json"
    out.write_text(json.dumps(details, indent=2))
    pd.DataFrame(rows).to_csv(article_dir() / "results" / "comparison.csv", index=False)
    pairs_path = article_dir() / "results" / "pairwise_wilcoxon.csv"
    pd.DataFrame(pair_rows, columns=list(PAIRWISE_COLUMNS)).to_csv(
        pairs_path, index=False
    )
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
