"""Checks for the DM-HLN statistic and the model confidence set."""

from __future__ import annotations

import numpy as np
from forecast_compare import diebold_mariano_hln, model_confidence_set, smape_loss


def test_hln_factor_matches_one_step_formula() -> None:
    rng = np.random.default_rng(0)
    noise = rng.normal(scale=0.2, size=40)
    loss_model = rng.normal(size=40)
    loss_ref = loss_model + 1.0 + noise
    result = diebold_mariano_hln(loss_model, loss_ref, h=1)
    assert result.n == 40
    assert result.better == "model"
    assert result.p_value < 0.01
    assert abs(result.dm_hln - result.dm * np.sqrt(39 / 40)) < 1e-9


def test_hln_rejects_degenerate_horizon() -> None:
    loss = np.ones(12)
    try:
        diebold_mariano_hln(loss, loss, h=12)
    except ValueError:
        return
    raise AssertionError("h=n must be rejected")


def test_mcs_drops_a_worse_model() -> None:
    rng = np.random.default_rng(1)
    n = 80
    base = rng.normal(size=n)
    losses = {
        "a": np.abs(base),
        "b": np.abs(base + rng.normal(scale=0.05, size=n)),
        "bad": np.abs(base) + 3.0,
    }
    result = model_confidence_set(losses, alpha=0.10, bootstrap=199, seed=1)
    assert "bad" not in result.included
    assert result.p_values["bad"] < 0.10
    assert set(result.included) <= {"a", "b"}


def test_mcs_keeps_equal_models() -> None:
    rng = np.random.default_rng(2)
    base = np.abs(rng.normal(size=60))
    losses = {"a": base, "b": base.copy(), "c": base + rng.normal(scale=0.01, size=60)}
    result = model_confidence_set(losses, alpha=0.10, bootstrap=199, seed=2)
    assert set(result.included) == {"a", "b", "c"}


def test_smape_loss_is_zero_when_forecast_matches() -> None:
    actual = np.array([10.0, 0.0, 5.0])
    loss = smape_loss(actual, actual)
    assert loss[0] == 0.0
    assert loss[1] == 0.0
    assert loss[2] == 0.0
