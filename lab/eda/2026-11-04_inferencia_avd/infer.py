"""Payment logits and quantile regressions selected by section 4.5.

The functions take a table that is already in memory. They do not open the
extract. The December 2025 loader is the section 4.5 loader and runs on the
machine that has the disk.

Section 4.5 fixes the specification. This module does not rerun that screen.
Declared: restricted cubic splines of log stock and debt age, plus a municipal
random intercept. Assessed: a spline of debt age, a linear log stock term, and
no random intercept. Both segments: quantile regression of log stock at
τ = 0.25, 0.50, 0.75 and 0.90. The linear recovery equation is a comparison.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import patsy
import plotly.graph_objects as go
import statsmodels.formula.api as smf
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
from statsmodels.tools.sm_exceptions import PerfectSeparationError

HERE = Path(__file__).resolve().parent
LIN = HERE.parent / "2026-11-04_diagnostico_linearidade" / "diagnose.py"


def _load_diagnose():
    spec = importlib.util.spec_from_file_location("lin_diagnose_avd", LIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diag = _load_diagnose()

SAMPLE_CAP = diag.SAMPLE_CAP
SPLINE_CAP = diag.SPLINE_CAP
MIXED_CAP = diag.MIXED_CAP
CLUSTER_PER_GROUP = diag.CLUSTER_PER_GROUP
QUANTILE_CAP = diag.QUANTILE_CAP
QUANTILES = diag.QUANTILES
SPLINE_DF = 4
POOLED = "pooled"
CACHE_VERSION = "avd47-v2"
POSTERIOR_INTERVAL = "approximate posterior"
Z = diag.Z_95

# Moment ICCs printed in the section 4.5 decision (municipality, full sample).
# They select the random intercept. They are not re-estimated here.
SPEC = {
    "declared": {
        "splines": ("log_stock", "debt_age"),
        "linear": (),
        "random_intercept": "municipality",
        "screen_icc": 0.061,
        "screen_grouping": "municipality",
    },
    "assessed": {
        "splines": ("debt_age",),
        "linear": ("log_stock",),
        "random_intercept": None,
        "screen_icc": 0.034,
        "screen_grouping": "municipality",
    },
}


def reject_forbidden(frame: pd.DataFrame) -> None:
    """Refuse person columns and the CNPJ. The firm join stays inside DuckDB."""
    diag.reject_person_columns(frame)
    found = [name for name in frame.columns if str(name).upper() == "CNPJ"]
    if found:
        raise RuntimeError(
            "The table contains CNPJ. The lab does not read that column."
        )


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    reject_forbidden(frame)
    out = diag.prepare(frame)
    reject_forbidden(out)
    return out


def payment_formula(frame: pd.DataFrame, segment: str) -> str:
    """Logit formula for one segment. Splines follow SPEC. Categories follow section 4.5."""
    spec = SPEC[segment]
    terms: list[str] = []
    for name in ("log_stock", "debt_age"):
        if frame[name].nunique(dropna=True) <= 1:
            continue
        if name in spec["splines"]:
            # Centering removes the column that sums to 1, which is collinear
            # with the intercept. df stays 4 after the constraint.
            terms.append(f"cr({name}, df={SPLINE_DF}, constraints='center')")
        else:
            terms.append(name)
    base = diag.rhs(frame, "paid_flag", by_segment=True)
    for term in base.split(" + "):
        if term in {"1", "log_stock", "debt_age"}:
            continue
        terms.append(term)
    if not terms:
        terms = ["1"]
    return "paid_flag ~ " + " + ".join(terms)


def _segment_rows(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    return frame.loc[frame["segment"].astype(str) == segment].copy()


def logit_rows(frame: pd.DataFrame, segment: str, seed: int) -> pd.DataFrame:
    """Fixed-effects logit sample. At most SPLINE_CAP rows in each segment."""
    part = _segment_rows(frame, segment)
    keys = ["paid_flag"] if "paid_flag" in part.columns else ["porte"]
    return diag.stratified_sample(part, SPLINE_CAP, keys, seed)


def mixed_rows(frame: pd.DataFrame, segment: str, seed: int) -> pd.DataFrame:
    """Cluster sample for the municipal random intercept. Empty when the segment has none."""
    part = _segment_rows(frame, segment)
    group = SPEC[segment]["random_intercept"]
    if not group:
        return part.iloc[:0].copy()
    return diag.cluster_sample(
        part,
        group,
        cap=MIXED_CAP,
        per_group=CLUSTER_PER_GROUP,
        seed=seed,
    )


def quantile_rows(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    """At most QUANTILE_CAP rows inside each segment. A smaller segment is kept whole."""
    keys = [key for key in ("porte", "paid_flag") if key in frame.columns]
    if "segment" not in frame.columns:
        if not keys:
            raise RuntimeError("The quantile sample needs porte or paid_flag.")
        return diag.stratified_sample(frame, QUANTILE_CAP, keys, seed)
    parts = []
    for _, part in frame.groupby("segment", dropna=False):
        use = keys if keys else ["segment"]
        parts.append(diag.stratified_sample(part, QUANTILE_CAP, use, seed))
    if not parts:
        return frame.iloc[:0].copy()
    return pd.concat(parts, ignore_index=True)


def require_full_rank(exog: np.ndarray) -> int:
    """Fail when the design is column-rank deficient."""
    matrix = np.asarray(exog, dtype=float)
    if matrix.ndim != 2:
        raise RuntimeError("The design is not a matrix.")
    n_rows, n_columns = matrix.shape
    if n_rows < n_columns:
        raise RuntimeError(f"The design has {n_rows} rows and {n_columns} columns.")
    rank = int(np.linalg.matrix_rank(matrix))
    if rank < n_columns:
        raise RuntimeError(
            f"The design is rank deficient: rank {rank} of {n_columns} columns."
        )
    return rank


def require_converged(result, segment: str) -> None:
    """Fail when the logit maximum did not converge."""
    retvals = getattr(result, "mle_retvals", None) or {}
    if not retvals.get("converged", False):
        raise RuntimeError(f"The {segment} logit did not converge.")


def prepare_logit_sample(
    frame: pd.DataFrame, segment: str, seed: int
) -> tuple[pd.DataFrame, dict]:
    """Cap, then drop a small pool and a separated level. The dict counts each drop."""
    n_estimation = len(_segment_rows(frame, segment))
    capped = logit_rows(frame, segment, seed)
    n_cap = len(capped)
    pooled = pool_sparse(capped)
    kept = drop_separated_levels(pooled)
    account = {
        "segment": segment,
        "n_estimation": n_estimation,
        "n_cap_sample": n_cap,
        "n_dropped_small_pool": n_cap - len(pooled),
        "n_dropped_separation": len(pooled) - len(kept),
        "n_fit": len(kept),
    }
    return kept, account


def _level_counts(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    return (
        frame.groupby(column, dropna=False)
        .agg(
            n_cda=("paid_flag", "size"),
            n_paid=("paid_flag", "sum"),
            n_firms=("root", "nunique"),
        )
        .reset_index()
    )


def pool_sparse(frame: pd.DataFrame) -> pd.DataFrame:
    """Pool a section or size level that the section 4.5 cell rule would blank.

    A level with fewer than 10 certificates, fewer than 5 firms, or 1 to 4
    payers is merged into ``pooled``. The pool is dropped when it is itself
    a small cell, so it does not become a coefficient.
    """
    out = frame.copy()
    if "paid_flag" not in out.columns:
        out["paid_flag"] = (out["paid"].to_numpy(dtype=float) > 0).astype(int)
    for column in ("section", "porte"):
        if column not in out.columns or "root" not in out.columns:
            continue
        counts = _level_counts(out, column)
        sparse = set(counts.loc[diag._small_cell_mask(counts).to_numpy(), column])
        if sparse:
            out.loc[out[column].isin(sparse), column] = POOLED
        pooled = out.loc[out[column] == POOLED]
        if len(pooled):
            info = pd.DataFrame(
                [
                    {
                        "n_cda": len(pooled),
                        "n_firms": int(pooled["root"].nunique()),
                        "n_paid": float(pooled["paid_flag"].sum()),
                    }
                ]
            )
            if bool(diag._small_cell_mask(info).iloc[0]):
                out.loc[out[column] == POOLED, column] = np.nan
        out = out.dropna(subset=[column]).reset_index(drop=True)
    return out


def drop_separated_levels(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop a factor level with no payers or no non-payers.

    A level with no events separates a logit. The drop is a fitting constraint.
    """
    out = frame
    for column in ("section", "porte"):
        if column not in out.columns:
            continue
        counts = out.groupby(column, dropna=False)["paid_flag"].agg(["sum", "size"])
        dead = counts.index[(counts["sum"] <= 0) | (counts["sum"] >= counts["size"])]
        if len(dead):
            out = out.loc[~out[column].isin(list(dead))].copy()
    return out.reset_index(drop=True)


