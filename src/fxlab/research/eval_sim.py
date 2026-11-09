"""Prop-firm evaluation simulator: would a strategy PASS, or BUST, at a given size?

Funded-evaluation accounts have two rules that matter: reach a profit TARGET to
pass, and never let equity fall a fixed amount below its running PEAK (a trailing
max drawdown) or the account is closed. Many also have a DAILY loss limit that
locks trading for the rest of the session without closing the account.

This bootstraps the per-trade P&L into many attempts and reports pass / bust /
timeout rates per contract size, which is how position size was chosen before
risking a real evaluation fee. Rule values here are illustrative; real figures
vary by provider and must be confirmed before use.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EvalRules:
    target: float              # profit needed to pass
    trailing_dd: float         # max drop from peak equity before the account busts
    horizon_trades: int = 250  # attempts that neither pass nor bust within this time out


@dataclass(frozen=True)
class EvalResult:
    contracts: int
    p_pass: float
    p_bust: float
    p_timeout: float
    median_trades_to_pass: float


def simulate(pnl_one_contract, contracts: int, rules: EvalRules,
             sims: int = 20_000, seed: int = 7) -> EvalResult:
    """Vectorized: sample `sims` trade paths at `contracts` size and apply the rules.

    The first event wins: a path that busts before it passes is a bust, even if it
    would have recovered later. That is how the real accounts behave.
    """
    p = np.asarray(pnl_one_contract, dtype=float)
    if p.size == 0:
        raise ValueError("empty P&L series")
    rng = np.random.default_rng(seed)
    samp = p[rng.integers(0, p.size, size=(sims, rules.horizon_trades))] * contracts
    eq = samp.cumsum(axis=1)
    peak = np.maximum.accumulate(np.maximum(eq, 0.0), axis=1)
    busted = (peak - eq) >= rules.trailing_dd
    passed = eq >= rules.target
    never = rules.horizon_trades + 1
    first_bust = np.where(busted.any(axis=1), busted.argmax(axis=1), never)
    first_pass = np.where(passed.any(axis=1), passed.argmax(axis=1), never)
    is_pass = first_pass < first_bust
    is_bust = first_bust < first_pass
    med = float(np.median(first_pass[is_pass]) + 1) if is_pass.any() else float("nan")
    return EvalResult(
        contracts=contracts,
        p_pass=float(is_pass.mean()),
        p_bust=float(is_bust.mean()),
        p_timeout=float((~is_pass & ~is_bust).mean()),
        median_trades_to_pass=med,
    )


def size_table(pnl_one_contract, rules: EvalRules, sizes=(1, 2, 3, 4),
               sims: int = 20_000) -> list[EvalResult]:
    """Pass/bust rates across contract sizes, e.g. to pick the largest size with ~0% bust."""
    return [simulate(pnl_one_contract, c, rules, sims=sims, seed=7 + c) for c in sizes]
