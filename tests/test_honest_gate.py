"""The honest gate must be exactly right on geometry and must have probe power:
it has to fire on constructions known to be fabricated, not just stay quiet on
clean data. A check that never fires passes every study and protects nothing."""
import numpy as np
import pandas as pd
import pytest

from fxlab.research.honest_gate import (
    FAIL, PASS, SKIP, WARN, check_entry_fill, check_permutation, check_same_bar_exit,
    classify_entry_ambiguous as entry_amb, classify_exit_ambiguous as exit_amb, run_gate,
    verdict, Result, _prep,
)

LONG = dict(direction="long", entry=100.0, stop=90.0, target=140.0)
SHORT = dict(direction="short", entry=100.0, stop=110.0, target=60.0)


@pytest.mark.parametrize("hi,lo,expected", [
    (105, 98, (False, False)),
    (101, 89, (True, False)),
    (141, 99, (False, True)),
    (141, 89, (True, True)),
    (100, 90, (True, False)),      # exact touches count
    (150, 101, (False, False)),    # never traded the entry: not a fill bar
])
def test_entry_geometry_long(hi, lo, expected):
    assert entry_amb(**LONG, bar_high=hi, bar_low=lo) == expected


@pytest.mark.parametrize("hi,lo,expected", [
    (102, 95, (False, False)),
    (111, 99, (True, False)),
    (101, 59, (False, True)),
    (111, 59, (True, True)),
])
def test_entry_geometry_short_mirrors_long(hi, lo, expected):
    assert entry_amb(**SHORT, bar_high=hi, bar_low=lo) == expected


def test_exit_ambiguity_first_touch_only():
    # first bar to reach either level reaches both -> ambiguous
    assert exit_amb("long", 90, 140, [150], [85]) is True
    # first touch is the target alone; a later wide bar is irrelevant
    assert exit_amb("long", 90, 140, [141, 150], [120, 85]) is False


def _book(n=400, seed=0, edge=6.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n).strftime("%Y-%m-%d")
    pnl = rng.normal(edge, 40.0, size=n)
    return pd.DataFrame({"date": dates, "direction": "long", "pnl": pnl})


def test_permutation_separates_edge_from_noise():
    assert check_permutation(_prep(_book(edge=8.0)), 2000).status == PASS
    assert check_permutation(_prep(_book(edge=-2.0)), 2000).status == FAIL


def test_probe_power_fabricated_winners_are_caught():
    """Every winner's fill bar also reached the target: the book's whole edge may
    never have existed. The gate must not pass it."""
    d = _prep(_book(edge=10.0))
    d["same_bar_entry_stop"] = False
    d["same_bar_entry_target"] = d.pnl > 0
    r = check_entry_fill(d, tick_value=0.5, ticks_per_point=4)
    assert r.status in (FAIL, WARN)


def test_clean_fills_pass_entry_check():
    d = _prep(_book(edge=10.0))
    d["same_bar_entry_stop"] = False
    d["same_bar_entry_target"] = False
    assert check_entry_fill(d, 0.5, 4).status == PASS


def test_unscoreable_when_entry_and_stop_collide_too_often():
    d = _prep(_book(edge=10.0))
    d["entry"], d["stop"] = 100.0, 95.0
    d["same_bar_entry_stop"] = np.arange(len(d)) % 3 == 0     # 33% > 25% limit
    d["same_bar_entry_target"] = False
    r = check_entry_fill(d, 0.5, 4)
    assert r.status == FAIL and "not scoreable" in r.detail


def test_missing_provenance_is_a_skip_and_blocks_live_candidate():
    d = _prep(_book())
    assert check_entry_fill(d, 0.5, 4).status == SKIP
    v, _ = verdict([Result("entry-fill ordering", SKIP, "")])
    assert v == "NEEDS-WORK"


def test_exit_ordering_artifact_is_killed():
    d = _prep(_book(edge=10.0))
    d["same_bar_exit_ambiguous"] = d.pnl > 0        # every winner rode an ambiguous bar
    assert check_same_bar_exit(d).status == FAIL


def test_advisory_checks_never_change_the_verdict():
    v, _ = verdict([Result("adverse fill", WARN, ""), Result("permutation", PASS, "")])
    assert v == "LIVE-CANDIDATE"


def test_full_gate_kills_pure_noise():
    rep = run_gate(_book(edge=-1.0), tick_value=0.5, sims=2000, permutations=2000)
    assert rep.verdict == "KILL"
