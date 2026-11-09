"""Bracket manager: keep every open contract protected, whatever the gateway does.

A position is entered with a resting limit and protected by a stop and a target
that cancel each other (OCO). The happy path is easy. This module exists for the
unhappy paths, each of which happened in production before it was handled here:

  * PROTECTIVE STOP REJECTED. On a fast reversal the exchange can reject a stop
    whose price the market has already passed. Without a backstop the position
    runs naked until the end-of-day flatten. Now: if price is past the stop, flatten
    at market; otherwise resubmit once, and flatten if that also fails.

  * PARTIAL FILLS. Protection is sized to what actually filled. When more fills
    arrive, an additional bracket ("-r1", "-r2", ...) covers the increment.

  * OCO SIBLING NOT CANCELLED. When one leg fills, the other must be confirmed
    cancelled, or it can open a brand-new position later. A fast target fill can
    take the book from live to flat in a few hundred milliseconds, so verification
    continues AFTER the position is flat until the cancel is confirmed.

  * BOTH LEGS FILLED. A whipsaw can fill the stop and the target. Net position is
    then the opposite sign; flatten it immediately.

  * BREAK-EVEN MOVE IGNORED. Moving the stop to entry can report success while the
    order is unchanged. The move is verified by reading the order back, and if it
    did not take, a synthetic watchdog flattens at market when price returns to entry.

  * NAKED POSITION. An independent poll compares the broker's net position with the
    working protection and flattens anything unprotected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .broker import BUY, FILLED, LIMIT, MARKET, REJECTED, SELL, STOP, WORKING, Broker, Order

IDLE, ENTRY_WORKING, LIVE, FLAT, DONE = "idle", "entry_working", "live", "flat", "done"


@dataclass
class Alert:
    level: str      # "info" | "urgent"
    message: str


@dataclass
class _Pair:
    qty: int
    stop_id: int
    target_id: int


@dataclass
class BracketManager:
    broker: Broker
    tag: str
    side: str                     # BUY = long, SELL = short
    qty: int
    entry: float
    stop: float
    target: float
    be_trigger_r: Optional[float] = None   # move stop to entry after this many R in profit
    max_cancel_retries: int = 3
    state: str = IDLE
    position: int = 0             # signed, from fill events
    alerts: list = field(default_factory=list)
    _entry_id: int = 0
    _pairs: list = field(default_factory=list)
    _protected: int = 0
    _pending_cancels: dict = field(default_factory=dict)   # order_id -> retries
    _resubmitted_stop: bool = False
    _be_done: bool = False
    _synthetic_be: bool = False

    # ------------------------------------------------------------------ helpers
    @property
    def _exit_side(self) -> str:
        return SELL if self.side == BUY else BUY

    @property
    def _long(self) -> bool:
        return self.side == BUY

    def _alert(self, level: str, msg: str) -> None:
        self.alerts.append(Alert(level, msg))

    def _past(self, level: float, px: float) -> bool:
        """True if price has moved through `level` against the position."""
        return px <= level if self._long else px >= level

    def _arm(self, qty: int) -> None:
        n = len(self._pairs)
        suffix = "" if n == 0 else f"-r{n}"
        sl = self.broker.place(Order(f"{self.tag}{suffix}-sl", self._exit_side, qty, STOP, self.stop))
        tp = self.broker.place(Order(f"{self.tag}{suffix}-tp", self._exit_side, qty, LIMIT, self.target))
        self._pairs.append(_Pair(qty, sl, tp))
        self._protected += qty

    def _working_stop_qty(self) -> int:
        return sum(p.qty for p in self._pairs
                   if self.broker.get(p.stop_id).status == WORKING)

    def _cancel(self, order_id: int) -> None:
        if self.broker.get(order_id).status == WORKING:
            self.broker.cancel(order_id)
        if self.broker.get(order_id).status == WORKING:
            self._pending_cancels.setdefault(order_id, 0)

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        self._entry_id = self.broker.place(Order(f"{self.tag}-entry", self.side, self.qty, LIMIT, self.entry))
        self.state = ENTRY_WORKING
        self.on_update()

    def flatten(self, reason: str, urgent: bool = True) -> None:
        for p in self._pairs:
            self._cancel(p.stop_id)
            self._cancel(p.target_id)
        self._cancel(self._entry_id)
        net = self.broker.net_position()
        net = self.position if net is None else net
        if net:
            side = SELL if net > 0 else BUY
            self.broker.place(Order(f"{self.tag}-flatten", side, abs(net), MARKET))
            self._apply_events()
        self._alert("urgent" if urgent else "info", f"FLATTEN {self.tag}: {reason}")
        self.state = FLAT

    def end_of_session(self) -> None:
        if self.state in (ENTRY_WORKING, LIVE):
            self.flatten("session cutoff", urgent=False)
        self.state = DONE if not self._pending_cancels else self.state

    # ------------------------------------------------------------------ main loop
    def on_update(self) -> None:
        """Call after every market update: process events, then run the safety checks."""
        self._apply_events()
        self._verify_cancels()
        if self.state == LIVE:
            self._break_even()
            self._naked_check()

    def _apply_events(self) -> None:
        # Handling one event can place orders that emit more events (a stop rejected
        # the instant it is armed), so drain until the queue is empty.
        while True:
            events = self.broker.drain()
            if not events:
                return
            self._handle(events)

    def _handle(self, events) -> None:
        for ev in events:
            if ev.kind == "fill":
                signed = ev.qty if self.broker.get(ev.order_id).side == BUY else -ev.qty
                self.position += signed
                if ev.order_id == self._entry_id:
                    self._on_entry_fill()
                else:
                    self._on_protective_fill(ev.order_id)
            elif ev.kind == "reject":
                self._on_reject(ev)

    def _on_entry_fill(self) -> None:
        filled = self.broker.get(self._entry_id).filled_qty
        if filled > self._protected:
            self._arm(filled - self._protected)
        self.state = LIVE

    def _on_protective_fill(self, order_id: int) -> None:
        for p in self._pairs:
            if order_id in (p.stop_id, p.target_id):
                sibling = p.target_id if order_id == p.stop_id else p.stop_id
                if self.broker.get(sibling).status == FILLED:
                    self.flatten("both protective legs filled (OCO over-fill)")
                    return
                self._cancel(sibling)
        if self.position == 0 and self.state == LIVE:
            if self.broker.get(self._entry_id).status == WORKING:
                self._cancel(self._entry_id)
            self.state = FLAT
        elif self.position != 0 and (self.position > 0) != self._long:
            self.flatten("position reversed after protective fill")

    def _on_reject(self, ev) -> None:
        if ev.order_id == self._entry_id:
            self._alert("info", f"{self.tag}: entry rejected, standing down")
            self.state = DONE
            return
        if not ev.tag.endswith(("-sl", "-slx")) or self.position == 0:
            self._alert("info", f"{self.tag}: {ev.tag} rejected")
            return
        px = self.broker.last_price()
        if self._past(self.stop, px):
            self.flatten(f"protective stop rejected with price {px} past stop {self.stop}")
        elif not self._resubmitted_stop:
            self._resubmitted_stop = True
            for p in self._pairs:
                if p.stop_id == ev.order_id:
                    p.stop_id = self.broker.place(
                        Order(f"{self.tag}-slx", self._exit_side, p.qty, STOP, self.stop))
            self._alert("info", f"{self.tag}: stop rejected, resubmitted once")
            self._apply_events()
        else:
            self.flatten("protective stop rejected twice")

    def _verify_cancels(self) -> None:
        """Runs in every state, including FLAT: a fast exit must not orphan the check."""
        for oid in list(self._pending_cancels):
            if self.broker.get(oid).status != WORKING:
                del self._pending_cancels[oid]
                self._alert("info", f"{self.tag}: cancel of order {oid} confirmed")
                continue
            self._pending_cancels[oid] += 1
            if self.broker.cancel(oid) or self.broker.get(oid).status != WORKING:
                del self._pending_cancels[oid]
                self._alert("info", f"{self.tag}: cancel of order {oid} confirmed")
                continue
            if self._pending_cancels[oid] == self.max_cancel_retries:
                self._alert("urgent", f"{self.tag}: order {oid} still working after "
                                      f"{self.max_cancel_retries} cancel attempts")

    def _break_even(self) -> None:
        if self.be_trigger_r is None:
            return
        px = self.broker.last_price()
        risk = abs(self.entry - self.stop)
        gain = (px - self.entry) if self._long else (self.entry - px)
        if not self._be_done and gain >= self.be_trigger_r * risk:
            self._be_done = True
            moved_all = True
            for p in self._pairs:
                self.broker.modify(p.stop_id, self.entry)
                if self.broker.get(p.stop_id).price != self.entry:   # read it back
                    moved_all = False
            if moved_all:
                self.stop = self.entry
            else:
                self._synthetic_be = True
                self._alert("urgent", f"{self.tag}: break-even move not effective, "
                                      f"synthetic watchdog armed")
        if self._synthetic_be and self._past(self.entry, px):
            self.flatten("synthetic break-even watchdog")

    def _naked_check(self) -> None:
        net = self.broker.net_position()
        if net is None or net == 0:
            return   # unknown net: protection stays sized to streamed fills
        if self._working_stop_qty() < abs(net):
            self.flatten(f"naked position: net {net}, protected {self._working_stop_qty()}")
