"""Linearity, spline, quantile and group diagnostics for one CDA cross-section.

The functions take a table that is already in memory. They do not open the
extract and they do not read person columns. The December 2025 loader lives
in ``analyze.py`` and runs on the machine that has the disk.

Working rules, shared with the decision note and not taken from the cited
papers: within each segment, a spline wins when ΔAIC is at least 10 or the
fitted log-odds are not monotone; the stock OLS is set aside when the RESET
powers add at least 0.01 to R² or the Spearman correlation of absolute
residuals and fitted values is at least 0.20; a mixed logit is fit only when
a latent ICC is at least 0.05. The funnel share is reported and does not
select the mixed logit.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import os
import time
import warnings
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
from statsmodels.stats.diagnostic import linear_harvey_collier, linear_reset
from statsmodels.tools.sm_exceptions import PerfectSeparationError

DELTA_AIC = 10.0
ICC_CUT = 0.05
PARTIAL_R2 = 0.01
FAN_CUT = 0.20
FUNNEL_CUT = 0.01
HALDANE = 0.5
Z_95 = 1.959963984540054
QUANTILES = (0.25, 0.5, 0.75, 0.9)
SAMPLE_CAP = 200_000
QUANTILE_CAP = 20_000
MIXED_CAP = 8_000
CLUSTER_PER_GROUP = 30
PAGE_GROUP_CAP = 50
SPLINE_CAP = 40_000
CACHE_VERSION = "lin45-v4"
MIN_CDA = 10
MIN_FIRMS = 5
# Section 5.9: a cell with 1 to 4 payers is not printed.
PAYER_PRINT_MIN = 5
N_BINS = 40
MIN_BIN_COUNT = 5
DECLARED = "#154C56"
ASSESSED = "#D55E00"
GREY = "#8A9399"
PERSON_COLUMNS = ("NOME_DEVEDOR", "CPF_DEVEDOR")
KEEP_COLUMNS = {
    "municipality",
    "uf",
    "division",
    "segment",
    "porte",
    "status",
    "variable",
    "group",
    "level",
    "bin",
    "term",
    "outcome",
    "sample",
    "se_type",
    "grouping",
    "estimator",
    "controls",
    "inference",
    "choice",
    "evidence",
    "model",
    "tau",
    "formula",
}
REQUIRED = (
    "stock",
    "paid",
    "debt_age",
    "segment",
    "porte",
    "section",
    "division",
    "situacao_cadastral",
    "municipality",
    "uf",
    "root",
)


def reject_person_columns(frame: pd.DataFrame) -> None:
    found = PERSON_COLUMNS.__class__(
        name for name in frame.columns if str(name).upper() in PERSON_COLUMNS
    )
    if found:
        joined = ", ".join(found)
        raise RuntimeError(
            f"The table contains {joined}. The lab does not read that column."
        )


def empirical_log_odds(k, n, a: float = HALDANE):
    """Haldane–Anscombe log-odds, finite at 0 successes and at 0 failures."""
    k_arr = np.asarray(k, dtype=float)
    n_arr = np.asarray(n, dtype=float)
    estimate = np.log((k_arr + a) / (n_arr - k_arr + a))
    se = np.sqrt(1.0 / (k_arr + a) + 1.0 / (n_arr - k_arr + a))
    low, high = estimate - Z_95 * se, estimate + Z_95 * se
    if np.ndim(k) == 0 and np.ndim(n) == 0:
        return float(estimate), float(se), float(low), float(high)
    return estimate, se, low, high


def _small_cell_mask(
    frame: pd.DataFrame,
    min_cda: int = MIN_CDA,
    min_firms: int = MIN_FIRMS,
) -> pd.Series:
    """Cells with fewer than 10 CDAs, fewer than 5 firms, or 1 to 4 payers."""
    small = (frame["n_cda"] < min_cda) | (frame["n_firms"] < min_firms)
    if "n_paid" in frame.columns:
        paid = frame["n_paid"]
        small = small | (paid.notna() & (paid >= 1) & (paid < PAYER_PRINT_MIN))
    return small


def suppress_cells(
    frame: pd.DataFrame,
    min_cda: int = MIN_CDA,
    min_firms: int = MIN_FIRMS,
) -> pd.DataFrame:
    """Blank numeric measures. A count of 1 to 4 payers is not printed."""
    out = frame.copy()
    if "n_cda" not in out.columns or "n_firms" not in out.columns:
        return out
    small = _small_cell_mask(out, min_cda, min_firms)
    for column in out.columns:
        if column in KEEP_COLUMNS:
            continue
        if pd.api.types.is_bool_dtype(out[column]):
            out[column] = out[column].astype(float)
        if pd.api.types.is_numeric_dtype(out[column]):
            out.loc[small, column] = np.nan
    return out


def allocate_strata(counts: pd.Series, n: int) -> pd.Series:
    """Largest-remainder allocation. The result sums to min(n, total)."""
    counts = counts.astype(int)
    total = int(counts.sum())
    if total <= n:
        return counts.copy()
    quota = counts * (n / total)
    take = np.minimum(np.floor(quota).astype(int), counts)
    remainder = int(n - int(take.sum()))
    fractional = (quota - np.floor(quota)).sort_values(ascending=False)
    for idx in fractional.index:
        if remainder <= 0:
            break
        if int(take.loc[idx]) < int(counts.loc[idx]):
            take.loc[idx] = int(take.loc[idx]) + 1
            remainder -= 1
    return take.astype(int)


def stratified_sample(
    frame: pd.DataFrame, n: int, keys: list[str], seed: int
) -> pd.DataFrame:
    if len(frame) <= n:
        return frame.copy()
    grouped = frame.groupby(keys, dropna=False)
    take = allocate_strata(grouped.size(), n)
    rng = np.random.default_rng(seed)
    parts = []
    for key, k in take.items():
        k = int(k)
        if k <= 0:
            continue
        # pandas 3: groupby([one name]) wants get_group((name,)), not get_group(name).
        block = grouped.get_group(key if isinstance(key, tuple) else (key,))
        choice = rng.choice(len(block), size=min(k, len(block)), replace=False)
        parts.append(block.iloc[np.sort(choice)])
    if not parts:
        return frame.iloc[:0].copy()
    return pd.concat(parts, axis=0)


def cluster_sample(
    frame: pd.DataFrame,
    group_col: str,
    cap: int = MIXED_CAP,
    per_group: int = CLUSTER_PER_GROUP,
    seed: int = 20261104,
) -> pd.DataFrame:
    """Draw whole groups, at most `per_group` certificates each, until `cap` rows.

    A row-wise sample of every municipality leaves about five certificates in each
    group and shrinks the random-intercept variance. Keeping the cluster together
    avoids that thinning. The last cluster may be shortened so the total stays
    within the cap.
    """
    if frame.empty:
        return frame.copy()
    rng = np.random.default_rng(seed)
    names = frame[group_col].drop_duplicates().to_numpy()
    rng.shuffle(names)
    grouped = frame.groupby(group_col, dropna=False)
    parts = []
    n_rows = 0
    for name in names:
        if n_rows >= cap:
            break
        block = grouped.get_group(name)
        if len(block) > per_group:
            choice = rng.choice(len(block), size=per_group, replace=False)
            block = block.iloc[np.sort(choice)]
        room = cap - n_rows
        if len(block) > room:
            choice = rng.choice(len(block), size=room, replace=False)
            block = block.iloc[np.sort(choice)]
        parts.append(block)
        n_rows += len(block)
    if not parts:
        return frame.iloc[:0].copy()
    return pd.concat(parts, axis=0)


def without_top_share(
    frame: pd.DataFrame, share: float = 0.01, column: str = "stock"
) -> pd.DataFrame:
    """Drop the largest ceil(share × n) rows by ``column``."""
    n = len(frame)
    if n == 0:
        return frame.copy()
    k = int(np.ceil(share * n))
    if k <= 0 or k >= n:
        return frame.copy()
    return frame.drop(index=frame[column].nlargest(k).index)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    reject_person_columns(frame)
    missing = [name for name in REQUIRED if name not in frame.columns]
    if missing:
        raise RuntimeError(f"The table is missing {', '.join(missing)}.")
    out = frame.loc[:, list(REQUIRED)].copy()
    out = out.loc[out["stock"] > 0].dropna(subset=["stock", "paid", "debt_age"])
    out["log_stock"] = np.log1p(out["stock"].to_numpy(dtype=float))
    out["recovery"] = out["paid"].to_numpy(dtype=float) / out["stock"].to_numpy(
        dtype=float
    )
    out["paid_flag"] = (out["paid"].to_numpy(dtype=float) > 0).astype(int)
    out["porte"] = out["porte"].fillna("unknown").astype(str)
    out["section"] = out["section"].fillna("unmatched").astype(str)
    out["division"] = out["division"].fillna("unmatched").astype(str)
    out["segment"] = out["segment"].astype(str)
    out["municipality"] = out["municipality"].fillna("Unknown").astype(str)
    out["uf"] = out["uf"].fillna("NA").astype(str)
    out["root"] = out["root"].astype(str)
    out["status"] = np.where(
        out["situacao_cadastral"].astype(str).eq("02"), "active", "other"
    )
    return out.reset_index(drop=True)


def _reference(series: pd.Series, preferred: str) -> str:
    values = series.astype(str)
    if (values == preferred).any():
        return preferred
    return str(values.value_counts().index[0])


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "") + "'"


def rhs(frame: pd.DataFrame, outcome: str, by_segment: bool = False) -> str:
    """Regressors from section 5.6.1 that exist in a single cross-section, plus the additions.

    Section 5.6.1 is ``paid_flag ~ log_stock + age + rate_l1 + C(year)`` on a
    certificate-year panel. This page has one snapshot, so the year effect and
    the lagged recovery are omitted. Debt age is that model's ``age``. Log stock
    is the outcome of the stock equation and a regressor of recovery and of the
    payment logit. Segment, CNAE section and firm size are the additions.
    """
    terms: list[str] = []
    if (
        outcome in {"recovery", "paid_flag"}
        and frame["log_stock"].nunique(dropna=True) > 1
    ):
        terms.append("log_stock")
    if frame["debt_age"].nunique(dropna=True) > 1:
        terms.append("debt_age")
    if not by_segment and frame["segment"].nunique(dropna=True) > 1:
        ref = _reference(frame["segment"], "declared")
        terms.append(f"C(segment, Treatment(reference={_quote(ref)}))")
    if frame["section"].nunique(dropna=True) > 1:
        ref = _reference(frame["section"], "G")
        terms.append(f"C(section, Treatment(reference={_quote(ref)}))")
    if frame["porte"].nunique(dropna=True) > 1:
        ref = _reference(frame["porte"], "01")
        terms.append(f"C(porte, Treatment(reference={_quote(ref)}))")
    return " + ".join(terms) if terms else "1"


def model_formula(outcome: str, frame: pd.DataFrame, by_segment: bool = False) -> str:
    return f"{outcome} ~ {rhs(frame, outcome, by_segment)}"


def classical_icc(y, groups) -> dict:
    """One-way random-intercept ICC for an unbalanced sample."""
    data = pd.DataFrame(
        {"y": np.asarray(y, dtype=float), "g": np.asarray(groups)}
    ).dropna()
    n = len(data)
    means = data.groupby("g", dropna=False)["y"].agg(["mean", "size"])
    g = len(means)
    empty = {"tau2": 0.0, "sigma2": float("nan"), "icc": 0.0, "n_groups": g, "n": n}
    if g < 2 or n <= g:
        return empty
    grand = float(data["y"].mean())
    ssb = float((means["size"] * (means["mean"] - grand) ** 2).sum())
    centered = data["y"] - data.groupby("g", dropna=False)["y"].transform("mean")
    ssw = float((centered**2).sum())
    msb = ssb / (g - 1)
    msw = ssw / (n - g)
    n0 = (n - float((means["size"] ** 2).sum()) / n) / (g - 1)
    if n0 <= 0:
        return empty
    tau2 = max(0.0, (msb - msw) / n0)
    sigma2 = float(msw)
    denom = tau2 + sigma2
    icc = float(tau2 / denom) if denom > 0 else 0.0
    return {
        "tau2": float(tau2),
        "sigma2": sigma2,
        "icc": icc,
        "n_groups": g,
        "n": int(n),
    }


def latent_icc(y, groups) -> dict:
    """Method-of-moments latent ICC for a binary outcome.

    The between-group variance of the payment rate is corrected for binomial
    sampling noise, mapped to the logit scale by the delta method, and divided
    by τ² + π²/3 (Goldstein, Browne and Rasbash 2002).
    """
    data = pd.DataFrame(
        {"y": np.asarray(y, dtype=float), "g": np.asarray(groups)}
    ).dropna()
    n = len(data)
    agg = data.groupby("g", dropna=False)["y"].agg(["mean", "size"])
    g = len(agg)
    pi = float(data["y"].mean()) if n else 0.0
    empty = {"tau2": 0.0, "icc": 0.0, "n_groups": g, "n": n, "pi": pi}
    if g < 2 or n == 0 or not 0.0 < pi < 1.0:
        return empty
    between = float((agg["size"] * (agg["mean"] - pi) ** 2).sum() / n)
    sampling = g * pi * (1.0 - pi) / n
    variance = max(0.0, between - sampling)
    scale = (pi * (1.0 - pi)) ** 2
    tau2 = float(variance / scale) if scale > 0 else 0.0
    icc = float(tau2 / (tau2 + np.pi**2 / 3.0))
    return {"tau2": tau2, "icc": icc, "n_groups": g, "n": int(n), "pi": pi}


def _harvey_p(res) -> float:
    try:
        out = linear_harvey_collier(res)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return float("nan")
    p_value = getattr(out, "pvalue", None)
    if p_value is None:
        p_value = out[1]
    return float(p_value)


def linearity_checks(formula: str, data: pd.DataFrame) -> dict:
    """Ramsey RESET (classical F and HC3) and the Harvey–Collier statistic."""
    res = smf.ols(formula, data=data).fit()
    reset = linear_reset(res, power=3, use_f=True)
    reset_hc3 = linear_reset(
        res, power=3, use_f=True, test_type="fitted", cov_type="HC3"
    )
    fitted = np.asarray(res.fittedvalues, dtype=float)
    exog = np.column_stack(
        [np.asarray(res.model.exog, dtype=float), fitted**2, fitted**3]
    )
    augmented = sm.OLS(np.asarray(res.model.endog, dtype=float), exog).fit()
    delta_r2 = float(augmented.rsquared - res.rsquared)
    rho = stats.spearmanr(np.abs(np.asarray(res.resid, dtype=float)), fitted)
    fan = abs(float(getattr(rho, "statistic", rho[0])))
    return {
        "reset_f": float(reset.statistic),
        "df_num": float(reset.df_num),
        "df_denom": float(reset.df_denom),
        "reset_p": float(reset.pvalue),
        "reset_f_hc3": float(reset_hc3.statistic),
        "reset_p_hc3": float(reset_hc3.pvalue),
        "delta_r2": max(0.0, delta_r2),
        "fan": fan,
        "harvey_p": _harvey_p(res),
        "n": int(res.nobs),
        "r2": float(res.rsquared),
        "formula": formula,
    }


def _coef_table(res, meta: dict) -> pd.DataFrame:
    ci = res.conf_int()
    if isinstance(ci, pd.DataFrame):
        low = ci.iloc[:, 0].to_numpy(dtype=float)
        high = ci.iloc[:, 1].to_numpy(dtype=float)
    else:
        low = np.asarray(ci[:, 0], dtype=float)
        high = np.asarray(ci[:, 1], dtype=float)
    out = pd.DataFrame(
        {
            "term": res.params.index.astype(str),
            "coef": np.asarray(res.params, dtype=float),
            "se": np.asarray(res.bse, dtype=float),
            "ci_low": low,
            "ci_high": high,
            "p_value": np.asarray(res.pvalues, dtype=float),
        }
    )
    for key, value in meta.items():
        out[key] = value
    out["n"] = int(res.nobs)
    out["r2"] = float(getattr(res, "rsquared", np.nan))
    return out


def _fit_ols(formula: str, data: pd.DataFrame, cluster: str | None = None):
    model = smf.ols(formula, data=data)
    if cluster is None:
        return model.fit(cov_type="HC3")
    return model.fit(cov_type="cluster", cov_kwds={"groups": data[cluster]})


def spline_compare(frame: pd.DataFrame, x: str, y: str = "paid_flag") -> dict:
    """Linear logit against a 4-df restricted cubic spline (Durrleman and Simon 1989)."""
    data = frame[[y, x]].dropna()
    failed = {
        "variable": x,
        "delta_aic": float("nan"),
        "lr": float("nan"),
        "lr_df": float("nan"),
        "lr_p": float("nan"),
        "aic_linear": float("nan"),
        "aic_spline": float("nan"),
        "curved": False,
        "n": len(data),
        "grid_x": np.array([]),
        "grid_eta_linear": np.array([]),
        "grid_eta_spline": np.array([]),
    }
    if data[y].nunique() < 2 or data[x].nunique() < 6:
        return failed
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            linear = smf.logit(f"{y} ~ {x}", data=data).fit(disp=False, maxiter=100)
            spline = smf.logit(f"{y} ~ cr({x}, df=4)", data=data).fit(
                disp=False, maxiter=200
            )
    except (ValueError, np.linalg.LinAlgError, PerfectSeparationError, RuntimeError):
        return failed
    delta = float(linear.aic - spline.aic)
    lr = 2.0 * (float(spline.llf) - float(linear.llf))
    df = float(spline.df_model - linear.df_model)
    p_value = float(stats.chi2.sf(lr, df)) if df > 0 else float("nan")
    grid = np.linspace(
        float(np.quantile(data[x], 0.05)), float(np.quantile(data[x], 0.95)), 40
    )
    pred = pd.DataFrame({x: grid})
    eta_linear = np.asarray(linear.predict(pred, which="linear"), dtype=float)
    eta_spline = np.asarray(spline.predict(pred, which="linear"), dtype=float)
    diff = np.diff(eta_spline)
    span = float(np.nanmax(eta_spline) - np.nanmin(eta_spline))
    tolerance = max(1e-3, 0.02 * span)
    curved = bool((diff > tolerance).any() and (diff < -tolerance).any())
    return {
        "variable": x,
        "delta_aic": delta,
        "lr": lr,
        "lr_df": df,
        "lr_p": p_value,
        "aic_linear": float(linear.aic),
        "aic_spline": float(spline.aic),
        "curved": curved,
        "n": int(linear.nobs),
        "grid_x": grid,
        "grid_eta_linear": eta_linear,
        "grid_eta_spline": eta_spline,
    }


def maybe_fit_mixed(
    moment_icc: float,
    funnel_share: float,
    fitter: Callable[[], dict],
    icc_cut: float = ICC_CUT,
    funnel_cut: float = FUNNEL_CUT,
):
    """Fit a random-intercept logit only when the latent ICC reaches the cut.

    ``funnel_share`` and ``funnel_cut`` stay in the signature so the call site
    can pass the diagnostic. They do not open the fit.
    """
    del funnel_share, funnel_cut
    icc = (
        0.0 if moment_icc is None or not np.isfinite(moment_icc) else float(moment_icc)
    )
    if icc >= icc_cut:
        return fitter()
    return None


def fit_mixed_logit(
    frame: pd.DataFrame, group_col: str, controls: bool = False
) -> dict:
    """Variational Bayes random-intercept logit. τ² is exp(vcp_mean) squared."""
    columns = [group_col, "paid_flag"]
    if controls:
        columns += ["log_stock", "debt_age", "segment", "section", "porte"]
    data = frame.loc[:, columns].dropna().copy()
    data[group_col] = data[group_col].astype(str)
    empty = {
        "converged": False,
        "tau": float("nan"),
        "tau2": float("nan"),
        "icc": float("nan"),
        "n": len(data),
        "controls": "design" if controls else "none",
    }
    if data[group_col].nunique() < 2 or data["paid_flag"].nunique() < 2:
        return empty
    fixed = (
        "paid_flag ~ 1"
        if not controls
        else "paid_flag ~ " + rhs(data, "paid_flag", False)
    )
    vc = {group_col: f"0 + C({group_col})"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = BinomialBayesMixedGLM.from_formula(fixed, vc, data).fit_vb(
                verbose=False
            )
        tau = float(np.exp(result.vcp_mean[0]))
        if not np.isfinite(tau):
            return empty
        tau2 = tau**2
        return {
            "converged": True,
            "tau": tau,
            "tau2": tau2,
            "icc": float(tau2 / (tau2 + np.pi**2 / 3.0)),
            "n": len(data),
            "controls": "design" if controls else "none",
        }
    except (ValueError, np.linalg.LinAlgError, FloatingPointError, RuntimeError):
        return empty


def quantile_table(frame: pd.DataFrame, formula: str, sample: str) -> pd.DataFrame:
    rows = []
    for tau in QUANTILES:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = smf.quantreg(formula, data=frame).fit(q=tau)
        rows.append(
            _coef_table(
                result,
                {
                    "outcome": "log_stock",
                    "sample": sample,
                    "se_type": "iid",
                    "segment": "pooled",
                    "tau": tau,
                    "formula": formula,
                },
            )
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def quantile_bins(frame: pd.DataFrame, x: str, n_bins: int = 20) -> pd.DataFrame:
    rows = []
    for segment, part in frame.groupby("segment", dropna=False):
        part = part.dropna(subset=[x, "paid_flag", "root"])
        if len(part) < n_bins or part[x].nunique() < 5:
            continue
        cats = pd.qcut(
            part[x], q=min(n_bins, int(part[x].nunique())), duplicates="drop"
        )
        for interval, block in part.groupby(cats, observed=True):
            k = float(block["paid_flag"].sum())
            n = len(block)
            estimate, se, low, high = empirical_log_odds(k, n)
            rows.append(
                {
                    "variable": x,
                    "segment": segment,
                    "bin": str(interval),
                    "midpoint": float((interval.left + interval.right) / 2.0),
                    "n_cda": n,
                    "n_firms": int(block["root"].nunique()),
                    "n_paid": k,
                    "rate": k / n,
                    "log_odds": estimate,
                    "se": se,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    if not rows:
        return pd.DataFrame()
    return suppress_cells(pd.DataFrame(rows))


def category_log_odds(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    rows = []
    for (segment, level), block in frame.groupby(["segment", column], dropna=False):
        k = float(block["paid_flag"].sum())
        n = len(block)
        estimate, se, low, high = empirical_log_odds(k, n)
        rows.append(
            {
                "variable": column,
                "segment": segment,
                "level": str(level),
                "n_cda": n,
                "n_firms": int(block["root"].nunique()),
                "n_paid": k,
                "rate": k / n if n else np.nan,
                "log_odds": estimate,
                "se": se,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return suppress_cells(pd.DataFrame(rows))


def porte_shape(frame: pd.DataFrame) -> pd.DataFrame:
    """Whether Micro → Small → Other sizes is monotone in the payment log-odds."""
    order = ["01", "03", "05"]
    rows = []
    for segment, part in frame.groupby("segment"):
        odds = []
        usable = True
        for level in order:
            block = part.loc[part["porte"].astype(str) == level]
            n_paid = float(block["paid_flag"].sum())
            if (
                len(block) < MIN_CDA
                or block["root"].nunique() < MIN_FIRMS
                or 1 <= n_paid < PAYER_PRINT_MIN
            ):
                usable = False
                break
            estimate, *_rest = empirical_log_odds(n_paid, len(block))
            odds.append(estimate)
        monotone = bool(usable and all(b >= a for a, b in itertools.pairwise(odds)))
        rows.append(
            {
                "segment": segment,
                "monotone_micro_small_other": monotone,
                "log_odds_micro": odds[0] if usable else np.nan,
                "log_odds_small": odds[1] if usable else np.nan,
                "log_odds_other": odds[2] if usable else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _group_raw(frame: pd.DataFrame, keys: list[str], segment: str) -> pd.DataFrame:
    agg = (
        frame.groupby(keys, dropna=False)
        .agg(
            n_cda=("paid_flag", "size"),
            n_firms=("root", "nunique"),
            n_paid=("paid_flag", "sum"),
        )
        .reset_index()
    )
    pi = float(frame["paid_flag"].mean()) if len(frame) else 0.0
    agg["rate"] = agg["n_paid"] / agg["n_cda"]
    estimate, se, low, high = empirical_log_odds(
        agg["n_paid"].to_numpy(), agg["n_cda"].to_numpy()
    )
    agg["log_odds"] = estimate
    agg["se"] = se
    agg["ci_low"] = low
    agg["ci_high"] = high
    se_rate = np.sqrt(
        np.maximum(pi * (1.0 - pi), 0.0) / agg["n_cda"].to_numpy(dtype=float)
    )
    z_998 = float(stats.norm.ppf(0.999))
    agg["lo95"] = pi - Z_95 * se_rate
    agg["hi95"] = pi + Z_95 * se_rate
    agg["lo998"] = pi - z_998 * se_rate
    agg["hi998"] = pi + z_998 * se_rate
    agg["outside_998"] = (agg["rate"] < agg["lo998"]) | (agg["rate"] > agg["hi998"])
    agg["segment"] = segment
    agg["grouping"] = "+".join(keys)
    return agg


def _publishable_groups(table: pd.DataFrame) -> pd.DataFrame:
    if table.empty:
        return table
    return table.loc[~_small_cell_mask(table)].copy()


def groups_for_page(
    published: pd.DataFrame,
    plot_rows: pd.DataFrame,
    cap: int = PAGE_GROUP_CAP,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Groups outside the 99.8 percent funnel, at most `cap` largest per grouping and segment.

    The counts cover every group. The returned table is the page list, not the full list.
    """
    count_rows = []
    shown = []
    if published.empty and plot_rows.empty:
        empty = published.copy()
        counts = pd.DataFrame(
            columns=["grouping", "segment", "n_groups", "n_outside", "n_shown"]
        )
        return empty, counts
    keys = published.groupby(["grouping", "segment"], dropna=False).size()
    for (grouping, segment), n_groups in keys.items():
        part = plot_rows.loc[
            (plot_rows["grouping"] == grouping) & (plot_rows["segment"] == segment)
        ]
        outside = part.loc[part["outside_998"].fillna(False).astype(bool)]
        take = outside.sort_values("n_cda", ascending=False).head(cap)
        shown.append(take)
        count_rows.append(
            {
                "grouping": grouping,
                "segment": segment,
                "n_groups": int(n_groups),
                "n_outside": len(outside),
                "n_shown": len(take),
            }
        )
    listed = pd.concat(shown, ignore_index=True) if shown else published.iloc[:0]
    return listed, pd.DataFrame(count_rows)


