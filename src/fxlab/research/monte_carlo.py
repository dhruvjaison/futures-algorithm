"""Bootstrap Monte Carlo on a trade sequence.

An IID bootstrap shuffles trades independently. That is useful for "how likely is
a losing year by sequence luck alone", but it UNDER-states drawdown because real
losing streaks cluster. The block bootstrap keeps runs of consecutive trades
together, so streaks survive resampling. The private system additionally fits a
2-state hidden Markov model for regime persistence; that is omitted here to keep
the dependency list small, and the block bootstrap is the documented stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MCSummary:
    sims: int
    p_negative: float          # share of paths that end below zero
    median_final: float
    p05_final: float
    median_max_dd: float
    p95_max_dd: float


def _paths(pnl: np.ndarray, sims: int, rng: np.random.Generator, block: int) -> np.ndarray:
    n = pnl.size
    if block <= 1:
        return pnl[rng.integers(0, n, size=(sims, n))]
    n_blocks = -(-n // block)
    starts = rng.integers(0, n, size=(sims, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return pnl[idx.reshape(sims, -1)[:, :n]]


def bootstrap(pnl, sims: int = 20_000, block: int = 1, seed: int = 0) -> MCSummary:
    """Resample the trade sequence `sims` times and summarize final P&L and drawdown.

    block=1 is a plain IID bootstrap; block>1 is a circular block bootstrap.
    Deterministic for a given seed.
    """
    p = np.asarray(pnl, dtype=float)
    if p.size < 2:
        raise ValueError("need at least 2 trades")
    rng = np.random.default_rng(seed)
    paths = _paths(p, sims, rng, block)
    eq = paths.cumsum(axis=1)
    peak = np.maximum.accumulate(np.maximum(eq, 0.0), axis=1)
    mdd = (peak - eq).max(axis=1)
    final = eq[:, -1]
    return MCSummary(
        sims=sims,
        p_negative=float((final < 0).mean()),
        median_final=float(np.median(final)),
        p05_final=float(np.percentile(final, 5)),
        median_max_dd=float(np.median(mdd)),
        p95_max_dd=float(np.percentile(mdd, 95)),
    )
