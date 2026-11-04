"""Diebold-Mariano with the HLN correction, the Wilcoxon signed-rank test, and the MCS.

Loss is the summand of sMAPE, so the tests use the same score as the rank.
The HLN factor follows Harvey, Leybourne and Newbold (1997) for horizon h=1:
the statistic is compared with a Student-t distribution on n-1 degrees of freedom.
The Wilcoxon procedure is specified in
lab/forecast/2026-11-04_wilcoxon_signed_rank.qmd. This module follows that page.
The model confidence set follows Hansen, Lunde and Nason (2011): a range
statistic, circular block bootstrap, and elimination until the set is not rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats
from statsmodels.stats.multitest import multipletests

EPS = 1e-12


def smape_loss(actual: np.ndarray, forecast: np.ndarray) -> np.ndarray:
    """Per-period sMAPE summand, in the same units as the percentage after ×100."""
    actual = np.asarray(actual, float)
    forecast = np.asarray(forecast, float)
    denom = np.abs(actual) + np.abs(forecast)
    loss = np.full(actual.shape, np.nan, dtype=float)
    finite = np.isfinite(actual) & np.isfinite(forecast)
    both_zero = finite & (denom <= EPS)
    loss[both_zero] = 0.0
    ok = finite & ~both_zero
    loss[ok] = 2.0 * np.abs(actual[ok] - forecast[ok]) / denom[ok] * 100.0
    return loss


@dataclass(frozen=True)
class DMResult:
    n: int
    h: int
    mean_differential: float
    dm: float
    dm_hln: float
    p_value: float
    better: str


def diebold_mariano_hln(
    loss_model: np.ndarray,
    loss_reference: np.ndarray,
    *,
    h: int = 1,
) -> DMResult:
    """Two-sided DM test of equal accuracy. Negative differential favours the model.

    Horizon ``h`` enters the Newey-West truncation (h-1) and the HLN factor.
    ``h=1`` is the one-step correction. The factor is undefined when it is not
    positive; that case raises ``ValueError``.
    """
    if h < 1:
        raise ValueError("h must be at least 1")
    diff = np.asarray(loss_model, float) - np.asarray(loss_reference, float)
    diff = diff[np.isfinite(diff)]
    n = int(diff.size)
    if n < 3:
        raise ValueError(f"need at least 3 paired losses, got {n}")
    factor_inner = (n + 1 - 2 * h + h * (h - 1) / n) / n
    if factor_inner <= 0:
        raise ValueError(f"HLN factor is not positive for n={n}, h={h}")
    mean = float(diff.mean())
    demeaned = diff - mean
    gamma0 = float(np.dot(demeaned, demeaned) / n)
    var = gamma0
    for lag in range(1, h):
        weight = 1.0 - lag / h
        gamma = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        var += 2.0 * weight * gamma
    var = max(var, 0.0)
    se = float(np.sqrt(var / n))
    if se < EPS or not np.isfinite(se):
        return DMResult(n, h, mean, 0.0, 0.0, 1.0, "tie")
    dm = mean / se
    dm_hln = dm * float(np.sqrt(factor_inner))
    p_value = float(2.0 * stats.t.sf(abs(dm_hln), df=n - 1))
    if mean < 0:
        better = "model"
    elif mean > 0:
        better = "seasonal_naive"
    else:
        better = "tie"
    return DMResult(n, h, mean, float(dm), float(dm_hln), p_value, better)


@dataclass(frozen=True)
class WilcoxonResult:
    n: int
    n_nonzero: int
    statistic: float
    p_value: float
    median_differential: float
    better: str


def wilcoxon_paired(
    loss_model: np.ndarray, loss_reference: np.ndarray
) -> WilcoxonResult:
    """Two-sided Wilcoxon signed-rank test on one pair of loss series.

    ``diff = loss_model - loss_reference`` uses the same finite pairs as the
    Diebold-Mariano test. A negative median favours ``loss_model``.
    All-zero differentials are a tie and do not raise.
    The reported statistic is SciPy's positive rank sum.
    """
    diff = np.asarray(loss_model, float) - np.asarray(loss_reference, float)
    diff = diff[np.isfinite(diff)]
    n = int(diff.size)
    if n < 1:
        raise ValueError(f"need at least 1 paired loss, got {n}")
    median = float(np.median(diff))
    if median < 0:
        better = "model"
    elif median > 0:
        better = "reference"
    else:
        better = "tie"
    n_nonzero = int(np.count_nonzero(diff))
    if n_nonzero == 0:
        return WilcoxonResult(n, 0, 0.0, 1.0, median, "tie")
    try:
        result = stats.wilcoxon(
            diff,
            zero_method="wilcox",
            alternative="two-sided",
            method="auto",
        )
    except ValueError:
        return WilcoxonResult(n, n_nonzero, 0.0, 1.0, median, "tie")
    statistic = float(result.statistic)
    p_value = float(result.pvalue)
    if not np.isfinite(statistic) or not np.isfinite(p_value):
        return WilcoxonResult(n, n_nonzero, 0.0, 1.0, median, "tie")
    return WilcoxonResult(n, n_nonzero, statistic, p_value, median, better)


def benjamini_hochberg(p_values: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values.

    Same call as ``lab/paper_eda/_prelude.py``: ``multipletests(..., method="fdr_bh")``.
    """
    p_values = np.asarray(p_values, dtype=float)
    if p_values.size == 0:
        return np.asarray(p_values, dtype=float)
    if p_values.ndim != 1:
        raise ValueError("p-values must be one-dimensional")
    if not np.isfinite(p_values).all():
        raise ValueError("p-values must be finite")
    adjusted = multipletests(p_values, method="fdr_bh")[1]
    return np.asarray(adjusted, dtype=float)