class _Fit:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _aligned_design(rhs: str, data: pd.DataFrame, names: list[str]):
    """Return the design in ``names`` order and the patsy info from before the slice.

    Slicing the frame drops ``design_info``. The curve rebuild needs that object.
    """
    built = patsy.dmatrix(rhs, data, return_type="dataframe")
    info = built.design_info
    missing = [name for name in names if name not in list(built.columns)]
    if missing:
        raise RuntimeError(f"The design is missing {', '.join(missing)}.")
    return built.loc[:, names], info


def _fit_record(
    formula: str,
    data: pd.DataFrame,
    names: list[str],
    beta: np.ndarray,
    se: np.ndarray,
    exog: np.ndarray,
    cov: np.ndarray | None = None,
) -> _Fit:
    rhs = formula.split("~", 1)[1].strip()
    design, info = _aligned_design(rhs, data, names)
    if not np.allclose(design.to_numpy(dtype=float), np.asarray(exog, dtype=float)):
        raise RuntimeError("The spline design does not match the fitted model.")
    medians = {
        name: float(data[name].median())
        for name in ("log_stock", "debt_age")
        if name in data.columns
    }
    return _Fit(
        formula=formula,
        names=names,
        beta=np.asarray(beta, dtype=float),
        se=np.asarray(se, dtype=float),
        cov=None if cov is None else np.asarray(cov, dtype=float),
        design_info=info,
        prototype=data.iloc[[0]].copy(),
        medians=medians,
        values={
            name: data[name].to_numpy(dtype=float)
            for name in ("log_stock", "debt_age")
            if name in data.columns
        },
        n=len(data),
    )


