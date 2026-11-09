"""End-to-end demo on synthetic data. No network, no credentials.

1. Backtest an illustrative strategy and put its trades through the honest gate.
2. Gate a book that only looks profitable because its winners rode fills that
   one-minute data cannot order.
3. Replay three execution failure modes against the simulated broker.

Run:  python scripts/demo.py
"""
import numpy as np
import pandas as pd

from fxlab.data.synthetic import minute_bars
from fxlab.execution.broker import BUY, SimBroker
from fxlab.execution.bracket import BracketManager
from fxlab.research.honest_gate import run_gate
from fxlab.strategies.example_retest import backtest


def gate_demo() -> None:
    bars = minute_bars(days=500, seed=11)
    trades = backtest(bars)
    print(f"Illustrative backtest: {len(trades)} trades, booked net {trades.pnl.sum():+,.0f}\n")
    report = run_gate(trades, tick_value=0.5, ticks_per_point=4, sims=5000, permutations=5000)
    print(report.table())


def fabricated_edge_demo() -> None:
    rng = np.random.default_rng(5)
    n = 600
    pnl = rng.normal(9.0, 45.0, n)
    trades = pd.DataFrame({
        "date": pd.bdate_range("2023-01-02", periods=n).strftime("%Y-%m-%d"),
        "direction": "long", "pnl": pnl,
        "same_bar_entry_stop": False,
        # most of the winners filled on a bar that also touched the target
        "same_bar_entry_target": (pnl > 0) & (rng.random(n) < 0.7),
    })
    print(f"\nBook that looks profitable: booked net {pnl.sum():+,.0f}\n")
    print(run_gate(trades, tick_value=0.5, sims=5000, permutations=5000).table())


def scenario(name, broker, prices, **kw):
    m = BracketManager(broker, "demo", BUY, 2, entry=100.0, stop=95.0, target=110.0, **kw)
    m.start()
    for p in prices:
        broker.set_price(p)
        m.on_update()
    print(f"\n[{name}] final net position: {broker.net_position()}")
    for a in m.alerts:
        print(f"  {a.level.upper():<7}{a.message}")


def execution_demo() -> None:
    scenario("stop rejected on a fast reversal",
             SimBroker(price=102.0, reject_tags={"demo-sl", "demo-slx"}), [100.0, 94.0])
    scenario("break-even move silently ignored",
             SimBroker(price=102.0, ignore_modify_tags={"demo-sl"}), [100.0, 103.0, 99.75],
             be_trigger_r=0.5)
    scenario("sibling cancel refused after a fast target fill",
             SimBroker(price=102.0, uncancellable_tags={"demo-sl"}), [100.0, 110.0, 108.0, 108.0],
             max_cancel_retries=2)


if __name__ == "__main__":
    gate_demo()
    fabricated_edge_demo()
    execution_demo()
