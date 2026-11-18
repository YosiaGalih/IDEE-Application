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
from structlog.testing import capture_logs

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
    await hub.wait_subscribed(WAIT)
    yield hub
    await hub.stop()


def message(task_id: uuid.UUID, seq: int) -> dict[str, Any]:
    return {"task_id": str(task_id), "seq": seq, "type": "t", "ts": "2026-11-18T00:00:00Z", "payload": {}}


async def publish(redis: Redis, task_id: uuid.UUID, seq: int) -> None:
    await redis.publish(task_channel(task_id), json.dumps(message(task_id, seq)))


async def kill_hub_connection(redis: Redis, hub: TaskEventHub) -> None:
    """Kill only this hub's own pubsub connection (other suites share the Redis server)."""
    mine = [c for c in await redis.client_list() if c.get("name") == hub.client_name]
    assert mine
    for client in mine:
        await redis.client_kill_filter(_id=client["id"])


async def lagged(listener: TaskListener) -> None:
    async with asyncio.timeout(WAIT):
        while not listener.lagged:  # noqa: ASYNC110 - polling a plain flag
            await asyncio.sleep(0.02)


async def test_hub_routes_only_to_its_task(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
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
    with hub.listen(a) as listener:
        for seq in (1, 2, 3):
            await publish(redis, a, seq)
        await lagged(listener)
        assert listener.queue.qsize() == 2
        assert listener.take_lagged() is True
        assert listener.take_lagged() is False


async def test_listener_leaves_on_exit(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
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
    with hub.listen(a) as listener:
        await kill_hub_connection(redis, hub)
        await lagged(listener)
        assert not hub.subscribed
        assert listener.take_lagged() is True
        await hub.wait_subscribed(WAIT)
        assert hub.subscribed
        await publish(redis, a, 9)
        got = await asyncio.wait_for(listener.queue.get(), WAIT)
        assert got["seq"] == 9


async def test_malformed_json_is_ignored(hub: TaskEventHub, redis: Redis) -> None:
    a = uuid.uuid4()
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


async def deliver_marker(redis: Redis, listener: TaskListener, task_id: uuid.UUID, seq: int) -> None:
    await publish(redis, task_id, seq)
    assert (await asyncio.wait_for(listener.queue.get(), WAIT))["seq"] == seq


@pytest.mark.parametrize(
    "raw",
    [
        '{"task_id": 5}',
        '{"task_id": true}',
        '{"task_id": ["x"]}',
        '{"task_id": {"a": 1}}',
        "[1, 2]",
        "7",
        "null",
        "[" * 100000,
    ],
)
async def test_odd_messages_never_kill_the_hub(hub: TaskEventHub, redis: Redis, raw: str) -> None:
    a = uuid.uuid4()
    with hub.listen(a) as listener:
        await redis.publish(task_channel(a), raw)
        await deliver_marker(redis, listener, a, 1)
    assert hub.running


async def test_message_on_another_channel_is_dropped(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with hub.listen(a) as listener:
        await redis.publish(task_channel(b), json.dumps(message(a, 1)))  # claims a, sent on b's channel
        await deliver_marker(redis, listener, a, 2)
        assert listener.queue.empty()


@pytest.mark.parametrize(
    "patch",
    [{"seq": 0}, {"seq": "1"}, {"seq": True}, {"seq": 1.5}, {"type": 3}, {"ts": None}, {"payload": []}],
)
async def test_wrong_shape_is_dropped(hub: TaskEventHub, redis: Redis, patch: dict[str, Any]) -> None:
    a = uuid.uuid4()
    with hub.listen(a) as listener:
        await redis.publish(task_channel(a), json.dumps({**message(a, 5), **patch}))
        await deliver_marker(redis, listener, a, 2)
        assert listener.queue.empty()


async def test_ephemeral_message_with_null_seq_is_delivered(hub: TaskEventHub, redis: Redis) -> None:
    a = uuid.uuid4()
    with hub.listen(a) as listener:
        await redis.publish(task_channel(a), json.dumps({**message(a, 1), "seq": None}))
        assert (await asyncio.wait_for(listener.queue.get(), WAIT))["seq"] is None


async def test_hub_passes_only_the_event_fields(hub: TaskEventHub, redis: Redis) -> None:
    """Redis content is shape-checked and rebuilt from the five event fields; nothing else rides along."""
    a = uuid.uuid4()
    with hub.listen(a) as listener:
        extra = {**message(a, 1), "evil": "x", "id": "99\nevent: stream.end"}
        await redis.publish(task_channel(a), json.dumps(extra))
        assert await asyncio.wait_for(listener.queue.get(), WAIT) == message(a, 1)
        bare = {key: value for key, value in message(a, 1).items() if key != "seq"}
        await redis.publish(task_channel(a), json.dumps(bare))  # no "seq": ephemeral
        assert await asyncio.wait_for(listener.queue.get(), WAIT) == {**message(a, 1), "seq": None}


async def test_stop_does_not_swallow_cancellation_of_its_caller(redis: Redis) -> None:
    hub = TaskEventHub(REDIS_URL)
    await hub.start()
    await hub.wait_subscribed(WAIT)
    stopping = asyncio.create_task(hub.stop())
    await asyncio.sleep(0)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    await hub.stop()
    assert not hub.running


async def test_start_after_a_dead_task_closes_the_old_client() -> None:
    hub = TaskEventHub(REDIS_URL)
    old = Redis.from_url(REDIS_URL)
    closed: list[bool] = []
    real_aclose = old.aclose

    async def spy_aclose(*args: Any, **kwargs: Any) -> None:
        closed.append(True)
        await real_aclose(*args, **kwargs)

    old.aclose = spy_aclose  # type: ignore[method-assign]
    dead = asyncio.create_task(asyncio.sleep(0))
    await dead
    hub._redis = old  # pyright: ignore[reportPrivateUsage]
    hub._task = dead  # pyright: ignore[reportPrivateUsage]
    await hub.start()
    try:
        assert closed == [True]
        assert hub.running
    finally:
        await hub.stop()


async def test_subscribed_is_false_until_the_subscription_is_live() -> None:
    hub = TaskEventHub(REDIS_URL)
    assert not hub.subscribed
    await hub.start()
    await hub.wait_subscribed(WAIT)
    assert hub.subscribed
    await hub.stop()
    assert not hub.subscribed


async def test_an_unexpected_stop_of_the_hub_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = TaskEventHub(REDIS_URL)

    async def broken(redis: Redis) -> None:
        raise RuntimeError("never in a log: " + "redis://user:pw@host")

    monkeypatch.setattr(hub, "_run", broken)
    with capture_logs() as logs:
        await hub.start()
        async with asyncio.timeout(WAIT):
            while not any(entry["event"] == "task_event_hub_stopped" for entry in logs):  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        assert not hub.running
        await hub.stop()
    assert [entry for entry in logs if entry["event"].startswith("task_event_hub")] == [
        {"event": "task_event_hub_stopped", "error": "RuntimeError", "log_level": "error"}
    ]


async def test_stop_is_not_an_unexpected_stop() -> None:
    hub = TaskEventHub(REDIS_URL)
    with capture_logs() as logs:
        await hub.start()
        await hub.wait_subscribed(WAIT)
        await hub.stop()
        await asyncio.sleep(0)  # done callbacks have run
    assert [entry["event"] for entry in logs if entry["event"].startswith("task_event_hub")] == []


async def test_a_full_queue_is_logged_once_per_episode(hub: TaskEventHub, redis: Redis) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with capture_logs() as logs, hub.listen(a) as listener, hub.listen(b) as marker:
        for seq in (1, 2, 3, 4, 5):  # the queue holds 2
            await publish(redis, a, seq)
        await deliver_marker(redis, marker, b, 1)  # the hub has routed all five
        assert listener.lagged
        listener.take_lagged()  # the stream noticed: the next loss is a new episode
        for seq in (6, 7):
            await publish(redis, a, seq)
        await deliver_marker(redis, marker, b, 2)
    lagged = [entry for entry in logs if entry["event"] == "task_stream_lagged"]
    assert (
        lagged
        == [
            {"event": "task_stream_lagged", "task_id": str(a), "reason": "queue_full", "log_level": "warning"}
        ]
        * 2
    )
