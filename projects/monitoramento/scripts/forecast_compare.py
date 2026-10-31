"""Diebold-Mariano with the HLN correction, and the model confidence set.

Loss is the summand of sMAPE, so the tests use the same score as the rank.
The HLN factor follows Harvey, Leybourne and Newbold (1997) for horizon h=1:
the statistic is compared with a Student-t distribution on n-1 degrees of freedom.
The model confidence set follows Hansen, Lunde and Nason (2011): a range
statistic, circular block bootstrap, and elimination until the set is not rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

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