def _exog_at(fit: _Fit, variable: str, value: float) -> np.ndarray:
    probe = fit.prototype.copy()
    probe[variable] = value
    built = patsy.build_design_matrices(
        [fit.design_info], probe, return_type="dataframe"
    )[0]
    return built.loc[:, fit.names].to_numpy(dtype=float).ravel()


def contrast_at(fit: _Fit, variable: str, value: float) -> float:
    """Partial log-odds at ``value`` minus the partial log-odds at the median."""
    contrast = _exog_at(fit, variable, value) - _exog_at(
        fit, variable, fit.medians[variable]
    )
    return float(contrast @ fit.beta)


def _curve_se(fit: _Fit, contrast: np.ndarray) -> float:
    if fit.cov is not None:
        quad = float(contrast @ fit.cov @ contrast)
        return float(np.sqrt(max(quad, 0.0)))
    return float(np.sqrt(np.sum((contrast**2) * (fit.se**2))))


def partial_curve(fit: _Fit, variable: str) -> pd.DataFrame:
    """Forty-point partial log-odds curve. The band uses the fit covariance when it exists."""
    values = fit.values[variable]
    lo, hi = np.quantile(values, [0.05, 0.95])
    if not np.isfinite(lo) or hi <= lo:
        hi = lo + 1.0
    grid = np.linspace(float(lo), float(hi), diag.N_BINS)
    base = _exog_at(fit, variable, fit.medians[variable])
    rows = []
    for x in grid:
        contrast = _exog_at(fit, variable, float(x)) - base
        estimate = float(contrast @ fit.beta)
        se = _curve_se(fit, contrast)
        rows.append(
            {
                "variable": variable,
                "x": float(x),
                "estimate": estimate,
                "se": se,
                "ci_low": estimate - Z * se,
                "ci_high": estimate + Z * se,
            }
        )
    return pd.DataFrame(rows)


