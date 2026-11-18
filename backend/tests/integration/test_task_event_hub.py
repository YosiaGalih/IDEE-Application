"""TaskEventHub: one Redis pubsub connection per api process fans events out to bounded queues (WP-3.2b)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from app.core.config import load_settings
from app.core.events import task_channel
from app.main import create_app
from app.services.task_events import TaskEventHub, TaskListener
from tests.conftest import make_test_env

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
WAIT = 5.0


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    yield client
    await client.aclose()


@pytest.fixture
async def hub() -> AsyncIterator[TaskEventHub]:
    hub = TaskEventHub(REDIS_URL, queue_max=2)
    await hub.start()
    yield hub
    await hub.stop()


def message(task_id: uuid.UUID, seq: int) -> dict[str, Any]:
    return {"task_id": str(task_id), "seq": seq, "type": "t", "ts": "2026-11-18T00:00:00Z", "payload": {}}


async def publish(redis: Redis, task_id: uuid.UUID, seq: int) -> None:
    await redis.publish(task_channel(task_id), json.dumps(message(task_id, seq)))


async def subscribed(redis: Redis) -> None:
    """Wait until the hub's pattern subscription is live (it shows as a pattern on the server)."""
    async with asyncio.timeout(WAIT):
        while (await redis.pubsub_numpat()) < 1:  # noqa: ASYNC110 - polling another process's state
            await asyncio.sleep(0.02)


async def lagged(listener: TaskListener) -> None:
    async with asyncio.timeout(WAIT):
        while not listener.lagged:  # noqa: ASYNC110 - polling a plain flag
            await asyncio.sleep(0.02)


async def test_hub_routes_only_to_its_task(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await subscribed(redis)
    with hub.listen(a) as la, hub.listen(b) as lb:
        await publish(redis, a, 1)
        got = await asyncio.wait_for(la.queue.get(), WAIT)
        assert got == message(a, 1)
        await publish(redis, b, 7)
        assert (await asyncio.wait_for(lb.queue.get(), WAIT))["seq"] == 7
        assert la.queue.empty()
        assert lb.queue.empty()


async def test_full_queue_marks_lagged(hub: TaskEventHub, redis: Redis) -> None:
    a = uuid.uuid4()
    await subscribed(redis)
    with hub.listen(a) as listener:
        for seq in (1, 2, 3):
            await publish(redis, a, seq)
        await lagged(listener)
        assert listener.queue.qsize() == 2
        assert listener.take_lagged() is True
        assert listener.take_lagged() is False


async def test_listener_leaves_on_exit(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await subscribed(redis)
    with hub.listen(a) as gone:
        pass
    with hub.listen(b) as marker:
        await publish(redis, a, 1)
        await publish(redis, b, 2)  # delivered after a's message: the hub has handled both
        await asyncio.wait_for(marker.queue.get(), WAIT)
    assert gone.queue.empty()
    assert not gone.lagged


async def test_hub_resubscribes_after_connection_loss(hub: TaskEventHub, redis: Redis) -> None:
    a = uuid.uuid4()
    await subscribed(redis)
    with hub.listen(a) as listener:
        await redis.execute_command("CLIENT", "KILL", "TYPE", "pubsub")
        await lagged(listener)
        assert listener.take_lagged() is True
        await subscribed(redis)
        async with asyncio.timeout(WAIT):
            while True:
                await publish(redis, a, 9)
                try:
                    got = await asyncio.wait_for(listener.queue.get(), 0.3)
                except TimeoutError:
                    continue
                break
        assert got["seq"] == 9


async def test_malformed_json_is_ignored(hub: TaskEventHub, redis: Redis) -> None:
    a = uuid.uuid4()
    await subscribed(redis)
    with hub.listen(a) as listener:
        await redis.publish(task_channel(a), "not json {")
        await publish(redis, a, 1)
        assert (await asyncio.wait_for(listener.queue.get(), WAIT))["seq"] == 1


async def test_start_and_stop_are_idempotent() -> None:
    hub = TaskEventHub(REDIS_URL)
    assert not hub.running
    await hub.start()
    await hub.start()
    assert hub.running
    await hub.stop()
    await hub.stop()
    assert not hub.running


async def test_lifespan_starts_and_stops_hub() -> None:
    app = create_app(load_settings(make_test_env(COOKIE_SECURE="false")))
    hub = app.state.dependencies.hub
    assert hub is not None
    assert not hub.running
    async with app.router.lifespan_context(app):
        assert hub.running
    assert not hub.running
