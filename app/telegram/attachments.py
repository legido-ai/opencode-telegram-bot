"""Attachment intake with explicit limits (contract T8, FR-015)."""

from __future__ import annotations

import base64
import logging

from app.config import Settings

log = logging.getLogger("bot.attachments")

ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
}
MAX_FILES = 5


class AttachmentError(RuntimeError):
    """Carries a user-facing explanation, never a path or a secret."""


def mime_for(name: str, declared: str | None) -> str | None:
    if declared in ALLOWED_MIME:
        return declared
    lower = name.lower()
    for mime, suffix in ALLOWED_MIME.items():
        if lower.endswith(suffix):
            return mime
    return None


def check_size(size: int | None, settings: Settings) -> None:
    if size is not None and size > settings.max_attachment_mb * 1024 * 1024:
        raise AttachmentError(
            f"that file is larger than the {settings.max_attachment_mb} MB limit. Send a smaller one."
        )


def _candidates(message: object) -> list[tuple[object, str, str | None]]:
    """Return (telegram file object, filename, declared mime) for a message."""

    files: list[tuple[object, str, str | None]] = []
    photo = getattr(message, "photo", None)
    if photo:
        largest = max(photo, key=lambda size: size.file_size or 0)
        files.append((largest, "photo.jpg", "image/jpeg"))
    document = getattr(message, "document", None)
    if document is not None:
        files.append(
            (document, document.file_name or "file", getattr(document, "mime_type", None))
        )
    return files


async def collect(bot: object, message: object, settings: Settings) -> list[dict[str, str]]:
    """Return ``PromptInput.FileAttachment`` refs for allowed attachments.

    OpenCode V2 does not read paths from the bot's filesystem: the prompt API
    accepts a ``uri`` and parses a ``data:<mime>;base64,...`` URI into the
    inline bytes itself. Sending ``{type, path}`` is rejected with a 400.
    """

    candidates = _candidates(message)
    if not candidates:
        return []
    if len(candidates) > MAX_FILES:
        raise AttachmentError(f"I can take at most {MAX_FILES} files per message.")

    refs: list[dict[str, str]] = []
    for file_obj, name, declared in candidates:
        mime = mime_for(name, declared)
        if mime is None:
            raise AttachmentError(
                "I can read images, PDF and text files only. That one is a different type."
            )
        check_size(getattr(file_obj, "file_size", None), settings)
        try:
            remote = await bot.get_file(file_obj.file_id)
            raw = await remote.download_as_bytearray()
        except Exception as exc:  # network or Telegram refusal
            log.warning("attachment download failed: %s", type(exc).__name__)
            raise AttachmentError("I could not download that file from Telegram.") from exc
        encoded = base64.b64encode(bytes(raw)).decode("ascii")
        refs.append({"uri": f"data:{mime};base64,{encoded}", "name": name})
    return refs