def _coef_frame(
    names: list[str],
    coef: np.ndarray,
    se: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    segment: str,
    model: str,
    se_type: str,
    n: int,
    formula: str,
    interval: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "term": names,
            "coef": coef,
            "se": se,
            "ci_low": low,
            "ci_high": high,
            "segment": segment,
            "outcome": "paid_flag",
            "model": model,
            "se_type": se_type,
            "interval": interval,
            "n": n,
            "formula": formula,
        }
    )


def _robust_table(result, segment: str, se_type: str, formula: str, interval: str):
    names = [str(name) for name in result.model.exog_names]
    params = np.asarray(result.params, dtype=float).ravel()
    se = np.asarray(result.bse, dtype=float).ravel()
    ci = np.asarray(result.conf_int(), dtype=float)
    return _coef_frame(
        names,
        params,
        se,
        ci[:, 0],
        ci[:, 1],
        segment,
        "logit",
        se_type,
        int(result.nobs),
        formula,
        interval,
    )


def fit_logit(data: pd.DataFrame, segment: str) -> dict:
    """Maximum-likelihood logit. Fails if the design is short of full rank or the fit does not converge."""
    formula = payment_formula(data, segment)
    if "root" not in data.columns or data["root"].nunique() < 2:
        raise RuntimeError(f"The {segment} logit needs at least two CNPJ roots.")
    try:
        model = smf.logit(formula, data=data)
        result = model.fit(disp=False, maxiter=200)
    except (ValueError, np.linalg.LinAlgError, PerfectSeparationError) as exc:
        raise RuntimeError(f"The {segment} logit did not fit. {exc}") from exc
    require_converged(result, segment)
    exog = np.asarray(result.model.exog, dtype=float)
    rank = require_full_rank(exog)
    start = np.asarray(result.params, dtype=float)
    try:
        hc3 = model.fit(disp=False, maxiter=200, start_params=start, cov_type="HC3")
        clustered = model.fit(
            disp=False,
            maxiter=200,
            start_params=start,
            cov_type="cluster",
            cov_kwds={"groups": data["root"].to_numpy()},
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        raise RuntimeError(
            f"The {segment} logit covariance did not fit. {exc}"
        ) from exc
    names = [str(name) for name in result.model.exog_names]
    curve = _fit_record(
        formula,
        data,
        names,
        np.asarray(hc3.params, dtype=float).ravel(),
        np.asarray(hc3.bse, dtype=float).ravel(),
        exog,
        cov=np.asarray(hc3.cov_params(), dtype=float),
    )
    status = {
        "segment": segment,
        "model": "logit",
        "n": int(result.nobs),
        "rank": rank,
        "n_columns": int(exog.shape[1]),
        "converged": True,
        "mcfadden_r2": float(result.prsquared),
    }
    tables = pd.concat(
        [
            _robust_table(hc3, segment, "HC3", formula, "HC3 95 percent"),
            _robust_table(clustered, segment, "cluster", formula, "cluster 95 percent"),
        ],
        ignore_index=True,
    )
    return {"coefficients": tables, "curve": curve, "status": status}


def fit_mixed(data: pd.DataFrame, segment: str) -> tuple[_Fit, dict]:
    formula = payment_formula(data, segment)
    group = SPEC[segment]["random_intercept"]
    rhs = formula.split("~", 1)[1].strip()
    design = patsy.dmatrix(rhs, data, return_type="dataframe")
    require_full_rank(design.to_numpy(dtype=float))
    vc = {group: f"0 + C({group})"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            result = BinomialBayesMixedGLM.from_formula(formula, vc, data).fit_vb(
                verbose=False
            )
        except (
            ValueError,
            np.linalg.LinAlgError,
            FloatingPointError,
            RuntimeError,
        ) as exc:
            raise RuntimeError(f"The {segment} mixed logit did not fit. {exc}") from exc
    names = [str(name) for name in result.model.exog_names]
    fit = _fit_record(
        formula,
        data,
        names,
        np.asarray(result.fe_mean, dtype=float),
        np.asarray(result.fe_sd, dtype=float),
        np.asarray(result.model.exog, dtype=float),
    )
    log_sd = float(result.vcp_mean[0])
    log_se = float(result.vcp_sd[0])
    tau = float(np.exp(log_sd))
    tau2 = tau**2
    variance = {
        "segment": segment,
        "grouping": group,
        "tau": tau,
        "tau_ci_low": float(np.exp(log_sd - Z * log_se)),
        "tau_ci_high": float(np.exp(log_sd + Z * log_se)),
        "tau2": tau2,
        "icc": float(tau2 / (tau2 + np.pi**2 / 3.0)),
        "n": len(data),
        "n_groups": int(data[group].nunique()),
        "ci_low": float(np.exp(log_sd - Z * log_se)),
        "ci_high": float(np.exp(log_sd + Z * log_se)),
        "interval": POSTERIOR_INTERVAL,
    }
    return fit, variance


def _curves_for(fit: _Fit, segment: str) -> pd.DataFrame:
    parts = []
    for variable in ("log_stock", "debt_age"):
        if variable not in fit.values:
            continue
        curve = partial_curve(fit, variable)
        curve["segment"] = segment
        parts.append(curve)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


_VARIANCE_COLUMNS = (
    "segment",
    "grouping",
    "tau",
    "tau_ci_low",
    "tau_ci_high",
    "tau2",
    "icc",
    "n",
    "n_groups",
    "ci_low",
    "ci_high",
    "interval",
)


def _empty_variance() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_VARIANCE_COLUMNS))


