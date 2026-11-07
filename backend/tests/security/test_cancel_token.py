"""A cancel from another thread wakes the waiters (AG-13 3.1a L3; spec WP-3.1d Goal 7): a cancel source such
as a Redis listener or a signal handler must stop a run's tools at once, not at the next unrelated timer."""

from __future__ import annotations

import asyncio
import threading

from app.core.cancel import CancelToken


async def test_cancel_from_another_thread_wakes_the_waiter() -> None:
    token = CancelToken()
    waiter = asyncio.create_task(token.wait())
    await asyncio.sleep(0)  # the waiter now blocks on the token's event
    # The other thread cancels while this loop sleeps in select(). Without call_soon_threadsafe nothing
    # writes to the loop's wake-up pipe, so the waiter only runs when an unrelated timer fires (here: 3 s).
    timer = threading.Timer(0.05, token.cancel)
    loop = asyncio.get_running_loop()
    started = loop.time()
    timer.start()
    done, _ = await asyncio.wait({waiter}, timeout=3)
    timer.join()
    assert done == {waiter}
    assert loop.time() - started < 1.0
    assert token.cancelled
