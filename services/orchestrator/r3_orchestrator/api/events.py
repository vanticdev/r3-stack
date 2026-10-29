"""Append-only event logs streamed as Server-Sent Events.

A run or agent session owns one ``EventLog``. Producers (worker threads) call
``emit``; any number of HTTP clients replay the log from the start (or from
``Last-Event-ID`` / ``?after=``) and then follow it live until it closes. Every
SSE message is::

    id: <seq>
    event: <type>
    data: {"seq": <seq>, "type": "<type>", "time": <unix s>, ...payload}

A comment line (``: keep-alive``) is sent every ``HEARTBEAT_S`` seconds of
silence so proxies keep the connection open.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, AsyncIterator

HEARTBEAT_S = 15.0
POLL_S = 0.05


class EventLog:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._closed = False

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("event log is closed")
            event = {"seq": len(self._events) + 1, "type": event_type, "time": time.time(), **payload}
            self._events.append(event)
            return event

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def since(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return self._events[max(after, 0):]

    def __len__(self) -> int:
        return len(self._events)


def format_sse(event: dict[str, Any]) -> bytes:
    data = json.dumps(event, default=str, separators=(",", ":"))
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n".encode()


async def stream(log: EventLog, after: int = 0, is_disconnected: Any = None, heartbeat: float = HEARTBEAT_S) -> AsyncIterator[bytes]:
    """Replay ``log`` after sequence number ``after``, then follow it until it is closed and drained."""
    cursor = after
    quiet_since = time.monotonic()
    yield b"retry: 2000\n\n"
    while True:
        events = log.since(cursor)
        for event in events:
            yield format_sse(event)
        cursor += len(events)
        if events:
            quiet_since = time.monotonic()
        elif log.closed and cursor >= len(log):
            return
        elif time.monotonic() - quiet_since >= heartbeat:
            quiet_since = time.monotonic()
            yield b": keep-alive\n\n"
        if is_disconnected is not None and await is_disconnected():
            return
        if not events:
            await asyncio.sleep(POLL_S)