def _mixed_coefficients(fit: _Fit, segment: str) -> pd.DataFrame:
    low = fit.beta - Z * fit.se
    high = fit.beta + Z * fit.se
    return _coef_frame(
        fit.names,
        fit.beta,
        fit.se,
        low,
        high,
        segment,
        "mixed_logit",
        "variational",
        fit.n,
        fit.formula,
        POSTERIOR_INTERVAL,
    )


def block_payment(frame: pd.DataFrame, segment: str, seed: int = 20261104) -> dict:
    """Fixed-effects logit on the spline cap. Declared also fits the municipal complement."""
    started = time.perf_counter()
    rows, account = prepare_logit_sample(frame, segment, seed)
    account_frame = pd.DataFrame([account])
    key = _signature(
        rows,
        ["paid_flag", "log_stock", "debt_age", "section", "porte"],
        f"pay|{segment}|{seed}",
    )
    cached = _read_cached(key)
    if cached is not None:
        cached["cache"] = "hit"
        cached["seconds"] = time.perf_counter() - started
        cached["seconds_logit"] = cached["seconds"]
        cached["seconds_mixed"] = 0.0
        cached["n"] = len(rows)
        cached["sample_account"] = account_frame
        print(
            f"payment {segment}: cache hit, {cached['seconds']:.1f} s",
            flush=True,
        )
        return cached
    logit_started = time.perf_counter()
    fitted = fit_logit(rows, segment)
    seconds_logit = time.perf_counter() - logit_started
    coefficients = fitted["coefficients"]
    curves = _curves_for(fitted["curve"], segment)
    status = pd.DataFrame([fitted["status"]])
    variance = _empty_variance()
    seconds_mixed = 0.0
    if SPEC[segment]["random_intercept"]:
        mixed_started = time.perf_counter()
        mixed_sample = drop_separated_levels(
            pool_sparse(mixed_rows(frame, segment, seed))
        )
        mixed_fit, variance_row = fit_mixed(mixed_sample, segment)
        coefficients = pd.concat(
            [coefficients, _mixed_coefficients(mixed_fit, segment)],
            ignore_index=True,
        )
        variance = pd.DataFrame([variance_row])
        seconds_mixed = time.perf_counter() - mixed_started
    seconds = time.perf_counter() - started
    out = {
        "coefficients": coefficients,
        "curves": curves,
        "variance": variance,
        "status": status,
        "sample_account": account_frame,
        "cache": "miss",
        "seconds": seconds,
        "seconds_logit": seconds_logit,
        "seconds_mixed": seconds_mixed,
        "n": len(rows),
    }
    _write_cached(
        key,
        {
            "coefficients": coefficients,
            "curves": curves,
            "variance": variance,
            "status": status,
        },
    )
    print(
        f"payment {segment}: cache miss, logit {seconds_logit:.1f} s, "
        f"mixed {seconds_mixed:.1f} s, n = {len(rows)}",
        flush=True,
    )
    return out


