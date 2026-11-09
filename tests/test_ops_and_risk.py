import time as _time
from datetime import date, time

from fxlab.data.synthetic import minute_bars
from fxlab.execution.risk import RiskGovernor
from fxlab.ops.calendar import SessionCalendar
from fxlab.ops.notify import Notifier
from fxlab.strategies.example_retest import backtest


def gov():
    return RiskGovernor(max_contracts=2, daily_loss_limit=500, trailing_drawdown=1000,
                        session_cutoff=time(15, 30))


def test_daily_limit_locks_session_then_resets():
    g = gov()
    g.record_trade(-300)
    assert g.can_enter(1, time(10))[0]
    g.record_trade(-250)
    assert g.can_enter(1, time(10)) == (False, "daily loss limit reached")
    g.new_session()
    assert g.can_enter(1, time(10))[0]


def test_trailing_drawdown_locks_account_permanently():
    g = gov()
    g.record_trade(800)
    g.new_session()
    g.record_trade(-400)
    g.new_session()
    g.record_trade(-450)
    g.new_session()
    g.record_trade(-200)
    assert not g.can_enter(1, time(10))[0] and g.account_locked


def test_contract_cap_and_cutoff():
    g = gov()
    assert g.can_enter(3, time(10))[0] is False
    assert g.can_enter(1, time(15, 45))[0] is False


def test_calendar_disarms_holidays_and_early_closes():
    cal = SessionCalendar(holidays={date(2026, 7, 3)},
                          early_closes={date(2026, 11, 27): time(13, 0)})
    assert cal.should_arm(date(2026, 7, 3))[0] is False
    assert cal.should_arm(date(2026, 7, 4))[0] is False          # Saturday
    assert cal.should_arm(date(2026, 11, 27))[0] is False
    assert cal.should_arm(date(2026, 11, 27), strategy_needs_full_day=False)[0]
    assert cal.should_arm(date(2026, 11, 30))[0]


def test_notifier_flushes_background_sends_before_exit():
    """Regression: a slow end-of-day push on a daemon thread was lost at exit."""
    sent = []

    def slow(title, body):
        _time.sleep(0.2)
        sent.append(title)

    n = Notifier(slow, timeout=2.0)
    n.send("EOD summary", "flat day")
    assert sent == []                 # still in flight
    assert n.close() == 0
    assert sent == ["EOD summary"]


def test_notifier_failure_never_raises():
    def broken(title, body):
        raise ConnectionError("push service down")

    n = Notifier(broken)
    n.send("x", "y", wait=True)
    assert n.failures and "push service down" in n.failures[0]


def test_example_strategy_emits_gate_columns():
    t = backtest(minute_bars(days=30, seed=4))
    assert len(t) > 5
    for col in ("same_bar_entry_stop", "same_bar_entry_target", "same_bar_exit_ambiguous"):
        assert col in t.columns
