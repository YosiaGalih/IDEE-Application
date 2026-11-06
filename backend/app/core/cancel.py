"""Cooperative cancellation shared by the LLM providers and the tools (ARCHITECTURE §4.2)."""

from __future__ import annotations

import asyncio


class CancelToken:
    """Set once; every waiter wakes. Safe to create outside a running event loop."""

    __slots__ = ("_cancelled", "_event")

    def __init__(self) -> None:
        self._cancelled = False
        self._event: asyncio.Event | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True
        if self._event is not None:
            self._event.set()

    async def wait(self) -> None:
        if self._event is None:
            self._event = asyncio.Event()
            if self._cancelled:
                self._event.set()
        await self._event.wait()