def block_quantiles(frame: pd.DataFrame, seed: int = 20261104) -> dict:
    """Quantile regression of log stock inside each segment. No OLS of that outcome."""
    started = time.perf_counter()
    sample = quantile_rows(frame, seed)
    key = _signature(
        sample,
        ["segment", "log_stock", "debt_age", "section", "porte"],
        f"q|{seed}",
    )
    cached = _read_cached(key)
    if cached is not None:
        cached["cache"] = "hit"
        cached["seconds"] = time.perf_counter() - started
        cached["n"] = len(sample)
        print(f"quantiles: cache hit, {cached['seconds']:.1f} s", flush=True)
        return cached
    tables = []
    for segment, part in sample.groupby("segment", dropna=False):
        part = pool_sparse(part)
        if len(part) < 40 or part["log_stock"].nunique() < 5:
            continue
        formula = diag.model_formula("log_stock", part, by_segment=True)
        if "log_stock" in formula.split("~", 1)[1]:
            raise RuntimeError("Log stock is the outcome, not a regressor.")
        for tau in QUANTILES:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = smf.quantreg(formula, data=part).fit(q=float(tau))
            table = diag._coef_table(
                result,
                {
                    "outcome": "log_stock",
                    "sample": "quantile",
                    "se_type": "iid",
                    "segment": str(segment),
                    "tau": float(tau),
                    "formula": formula,
                    "model": "quantile",
                },
            )
            tables.append(table)
    quantiles = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    seconds = time.perf_counter() - started
    _write_cached(key, {"quantiles": quantiles})
    print(
        f"quantiles: cache miss, {seconds:.1f} s, n = {len(sample)}",
        flush=True,
    )
    return {
        "quantiles": quantiles,
        "cache": "miss",
        "seconds": seconds,
        "n": len(sample),
    }


