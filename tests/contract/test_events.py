from __future__ import annotations

import asyncio

import httpx

from app.opencode.client import OpenCodeClient
from app.opencode.events import decode_event, decode_sse_data, iter_sse


def _sse_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"type":"session.text.delta","properties":{"sessionID":"ses_1","delta":"hi"}}\n\n'
            'data: {"type":"session.idle","properties":{"sessionID":"ses_1"}}\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    return httpx.MockTransport(handler)


async def test_client_exposes_a_stream_context_manager() -> None:
    client = OpenCodeClient("http://fake.local", "pw", transport=_sse_transport())
    async with client.stream("GET", "/api/event") as response:
        assert response.status_code == 200
    await client.aclose()


async def test_sse_lines_decode_into_events() -> None:
    client = OpenCodeClient("http://fake.local", "pw", transport=_sse_transport())
    seen = []
    async with client.stream("GET", "/api/event") as response:
        async for event in iter_sse(response.aiter_lines()):
            seen.append(event)
    await client.aclose()
    assert [event.kind for event in seen] == ["session.text.delta", "session.idle"]
    assert seen[0].session_id == "ses_1"
    assert seen[0].text_delta == "hi"


def test_decode_ignores_keepalives_and_junk() -> None:
    assert decode_sse_data("") is None
    assert decode_sse_data("not json") is None
    assert decode_sse_data("[DONE]") is None


async def test_event_bus_only_delivers_to_the_subscribed_session() -> None:
    from app.opencode.events import Event, EventBus

    bus = EventBus()
    queue = bus.subscribe("ses_a")
    assert bus.publish(Event(kind="x", session_id="ses_b")) == 0
    assert bus.publish(Event(kind="x", session_id="ses_a")) == 1
    assert await asyncio.wait_for(queue.get(), timeout=1) is not None


def test_form_created_payload_is_nested_under_data_form() -> None:
    event = decode_event(
        {
            "type": "form.created",
            "id": "evt_1",
            "data": {
                "form": {
                    "id": "frm_1",
                    "sessionID": "ses_1",
                    "title": "Questions",
                    "metadata": {"kind": "question"},
                    "fields": [
                        {
                            "key": "q0",
                            "title": "Clarify OZI",
                            "type": "string",
                            "options": [{"value": "OCI", "label": "OCI compute instances"}],
                        }
                    ],
                }
            },
        }
    )

    assert event is not None
    assert event.session_id == "ses_1"
    assert event.get("id") == "frm_1"
    assert event.get("title") == "Questions"
    assert event.get("fields")[0]["key"] == "q0"


def test_permission_asked_payload_uses_flat_data() -> None:
    event = decode_event(
        {
            "type": "permission.asked",
            "data": {
                "id": "per_1",
                "sessionID": "ses_1",
                "action": "bash",
                "resources": ["rm -rf build"],
            },
        }
    )

    assert event is not None
    assert event.session_id == "ses_1"
    assert event.get("id") == "per_1"
    assert event.get("action") == "bash"
