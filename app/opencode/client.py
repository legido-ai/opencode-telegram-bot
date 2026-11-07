"""The only module allowed to speak HTTP to the OpenCode V2 server.

Every endpoint, payload shape and auth detail from
``specs/001-telegram-group-bot/contracts/opencode-api.md`` lives here; handlers
call these methods and never build URLs themselves (constitution Principle VIII).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.opencode.errors import AuthError, Busy, InvalidRequest, SessionGone, Unavailable

PERMISSION_DECISIONS = ("once", "always", "reject")

log = logging.getLogger("bot.opencode")


def _data(payload: Any) -> Any:
    """Unwrap the ``{"data": ...}`` envelope used by the V2 API."""

    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


class OpenCodeClient:
    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        username: str = "opencode",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=(username, password),
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "opencode-telegram-bot/0.1"},
        )
        self._limits: dict[str, int] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def stream(self, method: str, path: str, **kwargs: Any) -> Any:
        """Context-manager passthrough used by the SSE consumer (C9)."""

        return self._client.stream(method, path, **kwargs)

    # -- plumbing --------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise Unavailable() from exc
        except httpx.HTTPError as exc:
            raise Unavailable() from exc

        if response.status_code == 401:
            raise AuthError()
        if response.status_code == 409:
            raise Busy()
        if response.status_code == 404:
            body = response.text.lower()
            if "session" in body:
                raise SessionGone()
        if response.status_code >= 500:
            raise Unavailable()
        if response.status_code >= 400:
            # 400 means we sent something the server refuses: an incoming bug,
            # not an outage. Log the body so the cause is visible.
            log.warning("agent rejected request: %s %s", response.status_code, response.text[:200])
            raise InvalidRequest()
        return response

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return _data(response.json())

    # -- C1 liveness -----------------------------------------------------

    async def info(self) -> dict[str, Any]:
        return await self._json("GET", "/api/info") or {}

    # -- C2 create session ----------------------------------------------

    async def create_session(
        self,
        title: str | None = None,
        *,
        agent: str | None = None,
        model: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if agent:
            body["agent"] = agent
        if model:
            # Session create expects Model.Ref: the model id travels as ``id``.
            body["model"] = {
                "providerID": model.get("providerID"),
                "id": model.get("modelID") or model.get("id"),
            }
        data = await self._json("POST", "/api/session", json=body)
        session_id = (data or {}).get("id")
        if not session_id:
            raise Unavailable()
        return str(session_id)

    # -- C3 list sessions ------------------------------------------------

    async def list_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        data = await self._json("GET", "/api/session", params={"limit": limit, "order": "desc"})
        if isinstance(data, dict):
            data = data.get("sessions") or data.get("items") or []
        return list(data or [])

    # -- C4 prompt -------------------------------------------------------

    async def generate(self, session_id: str, prompt: str) -> str:
        """Transient text from the session context, without mutating history.

        Used for audio requests: the coding agent would otherwise reach for
        tools and start building a TTS pipeline instead of producing a phrase.
        """

        data = await self._json(
            "POST", f"/api/session/{session_id}/generate", json={"prompt": prompt}
        )
        text = (data or {}).get("text") if isinstance(data, dict) else None
        return text.strip() if isinstance(text, str) else ""

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        files: Sequence[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        delivery: str | None = None,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if files:
            body["files"] = list(files)
        if metadata:
            body["metadata"] = metadata
        if delivery:
            # "steer" joins the run in progress at its next step boundary;
            # "queue" waits for the next idle boundary.
            body["delivery"] = delivery
        if resume is not None:
            body["resume"] = resume
        return await self._json("POST", f"/api/session/{session_id}/prompt", json=body) or {}

    async def active_sessions(self) -> set[str]:
        """Sessions currently being drained by the server (foreground runs)."""

        data = await self._json("GET", "/api/session/active")
        return set(data) if isinstance(data, dict) else set()

    async def inbox(self, session_id: str) -> list[dict[str, Any]]:
        """Durable inputs admitted but not yet delivered to the session."""

        data = await self._json("GET", f"/api/session/{session_id}/inbox")
        return list(data) if isinstance(data, list) else []

    # -- C5 interrupt ----------------------------------------------------

    async def interrupt(self, session_id: str) -> bool:
        data = await self._json(
            "POST", f"/api/session/{session_id}/interrupt", params={"resume": "false"}
        )
        return bool((data or {}).get("interrupted")) if isinstance(data, dict) else False

    # -- C6 permission ---------------------------------------------------

    async def reply_permission(
        self, session_id: str, request_id: str, decision: str, message: str | None = None
    ) -> None:
        if decision not in PERMISSION_DECISIONS:
            raise ValueError(f"invalid permission decision: {decision}")
        body: dict[str, Any] = {"decision": decision}
        if message:
            body["message"] = message
        await self._request(
            "POST", f"/api/session/{session_id}/permission/{request_id}/reply", json=body
        )

    # -- C7 form ---------------------------------------------------------

    async def reply_form(self, session_id: str, form_id: str, answer: dict[str, Any]) -> None:
        await self._request(
            "POST", f"/api/session/{session_id}/form/{form_id}/reply", json=answer
        )

    # -- C8 model and agent ----------------------------------------------

    async def set_model(self, session_id: str, provider_id: str, model_id: str) -> None:
        # Contract: {"model": Model.Ref} where Ref uses ``id`` for the model.
        await self._request(
            "POST",
            f"/api/session/{session_id}/model",
            json={"model": {"providerID": provider_id, "id": model_id}},
        )

    async def set_agent(self, session_id: str, agent: str) -> None:
        await self._request("POST", f"/api/session/{session_id}/agent", json={"agent": agent})

    async def models(self) -> list[dict[str, Any]]:
        data = await self._json("GET", "/api/model")
        if isinstance(data, dict):
            data = data.get("models") or data.get("items") or []
        return list(data or [])

    async def providers(self) -> list[dict[str, Any]]:
        data = await self._json("GET", "/api/provider")
        if isinstance(data, dict):
            data = data.get("providers") or data.get("items") or []
        return list(data or [])

    async def agents(self) -> list[dict[str, Any]]:
        data = await self._json("GET", "/api/agent")
        if isinstance(data, dict):
            data = data.get("agents") or data.get("items") or []
        return list(data or [])


    # -- C11 session info and model context limits -----------------------

    async def session(self, session_id: str) -> dict[str, Any]:
        """Return ``Session.Info``: cost, token usage, current model."""

        data = await self._json("GET", f"/api/session/{session_id}")
        return data or {}

    async def context_limits(self) -> dict[str, int]:
        """Map ``provider/model`` to ``limit.context`` from the model catalogue.

        Cached for the life of the process: model limits change when the agent
        server restarts, not per run.
        """

        if self._limits is None:
            limits: dict[str, int] = {}
            for model in await self.models():
                limit = model.get("limit")
                context = limit.get("context") if isinstance(limit, dict) else None
                provider = model.get("providerID")
                model_id = model.get("modelID") or model.get("id")
                if provider and model_id and isinstance(context, int) and context > 0:
                    limits[f"{provider}/{model_id}"] = context
            self._limits = limits
        return self._limits

    async def default_model(self) -> dict[str, Any]:
        """The model the server would use when a session has not chosen one."""

        data = await self._json("GET", "/api/model/default")
        return data if isinstance(data, dict) else {}

    async def context_usage(self, session_id: str) -> dict[str, Any]:
        """Return the prompt size of the last finished assistant message.

        ``Session.Info.tokens`` is cumulative over the whole session (megatokens
        after a long day) and cannot be compared with a context window. The last
        assistant message's own ``tokens`` is the actual occupancy:
        ``input + cache.read``.
        """

        messages = await self._json("GET", f"/api/session/{session_id}/context")
        if isinstance(messages, dict):
            messages = messages.get("data") or messages.get("messages") or []
        for message in reversed(list(messages or [])):
            if not isinstance(message, dict) or message.get("type") != "assistant":
                continue
            tokens = message.get("tokens")
            if not isinstance(tokens, dict):
                continue
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            used = int(tokens.get("input") or 0) + int(cache.get("read") or 0)
            model = message.get("model") if isinstance(message.get("model"), dict) else {}
            error = message.get("error")
            return {
                "used_tokens": used or None,
                "provider": model.get("providerID"),
                "model": model.get("modelID") or model.get("id"),
                "error": _error_text(error),
            }
        return {"used_tokens": None, "provider": None, "model": None, "error": None}

    async def context_limit(self, provider_id: str | None, model_id: str | None) -> int | None:
        if not provider_id or not model_id:
            return None
        return (await self.context_limits()).get(f"{provider_id}/{model_id}")


def _error_text(error: object) -> str | None:
    """Pull a human sentence out of a structured assistant error."""

    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    kind = error.get("type")
    return str(kind) if kind else None


def basic_auth_header(username: str, password: str) -> str:
    """Helper for tests and for documenting the exact header shape."""

    raw = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()
