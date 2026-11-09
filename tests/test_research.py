import numpy as np
import pytest

from fxlab.research.eval_sim import EvalRules, simulate, size_table
from fxlab.research.metrics import calmar, max_drawdown, profit_factor
from fxlab.research.monte_carlo import bootstrap


def test_profit_factor_and_drawdown():
    p = [10, -5, 10, -5]
    assert profit_factor(p) == 2.0
    assert max_drawdown([5, -3, -4, 10]) == 7.0
    assert profit_factor([1, 2]) == float("inf")
    assert np.isnan(profit_factor([]))
    assert calmar([10, -5, 10], years=1.0) == pytest.approx(3.0)


def test_drawdown_counts_losses_from_zero():
    assert max_drawdown([-5, -5]) == 10.0


def test_bootstrap_is_deterministic_and_block_widens_tails():
    rng = np.random.default_rng(3)
    # clustered losses: a block bootstrap should see deeper drawdowns than IID
    p = np.concatenate([rng.normal(2, 5, 200), np.full(20, -15.0), rng.normal(2, 5, 200)])
    a = bootstrap(p, sims=3000, seed=1)
    b = bootstrap(p, sims=3000, seed=1)
    assert a == b
    blk = bootstrap(p, sims=3000, block=20, seed=1)
    assert blk.p95_max_dd > a.p95_max_dd


def test_eval_sim_extremes():
    rules = EvalRules(target=1000, trailing_dd=500, horizon_trades=50)
    assert simulate([100.0], 1, rules).p_pass == 1.0
    assert simulate([-100.0], 1, rules).p_bust == 1.0


def test_bigger_size_busts_more():
    rng = np.random.default_rng(0)
    p = rng.normal(5, 60, 500)
    rules = EvalRules(target=3000, trailing_dd=2000)
    rows = size_table(p, rules, sizes=(1, 4), sims=4000)
    assert rows[1].p_bust > rows[0].p_bust