def block_recovery(frame: pd.DataFrame, seed: int = 20261104) -> dict:
    """OLS of recovery, kept only as the comparison named in section 4.5."""
    started = time.perf_counter()
    key = _signature(
        frame,
        ["segment", "recovery", "log_stock", "debt_age", "section", "porte", "root"],
        f"recovery|{seed}",
    )
    cached = _read_cached(key)
    if cached is not None:
        cached["cache"] = "hit"
        cached["seconds"] = time.perf_counter() - started
        print(f"recovery comparison: cache hit, {cached['seconds']:.1f} s", flush=True)
        return cached
    tables = []
    for segment, part in frame.groupby("segment", dropna=False):
        part = pool_sparse(part)
        if len(part) < 30:
            continue
        formula = diag.model_formula("recovery", part, by_segment=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hc3 = diag._fit_ols(formula, part)
            tables.append(
                diag._coef_table(
                    hc3,
                    {
                        "outcome": "recovery",
                        "sample": "full",
                        "se_type": "HC3",
                        "segment": str(segment),
                        "formula": formula,
                        "model": "comparison",
                    },
                )
            )
            if part["root"].nunique() >= 2:
                clustered = diag._fit_ols(formula, part, cluster="root")
                tables.append(
                    diag._coef_table(
                        clustered,
                        {
                            "outcome": "recovery",
                            "sample": "full",
                            "se_type": "cluster",
                            "segment": str(segment),
                            "formula": formula,
                            "model": "comparison",
                        },
                    )
                )
    coefficients = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    seconds = time.perf_counter() - started
    _write_cached(key, {"coefficients": coefficients})
    print(f"recovery comparison: cache miss, {seconds:.1f} s", flush=True)
    return {"coefficients": coefficients, "cache": "miss", "seconds": seconds}


def figures_from_curves(curves: pd.DataFrame) -> dict[str, go.Figure]:
    figures = {}
    labels = {
        "log_stock": "log(1 + stock)",
        "debt_age": "Debt age (years)",
    }
    colors = {"declared": diag.DECLARED, "assessed": diag.ASSESSED}
    for variable, x_title in labels.items():
        fig = go.Figure()
        part = curves.loc[curves["variable"] == variable]
        for segment, block in part.groupby("segment"):
            block = block.sort_values("x")
            color = colors.get(str(segment), diag.GREY)
            fig.add_trace(
                go.Scatter(
                    x=block["x"],
                    y=block["estimate"],
                    mode="lines",
                    name=str(segment),
                    line={"color": color, "width": 2},
                )
            )
            for edge, show in (("ci_low", True), ("ci_high", False)):
                fig.add_trace(
                    go.Scatter(
                        x=block["x"],
                        y=block[edge],
                        mode="lines",
                        name=f"{segment} 95 percent",
                        showlegend=show,
                        line={"color": color, "width": 1, "dash": "dot"},
                    )
                )
        styled = diag._style(
            fig,
            x_title,
            "Change in payment log-odds from the median",
        )
        diag.assert_bin_grid(styled)
        diag.figure_html(styled)
        figures[f"partial_{variable}"] = styled
    return figures


def run_inference(frame: pd.DataFrame, seed: int = 20261104) -> dict:
    reject_forbidden(frame)
    declared = block_payment(frame, "declared", seed)
    assessed = block_payment(frame, "assessed", seed)
    quantiles = block_quantiles(frame, seed)
    recovery = block_recovery(frame, seed)
    coefficients = pd.concat(
        [
            declared["coefficients"],
            assessed["coefficients"],
            recovery["coefficients"],
        ],
        ignore_index=True,
    )
    curves = pd.concat([declared["curves"], assessed["curves"]], ignore_index=True)
    variance = pd.concat(
        [declared["variance"], assessed["variance"]], ignore_index=True
    )
    status = pd.concat([declared["status"], assessed["status"]], ignore_index=True)
    sample_account = pd.concat(
        [declared["sample_account"], assessed["sample_account"]], ignore_index=True
    )
    flags = [
        declared["cache"],
        assessed["cache"],
        quantiles["cache"],
        recovery["cache"],
    ]
    seconds = pd.DataFrame(
        [
            {
                "block": "payment_declared_logit",
                "seconds": declared["seconds_logit"],
            },
            {
                "block": "payment_declared_mixed",
                "seconds": declared["seconds_mixed"],
            },
            {
                "block": "payment_assessed_logit",
                "seconds": assessed["seconds_logit"],
            },
            {"block": "quantiles", "seconds": quantiles["seconds"]},
            {"block": "recovery_comparison", "seconds": recovery["seconds"]},
        ]
    )
    return {
        "coefficients": coefficients,
        "quantiles": quantiles["quantiles"],
        "curves": curves,
        "variance": variance,
        "status": status,
        "sample_account": sample_account,
        "figures": figures_from_curves(curves),
        "seconds": seconds,
        "cache": "hit" if all(flag == "hit" for flag in flags) else "miss",
        "n_payment": {"declared": declared["n"], "assessed": assessed["n"]},
        "n_quantile": quantiles["n"],
    }


def fit_cache_dir() -> Path:
    base = os.environ.get("CEMEPI_CACHE")
    root = (
        Path(base) / "results" / "avd47-fit"
        if base
        else Path(os.environ.get("TMPDIR", "/tmp")) / "avd47-fit"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _signature(frame: pd.DataFrame, columns: list[str], label: str) -> str:
    digest = hashlib.sha256()
    digest.update(CACHE_VERSION.encode())
    digest.update(label.encode())
    digest.update(str(len(frame)).encode())
    for column in columns:
        if column not in frame.columns:
            continue
        digest.update(column.encode())
        hashed = pd.util.hash_pandas_object(frame[column], index=False).to_numpy()
        digest.update(np.ascontiguousarray(hashed).tobytes())
    return digest.hexdigest()[:32]


def _read_cached(key: str) -> dict | None:
    root = fit_cache_dir()
    marker = root / f"{key}.ok"
    if not marker.exists():
        return None
    frames = {}
    for name in marker.read_text(encoding="utf-8").split():
        path = root / f"{key}-{name}.parquet"
        if not path.exists():
            return None
        frames[name] = pd.read_parquet(path)
    return frames


def _write_cached(key: str, frames: dict[str, pd.DataFrame]) -> None:
    banned = {
        "root",
        "cnpj",
        "stock",
        "paid",
        "municipality",
        "nome_devedor",
        "cpf_devedor",
    }
    root = fit_cache_dir()
    for name, frame in frames.items():
        if frame is None:
            continue
        if banned & {column.lower() for column in frame.columns}:
            raise RuntimeError(
                f"Refusing to cache certificate-level columns in {name}."
            )
        frame.to_parquet(root / f"{key}-{name}.parquet", index=False)
    (root / f"{key}.ok").write_text(" ".join(frames), encoding="utf-8")
