"""SSE consumer and event decoding for ``GET /api/event`` (contract C9).

The transport envelope of V2 events is deliberately tolerant here: the OpenAPI
document publishes the event *names* and their encoded payload unions, so
decoding looks for a small set of well-known keys instead of pinning one nesting
shape. Tests pin the shapes we consume.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.opencode.client import OpenCodeClient

log = logging.getLogger("bot.events")

TEXT_DELTA = "session.text.delta"
TEXT_ENDED = "session.text.ended"
EXECUTION_DONE = {
    "session.execution.succeeded",
    "session.execution.failed",
    "session.execution.interrupted",
    "session.idle",
}
PERMISSION_ASKED = "permission.asked"
FORM_CREATED = "form.created"

_SESSION_KEYS = ("sessionID", "sessionId", "session_id", "session")
_NESTED_PAYLOADS = ("form", "permission", "request")


@dataclass(slots=True)
class Event:
    kind: str
    session_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def text_delta(self) -> str | None:
        if self.kind != TEXT_DELTA:
            return None
        for key in ("delta", "text", "chunk"):
            value = self.data.get(key)
            if isinstance(value, str):
                return value
        return None

    @property
    def text(self) -> str | None:
        for key in ("text", "content"):
            value = self.data.get(key)
            if isinstance(value, str):
                return value
        return None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    """Lift a nested payload (``data.form``) next to its envelope keys.

    V2 publishes ``form.created`` as ``{"data": {"form": {...}}}``. The form's
    own ``id`` and ``sessionID`` live one level down, so a flat reader would
    drop the event as session-less and the agent would wait forever for an
    answer nobody could give.
    """

    for key in _NESTED_PAYLOADS:
        nested = data.get(key)
        if isinstance(nested, dict):
            merged = dict(data)
            merged.update(nested)
            return merged
    return data


def decode_event(payload: dict[str, Any]) -> Event | None:
    kind = _first(payload, ("type", "kind", "event", "name"))
    if not isinstance(kind, str) or not kind:
        return None

    data = payload.get("properties")
    if not isinstance(data, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    data = _flatten(data)

    session_id = _first(payload, _SESSION_KEYS) or _first(data, _SESSION_KEYS)
    if isinstance(session_id, dict):
        session_id = _first(session_id, ("id", "sessionID"))

    return Event(kind=kind, session_id=str(session_id) if session_id else None, data=data)


def decode_sse_data(raw: str) -> Event | None:
    """Decode one ``data:`` payload. Returns None for keep-alives or junk."""

    raw = raw.strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("ignoring non-JSON SSE payload")
        return None
    if not isinstance(payload, dict):
        return None
    return decode_event(payload)


class EventBus:
    """Fan-out of agent events to the conversation run currently listening."""

    def __init__(self) -> None:
        self._subscribers: dict[str, asyncio.Queue[Event]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[session_id] = queue
        return queue

    def unsubscribe(self, session_id: str) -> None:
        self._subscribers.pop(session_id, None)

    def publish(self, event: Event) -> int:
        if not event.session_id:
            return 0
        queue = self._subscribers.get(event.session_id)
        if queue is None:
            return 0
        queue.put_nowait(event)
        return 1

    def subscriber_count(self) -> int:
        return len(self._subscribers)


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[Event]:
    async for line in lines:
        if not line.startswith("data:"):
            continue
        event = decode_sse_data(line[5:])
        if event is not None:
            yield event


async def consume_events(
    client: OpenCodeClient,
    bus: EventBus,
    stop: asyncio.Event,
    *,
    on_state: Any = None,
    backoff_cap: float = 30.0,
) -> None:
    """Keep the SSE stream open, republishing events onto ``bus``."""

    delay = 1.0
    while not stop.is_set():
        try:
            async with client.stream("GET", "/api/event", headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status()
                if on_state is not None:
                    on_state(True)
                delay = 1.0
                async for event in iter_sse(response.aiter_lines()):
                    bus.publish(event)
                    if stop.is_set():
                        break
        except (httpx.HTTPError, OSError) as exc:
            log.warning("event stream interrupted: %s", type(exc).__name__)
        finally:
            if on_state is not None:
                on_state(False)

        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            delay = min(delay * 2, backoff_cap)
