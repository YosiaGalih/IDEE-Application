"""CreateGate and the cleanup runner of services/projects.py, without a database.

The gate keeps creates from draining the DB pool: one at a time per owner, a fixed number per process,
waiters hold nothing. Its bookkeeping must survive cancellation at any point.
"""

from __future__ import annotations

import asyncio
import random
import uuid

import pytest

from app.services import projects
from app.services.projects import CreateGate


async def test_bounds_the_process_and_serialises_each_owner() -> None:
    gate = CreateGate(concurrency=4)
    active = 0
    peak = 0
    per_owner: dict[uuid.UUID, int] = {}
    peak_per_owner = 0

    async def work(owner: uuid.UUID) -> None:
        nonlocal active, peak, peak_per_owner
        async with gate.turn(owner):
            active += 1
            peak = max(peak, active)
            per_owner[owner] = per_owner.get(owner, 0) + 1
            peak_per_owner = max(peak_per_owner, per_owner[owner])
            await asyncio.sleep(0.005)
            per_owner[owner] -= 1
            active -= 1

    owners = [uuid.uuid4() for _ in range(3)]
    await asyncio.gather(*(work(owners[i % 3]) for i in range(30)), *(work(uuid.uuid4()) for _ in range(20)))
    assert peak == 4
    assert peak_per_owner == 1
    assert gate._owners == {}  # pyright: ignore[reportPrivateUsage]


async def test_cancellation_at_any_point_leaves_nothing_held() -> None:
    rng = random.Random(7)  # noqa: S311 - a reproducible cancel schedule, not a secret
    gate = CreateGate(concurrency=2)
    owners = [uuid.uuid4() for _ in range(3)]

    async def job(owner: uuid.UUID) -> None:
        async with gate.turn(owner):
            await asyncio.sleep(rng.random() * 0.005)

    for _ in range(100):
        tasks = [asyncio.create_task(job(rng.choice(owners))) for _ in range(12)]
        await asyncio.sleep(rng.random() * 0.003)
        for task in rng.sample(tasks, k=6):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert gate._owners == {}  # pyright: ignore[reportPrivateUsage]
    # All slots free again: as many turns as the concurrency run at once without waiting.
    entered = 0

    async def enter() -> None:
        nonlocal entered
        async with gate.turn(uuid.uuid4()):
            entered += 1
            await asyncio.sleep(0.01)

    await asyncio.wait_for(asyncio.gather(enter(), enter()), 0.5)
    assert entered == 2


async def test_a_cancelled_holder_lets_the_next_create_of_its_owner_run() -> None:
    gate = CreateGate(concurrency=1)
    owner = uuid.uuid4()
    holding = asyncio.Event()

    async def holder() -> None:
        async with gate.turn(owner):
            holding.set()
            await asyncio.Event().wait()

    ran: list[str] = []

    async def waiter() -> None:
        async with gate.turn(owner):
            ran.append("waiter")

    held = asyncio.create_task(holder())
    await holding.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    assert ran == []
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    await asyncio.wait_for(waiting, 1)
    assert ran == ["waiter"]
    assert gate._owners == {}  # pyright: ignore[reportPrivateUsage]


async def test_a_crashing_cleanup_never_replaces_the_create_error(caplog: pytest.LogCaptureFixture) -> None:
    async def crashing() -> bool:
        raise OSError("disk gone")

    # False: "the project does not exist", so the caller raises its own error, not this one.
    assert await projects._settle(crashing()) is False  # pyright: ignore[reportPrivateUsage]
    assert projects._settling == set()  # pyright: ignore[reportPrivateUsage]
