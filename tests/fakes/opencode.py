"""A scripted OpenCode V2 server: no sockets, no credentials, no waiting."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.opencode.events import Event, EventBus


class FakeOpenCode:
    def __init__(self, bus: EventBus | None = None, script: list[str] | None = None) -> None:
        self.bus = bus
        self.script = script if script is not None else ["Hello", " from the agent"]
        self.sessions: dict[str, dict[str, Any]] = {}
        self.prompts: list[tuple[str, str, dict[str, Any]]] = []
        self.deliveries: list[str | None] = []
        self.gate: asyncio.Event | None = None
        self.interrupts: list[str] = []
        self.permission_replies: list[tuple[str, str, str]] = []
        self.form_replies: list[tuple[str, str, dict[str, Any]]] = []
        self.model_calls: list[tuple[str, str, str]] = []
        self.agent_calls: list[tuple[str, str]] = []
        self.files: list[list[dict[str, str]]] = []
        self.generated: list[str] = []
        self.generate_text = '"Small steps still move you forward."'
        self.fail_prompt: int | None = None
        self.publish_on_prompt = True
        self.permission_request: tuple[str, str] | None = None
        self.form_request: dict[str, Any] | None = None
        self._form_wait: asyncio.Event | None = None
        self.answer_error: str | None = None
        self.session_stats: dict[str, Any] = {
            "cost": 0.03,
            "tokens": {
                "input": 1100000,
                "output": 500,
                "reasoning": 0,
                "cache": {"read": 2000, "write": 0},
            },
            "model": {"providerID": "go", "modelID": "flash"},
        }
        self._next = 0

    # -- helpers ---------------------------------------------------------

    def _session_id(self) -> str:
        self._next += 1
        return f"ses_test{self._next:04d}"

    def emit(self, kind: str, session_id: str, **data: Any) -> None:
        if self.bus is not None:
            self.bus.publish(Event(kind=kind, session_id=session_id, data=data))

    async def _stream_answer(self, session_id: str) -> None:
        await asyncio.sleep(0)
        self.emit("session.execution.started", session_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.answer_error:
            self.emit("session.execution.failed", session_id)
            return
        if self.permission_request is not None:
            request_id, tool = self.permission_request
            self.emit(
                "permission.asked",
                session_id,
                id=request_id,
                sessionID=session_id,
                action=tool,
                resources=[],
            )
            await asyncio.sleep(0)
        if self.form_request is not None:
            self._form_wait = asyncio.Event()
            form = dict(self.form_request)
            form["sessionID"] = session_id
            self.emit("form.created", session_id, form=form)
            await self._form_wait.wait()
        for chunk in self.script:
            self.emit("session.text.delta", session_id, delta=chunk)
            await asyncio.sleep(0)
        self.emit("session.text.ended", session_id)
        self.emit("session.execution.succeeded", session_id)

    # -- transport -------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if path == "/api/info":
            return httpx.Response(200, json={"data": {"version": "test"}})

        if path == "/api/session" and method == "POST":
            body = json.loads(request.content or b"{}")
            model = body.get("model")
            if model is not None and "id" not in model:
                return httpx.Response(
                    400,
                    json={"_tag": "InvalidRequestError", "message": "Missing key at [\"model\"][\"id\"]"},
                )
            session_id = self._session_id()
            self.sessions[session_id] = {"id": session_id, "title": body.get("title")}
            return httpx.Response(200, json={"data": self.sessions[session_id]})

        if path == "/api/session" and method == "GET":
            return httpx.Response(200, json={"data": list(self.sessions.values())})

        if path == "/api/session/active" and method == "GET":
            running: dict[str, Any] = {}
            if self.gate is not None and not self.gate.is_set():
                for session_id, _ in self.prompts:
                    running[session_id] = {"type": "running"}
            return httpx.Response(200, json={"data": running})

        if path.endswith("/inbox") and method == "GET":
            return httpx.Response(200, json={"data": []})

        if path.endswith("/context") and method == "GET":
            session_id = path.split("/")[3]
            if not any(prompt[0] == session_id for prompt in self.prompts):
                # A session with no turns has no occupancy and no cost yet.
                return httpx.Response(200, json={"data": []})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "msg_1", "type": "user"},
                        {
                            "id": "msg_2",
                            "type": "assistant",
                            "model": {"providerID": "go", "modelID": "flash"},
                            **(
                                {"error": {"type": "provider", "message": self.answer_error}}
                                if self.answer_error
                                else {}
                            ),
                            "tokens": {
                                "input": 40000,
                                "output": 500,
                                "reasoning": 0,
                                "cache": {"read": 2000, "write": 0},
                            },
                        },
                    ]
                },
            )

        if method == "GET" and path.startswith("/api/session/"):
            session_id = path.split("/")[3]
            if session_id not in self.sessions:
                return httpx.Response(404, json={"error": "SessionNotFound"})
            payload = {"id": session_id, **self.session_stats}
            if not any(prompt[0] == session_id for prompt in self.prompts):
                payload = {"id": session_id, "cost": 0, "model": None, "tokens": None}
            return httpx.Response(200, json={"data": payload})

        if path.endswith("/generate") and method == "POST":
            body = json.loads(request.content or b"{}")
            self.generated.append(body.get("prompt", ""))
            return httpx.Response(200, json={"data": {"text": self.generate_text}})

        if path.endswith("/prompt") and method == "POST":
            session_id = path.split("/")[3]
            if self.fail_prompt:
                return httpx.Response(self.fail_prompt, json={"error": "nope"})
            body = json.loads(request.content or b"{}")
            self.prompts.append((session_id, body.get("text", ""), body.get("metadata", {})))
            self.deliveries.append(body.get("delivery"))
            self.files.append(body.get("files", []))
            if self.publish_on_prompt:
                asyncio.get_running_loop().create_task(self._stream_answer(session_id))
            return httpx.Response(200, json={"data": {"id": "msg_1"}})

        if path.endswith("/interrupt"):
            session_id = path.split("/")[3]
            self.interrupts.append(session_id)
            return httpx.Response(200, json={"interrupted": True})

        if "/permission/" in path and path.endswith("/reply"):
            parts = path.split("/")
            session_id, request_id = parts[3], parts[5]
            body = json.loads(request.content or b"{}")
            self.permission_replies.append((session_id, request_id, body.get("decision")))
            return httpx.Response(204)

        if "/form/" in path and path.endswith("/reply"):
            parts = path.split("/")
            session_id, form_id = parts[3], parts[5]
            body = json.loads(request.content or b"{}")
            self.form_replies.append((session_id, form_id, body))
            if self._form_wait is not None:
                self._form_wait.set()
            return httpx.Response(204)

        if path.endswith("/model") and method == "POST":
            parts = path.split("/")
            session_id = parts[3]
            body = json.loads(request.content or b"{}")
            model = body.get("model")
            if not isinstance(model, dict) or "id" not in model:
                return httpx.Response(400, json={"error": "InvalidRequest"})
            self.model_calls.append((session_id, model["providerID"], model["id"]))
            return httpx.Response(204)

        if path.endswith("/agent") and method == "POST":
            parts = path.split("/")
            session_id = parts[3]
            body = json.loads(request.content or b"{}")
            self.agent_calls.append((session_id, body["agent"]))
            return httpx.Response(204)

        if path == "/api/model/default":
            return httpx.Response(
                200, json={"data": {"providerID": "go", "modelID": "flash", "id": "flash"}}
            )

        if path == "/api/model":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "providerID": "go",
                            "modelID": "flash",
                            "name": "Flash",
                            "limit": {"context": 200000, "output": 32000},
                        }
                    ]
                },
            )

        if path == "/api/provider":
            return httpx.Response(
                200,
                json={"data": [{"id": "go", "name": "OpenCode Go", "activation": "enabled"}]},
            )

        if path == "/api/agent":
            return httpx.Response(200, json={"data": [{"id": "build"}, {"id": "plan"}]})

        return httpx.Response(404, json={"error": "not found", "path": path})
