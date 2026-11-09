# Research method

The most expensive mistake in systematic trading is believing a backtest. This is the process used
to decide what gets to trade.

## 1. Treat every good number as a suspect

Several candidates in the private research log produced excellent backtests and turned out to be
artifacts. The causes, in order of how often they appeared:

1. **Fill ordering that one-minute bars cannot resolve.** If a single bar contains both the entry and
   the target, the data cannot prove the limit filled before price reached the target. The backtest
   books a win; reality may have been a missed trade.
2. **Exit ordering inside one bar.** A bar that touches the stop and the target forces the backtest to
   pick one. Engines that check the target first quietly inflate win rates.
3. **Break-even accounting.** Moving the stop to entry turns would-be losers into zero-P&L scratches,
   which shrinks gross loss and inflates the profit factor without adding edge.
4. **Fill rate.** Resting limits fill more often on losers than winners. Live fill rates on such books
   came in near half of what the backtest assumed.

## 2. The gate

Every candidate runs through the same battery (`honest_gate.py`). A check either passes, warns, fails
or is skipped, and skipping a critical check is never a pass.

| Check | Question it answers |
|---|---|
| Per-year stability | Is it positive in every year with meaningful sample size? |
| Sign permutation | If each trade's sign were a coin flip, how often would we see this mean? |
| Walk-forward | Does the later, untouched part of history agree with the earlier part? |
| Monte Carlo | IID and block bootstrap: how likely is a losing run, and how deep is the 95th-percentile drawdown? |
| Fill stress | Does it survive 1 and 2 extra ticks of slippage on every trade, in the instrument's own tick size? |
| Adverse fill *(advisory)* | If only the worse half of trades had filled, what is left? |
| Same-bar exit | How much P&L depends on an ordering the data cannot resolve? |
| Entry-fill ordering | Do the winners survive a neutral 50/50 call on unorderable fills? |

### Why 50/50

When a fill is ambiguous, assuming every one was a win is optimistic and assuming every one was a loss
is a floor, not an expectation. To pick a defensible middle, ambiguous trades from live books were
replayed against a recorded tick tape. They resolved to the target **46%** of the time (n=57). A naive
replay that ignored the break-even stop move read 75%; modeling the stop move is what brings it to a
coin flip. The gate therefore prices ambiguous winners at half, and reports the booked and worst-case
numbers beside it.

### Probe power

A check that never fires passes every study and protects nothing. Each check is tested on a
construction known to be fabricated, and must fire on it (`tests/test_honest_gate.py`). An earlier
version of the battery passed six test configurations whose entries collided with stops on 25-40% of trades;
the entry-fill check exists because of that.

### Regression

When a check changes, it is re-run over the full archive of past results (431 backtest files in the
private system). The change is accepted only if every existing verdict either stays the same or moves
for a reason that can be named.

## 3. Pre-registration

Before a study runs, its hypothesis, data window and kill criteria are written down. A result that
needed the criteria changed after the fact is reported as exploratory, not as a finding.

## 4. Live is the final test

The gate can only remove bad candidates. What survives is deployed at minimum size as a measurement:
every fill is logged with requested versus filled price, missed entries are scored after the fact
against what they would have done, and size only increases after at least 30 genuine live fills agree
with the backtest.
