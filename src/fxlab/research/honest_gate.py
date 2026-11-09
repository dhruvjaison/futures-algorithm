"""The honest gate: a kill-test battery every candidate strategy must pass before it
can trade real money.

It works on realized per-trade rows, not on a strategy engine, so any strategy
can be put through the same bar. Required columns:

    date, direction, pnl            (pnl in account currency, one contract)

Optional columns unlock more checks:

    entry, stop, target                         -> fill stress in ticks
    outcome                                     -> break-even detection
    same_bar_exit_ambiguous                     -> check: same-bar exit ordering
    same_bar_entry_stop, same_bar_entry_target  -> check: entry-fill ordering

Every check returns PASS / WARN / FAIL / SKIP. The verdict is:

    KILL            a critical check failed
    NEEDS-WORK      anything warned, failed, or a critical check could not run
    LIVE-CANDIDATE  everything critical passed

Why these checks exist: in the private system, several backtests that looked
excellent turned out to be artifacts. The most common cause was fill ordering
that one-minute bars cannot resolve. If a single bar touches both the entry and
the target, the data cannot prove the order filled before price ran to the
target; the "winner" may never have existed. The two ambiguity checks below were
written after those failures, and each is the twin of the other: one looks at the
exit bar, the other at the fill bar itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .metrics import profit_factor
from .monte_carlo import bootstrap

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# Above this share of entry+stop collisions, one-minute data cannot order the fills
# for enough of the book that no profit factor may honestly be quoted.
UNSCOREABLE_COLLISION_PCT = 25.0

CRITICAL = {
    "per-year stability", "permutation", "walk-forward", "monte carlo",
    "fill stress", "same-bar exit", "entry-fill ordering",
}
# Shown on the scorecard but never allowed to change the verdict.
ADVISORY = {"adverse fill"}


@dataclass
class Result:
    name: str
    status: str
    detail: str


@dataclass
class GateReport:
    verdict: str
    results: list[Result] = field(default_factory=list)
    reasons: list[Result] = field(default_factory=list)

    def table(self) -> str:
        lines = [f"{'CHECK':<22}{'STATUS':<7}DETAIL"]
        lines += [f"{r.name:<22}{r.status:<7}{r.detail}" for r in self.results]
        lines.append(f"VERDICT: {self.verdict}")
        return "\n".join(lines)


# --------------------------------------------------------------- geometry helpers
def classify_exit_ambiguous(direction: str, stop: float, target: float,
                            highs, lows) -> bool:
    """Scan bars from the fill onward. If the FIRST bar that reaches the stop or the
    target also reaches the other level, the intrabar order is unknown and the
    backtest silently picked one. Strategy engines call this to emit
    `same_bar_exit_ambiguous`."""
    long_ = str(direction).lower().startswith("l")
    for hi, lo in zip(highs, lows):
        hit_tp = hi >= target if long_ else lo <= target
        hit_sl = lo <= stop if long_ else hi >= stop
        if hit_tp or hit_sl:
            return bool(hit_tp and hit_sl)
    return False


def classify_entry_ambiguous(direction: str, entry: float, stop: float, target: float,
                             bar_high: float, bar_low: float) -> tuple[bool, bool]:
    """Within the single bar a resting limit filled in, can the data prove the fill
    came before an exit level was reached?

    Returns (entry_stop, entry_target):
      entry_stop    the fill bar also spans the stop: the loss may be real, but the
                    sequence is unknown.
      entry_target  the fill bar also spans the target: the booked winner may be
                    fabricated, because the run to target can precede the fill.

    Only meaningful for orders the market came TO (resting limits). Orders traded
    THROUGH (stops, breakouts) have no ambiguity, which is why the engine, which
    knows the order type, computes this rather than the gate.
    """
    long_ = str(direction).lower().startswith("l")
    if long_:
        entry_stop = bar_high >= entry and bar_low <= stop
        entry_target = bar_low <= entry and bar_high >= target
    else:
        entry_stop = bar_low <= entry and bar_high >= stop
        entry_target = bar_high >= entry and bar_low <= target
    return bool(entry_stop), bool(entry_target)


def sign_permutation_pvalue(pnl, n: int = 10_000, seed: int = 20260708) -> tuple[float, float]:
    """Placebo: if the strategy has no directional edge, each trade's sign is a coin
    flip. Flip signs n times and ask how often the shuffled mean beats the real one."""
    p = np.asarray(pnl, dtype=float)
    obs = float(p.mean())
    rng = np.random.default_rng(seed)
    mag = np.abs(p)
    hits, done = 0, 0
    while done < n:
        k = min(2000, n - done)
        means = (rng.choice([-1.0, 1.0], size=(k, p.size)) * mag).mean(axis=1)
        hits += int((means >= obs).sum())
        done += k
    return obs, (hits + 1) / (n + 1)


# --------------------------------------------------------------------- checks
def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["pnl"] = pd.to_numeric(d["pnl"], errors="coerce")
    d = d[d["pnl"].notna()].copy()
    d["date"] = d["date"].astype(str)
    d["year"] = d["date"].str[:4]
    return d.sort_values("date", kind="stable").reset_index(drop=True)


def check_per_year(d: pd.DataFrame) -> Result:
    rows = [(y, len(g), g.pnl.sum(), profit_factor(g.pnl)) for y, g in d.groupby("year")]
    detail = "  ".join(f"{y}:{net:+,.0f}/PF{pf_:.2f}" for y, _, net, pf_ in rows)
    bad = [y for y, n, net, _ in rows if net <= 0 and n >= 10]
    thin = [y for y, n, net, _ in rows if net <= 0 and n < 10]
    if bad:
        return Result("per-year stability", FAIL, f"losing year(s) {','.join(bad)} | {detail}")
    if thin:
        return Result("per-year stability", WARN, f"thin losing year(s) | {detail}")
    return Result("per-year stability", PASS, detail)


def check_permutation(d: pd.DataFrame, n: int) -> Result:
    obs, p = sign_permutation_pvalue(d.pnl, n=n)
    status = PASS if p < 0.05 else WARN if p < 0.15 else FAIL
    return Result("permutation", status, f"mean {obs:+.2f}/trade, p={p:.4f} ({n:,} sign shuffles)")


def check_walk_forward(d: pd.DataFrame, frac: float = 0.65) -> Result:
    k = int(len(d) * frac)
    if k < 10 or len(d) - k < 10:
        return Result("walk-forward", SKIP, "too few trades to split")
    train, oos = d.pnl.iloc[:k], d.pnl.iloc[k:]
    pf_oos = profit_factor(oos)
    detail = (f"train {train.sum():+,.0f}/PF{profit_factor(train):.2f} | "
              f"out-of-sample {oos.sum():+,.0f}/PF{pf_oos:.2f}")
    if oos.sum() <= 0 or pf_oos < 1.0:
        return Result("walk-forward", FAIL, detail)
    if pf_oos < 1.2:
        return Result("walk-forward", WARN, detail)
    return Result("walk-forward", PASS, detail)


def check_monte_carlo(d: pd.DataFrame, sims: int) -> Result:
    if len(d) < 30:
        return Result("monte carlo", SKIP, "n<30")
    iid = bootstrap(d.pnl, sims=sims, block=1, seed=1)
    blk = bootstrap(d.pnl, sims=sims, block=10, seed=2)
    p_neg = max(iid.p_negative, blk.p_negative) * 100
    detail = (f"P(final<0)={p_neg:.1f}%  p95 maxDD iid {iid.p95_max_dd:,.0f} "
              f"/ block {blk.p95_max_dd:,.0f}")
    if p_neg >= 10:
        return Result("monte carlo", FAIL, detail)
    if p_neg >= 1:
        return Result("monte carlo", WARN, detail)
    return Result("monte carlo", PASS, detail)


def check_fill_stress(d: pd.DataFrame, tick_value: float, slip_ticks=(1, 2)) -> Result:
    """Charge extra slippage on every trade, in TICKS of the instrument (a flat
    'one point' is two dollars on one contract and a hundred on another), and
    separately drop the best 5% of winners."""
    pnl = d.pnl.to_numpy(float)
    keep = np.sort(pnl)[: len(pnl) - max(1, int(len(pnl) * 0.05))]
    nets = {t: (pnl - t * tick_value).sum() for t in slip_ticks}
    pfs = {t: profit_factor(pnl - t * tick_value) for t in slip_ticks}
    lo, hi = min(slip_ticks), max(slip_ticks)
    detail = " | ".join(f"+{t}tk: {nets[t]:+,.0f}/PF{pfs[t]:.2f}" for t in slip_ticks)
    detail += f" | drop best 5%: {keep.sum():+,.0f}/PF{profit_factor(keep):.2f}"
    if nets[lo] <= 0 or keep.sum() <= 0:
        return Result("fill stress", FAIL, "edge flips negative | " + detail)
    if nets[hi] <= 0 or pfs[lo] < 1.2 or profit_factor(keep) < 1.2:
        return Result("fill stress", WARN, "cost-fragile | " + detail)
    return Result("fill stress", PASS, detail)


def check_adverse_fill(d: pd.DataFrame, fill_rate: float = 0.5) -> Result:
    """ADVISORY. Resting limits get filled more often on losers than winners. Assume
    the worst: the trades that did not fill were the winners. Live fill rates on
    resting-limit books in the private system came in near half the backtest's,
    which is why this check exists. It warns but never kills; only real fills
    settle it."""
    pnl = np.sort(d.pnl.to_numpy(float))
    if len(pnl) < 10:
        return Result("adverse fill", SKIP, "n<10")
    worst = pnl[: max(1, int(round(len(pnl) * fill_rate)))]
    detail = f"{fill_rate:.0%} fill, unfilled = winners: net {worst.sum():+,.0f}, PF {profit_factor(worst):.2f}"
    return Result("adverse fill", WARN if worst.sum() <= 0 else PASS, detail)


def check_same_bar_exit(d: pd.DataFrame) -> Result:
    if "same_bar_exit_ambiguous" not in d:
        return Result("same-bar exit", SKIP, "engine did not emit same_bar_exit_ambiguous")
    amb = d.same_bar_exit_ambiguous.astype(bool).to_numpy()
    pnl = d.pnl.to_numpy(float)
    booked = pnl.sum()
    amb_win = pnl[amb & (pnl > 0)].sum()
    share = amb_win / booked * 100 if booked > 0 else 0.0
    detail = f"{amb.mean()*100:.1f}% of trades ambiguous, {share:.0f}% of net P&L rides on them"
    if booked > 0 and booked - amb_win <= 0:
        return Result("same-bar exit", FAIL, "edge is an ordering artifact | " + detail)
    if share >= 25:
        return Result("same-bar exit", WARN, detail)
    return Result("same-bar exit", PASS, detail)


def check_entry_fill(d: pd.DataFrame, tick_value: float, ticks_per_point: float) -> Result:
    """Entry-side ambiguity. Winners whose fill bar also reached the target are
    re-priced on a NEUTRAL 50/50 bound: in the private system, a tick-level replay
    of ambiguous trades resolved to the target 46% of the time, so 0.5 is a
    calibrated guess, not an arbitrary one. Booked and worst-case are reported too.
    """
    cols = {"same_bar_entry_stop", "same_bar_entry_target"}
    if not cols <= set(d.columns):
        return Result("entry-fill ordering", SKIP,
                      "engine did not emit same_bar_entry_stop / same_bar_entry_target")
    es = d.same_bar_entry_stop.astype(bool).to_numpy()
    et = d.same_bar_entry_target.astype(bool).to_numpy()
    pnl = d.pnl.to_numpy(float)
    booked = pnl.sum()
    es_pct, et_pct = es.mean() * 100, et.mean() * 100

    amb_win = pnl[(pnl > 0) & (et | es)].sum()
    neutral = booked - 0.5 * amb_win
    worst = 0.0
    for i, p in enumerate(pnl):
        if p > 0 and et[i]:
            continue                                  # treated as never filled
        if p > 0 and es[i] and {"entry", "stop"} <= set(d.columns):
            row = d.iloc[i]
            long_ = str(row.direction).lower().startswith("l")
            pts = (row.stop - row.entry) if long_ else (row.entry - row.stop)
            worst += pts * ticks_per_point * tick_value  # booked as the stop-out
        else:
            worst += p
    detail = (f"entry+stop {es_pct:.1f}%, entry+target {et_pct:.1f}% | booked {booked:+,.0f} "
              f"-> neutral {neutral:+,.0f} -> worst {worst:+,.0f}")
    if es_pct > UNSCOREABLE_COLLISION_PCT:
        return Result("entry-fill ordering", FAIL, "not scoreable at 1-minute resolution | " + detail)
    if not (es.any() or et.any()):
        return Result("entry-fill ordering", PASS, "no fill-bar collisions | " + detail)
    if neutral <= 0:
        return Result("entry-fill ordering", FAIL, "edge does not survive a coin flip | " + detail)
    if worst <= 0 or es_pct >= 10 or et_pct >= 10:
        return Result("entry-fill ordering", WARN, detail)
    return Result("entry-fill ordering", PASS, detail)


# --------------------------------------------------------------------- driver
def verdict(results: list[Result]) -> tuple[str, list[Result]]:
    scored = [r for r in results if r.name not in ADVISORY]
    crit_fail = [r for r in scored if r.status == FAIL and r.name in CRITICAL]
    if crit_fail:
        return "KILL", crit_fail
    issues = [r for r in scored if r.status in (FAIL, WARN)
              or (r.status == SKIP and r.name in CRITICAL)]
    if issues:
        return "NEEDS-WORK", issues
    return "LIVE-CANDIDATE", []


def run_gate(trades: pd.DataFrame, tick_value: float, ticks_per_point: float = 4.0,
             sims: int = 20_000, permutations: int = 10_000) -> GateReport:
    d = _prep(trades)
    results = [
        check_per_year(d),
        check_permutation(d, permutations),
        check_walk_forward(d),
        check_monte_carlo(d, sims),
        check_fill_stress(d, tick_value),
        check_adverse_fill(d),
        check_same_bar_exit(d),
        check_entry_fill(d, tick_value, ticks_per_point),
    ]
    v, reasons = verdict(results)
    return GateReport(v, results, reasons)
