"""Read-only SSE replay/tail over the committed event outbox.

Memory per subscriber is bounded by one page (`sse_batch_size` events of at most
`max_event_bytes` each); nothing is queued per subscriber between pages. The
in-memory notifier only shortens wakeup latency; the outbox query is the source
of truth, so a missed notification delays delivery by at most one heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Callable

import anyio
from anyio import to_thread
from starlette.responses import StreamingResponse
from starlette.types import Send

from .service import TERMINAL_RUN_STATES, Service, stream_cursor
from .config import Settings
from .telemetry import Telemetry


class Notifier:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def current(self) -> asyncio.Event:
        return self._event

    def notify(self) -> None:
        self._event.set()
        self._event = asyncio.Event()


class StreamSlots:
    """Per-principal and global subscriber bounds (single process)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.total = 0
        self.by_actor: dict[str, int] = {}

    def acquire(self, actor_id: str) -> bool:
        if self.total >= self.settings.max_streams_global or \
                self.by_actor.get(actor_id, 0) >= self.settings.max_streams_per_principal:
            return False
        self.total += 1
        self.by_actor[actor_id] = self.by_actor.get(actor_id, 0) + 1
        return True

    def release(self, actor_id: str) -> None:
        self.total -= 1
        self.by_actor[actor_id] -= 1
        if not self.by_actor[actor_id]:
            del self.by_actor[actor_id]


class SSEResponse(StreamingResponse):
    """A stalled reader is disconnected after a bounded send time instead of blocking forever."""

    def __init__(self, content: AsyncIterator[bytes], *, send_timeout: float, telemetry: Telemetry,
                 release: Callable[[], None]):
        super().__init__(content, media_type="text/event-stream",
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
        self.send_timeout, self.telemetry, self._release = send_timeout, telemetry, release

    async def __call__(self, scope, receive, send) -> None:
        # The slot is owned here, not by the generator: an async generator that never
        # started does not run its finally block on aclose(), e.g. after an early disconnect.
        try:
            await super().__call__(scope, receive, send)
        finally:
            release, self._release = self._release, None
            if release is not None:
                release()
                self.telemetry.count("streams_closed")

    async def stream_response(self, send: Send) -> None:
        try:
            with anyio.fail_after(self.send_timeout):
                await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            async for chunk in self.body_iterator:
                try:
                    with anyio.fail_after(self.send_timeout):
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                except TimeoutError:
                    self.telemetry.count("streams_slow_disconnected")
                    return  # Connection closes; the client resumes with Last-Event-ID.
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except TimeoutError:
            self.telemetry.count("streams_slow_disconnected")
        finally:
            await self.body_iterator.aclose()


def frame(sequence: int, run_id: str, event_type: str, envelope: str, generation: str | None = None) -> bytes:
    # Envelope is compact JSON (no raw newlines), so one data line is valid framing.
    return f"id: {stream_cursor(run_id, sequence, generation)}\nevent: {event_type}\ndata: {envelope}\n\n".encode("utf-8")


def replay_expired_frame(run_id: str) -> bytes:
    """Transport notice (no id: not a business event). The client recovers from the snapshot."""
    data = json.dumps({"code": "replay_expired", "snapshot": f"/api/v1/runs/{run_id}"}, separators=(",", ":"))
    return f"event: serving.replay_expired\ndata: {data}\n\n".encode("utf-8")


async def event_stream(*, service: Service, settings: Settings, notifier: Notifier, telemetry: Telemetry,
                       run_id: str, after: int, still_authorized: Callable[[], bool]) -> AsyncIterator[bytes]:
    started = time.monotonic()
    last_auth_check = started
    cursor = after
    generation = await to_thread.run_sync(service.stream_generation)
    telemetry.count("streams_opened")
    yield b"retry: 2000\n\n"
    while True:
        # Lifetime and credential checks run on every page, including during a long backlog.
        now = time.monotonic()
        if now - started >= settings.sse_max_stream_seconds:
            return
        if now - last_auth_check >= settings.sse_heartbeat_seconds:
            last_auth_check = now
            if not await to_thread.run_sync(still_authorized):
                telemetry.count("streams_closed_revoked")
                return
        wakeup = notifier.current()  # Taken before the query: a commit after it still wakes us.
        rows, state, purged_through = await to_thread.run_sync(service.events_after, run_id, cursor,
                                                               settings.sse_batch_size)
        if purged_through > cursor or (rows and rows[0][0] != cursor + 1):
            telemetry.count("streams_replay_expired")
            yield replay_expired_frame(run_id)  # Never end a replay "successfully" with a gap.
            return
        for sequence, event_type, envelope in rows:
            yield frame(sequence, run_id, event_type, envelope, generation)
            cursor = sequence
        if len(rows) == settings.sse_batch_size:
            continue
        if state in TERMINAL_RUN_STATES:
            return  # Same read snapshot: every event of the terminal run has been delivered.
        # Sleep no later than the next credential check, so a wakeup just before it is due
        # cannot push revocation out by a further heartbeat.
        due_in = settings.sse_heartbeat_seconds - (time.monotonic() - last_auth_check)
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=max(due_in, 0.0))
        except asyncio.TimeoutError:
            yield b": keepalive\n\n"
