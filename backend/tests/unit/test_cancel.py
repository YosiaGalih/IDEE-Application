"""CancelToken (ARCHITECTURE §4.2; spec WP-3.1d Goal 7, AG-13 3.1a L3): a cancel from any thread wakes."""

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


async def test_cancel_before_wait_returns_at_once() -> None:
    token = CancelToken()
    token.cancel()
    await asyncio.wait_for(token.wait(), timeout=1)


async def test_cancel_on_the_loop_thread_wakes_every_waiter() -> None:
    token = CancelToken()
    waiters = [asyncio.create_task(token.wait()) for _ in range(3)]
    await asyncio.sleep(0)
    token.cancel()
    await asyncio.wait_for(asyncio.gather(*waiters), timeout=1)


async def test_cancel_after_the_loop_closed_does_not_raise() -> None:
    token = CancelToken()
    done = threading.Event()

    def run_other_loop() -> None:
        async def wait_briefly() -> None:
            # A waiter whose loop then ends without the token being cancelled.
            task = asyncio.create_task(token.wait())
            await asyncio.sleep(0)
            task.cancel()

        asyncio.run(wait_briefly())
        done.set()

    thread = threading.Thread(target=run_other_loop)
    thread.start()
    thread.join()
    assert done.is_set()
    token.cancel()  # the waiter's loop is gone: nothing to wake, and no error
    assert token.cancelled


def test_a_token_is_created_outside_a_loop() -> None:
    token = CancelToken()
    assert not token.cancelled
