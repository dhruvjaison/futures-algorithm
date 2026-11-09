"""Session calendar guard.

Bots are started by a timer every weekday. On an exchange holiday or an early
close, the guard disarms them before they connect, so a thin or closed market is
never traded by accident. Holiday lists are data, maintained per year.
"""
from __future__ import annotations

from datetime import date, time
from typing import Iterable, Optional


class SessionCalendar:
    def __init__(self, holidays: Iterable[date] = (), early_closes: Optional[dict] = None,
                 regular_close: time = time(16, 0)):
        self.holidays = set(holidays)
        self.early_closes = dict(early_closes or {})
        self.regular_close = regular_close

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def close_time(self, d: date) -> time:
        return self.early_closes.get(d, self.regular_close)

    def should_arm(self, d: date, strategy_needs_full_day: bool = True) -> tuple[bool, str]:
        """Decide whether a bot may start today."""
        if not self.is_trading_day(d):
            return False, "weekend or exchange holiday"
        if strategy_needs_full_day and d in self.early_closes:
            return False, f"early close at {self.close_time(d)}"
        return True, "regular session"
