"""Pure performance metrics on a per-trade P&L series (fixed size, no compounding)."""
from __future__ import annotations

import numpy as np


def profit_factor(pnl) -> float:
    """Gross win / gross loss. inf if there are no losses, nan if empty."""
    p = np.asarray(pnl, dtype=float)
    if p.size == 0:
        return float("nan")
    gross_win = p[p > 0].sum()
    gross_loss = -p[p < 0].sum()
    return float(gross_win / gross_loss) if gross_loss > 0 else float("inf")


def max_drawdown(pnl) -> float:
    """Largest peak-to-trough drop of the cumulative equity curve, in P&L units."""
    eq = np.cumsum(np.asarray(pnl, dtype=float))
    if eq.size == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float((peak - eq).max())


def calmar(pnl, years: float) -> float:
    """Annualized P&L divided by max drawdown. With fixed size this is size-independent."""
    if years <= 0:
        raise ValueError("years must be positive")
    p = np.asarray(pnl, dtype=float)
    mdd = max_drawdown(p)
    ann = p.sum() / years
    return float(ann / mdd) if mdd > 0 else float("inf")


def expectancy(pnl) -> float:
    p = np.asarray(pnl, dtype=float)
    return float(p.mean()) if p.size else float("nan")
