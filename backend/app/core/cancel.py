"""Cooperative cancellation shared by the LLM providers and the tools (ARCHITECTURE §4.2).

``cancel()`` may be called from any thread (a cancel source such as a Redis listener or a worker signal
handler; AG-13 3.1a L3). Waiters belong to the event loop of the first ``wait()``; a cancel from another
thread wakes that loop through ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import threading


class CancelToken:
    """Set once; every waiter wakes. Safe to create outside a running event loop."""

    __slots__ = ("_cancelled", "_event", "_lock", "_loop")

    def __init__(self) -> None:
        self._cancelled = False
        self._event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            event, loop = self._event, self._loop
        if event is None or loop is None:
            return  # nobody waits yet; a later wait() sees the flag
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            event.set()
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass  # the waiters' loop is closed: nothing left to wake

    async def wait(self) -> None:
        with self._lock:
            if self._event is None:
                self._event = asyncio.Event()
                self._loop = asyncio.get_running_loop()
                if self._cancelled:
                    self._event.set()
            event = self._event
        await event.wait()
