"""A TCP proxy that can black-hole a database connection: no FIN, no RST, no data, as a partition does.

Once armed, the next client-to-server chunk is held and all traffic on every proxied connection freezes.

Teardown order matters. Closing a frozen connection makes SQLAlchemy wait on a cancel-proof
``close(timeout=2)`` per connection, and a pool ``dispose()`` can hang behind them. Call, in this order:

1. ``hole.release()``: the held traffic flows again, so the engine's cleanup can finish;
2. ``await engine.dispose()``;
3. ``await hole.close()``: drop the proxy's sockets and stop listening.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit, urlunsplit


class BlackHole:
    def __init__(self, upstream_url: str) -> None:
        self._url = urlsplit(upstream_url)
        self._upstream = (self._url.hostname or "localhost", self._url.port or 5432)
        self._armed = False
        self._flowing = asyncio.Event()
        self._flowing.set()
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None
        self.frozen_at: float | None = None  # time.monotonic() of the first held chunk

    async def start(self) -> str:
        """Listen on a free local port; return the database URL that goes through the proxy."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        netloc = f"{self._url.username}:{self._url.password}@127.0.0.1:{port}"
        return urlunsplit(self._url._replace(netloc=netloc))

    def arm(self) -> None:
        self._armed = True

    def release(self) -> None:
        """Let traffic flow again for good: disarmed too, or the next client chunk would refreeze it."""
        self._armed = False
        self._flowing.set()

    async def close(self) -> None:
        self.release()
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(*self._upstream)
        self._writers += [client_writer, upstream_writer]

        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, from_client: bool) -> None:
            try:
                while data := await reader.read(65536):
                    if from_client and self._armed and self._flowing.is_set():
                        self._flowing.clear()
                        self.frozen_at = time.monotonic()
                    await self._flowing.wait()
                    writer.write(data)
                    await writer.drain()
            except Exception:  # noqa: S110 - a dropped peer ends this direction; the other one follows
                pass
            finally:
                writer.close()

        await asyncio.gather(
            pump(client_reader, upstream_writer, True), pump(upstream_reader, client_writer, False)
        )
