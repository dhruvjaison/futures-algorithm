"""Bracket manager: each test reproduces a failure mode seen in production."""
from fxlab.execution.broker import BUY, CANCELLED, SELL, SimBroker, WORKING
from fxlab.execution.bracket import FLAT, LIVE, BracketManager


def long_bracket(broker, qty=2, **kw):
    m = BracketManager(broker, "t1", BUY, qty, entry=100.0, stop=95.0, target=110.0, **kw)
    m.start()
    return m


def step(broker, mgr, *prices):
    for p in prices:
        broker.set_price(p)
        mgr.on_update()


def urgent(mgr):
    return [a.message for a in mgr.alerts if a.level == "urgent"]


def test_happy_path_target_hit_and_stop_cancelled():
    b = SimBroker(price=102.0)
    m = long_bracket(b)
    step(b, m, 100.0)
    assert m.state == LIVE and m.position == 2
    step(b, m, 110.0)
    assert m.state == FLAT and b.net_position() == 0
    sl = [o for o in b.orders.values() if o.tag == "t1-sl"][0]
    assert sl.status == CANCELLED
    assert urgent(m) == []


def test_partial_fill_protection_sized_then_rearmed():
    b = SimBroker(price=102.0, partial_tags={"t1-entry": 1})
    m = long_bracket(b, qty=3)
    step(b, m, 100.0)
    tags = sorted(o.tag for o in b.orders.values() if o.status == WORKING and "sl" in o.tag)
    assert tags == ["t1-sl"] and m.position == 1          # protects only what filled
    step(b, m, 100.0)
    tags = sorted(o.tag for o in b.orders.values() if o.status == WORKING and "sl" in o.tag)
    assert tags == ["t1-r1-sl", "t1-sl"] and m.position == 2   # increment re-armed
    step(b, m, 100.0, 110.0)
    assert b.net_position() == 0 and m.state == FLAT


def test_rejected_stop_with_price_past_it_flattens_at_market():
    b = SimBroker(price=102.0, reject_tags={"t1-sl"})
    m = long_bracket(b)
    b.price = 94.0                       # fast reversal: fill, then price is through the stop
    b.set_price(100.0)
    b.price = 94.0
    m.on_update()
    assert b.net_position() == 0
    assert any("protective stop rejected" in u for u in urgent(m))


def test_rejected_stop_resubmitted_once_when_price_not_past():
    b = SimBroker(price=102.0, reject_tags={"t1-sl"})
    m = long_bracket(b)
    step(b, m, 100.0)
    assert any(o.tag == "t1-slx" and o.status == WORKING for o in b.orders.values())
    assert b.net_position() == 2 and urgent(m) == []
    step(b, m, 95.0)                     # resubmitted stop protects the position
    assert b.net_position() == 0


def test_stop_rejected_twice_flattens():
    b = SimBroker(price=102.0, reject_tags={"t1-sl", "t1-slx"})
    m = long_bracket(b)
    step(b, m, 100.0)
    assert b.net_position() == 0
    assert any("rejected twice" in u for u in urgent(m))


def test_cancel_verification_continues_after_flat():
    """A fast target fill goes live -> flat before the sibling cancel is confirmed.
    Verification must keep running in FLAT, alert if it never confirms, and the
    alert must resolve once the cancel goes through."""
    b = SimBroker(price=102.0, uncancellable_tags={"t1-sl"})
    m = long_bracket(b, max_cancel_retries=2)
    step(b, m, 100.0, 110.0)
    assert m.state == FLAT
    step(b, m, 108.0, 108.0)
    assert any("still working" in u for u in urgent(m))
    b.uncancellable_tags.clear()
    step(b, m, 108.0)
    assert any("confirmed" in a.message for a in m.alerts)
    assert all(o.status != WORKING for o in b.orders.values())


def test_both_protective_legs_filled_is_flattened():
    b = SimBroker(price=102.0, uncancellable_tags={"t1-sl"})
    m = long_bracket(b)
    step(b, m, 100.0, 110.0)             # target fills; stop refuses to cancel
    step(b, m, 95.0)                     # whipsaw fills the stale stop: net goes short
    assert b.net_position() == 0
    assert any("reversed" in u or "both protective" in u for u in urgent(m))


def test_break_even_move_ignored_arms_synthetic_watchdog():
    b = SimBroker(price=102.0, ignore_modify_tags={"t1-sl"})
    m = long_bracket(b, be_trigger_r=0.5)
    step(b, m, 100.0, 103.0)             # +3 points = 0.6R: break-even move attempted
    assert any("not effective" in u for u in urgent(m))
    step(b, m, 99.75)                    # back through entry; the real stop is still at 95
    assert b.net_position() == 0
    assert any("synthetic break-even" in u for u in urgent(m))


def test_break_even_move_verified():
    b = SimBroker(price=102.0)
    m = long_bracket(b, be_trigger_r=0.5)
    step(b, m, 100.0, 103.0)
    sl = [o for o in b.orders.values() if o.tag == "t1-sl"][0]
    assert sl.price == 100.0 and urgent(m) == []


def test_naked_position_detected_and_flattened():
    b = SimBroker(price=102.0)
    m = long_bracket(b)
    step(b, m, 100.0)
    for o in b.orders.values():          # protection vanishes outside our control
        if o.tag == "t1-sl":
            o.status = CANCELLED
    step(b, m, 101.0)
    assert b.net_position() == 0
    assert any("naked" in u for u in urgent(m))


def test_unknown_net_position_never_triggers_a_false_flatten():
    b = SimBroker(price=102.0, net_unknown=True)
    m = long_bracket(b)
    step(b, m, 100.0, 101.0, 102.0)
    assert m.state == LIVE and urgent(m) == []


def test_short_side_mirrors_long():
    b = SimBroker(price=98.0)
    m = BracketManager(b, "s1", SELL, 1, entry=100.0, stop=105.0, target=90.0)
    m.start()
    step(b, m, 100.0, 90.0)
    assert m.state == FLAT and b.net_position() == 0


def test_session_cutoff_flattens_open_position():
    b = SimBroker(price=102.0)
    m = long_bracket(b)
    step(b, m, 100.0)
    m.end_of_session()
    assert b.net_position() == 0
