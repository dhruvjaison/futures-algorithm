"""A textbook opening-range retest, used only to exercise the pipeline.

This is NOT a live strategy and its parameters are arbitrary. It exists to show
how a strategy engine emits the fill-provenance columns the honest gate needs.

Rules: take the high and low of the first `range_minutes`. When a later bar
breaks above the range, rest a buy limit back at the range high (a retest), with
the stop at the range low and the target at `reward_r` times the risk. Mirror for
shorts. One trade per day, flat at the close.

Deliberately, the backtest books the OPTIMISTIC ordering when a bar is ambiguous
(fills first, target first). That is the classic mistake; the honest gate is
supposed to catch it, and the demo shows it doing so.
"""
from __future__ import annotations

import pandas as pd

from ..research.honest_gate import classify_entry_ambiguous, classify_exit_ambiguous


def backtest(bars: pd.DataFrame, range_minutes: int = 15, reward_r: float = 1.5,
             point_value: float = 2.0) -> pd.DataFrame:
    trades = []
    for day, g in bars.groupby(bars.ts.dt.date):
        g = g.reset_index(drop=True)
        rng = g.iloc[:range_minutes]
        hi, lo = rng.high.max(), rng.low.min()
        rest = g.iloc[range_minutes:].reset_index(drop=True)
        side = None
        for i, b in rest.iterrows():
            if b.high > hi:
                side, entry, stop = "long", hi, lo
            elif b.low < lo:
                side, entry, stop = "short", lo, hi
            if side:
                start = i + 1
                break
        if not side:
            continue
        risk = abs(entry - stop)
        target = entry + reward_r * risk if side == "long" else entry - reward_r * risk
        after = rest.iloc[start:].reset_index(drop=True)
        fill_idx = None
        for j, b in after.iterrows():
            touched = b.low <= entry if side == "long" else b.high >= entry
            if touched:
                fill_idx = j
                break
        if fill_idx is None:
            continue
        fb = after.iloc[fill_idx]
        es, et = classify_entry_ambiguous(side, entry, stop, target, fb.high, fb.low)
        path = after.iloc[fill_idx:]
        amb_exit = classify_exit_ambiguous(side, stop, target, path.high, path.low)
        exit_px, outcome = float(path.close.iloc[-1]), "close"
        for _, b in path.iterrows():
            hit_tp = b.high >= target if side == "long" else b.low <= target
            hit_sl = b.low <= stop if side == "long" else b.high >= stop
            if hit_tp:                       # optimistic: target checked first
                exit_px, outcome = target, "tp"
                break
            if hit_sl:
                exit_px, outcome = stop, "sl"
                break
        pts = (exit_px - entry) if side == "long" else (entry - exit_px)
        trades.append(dict(date=str(day), direction=side, entry=entry, stop=stop,
                           target=target, exit=exit_px, outcome=outcome,
                           pnl=pts * point_value, same_bar_entry_stop=es,
                           same_bar_entry_target=et, same_bar_exit_ambiguous=amb_exit))
    return pd.DataFrame(trades)
