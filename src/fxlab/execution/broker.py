"""Broker interface and a deterministic simulated broker.

The live system talks to real futures APIs. Everything above this file only sees
the `Broker` protocol, so the same bracket and risk logic runs against the
simulator in tests and against a real gateway in production. The simulator can be
scripted to misbehave in exactly the ways real gateways do: rejecting an order,
filling part of it, refusing a cancel, ignoring a stop modification, or reporting
an unknown net position.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional, Protocol

BUY, SELL = "buy", "sell"
LIMIT, STOP, MARKET = "limit", "stop", "market"
WORKING, FILLED, CANCELLED, REJECTED = "working", "filled", "cancelled", "rejected"


@dataclass
class Order:
    tag: str
    side: str
    qty: int
    kind: str
    price: Optional[float] = None
    id: int = 0
    status: str = WORKING
    filled_qty: int = 0
    avg_price: float = 0.0

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty


@dataclass(frozen=True)
class Event:
    kind: str           # "fill" | "reject" | "cancelled"
    order_id: int
    tag: str
    qty: int = 0
    price: float = 0.0


class Broker(Protocol):
    def place(self, order: Order) -> int: ...
    def cancel(self, order_id: int) -> bool: ...
    def modify(self, order_id: int, price: float) -> bool: ...
    def get(self, order_id: int) -> Order: ...
    def net_position(self) -> Optional[int]: ...
    def last_price(self) -> float: ...
    def drain(self) -> list[Event]: ...


@dataclass
class SimBroker:
    """Deterministic, price-driven broker simulator.

    Call `set_price(p)` to advance the market; working orders that the new price
    crosses are filled and `fill` events are queued. Scripting knobs:

      reject_tags        orders whose tag is exactly one of these are rejected
      partial_tags       {tag_prefix: max qty filled per price update}
      uncancellable_tags cancels on these tags return False
      ignore_modify_tags modify() reports success but the price does not change
      net_unknown        net_position() returns None (gateway not answering)
    """
    price: float = 0.0
    reject_tags: set = field(default_factory=set)
    partial_tags: dict = field(default_factory=dict)
    uncancellable_tags: set = field(default_factory=set)
    ignore_modify_tags: set = field(default_factory=set)
    net_unknown: bool = False
    orders: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)
    _events: list = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    # -- helpers ----------------------------------------------------------------
    @staticmethod
    def _match(tag: str, prefixes) -> bool:
        return any(tag.startswith(p) for p in prefixes)

    def _fill(self, o: Order, qty: int, px: float) -> None:
        cap = next((v for k, v in self.partial_tags.items() if o.tag.startswith(k)), None)
        if cap is not None:
            qty = min(qty, cap)
        qty = min(qty, o.remaining)
        if qty <= 0:
            return
        o.avg_price = (o.avg_price * o.filled_qty + px * qty) / (o.filled_qty + qty)
        o.filled_qty += qty
        if o.remaining == 0:
            o.status = FILLED
        signed = qty if o.side == BUY else -qty
        self.fills.append((o.tag, signed, px))
        self._events.append(Event("fill", o.id, o.tag, qty, px))

    def _crossed(self, o: Order, px: float) -> bool:
        if o.kind == MARKET:
            return True
        if o.kind == LIMIT:
            return px <= o.price if o.side == BUY else px >= o.price
        return px >= o.price if o.side == BUY else px <= o.price  # STOP

    def _fill_price(self, o: Order, px: float) -> float:
        return o.price if o.kind == LIMIT else px

    # -- Broker API ---------------------------------------------------------------
    def place(self, order: Order) -> int:
        order.id = next(self._ids)
        self.orders[order.id] = order
        if order.tag in self.reject_tags:
            order.status = REJECTED
            self._events.append(Event("reject", order.id, order.tag))
        elif self._crossed(order, self.price):
            self._fill(order, order.remaining, self._fill_price(order, self.price))
        return order.id

    def cancel(self, order_id: int) -> bool:
        o = self.orders[order_id]
        if o.status != WORKING:
            return o.status == CANCELLED
        if self._match(o.tag, self.uncancellable_tags):
            return False
        o.status = CANCELLED
        self._events.append(Event("cancelled", o.id, o.tag))
        return True

    def modify(self, order_id: int, price: float) -> bool:
        o = self.orders[order_id]
        if o.status != WORKING:
            return False
        if not self._match(o.tag, self.ignore_modify_tags):
            o.price = price
        return True

    def get(self, order_id: int) -> Order:
        return self.orders[order_id]

    def net_position(self) -> Optional[int]:
        if self.net_unknown:
            return None
        return sum(q for _, q, _ in self.fills)

    def last_price(self) -> float:
        return self.price

    def drain(self) -> list[Event]:
        ev, self._events = self._events, []
        return ev

    def set_price(self, px: float) -> None:
        self.price = px
        for o in list(self.orders.values()):
            if o.status == WORKING and self._crossed(o, px):
                self._fill(o, o.remaining, self._fill_price(o, px))
