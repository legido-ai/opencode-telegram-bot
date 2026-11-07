from __future__ import annotations

import json

import httpx
import pytest

from app.opencode.client import OpenCodeClient
from app.opencode.errors import AuthError, Busy, SessionGone, Unavailable


class Recorder:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.content:
            self.bodies.append(json.loads(request.content))
        return httpx.Response(self.status, json=self.payload or {"data": {}})


def build(recorder: Recorder) -> OpenCodeClient:
    return OpenCodeClient(
        "http://fake.local", "pw", transport=httpx.MockTransport(recorder.handler)
    )


async def test_prompt_payload_matches_contract() -> None:
    recorder = Recorder(payload={"data": {"id": "msg_1"}})
    client = build(recorder)
    await client.prompt(
        "ses_1",
        "hello",
        files=[{"uri": "data:image/png;base64,aGVsbG8=", "name": "a.png"}],
        metadata={"telegram": {"user_id": 111}},
    )
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/session/ses_1/prompt"
    body = recorder.bodies[0]
    assert set(body) == {"text", "files", "metadata"}
    assert body["text"] == "hello"
    assert body["files"][0]["uri"].startswith("data:image/png;base64,")
    await client.aclose()


async def test_prompt_can_steer_a_running_turn() -> None:
    recorder = Recorder(payload={"data": {"id": "msg_1"}})
    client = build(recorder)
    await client.prompt("ses_1", "change of plan", delivery="steer")
    assert recorder.bodies[0]["delivery"] == "steer"
    await client.aclose()


async def test_basic_auth_header_is_sent() -> None:
    recorder = Recorder()
    client = build(recorder)
    await client.info()
    assert recorder.requests[0].headers["authorization"].startswith("Basic ")
    await client.aclose()


async def test_permission_decision_uses_server_enum() -> None:
    recorder = Recorder(status=204)
    client = build(recorder)
    await client.reply_permission("ses_1", "req_1", "once")
    body = recorder.bodies[0]
    assert body["decision"] == "once"
    assert recorder.requests[0].url.path == "/api/session/ses_1/permission/req_1/reply"
    await client.aclose()


async def test_invalid_decision_is_refused_locally() -> None:
    client = build(Recorder(status=204))
    with pytest.raises(ValueError):
        await client.reply_permission("ses_1", "req_1", "allow")
    await client.aclose()


async def test_interrupt_uses_resume_false() -> None:
    recorder = Recorder(payload={"data": {"interrupted": True}})
    client = build(recorder)
    assert await client.interrupt("ses_1") is True
    assert recorder.requests[0].url.params["resume"] == "false"
    await client.aclose()


async def test_conflict_maps_to_busy() -> None:
    client = build(Recorder(status=409))
    with pytest.raises(Busy):
        await client.prompt("ses_1", "hi")
    await client.aclose()


async def test_unauthorized_maps_to_auth_error() -> None:
    client = build(Recorder(status=401))
    with pytest.raises(AuthError):
        await client.info()
    await client.aclose()


async def test_missing_session_maps_to_session_gone() -> None:
    client = build(Recorder(status=404, payload={"error": "SessionNotFound"}))
    with pytest.raises(SessionGone):
        await client.prompt("ses_gone", "hi")
    await client.aclose()


async def test_server_error_maps_to_unavailable() -> None:
    client = build(Recorder(status=503))
    with pytest.raises(Unavailable):
        await client.info()
    await client.aclose()
