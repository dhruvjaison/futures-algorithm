"""Pre-trade risk governor.

Every entry asks the governor first. It enforces the account rules that end an
evaluation if broken: a maximum contract count, a daily loss limit that locks
trading for the rest of the session, a trailing max drawdown that locks the
account permanently, and a session cutoff after which no new risk is opened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional


@dataclass
class RiskGovernor:
    max_contracts: int
    daily_loss_limit: float
    trailing_drawdown: float
    session_cutoff: time
    balance: float = 0.0
    peak_balance: float = 0.0
    day_pnl: float = 0.0
    day_locked: bool = False
    account_locked: bool = False
    log: list = field(default_factory=list)

    def record_trade(self, pnl: float) -> None:
        self.balance += pnl
        self.day_pnl += pnl
        self.peak_balance = max(self.peak_balance, self.balance)
        if self.day_pnl <= -self.daily_loss_limit and not self.day_locked:
            self.day_locked = True
            self.log.append(f"daily loss limit hit ({self.day_pnl:+.2f}); locked for the session")
        if self.peak_balance - self.balance >= self.trailing_drawdown and not self.account_locked:
            self.account_locked = True
            self.log.append("trailing drawdown breached; account locked")

    def new_session(self) -> None:
        self.day_pnl = 0.0
        self.day_locked = False

    def can_enter(self, contracts: int, now: time) -> tuple[bool, Optional[str]]:
        if self.account_locked:
            return False, "account locked (trailing drawdown)"
        if self.day_locked:
            return False, "daily loss limit reached"
        if contracts > self.max_contracts:
            return False, f"{contracts} contracts exceeds cap {self.max_contracts}"
        if now >= self.session_cutoff:
            return False, "past session cutoff"
        return True, None
