"""Inline keyboards and their compact callback payloads (contract T5)."""

from __future__ import annotations

from typing import Any

from app.telegram.footer import format_tokens, percent_text


def _markup(rows: list[list[tuple[str, str]]]) -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows]
    )


MODEL_LABEL = "🧠 Model"
CONTEXT_EMPTY = "📊 0"


def context_label(used: int | None, limit: int | None) -> str:
    """The context button *shows* the usage: 📊 6K / 1.0M (1%)."""

    if limit:
        percent = percent_text(used, limit) or "0%"
        return f"📊 {format_tokens(used or 0)} / {format_tokens(limit)} ({percent})"
    if used:
        return f"📊 {format_tokens(used)}"
    return CONTEXT_EMPTY


def main_menu(context: str | None = None, model: str | None = None) -> Any:
    """One row, two blocks: current model and live context usage."""

    from telegram import KeyboardButton, ReplyKeyboardMarkup

    label = f"🧠 {model}"[:40] if model else MODEL_LABEL
    return ReplyKeyboardMarkup(
        [[KeyboardButton(label), KeyboardButton(context or CONTEXT_EMPTY)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_command(text: str) -> str | None:
    """Buttons are matched by icon: the context label changes every run."""

    stripped = text.strip()
    if stripped.startswith("🧠"):
        return "/model"
    if stripped.startswith("📊"):
        return "/context"
    return None


def permission_keyboard(session_id: str, request_id: str) -> Any:
    return _markup(
        [
            [
                ("Allow once", f"p:{session_id}:{request_id}:once"),
                ("Always", f"p:{session_id}:{request_id}:always"),
            ],
            [("Deny", f"p:{session_id}:{request_id}:reject")],
        ]
    )


def form_keyboard(session_id: str, fields: list[dict[str, Any]], active: int = 0) -> Any:
    """Buttons for one form field; the callback carries indices, not ids."""

    if not (0 <= active < len(fields)):
        return None
    options = fields[active].get("options") or []
    rows = [
        [(str(option.get("label") or option.get("value"))[:40], f"f:{session_id}:{active}:{index}")]
        for index, option in enumerate(options[:8])
    ]
    return _markup(rows) if rows else None


def provider_keyboard(providers: list[tuple[str, str]]) -> Any:
    """``providers`` items are (token, label)."""

    return _markup([[(label[:40], f"P:{token}")] for token, label in providers[:12]])


def model_keyboard(models: list[tuple[str, str]]) -> Any:
    """``models`` items are (token, label); tokens keep callback data short."""

    return _markup([[(label[:40], f"m:{token}")] for token, label in models[:10]])


def agent_keyboard(agents: list[tuple[str, str]]) -> Any:
    return _markup([[(label[:40], f"a:{token}")] for token, label in agents[:10]])


def sessions_keyboard(chat_id: int, thread_id: int, sessions: list[tuple[str, str]]) -> Any:
    return _markup(
        [[(title[:40], f"s:{chat_id}:{thread_id}:{session_id}")] for session_id, title in sessions[:10]]
    )


def parse_callback(data: str) -> tuple[str, list[str]]:
    parts = data.split(":")
    if not parts or not parts[0]:
        return "", []
    return parts[0], parts[1:]