def funnel_outside_share(frame: pd.DataFrame, keys: list[str]) -> float:
    table = _publishable_groups(_group_raw(frame, keys, "pooled"))
    if table.empty:
        return float("nan")
    return float(table["outside_998"].mean())


def group_tables(
    frame: pd.DataFrame, keys: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Suppressed table for the page, and the publishable rows for the funnel."""
    published = []
    plot_rows = []
    slices = [("pooled", frame), *list(frame.groupby("segment"))]
    for segment, part in slices:
        raw = _group_raw(part, keys, str(segment))
        published.append(suppress_cells(raw))
        plot_rows.append(_publishable_groups(raw))
    return pd.concat(published, ignore_index=True), pd.concat(
        plot_rows, ignore_index=True
    )


def apply_svg_template(fig: go.Figure) -> go.Figure:
    template = copy.deepcopy(pio.templates["plotly_white"])
    for name in list(template.data):
        label = str(name)
        if label.endswith("gl") or label.startswith("histogram2d"):
            setattr(template.data, name, None)
    fig.layout.template = template
    return fig


def figure_html(fig: go.Figure) -> str:
    apply_svg_template(fig)
    html = fig.to_html(include_plotlyjs=False, full_html=False)
    if "scattergl" in html or "Scattergl" in html:
        raise RuntimeError("Figure HTML contains a WebGL trace.")
    return html


def _style(fig: go.Figure, x_title: str, y_title: str) -> go.Figure:
    apply_svg_template(fig)
    fig.update_layout(
        font={"family": "Arial", "size": 13, "color": "#1c1c1c"},
        legend={"orientation": "h", "y": 1.14, "x": 0},
        margin={"l": 64, "r": 20, "t": 28, "b": 52},
        height=460,
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title=x_title,
        yaxis_title=y_title,
    )
    return fig


def _bin_edges(values: np.ndarray, n_bins: int) -> np.ndarray:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.linspace(0.0, 1.0, n_bins + 1)
    lo = float(np.min(finite))
    hi = float(np.max(finite))
    if hi <= lo:
        hi = lo + 1.0
    return np.linspace(lo, hi, n_bins + 1)


def binned_counts(x, y, n_bins: int = N_BINS, min_count: int = MIN_BIN_COUNT):
    """Counts on a fixed grid. Bins with fewer than 5 certificates are blank."""
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_arr, y_arr = x_arr[mask], y_arr[mask]
    x_edges = _bin_edges(x_arr, n_bins)
    y_edges = _bin_edges(y_arr, n_bins)
    if x_arr.size == 0:
        counts = np.zeros((n_bins, n_bins), dtype=float)
    else:
        counts, x_edges, y_edges = np.histogram2d(x_arr, y_arr, bins=[x_edges, y_edges])
    # Plotly reads z[row, col] as (y, x). histogram2d stores (x, y).
    z = counts.T.astype(float)
    z[z < min_count] = np.nan
    x_centre = (x_edges[:-1] + x_edges[1:]) / 2.0
    y_centre = (y_edges[:-1] + y_edges[1:]) / 2.0
    return x_centre, y_centre, z


def assert_bin_grid(fig: go.Figure) -> None:
    """A cloud figure carries bin centres and counts, not certificate values."""
    for trace in fig.data:
        kind = str(trace.type)
        if kind in {"histogram2d", "scattergl"}:
            raise RuntimeError(f"Figure trace {kind} is not an aggregate grid.")
        if kind == "heatmap":
            z = np.asarray(trace.z, dtype=float)
            if z.ndim != 2 or z.shape[0] > N_BINS or z.shape[1] > N_BINS:
                raise RuntimeError("Heatmap is larger than the bin grid.")
            finite = z[np.isfinite(z)]
            if finite.size and np.any(finite < MIN_BIN_COUNT):
                raise RuntimeError("A heatmap bin has fewer than 5 certificates.")
            if len(trace.x) > N_BINS or len(trace.y) > N_BINS:
                raise RuntimeError("Heatmap axes carry more than the bin centres.")
        elif kind == "scatter":
            n_points = 0 if trace.x is None else len(trace.x)
            if n_points > N_BINS:
                raise RuntimeError(
                    "A scatter trace carries more points than the bin grid."
                )


def _heatmap(fig: go.Figure, x, y, name: str, color: str) -> None:
    x_centre, y_centre, z = binned_counts(x, y)
    fig.add_trace(
        go.Heatmap(
            x=x_centre,
            y=y_centre,
            z=z,
            name=name,
            colorscale=[[0, "#f7f4f2"], [1, color]],
            showscale=False,
            hovertemplate="bin count=%{z}<extra></extra>",
        )
    )


def residual_figures(actual, fitted, resid, continuous: dict) -> dict:
    """Bin counts and a short quantile summary. No certificate residual is stored."""
    actual = np.asarray(actual, dtype=float)
    fitted = np.asarray(fitted, dtype=float)
    resid = np.asarray(resid, dtype=float)
    figs = {}
    residual = go.Figure()
    _heatmap(residual, fitted, resid, "Certificates", DECLARED)
    residual.add_hline(y=0, line_color=GREY, line_width=1)
    figs["residual_vs_fitted"] = _style(
        residual, "Fitted value (bin centre)", "Residual (bin centre)"
    )

    observed = go.Figure()
    _heatmap(observed, fitted, actual, "Certificates", DECLARED)
    finite_fit = fitted[np.isfinite(fitted)]
    finite_act = actual[np.isfinite(actual)]
    lo = float(min(np.min(finite_fit), np.min(finite_act)))
    hi = float(max(np.max(finite_fit), np.max(finite_act)))
    observed.add_trace(
        go.Scatter(
            x=[lo, hi],
            y=[lo, hi],
            mode="lines",
            name="45 degrees",
            line={"color": "#1B1B1B", "dash": "dash", "width": 1.4},
        )
    )
    figs["actual_vs_fitted"] = _style(
        observed, "Fitted value (bin centre)", "Observed value (bin centre)"
    )

    sample = resid[np.isfinite(resid)]
    probs = (np.arange(1, N_BINS + 1) - 0.5) / N_BINS
    theoretical = stats.norm.ppf(probs)
    ordered = np.quantile(sample, probs) if sample.size else np.full(N_BINS, np.nan)
    qq = go.Figure()
    qq.add_trace(
        go.Scatter(
            x=theoretical,
            y=ordered,
            mode="markers",
            name="Residual quantile",
            marker={"size": 6, "color": DECLARED},
        )
    )
    q_t = np.quantile(theoretical, [0.25, 0.75])
    q_s = np.quantile(ordered, [0.25, 0.75])
    slope = (q_s[1] - q_s[0]) / (q_t[1] - q_t[0]) if q_t[1] != q_t[0] else 1.0
    intercept = float(q_s[0] - slope * q_t[0])
    qq.add_trace(
        go.Scatter(
            x=[float(theoretical[0]), float(theoretical[-1])],
            y=[
                intercept + slope * float(theoretical[0]),
                intercept + slope * float(theoretical[-1]),
            ],
            mode="lines",
            name="Normal reference",
            line={"color": GREY, "dash": "dash"},
        )
    )
    figs["qq"] = _style(qq, "Normal quantile", "Residual quantile")
    for name, values in continuous.items():
        panel = go.Figure()
        _heatmap(panel, values, resid, "Certificates", ASSESSED)
        panel.add_hline(y=0, line_color=GREY, line_width=1)
        figs[f"residual_vs_{name}"] = _style(
            panel, f"{name} (bin centre)", "Residual (bin centre)"
        )
    for fig in figs.values():
        assert_bin_grid(fig)
    return figs


def logodds_figure(
    bins: pd.DataFrame, curves: pd.DataFrame, variable: str
) -> go.Figure:
    fig = go.Figure()
    colors = {"declared": DECLARED, "assessed": ASSESSED}
    part = bins.loc[(bins["variable"] == variable) & bins["log_odds"].notna()]
    for segment, block in part.groupby("segment"):
        color = colors.get(str(segment), GREY)
        fig.add_trace(
            go.Scatter(
                x=block["midpoint"],
                y=block["log_odds"],
                mode="markers",
                name=f"{segment} bins",
                marker={"color": color, "size": 8},
                error_y={
                    "type": "data",
                    "array": Z_95 * block["se"],
                    "color": color,
                    "thickness": 1,
                },
            )
        )
    curve = curves.loc[curves["variable"] == variable]
    for segment, block in curve.groupby("segment"):
        color = colors.get(str(segment), GREY)
        for model, dash in (("linear", "dot"), ("spline", "solid")):
            piece = block.loc[block["model"] == model]
            fig.add_trace(
                go.Scatter(
                    x=piece["x"],
                    y=piece["eta"],
                    mode="lines",
                    name=f"{segment} {model}",
                    line={"color": color, "dash": dash, "width": 2},
                )
            )
    label = "log(1 + stock)" if variable == "log_stock" else "Debt age (years)"
    styled = _style(fig, f"Bin midpoint of {label}", "Payment log-odds")
    assert_bin_grid(styled)
    return styled


def funnel_figure(plot_rows: pd.DataFrame, grouping: str) -> go.Figure:
    fig = go.Figure()
    colors = {"declared": DECLARED, "assessed": ASSESSED, "pooled": "#1B1B1B"}
    part = plot_rows.loc[plot_rows["grouping"] == grouping]
    for segment, block in part.groupby("segment"):
        fig.add_trace(
            go.Scatter(
                x=block["n_cda"],
                y=block["rate"],
                mode="markers",
                name=str(segment),
                marker={
                    "color": colors.get(str(segment), GREY),
                    "size": 7,
                    "opacity": 0.75,
                },
            )
        )
    pooled = part.loc[part["segment"] == "pooled"].sort_values("n_cda")
    if not pooled.empty:
        fig.add_trace(
            go.Scatter(
                x=pooled["n_cda"],
                y=pooled["hi998"],
                mode="lines",
                name="99.8% limit",
                line={"color": GREY, "width": 1},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=pooled["n_cda"],
                y=pooled["lo998"],
                mode="lines",
                name="99.8% limit",
                line={"color": GREY, "width": 1},
                showlegend=False,
            )
        )
    return _style(fig, "Certificates in the group", "Payment rate")


def _fmt(value, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "not available"
    return f"{float(value):.{digits}f}"


def _spline_needed(delta_aic: float, curved: bool) -> bool:
    aic_hit = np.isfinite(delta_aic) and float(delta_aic) >= DELTA_AIC
    return bool(aic_hit or curved)


def _payment_lines(rows: list[dict]) -> tuple[str, str]:
    """One clause per segment, from that segment's own ΔAIC and curved flag."""
    labels = {"log_stock": "log stock", "debt_age": "debt age"}
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(str(row["segment"]), []).append(row)
    choice_parts = []
    evidence_parts = []
    for segment in sorted(by_segment):
        needed = []
        for row in by_segment[segment]:
            variable = str(row["variable"])
            delta_aic = float(row["delta_aic"])
            curved = bool(row["curved"])
            evidence_parts.append(
                f"{segment} {labels.get(variable, variable)}: "
                f"ΔAIC = {_fmt(delta_aic)}, curved = {curved}"
            )
            if _spline_needed(delta_aic, curved):
                needed.append(labels.get(variable, variable))
        if needed:
            choice_parts.append(f"{segment}: Spline on {' and '.join(needed)}")
        else:
            choice_parts.append(f"{segment}: Linear logit in log stock and debt age")
    if not choice_parts:
        return (
            "Linear logit in log stock and debt age.",
            "Spline comparison was not available.",
        )
    return ". ".join(choice_parts) + ".", "; ".join(evidence_parts) + "."


def _group_lines(
    rows: list[dict],
    funnel_municipality: float,
    funnel_division: float,
    variational_note: str,
) -> tuple[str, str]:
    """One clause per segment from that segment's full-sample moment ICC."""
    by_segment: dict[str, list[dict]] = {}
    for row in rows:
        by_segment.setdefault(str(row["segment"]), []).append(row)
    choice_parts = []
    evidence_parts = []
    for segment in sorted(by_segment):
        needed = []
        for row in by_segment[segment]:
            icc = float(row["icc"])
            grouping = str(row["grouping"])
            evidence_parts.append(f"{segment} {grouping}: moment ICC = {_fmt(icc, 3)}")
            if np.isfinite(icc) and icc >= ICC_CUT:
                needed.append(grouping)
        if needed:
            choice_parts.append(
                f"{segment}: Mixed logit with a random intercept for {' and '.join(needed)}"
            )
        else:
            choice_parts.append(f"{segment}: No mixed effects")
    if not choice_parts:
        choice = (
            "No mixed effects. Keep CNAE section fixed effects and cluster "
            "standard errors by the CNPJ root."
        )
    else:
        choice = (
            ". ".join(choice_parts)
            + ". UF is not a grouping factor: the debtors are in São Paulo."
        )
    evidence = (
        "; ".join(evidence_parts)
        + ". Funnel share outside the 99.8% limits (diagnostic only) = "
        + f"{_fmt(funnel_municipality, 3)} and {_fmt(funnel_division, 3)}."
    )
    if variational_note:
        evidence += (
            " Supporting cluster-sample fit, not used for the decision: "
            + variational_note
            + ". Groups smaller than about 30 certificates, and the cap of 30 inside a drawn group, can bias this fit."
        )
    return choice, evidence


def inference_decision(evidence: dict) -> pd.DataFrame:
    """Short decision table. Large-sample p-values are not the rule."""
    payment, payment_evidence = _payment_lines(list(evidence["spline_by_segment"]))

    stock_material = (
        evidence["delta_r2_stock"] >= PARTIAL_R2 or evidence["fan_stock"] >= FAN_CUT
    )
    trim_material = (
        evidence["delta_r2_stock_trim"] >= PARTIAL_R2
        or evidence["fan_stock_trim"] >= FAN_CUT
    )
    if stock_material:
        balance = (
            "Quantile regression of log stock at τ = 0.25, 0.50, 0.75 and 0.90. "
            "Leave OLS out of the stock equation."
        )
    else:
        balance = (
            "OLS of log stock with HC3 standard errors. "
            "Keep quantile regression as a robustness check."
        )
    if stock_material != trim_material:
        other = "OLS of log stock" if stock_material else "Quantile regression"
        balance += f" The sample without the top 1 percent points to {other}."

    recovery_material = (
        evidence["delta_r2_recovery"] >= PARTIAL_R2
        or evidence["fan_recovery"] >= FAN_CUT
    )
    if recovery_material:
        recovery = (
            "The linear recovery equation is curved or fan-shaped. "
            "Use it only as a comparison with the payment logit."
        )
    else:
        recovery = (
            "OLS of recovery with HC3 standard errors is a usable linear description. "
            "The payment model remains the logit."
        )

    group_choice, group_evidence = _group_lines(
        list(evidence["icc_by_segment"]),
        float(evidence["funnel_municipality"]),
        float(evidence["funnel_division"]),
        str(evidence.get("variational_note", "")),
    )
    package = f"For the EAA text: {payment} {balance} {group_choice}"
    rows = [
        (
            "Probability of payment",
            payment,
            payment_evidence,
        ),
        (
            "Debt balance",
            balance,
            (
                "Partial R² of RESET powers = "
                f"{_fmt(evidence['delta_r2_stock'], 3)}; Spearman |residual| vs fitted = "
                f"{_fmt(evidence['fan_stock'])}; trim partial R² = "
                f"{_fmt(evidence['delta_r2_stock_trim'], 3)}."
            ),
        ),
        (
            "Recovery",
            recovery,
            (
                "Partial R² = "
                f"{_fmt(evidence['delta_r2_recovery'], 3)}; Spearman |residual| vs fitted = "
                f"{_fmt(evidence['fan_recovery'])}."
            ),
        ),
        (
            "Group heterogeneity",
            group_choice,
            group_evidence,
        ),
        (
            "Package for the paper",
            package,
            (
                f"Estimation sample n = {int(evidence['n_estimation'])}. "
                "Premissas: ΔAIC 10 and ICC 0.05 select the specification. "
                "The 99.8% funnel and its 1% share are reported and do not select a mixed logit."
            ),
        ),
    ]
    return pd.DataFrame(rows, columns=["inference", "choice", "evidence"])


def side_by_side(
    coefficients: pd.DataFrame, outcome: str, se_type: str = "HC3"
) -> pd.DataFrame:
    part = coefficients.loc[
        (coefficients["outcome"] == outcome)
        & (coefficients["se_type"] == se_type)
        & (coefficients["segment"] == "pooled")
    ]
    full = part.loc[part["sample"] == "full"].set_index("term")
    trim = part.loc[part["sample"] == "trim"].set_index("term")
    out = pd.DataFrame(
        {
            "coef_full": full["coef"],
            "se_full": full["se"],
            "p_full": full["p_value"],
            "coef_trim": trim["coef"],
            "se_trim": trim["se"],
            "p_trim": trim["p_value"],
        }
    )
    return out.reset_index()


def publish(frame: pd.DataFrame) -> str:
    """HTML table of every row. More than 25 rows scroll under a sticky header."""
    html = frame.to_html(
        index=False,
        border=0,
        classes="lin-table",
        float_format=lambda value: f"{value:.6g}",
    )
    if len(frame) <= 25:
        return html
    return (
        '<div style="max-height:28rem;overflow:auto">'
        "<style>.lin-table thead th{position:sticky;top:0;background:#fff;z-index:1}</style>"
        + html
        + "</div>"
    )


def _icc_record(summary: dict, **meta) -> dict:
    row = {
        "tau2": summary.get("tau2", np.nan),
        "icc": summary.get("icc", np.nan),
        "n_groups": summary.get("n_groups", np.nan),
        "n": summary.get("n", np.nan),
        "converged": True,
    }
    row.update(meta)
    return row


def fit_cache_dir() -> Path:
    """Aggregate-only cache. Certificate rows are never written here."""
    base = os.environ.get("CEMEPI_CACHE")
    root = (
        Path(base) / "results" / "lin45-fit"
        if base
        else Path(os.environ.get("TMPDIR", "/tmp")) / "lin45-fit"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def frame_signature(frame: pd.DataFrame, columns: list[str], label: str) -> str:
    """Hash of the columns that define a fit. The hash is not the microdata."""
    digest = hashlib.sha256()
    digest.update(CACHE_VERSION.encode())
    digest.update(label.encode())
    digest.update(str(len(frame)).encode())
    for column in columns:
        digest.update(column.encode())
        hashed = pd.util.hash_pandas_object(frame[column], index=False).to_numpy()
        digest.update(np.ascontiguousarray(hashed).tobytes())
    return digest.hexdigest()[:32]


def _read_cached_frames(key: str) -> dict[str, pd.DataFrame] | None:
    root = fit_cache_dir()
    marker = root / f"{key}.ok"
    if not marker.exists():
        return None
    names = marker.read_text(encoding="utf-8").split()
    frames = {}
    for name in names:
        path = root / f"{key}-{name}.parquet"
        if not path.exists():
            return None
        frames[name] = pd.read_parquet(path)
    return frames


def _write_cached_frames(key: str, frames: dict[str, pd.DataFrame]) -> None:
    root = fit_cache_dir()
    for name, frame in frames.items():
        banned = {"root", "cnpj", "stock", "paid", "municipality"}
        if banned & {column.lower() for column in frame.columns}:
            raise RuntimeError(
                f"Refusing to cache certificate-level columns in {name}."
            )
        frame.to_parquet(root / f"{key}-{name}.parquet", index=False)
    (root / f"{key}.ok").write_text(" ".join(frames), encoding="utf-8")


def _clock(label: str, started: float) -> float:
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.1f} s", flush=True)
    return elapsed


def block_ols_reset(
    frame: pd.DataFrame,
    sample_name: str = "full",
    with_quantiles: bool = False,
    seed: int = 20261104,
) -> dict:
    """OLS, HC3, cluster standard errors, RESET and binned residual figures for one sample."""
    started = time.perf_counter()
    coef_rows: list[pd.DataFrame] = []
    reset_rows: list[dict] = []
    figures: dict[str, go.Figure] = {}
    checks: dict = {}
    data = frame
    if len(data) >= 30:
        for outcome in ("log_stock", "recovery"):
            formula = model_formula(outcome, data, by_segment=False)
            hc3 = _fit_ols(formula, data)
            coef_rows.append(
                _coef_table(
                    hc3,
                    {
                        "outcome": outcome,
                        "sample": sample_name,
                        "se_type": "HC3",
                        "segment": "pooled",
                        "formula": formula,
                    },
                )
            )
            if data["root"].nunique() >= 2:
                clustered = _fit_ols(formula, data, cluster="root")
                coef_rows.append(
                    _coef_table(
                        clustered,
                        {
                            "outcome": outcome,
                            "sample": sample_name,
                            "se_type": "cluster",
                            "segment": "pooled",
                            "formula": formula,
                        },
                    )
                )
            pooled = linearity_checks(formula, data)
            pooled.update(
                {"outcome": outcome, "sample": sample_name, "segment": "pooled"}
            )
            reset_rows.append(pooled)
            checks[(outcome, sample_name)] = pooled
            continuous = {"debt_age": data["debt_age"].to_numpy(dtype=float)}
            if outcome == "recovery":
                continuous["log_stock"] = data["log_stock"].to_numpy(dtype=float)
            panels = residual_figures(
                hc3.model.endog, hc3.fittedvalues, hc3.resid, continuous
            )
            figures.update(
                {f"{outcome}_{sample_name}_{name}": fig for name, fig in panels.items()}
            )
            for segment, part in data.groupby("segment"):
                if len(part) < 40:
                    continue
                segment_formula = model_formula(outcome, part, by_segment=True)
                try:
                    segment_check = linearity_checks(segment_formula, part)
                except (ValueError, np.linalg.LinAlgError):
                    continue
                segment_check.update(
                    {
                        "outcome": outcome,
                        "sample": sample_name,
                        "segment": str(segment),
                    }
                )
                reset_rows.append(segment_check)
    quantiles = pd.DataFrame()
    if with_quantiles and len(data) >= 30:
        q_frame = stratified_sample(
            data, QUANTILE_CAP, ["segment", "porte", "paid_flag"], seed
        )
        q_formula = model_formula("log_stock", q_frame, by_segment=False)
        quantiles = quantile_table(q_frame, q_formula, sample_name)
        print(f"quantile regression rows: {len(q_frame)}", flush=True)
    for fig in figures.values():
        figure_html(fig)
    return {
        "coefficients": pd.concat(coef_rows, ignore_index=True)
        if coef_rows
        else pd.DataFrame(),
        "reset": pd.DataFrame(reset_rows),
        "figures": figures,
        "checks": checks,
        "quantiles": quantiles,
        "n": len(data),
        "seconds": _clock(f"ols-reset {sample_name}", started),
    }


def block_splines(frame: pd.DataFrame, seed: int = 20261104) -> dict:
    """Log-odds bins on the full sample. Logit and spline fits use at most SPLINE_CAP rows."""
    started = time.perf_counter()
    columns = [
        "segment",
        "paid_flag",
        "log_stock",
        "debt_age",
        "porte",
        "status",
        "root",
    ]
    key = frame_signature(frame, columns, f"splines|{SPLINE_CAP}|{seed}")
    cached = _read_cached_frames(key)
    spline_rows = []
    curve_rows = []
    cache = "hit" if cached is not None else "miss"
    if cached is not None:
        spline = cached["spline"]
        curves = cached["curves"]
        print("spline cache: hit", flush=True)
    else:
        for variable in ("log_stock", "debt_age"):
            for segment, part in frame.groupby("segment"):
                fit_rows = stratified_sample(part, SPLINE_CAP, ["paid_flag"], seed)
                compared = spline_compare(fit_rows, variable)
                spline_rows.append(
                    {
                        "variable": variable,
                        "segment": str(segment),
                        "delta_aic": compared["delta_aic"],
                        "lr": compared["lr"],
                        "lr_df": compared["lr_df"],
                        "lr_p": compared["lr_p"],
                        "aic_linear": compared["aic_linear"],
                        "aic_spline": compared["aic_spline"],
                        "curved": compared["curved"],
                        "n": compared["n"],
                    }
                )
                if compared["grid_x"].size:
                    for model, eta in (
                        ("linear", compared["grid_eta_linear"]),
                        ("spline", compared["grid_eta_spline"]),
                    ):
                        curve_rows.append(
                            pd.DataFrame(
                                {
                                    "variable": variable,
                                    "segment": str(segment),
                                    "model": model,
                                    "x": compared["grid_x"],
                                    "eta": eta,
                                }
                            )
                        )
        spline = pd.DataFrame(spline_rows)
        curves = (
            pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame()
        )
        if not curves.empty:
            _write_cached_frames(key, {"spline": spline, "curves": curves})
        if curves.empty:
            curves = pd.DataFrame(columns=["variable", "segment", "model", "x", "eta"])
        if not spline.empty:
            _write_cached_frames(key, {"spline": spline, "curves": curves})
        print("spline cache: miss", flush=True)
    bins = pd.concat(
        [quantile_bins(frame, "log_stock"), quantile_bins(frame, "debt_age")],
        ignore_index=True,
    )
    figures = {}
    for variable in ("log_stock", "debt_age"):
        figures[f"logodds_{variable}"] = logodds_figure(bins, curves, variable)
        figure_html(figures[f"logodds_{variable}"])
    categories = pd.concat(
        [category_log_odds(frame, "porte"), category_log_odds(frame, "status")],
        ignore_index=True,
    )
    return {
        "spline": spline,
        "curves": curves,
        "bins": bins,
        "categories": categories,
        "porte_shape": porte_shape(frame),
        "figures": figures,
        "cache": cache,
        "seconds": _clock("splines", started),
    }


def block_groups(frame: pd.DataFrame, seed: int = 20261104) -> dict:
    """Group tables, funnel and latent ICC. The variational logit uses at most MIXED_CAP rows."""
    started = time.perf_counter()
    muni_table, muni_plot = group_tables(frame, ["municipality", "uf"])
    div_table, div_plot = group_tables(frame, ["division"])
    published = pd.concat([muni_table, div_table], ignore_index=True)
    plot_groups = pd.concat([muni_plot, div_plot], ignore_index=True)
    groups, group_counts = groups_for_page(published, plot_groups)
    figures = {
        "funnel_municipality": funnel_figure(groups, "municipality+uf"),
        "funnel_division": funnel_figure(groups, "division"),
    }
    icc_rows: list[dict] = []
    cache_flags = []
    funnels: dict[str, float] = {}
    for grouping, column in (
        ("municipality", "municipality"),
        ("division", "division"),
    ):
        slices = [
            ("pooled", frame),
            *[(str(s), p) for s, p in frame.groupby("segment")],
        ]
        for segment, part in slices:
            latent = latent_icc(part["paid_flag"], part[column])
            icc_rows.append(
                _icc_record(
                    latent,
                    grouping=grouping,
                    segment=segment,
                    outcome="paid_flag",
                    estimator="moments",
                    controls="none",
                )
            )
            for outcome in ("log_stock", "recovery"):
                classic = classical_icc(part[outcome], part[column])
                icc_rows.append(
                    _icc_record(
                        classic,
                        grouping=grouping,
                        segment=segment,
                        outcome=outcome,
                        estimator="anova",
                        controls="none",
                    )
                )
        share = funnel_outside_share(
            frame,
            ["municipality", "uf"] if grouping == "municipality" else ["division"],
        )
        funnels[grouping] = share
        moment = next(
            row["icc"]
            for row in icc_rows
            if row["grouping"] == grouping
            and row["segment"] == "pooled"
            and row["outcome"] == "paid_flag"
            and row["estimator"] == "moments"
        )
        capped = cluster_sample(frame, column, cap=MIXED_CAP, seed=seed)
        sig_cols = [
            "paid_flag",
            column,
            "log_stock",
            "debt_age",
            "segment",
            "section",
            "porte",
        ]
        key = frame_signature(
            capped, sig_cols, f"mixed|{grouping}|{MIXED_CAP}|{seed}|{moment:.6f}"
        )
        cached = _read_cached_frames(key)
        if cached is not None:
            stored = cached["mixed"]
            cache_flags.append("hit")
            print(f"mixed cache {grouping}: hit", flush=True)
        else:
            rows = []
            fitted = maybe_fit_mixed(
                moment,
                share,
                lambda capped=capped, column=column: fit_mixed_logit(
                    capped, column, controls=False
                ),
            )
            if fitted is not None:
                rows.append(fitted)
                if fitted.get("converged") and float(fitted["icc"]) >= ICC_CUT:
                    rows.append(fit_mixed_logit(capped, column, controls=True))
            if not rows:
                rows.append(
                    {
                        "controls": "none",
                        "tau": np.nan,
                        "tau2": np.nan,
                        "icc": np.nan,
                        "n": 0,
                        "converged": False,
                        "skipped": True,
                    }
                )
            stored = pd.DataFrame(rows)
            if "skipped" not in stored.columns:
                stored["skipped"] = False
            _write_cached_frames(key, {"mixed": stored})
            cache_flags.append("miss")
            print(f"mixed cache {grouping}: miss", flush=True)
        for fitted_row in stored.to_dict(orient="records"):
            if bool(fitted_row.get("skipped", False)):
                continue
            icc_rows.append(
                {
                    "grouping": grouping,
                    "segment": "pooled",
                    "outcome": "paid_flag",
                    "estimator": "variational",
                    "controls": fitted_row["controls"],
                    "tau2": fitted_row["tau2"],
                    "icc": fitted_row["icc"],
                    "n_groups": np.nan,
                    "n": fitted_row["n"],
                    "converged": fitted_row["converged"],
                }
            )
    for fig in figures.values():
        figure_html(fig)
    return {
        "groups": groups,
        "group_counts": group_counts,
        "icc": pd.DataFrame(icc_rows),
        "figures": figures,
        "cache": "hit"
        if cache_flags and all(flag == "hit" for flag in cache_flags)
        else "miss",
        "funnel_municipality": funnels.get("municipality", float("nan")),
        "funnel_division": funnels.get("division", float("nan")),
        "seconds": _clock("icc-mixed", started),
    }


def assemble_decision(
    ols: dict, splines: dict, groups: dict, top: dict, n_estimation: int
) -> pd.DataFrame:
    spline = splines["spline"]
    icc = groups["icc"]
    checks = {**ols["checks"], **top["checks"]}

    def _pull(outcome: str, sample_name: str, field: str) -> float:
        found = checks.get((outcome, sample_name))
        if not found:
            return float("nan")
        return float(found[field])

    moment = icc.loc[
        (icc["outcome"] == "paid_flag")
        & (icc["estimator"] == "moments")
        & icc["segment"].isin(["declared", "assessed"])
    ]
    icc_by_segment = [
        {
            "segment": str(row.segment),
            "grouping": "municipality"
            if row.grouping == "municipality"
            else "CNAE division",
            "icc": float(row.icc),
        }
        for row in moment.itertuples(index=False)
    ]
    support = icc.loc[
        (icc["estimator"] == "variational")
        & (icc["controls"] == "none")
        & (icc["segment"] == "pooled")
        & icc["converged"].eq(True)
    ]
    variational_note = "; ".join(
        f"{row.grouping} variational ICC = {_fmt(row.icc, 3)} on n = {int(row.n)}"
        for row in support.itertuples(index=False)
    )

    spline_rows = []
    if spline is not None and not spline.empty:
        for record in spline.itertuples(index=False):
            spline_rows.append(
                {
                    "segment": str(record.segment),
                    "variable": str(record.variable),
                    "delta_aic": float(record.delta_aic),
                    "curved": bool(record.curved),
                }
            )
    funnel_muni = float(groups.get("funnel_municipality", float("nan")))
    funnel_div = float(groups.get("funnel_division", float("nan")))
    evidence = {
        "spline_by_segment": spline_rows,
        "delta_r2_stock": _pull("log_stock", "full", "delta_r2"),
        "fan_stock": _pull("log_stock", "full", "fan"),
        "delta_r2_stock_trim": _pull("log_stock", "trim", "delta_r2"),
        "fan_stock_trim": _pull("log_stock", "trim", "fan"),
        "delta_r2_recovery": _pull("recovery", "full", "delta_r2"),
        "fan_recovery": _pull("recovery", "full", "fan"),
        "icc_by_segment": icc_by_segment,
        "variational_note": variational_note,
        "funnel_municipality": funnel_muni,
        "funnel_division": funnel_div,
        "n_estimation": n_estimation,
    }
    return inference_decision(evidence)


def run_diagnostics(
    raw: pd.DataFrame, sample_cap: int = SAMPLE_CAP, seed: int = 20261104
) -> dict:
    """Fit the diagnostic on an in-memory CDA table and return tables plus figures.

    The page calls the same blocks from separate cells. This wrapper keeps the tests on one entry point.
    """
    started = time.perf_counter()
    frame = prepare(raw)
    n_before = len(frame)
    frame = stratified_sample(
        frame, sample_cap, ["segment", "porte", "paid_flag"], seed
    )
    n_after = len(frame)
    print(f"complete cases before the estimation cap: {n_before}", flush=True)
    print(f"estimation sample: {n_after}", flush=True)
    ols = block_ols_reset(frame, "full", with_quantiles=True, seed=seed)
    splines = block_splines(frame, seed=seed)
    groups = block_groups(frame, seed=seed)
    top = block_ols_reset(
        without_top_share(frame).reset_index(drop=True), "trim", seed=seed
    )
    print(f"without the top 1 percent: {top['n']}", flush=True)
    decision = assemble_decision(ols, splines, groups, top, n_after)
    figures = {}
    for block in (ols, splines, groups, top):
        figures.update(block["figures"])
    coefficients = pd.concat(
        [ols["coefficients"], top["coefficients"]], ignore_index=True
    )
    reset = pd.concat([ols["reset"], top["reset"]], ignore_index=True)
    uf_share = (
        float(frame["uf"].astype(str).str.upper().eq("SP").mean())
        if len(frame)
        else float("nan")
    )
    _clock("all blocks", started)
    return {
        "coefficients": coefficients,
        "reset": reset,
        "bins": splines["bins"],
        "spline": splines["spline"],
        "categories": splines["categories"],
        "porte_shape": splines["porte_shape"],
        "groups": groups["groups"],
        "group_counts": groups["group_counts"],
        "icc": groups["icc"],
        "quantiles": ols["quantiles"],
        "decision": decision,
        "figures": figures,
        "meta": {
            "n_before": n_before,
            "n_after": n_after,
            "n_trim": int(top["n"]),
            "uf_sp_share": uf_share,
            "stock_formula": model_formula("log_stock", frame),
            "recovery_formula": model_formula("recovery", frame),
            "seconds": {
                "ols_reset": ols["seconds"],
                "splines": splines["seconds"],
                "icc_mixed": groups["seconds"],
                "top1": top["seconds"],
            },
        },
    }
