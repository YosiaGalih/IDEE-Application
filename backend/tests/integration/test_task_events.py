"""Task events: per-task seq in the caller's transaction, publish only after commit (ARCHITECTURE §9)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import make_sessionmaker
from app.core.events import RedisEventBus, emit_task_event, publish_pending, task_channel
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from tests.conftest import make_test_env, reset_data

REDACTOR = Redactor()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(make_test_env()["REDIS_URL"])
    yield client
    await client.aclose()


@pytest.fixture
async def task_id(engine: AsyncEngine) -> uuid.UUID:
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id)
        session.add(task)
        await session.commit()
        return task.id


@pytest.fixture
async def subscription(redis: Redis, task_id: uuid.UUID) -> AsyncIterator[PubSub]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(task_channel(task_id))
    await pubsub.get_message(timeout=1)  # subscribe confirmation
    yield pubsub
    await pubsub.aclose()


async def next_message(pubsub: PubSub, wait: float = 0.5) -> dict[str, Any] | None:
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait)
    return json.loads(message["data"]) if message else None


async def test_emit_assigns_monotonic_seq(session: AsyncSession, redis: Redis, task_id: uuid.UUID) -> None:
    bus = RedisEventBus(redis)
    seqs = [
        await emit_task_event(
            session, bus, task_id, "task.log", {"level": "info", "message": str(i)}, redactor=REDACTOR
        )
        for i in range(3)
    ]
    await session.commit()
    assert seqs == [1, 2, 3]
    stored = (await session.scalars(select(TaskEvent.seq).where(TaskEvent.task_id == task_id))).all()
    assert sorted(stored) == [1, 2, 3]
    assert await session.scalar(select(Task.event_seq).where(Task.id == task_id)) == 3


async def test_parallel_emits_have_gapless_seq(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    sessions = make_sessionmaker(engine)

    async def one(i: int) -> int | None:
        async with sessions() as session:
            seq = await emit_task_event(
                session, None, task_id, "task.log", {"level": "info", "message": str(i)}, redactor=REDACTOR
            )
            await session.commit()
            return seq

    seqs = await asyncio.gather(*(one(i) for i in range(20)))
    assert sorted(s for s in seqs if s is not None) == list(range(1, 21))


async def test_publish_after_commit_only(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    seq = await emit_task_event(
        session, bus, task_id, "task.log", {"level": "info", "message": "hi"}, redactor=REDACTOR
    )
    assert await next_message(subscription) is None
    await publish_pending(session, bus)  # before commit: nothing is publishable yet
    assert await next_message(subscription) is None
    await session.commit()
    await publish_pending(session, bus)
    message = await next_message(subscription, wait=2)
    assert message is not None
    assert message["task_id"] == str(task_id)
    assert message["seq"] == seq
    assert message["type"] == "task.log"
    assert message["payload"] == {"level": "info", "message": "hi"}
    assert "ts" in message
    await publish_pending(session, bus)  # queue drained: nothing published twice
    assert await next_message(subscription) is None


async def test_rollback_discards_pending_events(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    await emit_task_event(
        session, bus, task_id, "task.log", {"level": "info", "message": "lost"}, redactor=REDACTOR
    )
    await session.rollback()  # no manual discard: the session hooks drop uncommitted events
    await publish_pending(session, bus)
    assert await next_message(subscription) is None
    assert await session.scalar(select(TaskEvent.id).where(TaskEvent.task_id == task_id)) is None


async def test_event_payload_is_redacted(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    payload = {
        "message": "export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456",
        "api_key": "plain-value-1",
    }
    await emit_task_event(session, bus, task_id, "task.log", payload, redactor=REDACTOR)
    await session.commit()
    await publish_pending(session, bus)
    stored = await session.scalar(select(TaskEvent.payload).where(TaskEvent.task_id == task_id))
    published = await next_message(subscription, wait=2)
    for rendered in (json.dumps(stored), json.dumps(published)):
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in rendered
        assert "plain-value-1" not in rendered


async def test_ephemeral_is_published_not_stored(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    seq = await emit_task_event(
        session,
        bus,
        task_id,
        "agent.delta",
        {"agent_run_id": "r1", "text": "partial"},
        ephemeral=True,
        redactor=REDACTOR,
    )
    assert seq is None
    message = await next_message(subscription, wait=2)
    assert message is not None and message["seq"] is None and message["type"] == "agent.delta"
    await session.commit()
    assert await session.scalar(select(TaskEvent.id).where(TaskEvent.task_id == task_id)) is None
    assert await session.scalar(select(Task.event_seq).where(Task.id == task_id)) == 0


async def test_rejects_overlong_type_and_unknown_task(session: AsyncSession, task_id: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="type"):
        await emit_task_event(session, None, task_id, "x" * 61, {}, redactor=REDACTOR)
    with pytest.raises(ValueError, match="task"):
        await emit_task_event(session, None, uuid.uuid4(), "task.log", {}, redactor=REDACTOR)
    with pytest.raises(ValueError, match="bus"):
        await emit_task_event(session, None, task_id, "agent.delta", {}, ephemeral=True, redactor=REDACTOR)


async def test_rolled_back_savepoint_events_are_not_published(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    await emit_task_event(session, bus, task_id, "task.log", {"message": "kept"}, redactor=REDACTOR)
    try:
        async with session.begin_nested():
            await emit_task_event(
                session, bus, task_id, "task.log", {"message": "dropped"}, redactor=REDACTOR
            )
            raise RuntimeError("step failed")
    except RuntimeError:
        pass
    await session.commit()
    await publish_pending(session, bus)
    first = await next_message(subscription, wait=2)
    assert first is not None and first["payload"] == {"message": "kept"}
    assert await next_message(subscription) is None


async def test_committed_events_survive_a_later_rollback(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    seq = await emit_task_event(session, bus, task_id, "task.log", {"message": "durable"}, redactor=REDACTOR)
    await session.commit()
    await session.execute(select(Task.id))  # a second unit of work that rolls back
    await session.rollback()
    await publish_pending(session, bus)
    message = await next_message(subscription, wait=2)
    assert message is not None and message["seq"] == seq


async def test_publish_after_commit_even_when_a_new_transaction_began(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    bus = RedisEventBus(redis)
    await emit_task_event(session, bus, task_id, "task.log", {"message": "x"}, redactor=REDACTOR)
    await session.commit()
    await session.execute(select(Task.id))  # autobegins
    await publish_pending(session, bus)
    assert await next_message(subscription, wait=2) is not None


class FailingBus:
    def __init__(self, fail_seq: int | None) -> None:
        self.fail_seq = fail_seq
        self.sent: list[int | None] = []

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if message["seq"] == self.fail_seq:
            raise ConnectionError("redis went away")
        self.sent.append(message["seq"])


async def test_publish_failure_is_logged_with_context(
    session: AsyncSession, task_id: uuid.UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.logging import configure_logging

    configure_logging("INFO", REDACTOR)
    bus = FailingBus(fail_seq=2)
    for i in range(3):
        await emit_task_event(session, bus, task_id, "task.log", {"message": str(i)}, redactor=REDACTOR)
    await session.commit()
    await publish_pending(session, bus)
    assert bus.sent == [1, 3]
    line = next(
        json.loads(row) for row in capsys.readouterr().out.splitlines() if "task_event_publish_failed" in row
    )
    assert line["seq"] == 2 and line["type"] == "task.log" and line["error_type"] == "ConnectionError"
    assert line["task_id"] == str(task_id)


async def test_commit_and_publish_is_one_step(
    session: AsyncSession, redis: Redis, subscription: PubSub, task_id: uuid.UUID
) -> None:
    from app.core.events import commit_and_publish

    bus = RedisEventBus(redis)
    seq = await emit_task_event(session, bus, task_id, "task.log", {"message": "x"}, redactor=REDACTOR)
    await commit_and_publish(session, bus)
    message = await next_message(subscription, wait=2)
    assert message is not None and message["seq"] == seq


async def test_ephemeral_publish_failure_is_logged_not_raised(
    session: AsyncSession, task_id: uuid.UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.logging import configure_logging

    configure_logging("INFO", REDACTOR)
    bus = FailingBus(fail_seq=None)
    seq = await emit_task_event(
        session,
        bus,
        task_id,
        "agent.delta",
        {"agent_run_id": "r", "text": "t"},
        redactor=REDACTOR,
        ephemeral=True,
    )
    assert seq is None
    assert "task_event_publish_failed" in capsys.readouterr().out


async def test_non_json_payload_is_normalised(session: AsyncSession, task_id: uuid.UUID) -> None:
    await emit_task_event(
        session, None, task_id, "task.log", {"ratio": float("nan"), "tags": {"b", "a"}}, redactor=REDACTOR
    )
    await session.commit()
    stored = await session.scalar(select(TaskEvent.payload).where(TaskEvent.task_id == task_id))
    assert stored == {"ratio": None, "tags": ["a", "b"]}


async def test_hung_bus_does_not_block_publishing_forever(session: AsyncSession, task_id: uuid.UUID) -> None:
    class HungBus:
        async def publish(self, channel: str, message: dict[str, Any]) -> None:
            await asyncio.sleep(3600)

    await emit_task_event(session, None, task_id, "task.log", {"m": 1}, redactor=REDACTOR)
    await session.commit()
    await asyncio.wait_for(publish_pending(session, HungBus()), timeout=10)
