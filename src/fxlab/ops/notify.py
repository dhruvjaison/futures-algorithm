"""Push notifications with a delivery guarantee at shutdown.

Intraday alerts are sent on a background thread so a slow notification service
never delays order handling. That design had a bug in production: the end-of-day
summary was also sent on a daemon thread, and the process exited in the same
second, killing the thread before the request completed. Every end-of-day summary
was silently lost for weeks while intraday pushes worked, which made it look like
an intentional quiet day. The fix: anything sent just before exit must be flushed.
`close()` joins outstanding sends with a timeout, and `send(..., wait=True)` blocks.
"""
from __future__ import annotations

import threading
from typing import Callable


class Notifier:
    def __init__(self, transport: Callable[[str, str], None], timeout: float = 10.0):
        """`transport(title, body)` performs the actual delivery (HTTP push, email...)."""
        self._transport = transport
        self._timeout = timeout
        self._threads: list[threading.Thread] = []
        self.failures: list[str] = []

    def _deliver(self, title: str, body: str) -> None:
        try:
            self._transport(title, body)
        except Exception as exc:  # a failed push must never crash the trading loop
            self.failures.append(f"{title}: {exc}")

    def send(self, title: str, body: str, wait: bool = False) -> None:
        if wait:
            self._deliver(title, body)
            return
        t = threading.Thread(target=self._deliver, args=(title, body), daemon=True)
        t.start()
        self._threads.append(t)

    def close(self) -> int:
        """Wait for in-flight sends. Returns how many were still running at timeout."""
        for t in self._threads:
            t.join(self._timeout)
        pending = sum(t.is_alive() for t in self._threads)
        self._threads = [t for t in self._threads if t.is_alive()]
        return pending
