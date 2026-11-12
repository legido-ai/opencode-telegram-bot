"""Streaming answers: coalesced edits, safe splitting, no rate-limit surprises."""

from __future__ import annotations

import html
import time
from collections.abc import Awaitable, Callable

LIMIT = 4096
ATTRIBUTION_LIMIT = 12


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Split on paragraph, then line, then hard boundary, always under ``limit``."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return [chunk for chunk in chunks if chunk]


class _Clock:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now()


class StreamEditor:
    """Turn a growing answer into as few Telegram API calls as possible."""

    def __init__(
        self,
        send: Callable[..., Awaitable[int]],
        edit: Callable[..., Awaitable[None]],
        chat_id: int,
        *,
        thread_id: int = 0,
        reply_to: int | None = None,
        min_interval: float = 1.0,
        now: Callable[[], float] = time.monotonic,
        prefix: str = "",
        markdown: bool = False,
        defer_until_finish: bool = False,
        placeholder: str | None = "…",
    ) -> None:
        self._send = send
        self._edit = edit
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._reply_to = reply_to
        self._interval = min_interval
        self._now = _Clock(now)
        self._prefix = prefix
        self._markdown = markdown
        self._defer = defer_until_finish
        self._placeholder = placeholder
        self._placeholder_only = False
        self._buffer = ""
        self._sent: list[str] = []
        self._message_ids: list[int] = []
        self._last_edit = 0.0
        self._started = False

    @property
    def text(self) -> str:
        return self._buffer

    @property
    def message_ids(self) -> list[int]:
        return list(self._message_ids)

    def append(self, delta: str) -> None:
        self._buffer += delta

    def replace(self, text: str) -> None:
        """Swap the buffered answer before the final flush (used by filters)."""

        self._buffer = text

    def _compose(self, index: int) -> str:
        chunks = split_message(self._buffer)
        if not chunks:
            return ""
        chunk = chunks[min(index, len(chunks) - 1)]
        if index == 0 and self._prefix:
            return f"{self._prefix}\n{chunk}"
        return chunk

    def _due(self) -> bool:
        # The first real text always shows at once: the placeholder must be
        # replaced the moment it stops being the truth.
        if self._placeholder_only:
            return True
        return self._now() - self._last_edit >= self._interval

    async def start(self) -> None:
        """Open the answer with the three dots the chat is waiting for.

        Telegram Web hides the typing action while the agent is still silent,
        so a placeholder bubble goes out immediately and the first flush turns
        it into the answer (the same three dots Pi shows).
        """

        self._started = True
        self._last_edit = self._now()
        if not self._placeholder:
            return
        message_id = await self._send(
            self._chat_id,
            self._placeholder,
            thread_id=self._thread_id,
            reply_to=self._reply_to,
            markdown=False,
        )
        self._message_ids.append(message_id)
        self._sent.append(self._placeholder)
        self._placeholder_only = True

    async def flush(self, *, force: bool = False) -> None:
        # Audio-request answers are held back until the filter has removed the
        # model's capability disclaimer; nothing false reaches the screen.
        if self._defer and not force:
            return
        # The very first send is never gated: until something is on screen there
        # is nothing to coalesce.
        if not force and self._message_ids and not self._due():
            return
        if not self._buffer.strip():
            # Nothing has arrived yet: the dots stay, and there is nothing to
            # replace them with.
            return

        chunks = split_message(self._buffer) or [""]
        for index, chunk in enumerate(chunks):
            rendered = f"{self._prefix}\n{chunk}" if index == 0 and self._prefix else chunk
            if index < len(self._message_ids):
                if index < len(self._sent) and self._sent[index] == rendered:
                    continue
                await self._edit(
                    self._chat_id, self._message_ids[index], rendered, markdown=self._markdown
                )
                self._sent[index] = rendered
            else:
                message_id = await self._send(
                    self._chat_id,
                    rendered,
                    thread_id=self._thread_id,
                    reply_to=self._reply_to,
                    markdown=self._markdown,
                )
                self._message_ids.append(message_id)
                self._sent.append(rendered)
        self._placeholder_only = False
        self._last_edit = self._now()

    async def finish(self, footer: str = "") -> None:
        text = self._buffer.strip()
        if not text:
            # Nothing streamed: a notice (timeout, failure) takes the dots'
            # place; without one the dots are all the chat gets.
            self._buffer = footer or self._placeholder or "…"
        elif footer:
            self._buffer = f"{self._buffer.rstrip()}\n\n{footer}"
        await self.flush(force=True)