@dataclass(frozen=True)
class PairwiseWilcoxon:
    model_a: str
    model_b: str
    n: int
    n_nonzero: int
    median_differential: float
    statistic: float
    p_raw: float
    p_bh: float
    better: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model_a": self.model_a,
            "model_b": self.model_b,
            "n": self.n,
            "n_nonzero": self.n_nonzero,
            "median_diff": self.median_differential,
            "stat": self.statistic,
            "p_raw": self.p_raw,
            "p_bh": self.p_bh,
            "better": self.better,
        }


PAIRWISE_COLUMNS = (
    "series",
    "grain",
    "model_a",
    "model_b",
    "n",
    "n_nonzero",
    "median_diff",
    "stat",
    "p_raw",
    "p_bh",
    "better",
)


def pairwise_wilcoxon(losses: dict[str, np.ndarray]) -> list[PairwiseWilcoxon]:
    """Every unordered pair, with Benjamini-Hochberg adjustment inside this dict.

    ``model_a`` is the earlier key. The median is loss(model_a) minus loss(model_b).
    The family is this dictionary only. It is not pooled across cells.
    """
    names = list(losses)
    computed: list[tuple[str, str, WilcoxonResult, str]] = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            result = wilcoxon_paired(losses[left], losses[right])
            if result.better == "model":
                winner = left
            elif result.better == "reference":
                winner = right
            else:
                winner = "tie"
            computed.append((left, right, result, winner))
    if not computed:
        return []
    raw = np.array([item[2].p_value for item in computed], dtype=float)
    adjusted = benjamini_hochberg(raw)
    rows: list[PairwiseWilcoxon] = []
    for (left, right, result, winner), p_bh in zip(computed, adjusted, strict=True):
        rows.append(
            PairwiseWilcoxon(
                left,
                right,
                result.n,
                result.n_nonzero,
                result.median_differential,
                result.statistic,
                result.p_value,
                float(p_bh),
                winner,
            )
        )
    return rows


def summarize_pairwise(
    best: str,
    pairs: list[dict[str, object]],
    *,
    alpha: float = 0.05,
) -> dict[str, int | bool]:
    """Counts for one cell. Significant means adjusted p below ``alpha``.

    The leader beats all rivals only when every pair that includes ``best``
    is significant and the median differential names ``best``.
    """
    rivals: list[dict[str, object]] = []
    n_significant = 0
    for pair in pairs:
        p_bh = float(pair["p_bh"])
        if p_bh < alpha:
            n_significant += 1
        if pair["model_a"] == best or pair["model_b"] == best:
            rivals.append(pair)
    beats_all = bool(rivals) and all(
        float(pair["p_bh"]) < alpha and pair["better"] == best for pair in rivals
    )
    return {
        "n_pairs": len(pairs),
        "n_significant": n_significant,
        "leader_beats_all": beats_all,
    }


def _block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=n_blocks)
    rows = np.empty(n_blocks * block, dtype=int)
    for i, start in enumerate(starts):
        rows[i * block : (i + 1) * block] = (start + np.arange(block)) % n
    return rows[:n]


def _tmax(losses: np.ndarray) -> tuple[float, int]:
    """Max t-statistic of each model against the average of the others."""
    n_obs, n_models = losses.shape
    if n_models < 2:
        return 0.0, 0
    total = losses.sum(axis=1, keepdims=True)
    others = (total - losses) / (n_models - 1)
    differential = losses - others
    mean = differential.mean(axis=0)
    var = differential.var(axis=0, ddof=1) / n_obs
    var = np.maximum(var, EPS)
    t_stat = mean / np.sqrt(var)
    worst = int(np.argmax(t_stat))
    return float(t_stat[worst]), worst


@dataclass(frozen=True)
class MCSResult:
    alpha: float
    block: int
    bootstrap: int
    p_values: dict[str, float]
    included: list[str]


def model_confidence_set(
    losses: dict[str, np.ndarray],
    *,
    alpha: float = 0.10,
    bootstrap: int = 999,
    block: int | None = None,
    seed: int = 42,
) -> MCSResult:
    """MCS at level ``alpha``. A model stays in when its MCS p-value is at least ``alpha``."""
    names = list(losses)
    if len(names) < 2:
        raise ValueError("MCS needs at least two models")
    matrix = np.column_stack([np.asarray(losses[name], float) for name in names])
    if not np.isfinite(matrix).all():
        raise ValueError("MCS losses must be finite on every test date")
    n_obs = matrix.shape[0]
    if block is None:
        block = max(1, round(n_obs ** (1.0 / 3.0)))
    block = max(1, min(block, n_obs))
    rng = np.random.default_rng(seed)
    active = list(range(len(names)))
    p_values = {name: 1.0 for name in names}
    running_max = 0.0
    while len(active) > 1:
        current = matrix[:, active]
        stat, worst_local = _tmax(current)
        centered = current - current.mean(axis=0, keepdims=True)
        boot = np.empty(bootstrap, dtype=float)
        for draw in range(bootstrap):
            sample = centered[_block_indices(n_obs, block, rng)]
            boot[draw] = _tmax(sample)[0]
        p_value = float((np.sum(boot >= stat - 1e-12) + 1) / (bootstrap + 1))
        running_max = max(running_max, p_value)
        if p_value > alpha:
            for idx in active:
                p_values[names[idx]] = max(running_max, p_value)
            break
        eliminated = active.pop(worst_local)
        p_values[names[eliminated]] = running_max
    else:
        if active:
            p_values[names[active[0]]] = max(p_values[names[active[0]]], running_max)
    included = [name for name in names if p_values[name] >= alpha]
    return MCSResult(alpha, block, bootstrap, p_values, included)
