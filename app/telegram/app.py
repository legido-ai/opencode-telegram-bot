"""Telegram wiring: intake, triggers, commands and callbacks.

Handlers are thin on purpose. Every decision that matters lives in a pure module
(``trigger``, ``identity``, ``streaming``) or in the runner, which keeps the
behaviour testable without a live bot.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.config import Settings
from app.health import HealthState
from app.opencode.client import PERMISSION_DECISIONS, OpenCodeClient
from app.opencode.errors import OpenCodeError
from app.opencode.events import FORM_CREATED, PERMISSION_ASKED, Event
from app.speech.stt import SttClient, SttError
from app.store import TTS_MODES, Store
from app.telegram import commands, keyboards, voice
from app.telegram.attachments import AttachmentError, collect
from app.telegram.auth import Authorizer
from app.telegram.convert import from_update
from app.telegram.messenger import Messenger
from app.telegram.ptb_messenger import TelegramMessenger
from app.telegram.runner import Job, Runner, RunState
from app.telegram.trigger import detect
from app.telegram.types import Inbound, Trigger

log = logging.getLogger("bot.app")


def _form_fields(raw: object) -> list[dict[str, Any]]:
    """Normalise ``Form.Info.fields`` into the option lists a keyboard can show."""

    fields: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        options: list[dict[str, str]] = []
        for option in item.get("options") or []:
            if isinstance(option, dict):
                value = str(option.get("value") or option.get("label") or "").strip()
                label = str(option.get("label") or value).strip()
            else:
                value = str(option).strip()
                label = value
            if value:
                options.append({"label": label or value, "value": value})
        fields.append(
            {
                "key": key,
                "title": str(item.get("title") or item.get("description") or "Question").strip(),
                "description": str(item.get("description") or "").strip(),
                "options": options,
            }
        )
    return fields


def _form_text(display_name: str, title: str, field: dict[str, Any], index: int, total: int) -> str:
    heading = f"{display_name} — {title or 'The agent asks'}"
    if total > 1:
        heading += f" ({index + 1}/{total})"
    text = f"{heading}\n\n{field.get('title') or 'Question'}"
    description = field.get("description")
    if description:
        text += f"\n{description}"
    return text


class BotApp:
    def __init__(
        self,
        *,
        settings: Settings,
        store: Store,
        client: OpenCodeClient,
        runner: Runner,
        state: HealthState,
        messenger: Messenger | None = None,
        stt: SttClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self.runner = runner
        self.state = state
        self.stt = stt
        self.authorizer = Authorizer(settings, store)
        # Telegram caps callback_data at 64 bytes; a short token keeps menus
        # usable without embedding session ids and model names in every button.
        self._choices: dict[str, tuple[str, str, str, str]] = {}
        self._form_answers: dict[str, dict[str, Any]] = {}
        self.messenger: Messenger | None = messenger
        self.bot_id = 0
        self.bot_username = ""

    # -- startup ---------------------------------------------------------

    async def on_startup(self, application: Application) -> None:
        me = await application.bot.get_me()
        self.bot_id = me.id
        self.bot_username = me.username or ""
        self.messenger = TelegramMessenger(application.bot)
        self.state.poller_alive = True
        log.info("bot identified", extra={"event": f"@{self.bot_username}"})

    def build(self) -> Application:
        application = (
            Application.builder()
            .token(self.settings.bot_token)
            # PTB's defaults (5 s read/connect, 1 s pool) are too short for
            # Telegram file downloads on a small box: voice notes and
            # documents failed with TimedOut. Keep polling responsive but let
            # getFile/downloads finish.
            .connect_timeout(20.0)
            .read_timeout(120.0)
            .write_timeout(120.0)
            .pool_timeout(10.0)
            .media_write_timeout(120.0)
            .post_init(self.on_startup)
            .concurrent_updates(True)
            .build()
        )
        # Registered first, so every delivered update is visible in the log even
        # when the intake path drops it.
        application.add_handler(TypeHandler(Update, self.on_any_update), group=-1)
        # Voice and audio are transcribed before they reach the normal prompt
        # path; register them first so the ALL handler never sees them as an
        # empty text request.
        application.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice)
        )
        application.add_handler(MessageHandler(filters.ALL, self.on_message))
        application.add_handler(CallbackQueryHandler(self.on_callback))
        application.add_error_handler(self.on_error)
        return application

    async def on_any_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        kind = next(
            (
                name
                for name in (
                    "message",
                    "edited_message",
                    "channel_post",
                    "callback_query",
                    "my_chat_member",
                    "chat_member",
                )
                if getattr(update, name, None) is not None
            ),
            "other",
        )
        chat = getattr(getattr(update, "effective_chat", None), "id", None)
        log.info(
            "update delivered",
            extra={"update_id": update.update_id, "chat_id": chat, "event": kind},
        )

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Never fail silently: log the cause and tell the chat something broke."""

        log.exception("handler failed", exc_info=context.error)
        message = from_update(update, self.bot_id) if hasattr(update, "update_id") else None
        if message is None or self.messenger is None:
            return
        try:
            await self.messenger.send_text(
                message.chat_id,
                "That command failed inside the bot. The error is logged; try again.",
                thread_id=message.thread_id,
            )
        except Exception:
            log.debug("could not report the failure to the chat")

    # -- intake ----------------------------------------------------------

    def _inbound(self, update: Update) -> Inbound | None:
        return from_update(update, self.bot_id)

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = self._inbound(update)
        if message is None:
            return
        if message.is_bot:
            log.debug("ignoring message from a bot")
            return
        if message.voice_input:
            # Handled by on_voice, which must transcribe before submitting.
            return
        log.info(
            "update received",
            extra={
                "update_id": message.update_id,
                "chat_id": message.chat_id,
                "session_id": (
                    f"{message.chat_type}/text={len(message.text)}/entities="
                    + (",".join(entity.type for entity in message.entities) or "none")
                ),
            },
        )
        if not self.authorizer.allow(message):
            return
        if not self.store.mark_seen(message.update_id):
            log.debug("duplicate update ignored", extra={"update_id": message.update_id})
            return

        self.state.note_update()
        self.authorizer.register(message)

        # The persistent keyboard sends plain labels; treat them as commands.
        if (menu_command := keyboards.menu_command(message.text)) is not None:
            log.info(
                "keyboard button pressed",
                extra={"event": menu_command, "chat_id": message.chat_id},
            )
            await self._handle_command(message, menu_command)
            return

        conversation = self.store.touch_conversation(
            message.chat_id, message.thread_id, message.chat_type, message.chat_title
        )
        policy = conversation.trigger_policy or self.settings.trigger
        trigger = detect(
            message,
            bot_id=self.bot_id,
            bot_username=self.bot_username,
            policy=policy,
        )
        # Quoting the bot's own bookkeeping is not a request: the status card and
        # the sign-off line are the messages clients auto-reply to.
        if trigger is not None and trigger.kind == "reply" and message.quote is not None:
            dashboard_id = self.store.pinned_message_id(message.chat_id, message.thread_id)
            bookkeeping = (message.quote.text or "").strip() in ("✅ I'm done",)
            bookkeeping = bookkeeping or (dashboard_id and message.quote.message_id == dashboard_id)
            bookkeeping = bookkeeping or (message.quote.text or "").startswith("📊")
            # Only ignore it when the human did not address the bot explicitly:
            # an auto-reply plus a mention is still a request.
            mentioned = any(e.type in ("mention", "text_mention") for e in message.entities)
            if bookkeeping and not mentioned:
                return
        if trigger is None:
            return

        if trigger.kind == "command":
            await self._handle_command(message, trigger.text)
            return
        if trigger.kind == "empty":
            await self._reply(message, "Yes? Tell me what you need.")
            return

        files: list[dict[str, str]] = []
        try:
            files = await collect(context.bot, update.effective_message, self.settings)
        except AttachmentError as exc:
            await self._reply(message, str(exc))
            return

        await self.runner.submit(Job(message=message, trigger=trigger, files=files or None))

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Transcribe a voice note, then run it through the normal prompt path."""

        message = self._inbound(update)
        if message is None or message.is_bot:
            return
        if not self.authorizer.allow(message):
            return
        if not self.store.mark_seen(message.update_id):
            log.debug("duplicate update ignored", extra={"update_id": message.update_id})
            return

        self.state.note_update()
        self.authorizer.register(message)
        log.info(
            "voice update received",
            extra={"update_id": message.update_id, "chat_id": message.chat_id},
        )

        conversation = self.store.touch_conversation(
            message.chat_id, message.thread_id, message.chat_type, message.chat_title
        )
        policy = conversation.trigger_policy or self.settings.trigger
        trigger = detect(
            message,
            bot_id=self.bot_id,
            bot_username=self.bot_username,
            policy=policy,
        )
        if trigger is None:
            # A caption-less voice note still counts as a request when the chat
            # is in "answer everything" mode; otherwise it is a private thought.
            if policy != "all" or message.is_private:
                return
            trigger = Trigger(kind="policy_all", text="", quote=message.quote)

        if trigger.kind == "command":
            await self._handle_command(message, trigger.text)
            return

        if self.stt is None or not self.stt.available:
            await self._reply(
                message,
                "🎤 Voice recognition is not configured.\n\n"
                "Set STT_API_URL and STT_API_KEY to enable it.",
            )
            return

        status_id = await self._reply(message, "🎤 Recognizing audio…")
        try:
            assert context.bot is not None
            audio, filename = await voice.download(
                context.bot, update.effective_message, self.settings
            )
            transcript = await self.stt.transcribe(audio, filename)
        except SttError as exc:
            await self._finish_transcription(message, status_id, f"🔴 Failed to recognize audio: {exc.user_message}")
            return
        except voice.VoiceError as exc:
            await self._finish_transcription(message, status_id, f"🔴 {exc.user_message}")
            return

        await self._finish_transcription(message, status_id, f"🎤 Recognized:\n{transcript}")

        recognized = replace(message, text=transcript, entities=(), voice_input=True)
        prompt_trigger = Trigger(kind=trigger.kind, text=transcript, quote=message.quote)
        note = self.settings.stt_note_prompt
        if note.lower() in ("false", "0"):
            note = ""
        await self.runner.submit(
            Job(message=recognized, trigger=prompt_trigger, prompt_note=note)
        )

    async def _finish_transcription(
        self, message: Inbound, status_id: int | None, text: str
    ) -> None:
        if status_id is None:
            await self._reply(message, text)
            return
        try:
            assert self.messenger is not None
            await self.messenger.edit_text(message.chat_id, status_id, text)
        except Exception:
            log.debug("could not edit the transcription status message")
            await self._reply(message, text)

    # -- commands --------------------------------------------------------

    async def _handle_command(self, message: Inbound, text: str) -> None:
        name, argument = commands.parse(text)
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            await self._reply(message, "Unknown command. Try /help.")
            return
        await handler(message, argument)

    async def _reply(self, message: Inbound, text: str, keyboard: Any = None) -> int | None:
        if self.messenger is None:
            return None
        return await self.messenger.send_text(
            message.chat_id, text, thread_id=message.thread_id, keyboard=keyboard
        )

    async def _cmd_start(self, message: Inbound, argument: str) -> None:
        await self._reply(
            message,
            commands.help_text(self.bot_username),
            await self.runner.keyboard(message),
        )

    async def _cmd_help(self, message: Inbound, argument: str) -> None:
        await self._reply(
            message,
            commands.help_text(self.bot_username),
            await self.runner.keyboard(message),
        )

    async def _cmd_id(self, message: Inbound, argument: str) -> None:
        await self._reply(message, commands.id_text(message))

    async def _cmd_new(self, message: Inbound, argument: str) -> None:
        session_id, _ = await self.runner.new_session(message, message.user_id)
        await self._reply(
            message,
            f"New session: {session_id[-6:]}",
            await self.runner.keyboard(message),
        )
        await self.runner.refresh_dashboard(message)

    async def _cmd_context(self, message: Inbound, argument: str) -> None:
        await self._reply(
            message,
            await self.runner.context_line(message),
            await self.runner.keyboard(message),
        )

    async def _cmd_status(self, message: Inbound, argument: str) -> None:
        await self._reply(message, self.runner.status_line(message))

    async def _cmd_abort(self, message: Inbound, argument: str) -> None:
        try:
            stopped = await self.runner.abort(message)
        except OpenCodeError as exc:
            await self._reply(message, exc.user_message)
            return
        await self._reply(
            message, "Stopped." if stopped else "Nothing is running in this conversation."
        )

    async def _cmd_trigger(self, message: Inbound, argument: str) -> None:
        conversation = self.store.get_conversation(message.chat_id, message.thread_id)
        current = conversation.trigger_policy if conversation else self.settings.trigger
        if not argument:
            await self._reply(
                message,
                f"trigger policy here: {current}\nUse /trigger mention or /trigger all.",
            )
            return
        policy = argument.strip().lower()
        if policy not in ("mention", "all"):
            await self._reply(message, "Policy must be mention or all.")
            return
        if message.is_private:
            await self._reply(message, "Trigger policy only applies to groups.")
            return
        if not self.authorizer.is_admin(message):
            await self._reply(message, "Only the operator can change the trigger policy.")
            return
        self.store.set_trigger_policy(message.chat_id, message.thread_id, policy)
        await self._reply(message, f"trigger policy set to {policy}")

    async def _cmd_tts(self, message: Inbound, argument: str) -> None:
        conversation = self.store.touch_conversation(
            message.chat_id, message.thread_id, message.chat_type, message.chat_title
        )
        current = conversation.tts_mode if conversation else "off"
        mode = argument.strip().lower()
        if not mode:
            await self._reply(
                message,
                f"audio replies: {current}\nUse /tts off, /tts all or /tts auto.",
            )
            return
        if mode not in TTS_MODES:
            await self._reply(message, "Audio replies must be off, all or auto.")
            return
        if mode != "off" and not self.settings.tts_configured:
            await self._reply(
                message,
                "⚠️ Audio replies are unavailable. Set TTS_API_URL and TTS_API_KEY first.",
            )
            return
        self.store.set_tts_mode(message.chat_id, message.thread_id, mode)
        copy = {
            "off": "🔇 Audio replies disabled.",
            "all": "🔊 Audio replies enabled for all messages.",
            "auto": "🎤 Audio replies enabled for voice/audio messages only.",
        }
        await self._reply(message, copy[mode])

    async def _cmd_sessions(self, message: Inbound, argument: str) -> None:
        bindings = self.store.list_bindings(message.chat_id, message.thread_id, limit=10)
        rows = [(b.session_id, b.title or "untitled", b.active) for b in bindings]
        text = commands.sessions_text(rows)
        keyboard = None
        if bindings:
            keyboard = keyboards.sessions_keyboard(
                message.chat_id,
                message.thread_id,
                [(b.session_id, b.title or "untitled") for b in bindings],
            )
        await self._reply(message, text, keyboard)

    async def _cmd_switch(self, message: Inbound, argument: str) -> None:
        await self._cmd_sessions(message, argument)

    def _token(self, kind: str, session_id: str, a: str, b: str = "") -> str:
        if len(self._choices) > 400:
            self._choices.clear()
        token = f"c{len(self._choices)}"
        self._choices[token] = (kind, session_id, a, b)
        return token

    async def _cmd_model(self, message: Inbound, argument: str) -> None:
        session_id, _ = await self.runner.ensure_session(message)
        try:
            models = await self.client.models()
            providers = await self.client.providers()
        except OpenCodeError as exc:
            await self._reply(message, exc.user_message)
            return

        names = {
            str(p.get("id")): str(p.get("name") or p.get("id"))
            for p in providers
            if p.get("id")
        }
        counts: dict[str, int] = {}
        for item in models:
            provider = str(item.get("providerID") or "?")
            counts[provider] = counts.get(provider, 0) + 1
        if not counts:
            await self._reply(message, "The server reported no models.")
            return

        if len(counts) == 1:
            only = next(iter(counts))
            await self._show_models(message, session_id, only, names.get(only, only), models)
            return

        entries = [
            (self._token("p", session_id, provider), f"{names.get(provider, provider)} ({count})")
            for provider, count in sorted(counts.items(), key=lambda item: item[0])
        ]
        await self._reply(
            message,
            f"Current: {await self._current_model(message)}\nChoose a provider:",
            keyboards.provider_keyboard(entries),
        )

    async def _current_model(self, message: Inbound) -> str:
        conversation = self.store.get_conversation(message.chat_id, message.thread_id)
        model = conversation.model if conversation else None
        if model:
            return f"{model['providerID']}/{model['modelID']}"
        return "server default"

    async def _show_models(
        self,
        message: Inbound,
        session_id: str,
        provider_id: str,
        provider_name: str,
        models: list[dict[str, Any]] | None = None,
    ) -> None:
        if models is None:
            try:
                models = await self.client.models()
            except OpenCodeError as exc:
                await self._reply(message, exc.user_message)
                return
        mine = [m for m in models if str(m.get("providerID")) == provider_id]
        entries = [
            (
                self._token("m", session_id, provider_id, str(m.get("modelID") or m.get("id"))),
                f"{provider_id}/{m.get('modelID') or m.get('id')}",
            )
            for m in mine[:20]
        ]
        if not entries:
            await self._reply(message, f"{provider_name} reports no models.")
            return
        await self._reply(
            message,
            f"{provider_name} — {len(mine)} models\nCurrent: {await self._current_model(message)}",
            keyboards.model_keyboard(entries),
        )

    async def _cmd_agent(self, message: Inbound, argument: str) -> None:
        session_id, _ = await self.runner.ensure_session(message)
        try:
            agents = await self.client.agents()
        except OpenCodeError as exc:
            await self._reply(message, exc.user_message)
            return
        names = [str(item.get("id") or item.get("name")) for item in agents][:10]
        if not names:
            await self._reply(message, "The server reported no agents.")
            return
        entries = [(self._token("a", session_id, name), name) for name in names]
        await self._reply(
            message, "Pick an agent mode for this conversation:", keyboards.agent_keyboard(entries)
        )

    # -- callbacks -------------------------------------------------------

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = self._inbound(update)
        if message is None or message.callback_data is None:
            return
        if not self.authorizer.allow(message):
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "Not allowed.")
            return
        if not self.store.mark_seen(message.update_id):
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "Already handled.")
            return
        self.authorizer.register(message)

        action, parts = keyboards.parse_callback(message.callback_data)
        if action == "P":
            await self._callback_provider(message, parts)
        elif action == "p":
            await self._callback_permission(message, parts)
        elif action == "f":
            await self._callback_form(message, parts)
        elif action == "m":
            await self._callback_model(message, parts)
        elif action == "a":
            await self._callback_agent(message, parts)
        elif action == "s":
            await self._callback_switch(message, parts)
        else:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "This control has expired.")

    async def _callback_permission(self, message: Inbound, parts: list[str]) -> None:
        if len(parts) < 3:
            return
        session_id, request_id, decision = parts[0], parts[1], parts[2]
        pending_id = f"permission:{request_id}"
        if decision not in PERMISSION_DECISIONS:
            return
        accepted, pending = self.store.decide_pending(
            pending_id, message.user_id, "allowed" if decision != "reject" else "denied"
        )
        if not accepted:
            who = self.store.participant_name(pending.decided_by) if pending and pending.decided_by else "someone"
            if self.messenger:
                await self.messenger.answer_callback(
                    message.callback_id or "", f"Already decided by {who}."
                )
            return
        await self.client.reply_permission(session_id, request_id, decision)
        state = "Allowed" if decision != "reject" else "Denied"
        if self.messenger:
            await self.messenger.answer_callback(message.callback_id or "", f"{state}.")
            await self.messenger.edit_text(
                message.chat_id,
                message.message_id,
                f"{state} by {message.display_name}.",
            )

    async def _callback_form(self, message: Inbound, parts: list[str]) -> None:
        if len(parts) < 3:
            return
        session_id = parts[0]
        try:
            field_index, option_index = int(parts[1]), int(parts[2])
        except ValueError:
            return
        pending = next(
            (
                item
                for item in self.store.pending_for_session(session_id)
                if item.kind == "form"
            ),
            None,
        )
        if pending is None:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "This control has expired.")
            return
        fields = pending.payload.get("fields") or []
        if not isinstance(fields, list) or not (0 <= field_index < len(fields)):
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "That option is gone.")
            return
        field = fields[field_index]
        options = field.get("options") or []
        if not (0 <= option_index < len(options)):
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "That option is gone.")
            return

        answers = self._form_answers.setdefault(pending.id, {})
        answers[field["key"]] = options[option_index]["value"]
        next_index = next(
            (
                index
                for index, item in enumerate(fields)
                if item.get("options") and item["key"] not in answers
            ),
            None,
        )
        if next_index is not None:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "Next question.")
                await self.messenger.edit_text(
                    message.chat_id,
                    message.message_id,
                    _form_text(
                        message.display_name,
                        str(pending.payload.get("form_title") or ""),
                        fields[next_index],
                        next_index,
                        len(fields),
                    ),
                    keyboard=keyboards.form_keyboard(session_id, fields, next_index),
                )
            return

        accepted, _ = self.store.decide_pending(pending.id, message.user_id, "answered")
        if not accepted:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "Already answered.")
            return
        form_id = str(pending.payload.get("form_id") or pending.id.removeprefix("form:"))
        await self.client.reply_form(session_id, form_id, {"answer": answers})
        self._form_answers.pop(pending.id, None)
        summary = ", ".join(str(value) for value in answers.values())
        if self.messenger:
            await self.messenger.answer_callback(message.callback_id or "", "Answered.")
            await self.messenger.edit_text(
                message.chat_id,
                message.message_id,
                f"Answered by {message.display_name}: {summary}",
            )

    async def _callback_provider(self, message: Inbound, parts: list[str]) -> None:
        if not parts:
            return
        choice = self._choices.get(parts[0])
        if choice is None:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "This control has expired.")
            return
        _, session_id, provider_id, _ = choice
        try:
            providers = await self.client.providers()
        except OpenCodeError as exc:
            await self._reply(message, exc.user_message)
            return
        name = next(
            (str(p.get("name")) for p in providers if str(p.get("id")) == provider_id),
            provider_id,
        )
        await self._show_models(message, session_id, provider_id, name)

    async def _callback_model(self, message: Inbound, parts: list[str]) -> None:
        if not parts:
            return
        if parts[0].startswith("c"):
            choice = self._choices.get(parts[0])
            if choice is None:
                if self.messenger:
                    await self.messenger.answer_callback(message.callback_id or "", "This control has expired.")
                return
            _, session_id, provider, model_id = choice
        elif len(parts) >= 3:
            session_id, provider, model_id = parts[0], parts[1], parts[2]
        else:
            return
        try:
            await self.client.set_model(session_id, provider, model_id)
        except OpenCodeError as exc:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", exc.user_message)
            return
        self.store.set_model(
            message.chat_id, message.thread_id, {"providerID": provider, "modelID": model_id}
        )
        if self.messenger:
            await self.messenger.answer_callback(message.callback_id or "", "Model updated.")
            # A fresh message is the only way to relabel the reply keyboard.
            await self.messenger.send_text(
                message.chat_id,
                f"Model set to {provider}/{model_id}.",
                thread_id=message.thread_id,
                keyboard=await self.runner.keyboard(message),
            )
            await self.messenger.delete_message(message.chat_id, message.message_id)

    async def _callback_agent(self, message: Inbound, parts: list[str]) -> None:
        if not parts:
            return
        if parts[0].startswith("c"):
            choice = self._choices.get(parts[0])
            if choice is None:
                if self.messenger:
                    await self.messenger.answer_callback(message.callback_id or "", "This control has expired.")
                return
            _, session_id, agent, _ = choice
        elif len(parts) >= 2:
            session_id, agent = parts[0], parts[1]
        else:
            return
        try:
            await self.client.set_agent(session_id, agent)
        except OpenCodeError as exc:
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", exc.user_message)
            return
        self.store.set_agent(message.chat_id, message.thread_id, agent)
        if self.messenger:
            await self.messenger.answer_callback(message.callback_id or "", "Agent updated.")
            await self.messenger.edit_text(
                message.chat_id, message.message_id, f"Agent set to {agent}."
            )

    async def _callback_switch(self, message: Inbound, parts: list[str]) -> None:
        if len(parts) < 3:
            return
        chat_id, thread_id, session_id = int(parts[0]), int(parts[1]), parts[2]
        if (chat_id, thread_id) != (message.chat_id, message.thread_id):
            if self.messenger:
                await self.messenger.answer_callback(message.callback_id or "", "Wrong conversation.")
            return
        self.store.bind_session(chat_id, thread_id, session_id, None, bound_by=message.user_id)
        await self.runner.refresh_dashboard(message)
        if self.messenger:
            await self.messenger.answer_callback(message.callback_id or "", "Switched.")
            await self.messenger.edit_text(
                message.chat_id, message.message_id, f"Switched to session {session_id[-6:]}."
            )

    # -- agent interactions ---------------------------------------------

    async def on_interaction(self, kind: str, event: Event, state: RunState) -> None:
        if self.messenger is None:
            return
        if kind == PERMISSION_ASKED:
            request_id = str(event.get("id") or event.get("requestID") or event.get("request") or "")
            if not request_id:
                return
            action = str(event.get("action") or event.get("tool") or event.get("permission") or "")
            resources = event.get("resources") or []
            detail = action or "a tool"
            if isinstance(resources, list) and resources:
                detail = f"{detail}: " + ", ".join(str(item) for item in resources[:3])
            tool = event.get("title") or detail
            pending_id = f"permission:{request_id}"
            _, created = self.store.create_pending(
                pending_id=pending_id,
                kind="permission",
                session_id=state.session_id,
                chat_id=state.message.chat_id,
                thread_id=state.message.thread_id,
                telegram_message_id=None,
                payload={"tool": str(tool)},
            )
            if not created:
                return
            message_id = await self.messenger.send_text(
                state.message.chat_id,
                f"{state.message.display_name} — approval needed: {tool}\nWhoever answers first decides.",
                thread_id=state.message.thread_id,
                keyboard=keyboards.permission_keyboard(state.session_id, request_id),
            )
            self.store.set_pending_message(pending_id, message_id)
            return

        if kind == FORM_CREATED:
            form = event.get("form")
            if not isinstance(form, dict):
                form = event.data
            form_id = str(form.get("id") or event.get("formID") or "")
            fields = _form_fields(form.get("fields"))
            if not form_id or not fields:
                return
            active = next(
                (index for index, field in enumerate(fields) if field.get("options")),
                None,
            )
            pending_id = f"form:{form_id}"
            _, created = self.store.create_pending(
                pending_id=pending_id,
                kind="form",
                session_id=state.session_id,
                chat_id=state.message.chat_id,
                thread_id=state.message.thread_id,
                telegram_message_id=None,
                payload={
                    "form_id": form_id,
                    "form_title": str(form.get("title") or ""),
                    "fields": fields,
                },
            )
            if not created:
                return
            self._form_answers[pending_id] = {}
            field = fields[active if active is not None else 0]
            text = _form_text(
                state.message.display_name,
                str(form.get("title") or ""),
                field,
                active if active is not None else 0,
                len(fields),
            )
            message_id = await self.messenger.send_text(
                state.message.chat_id,
                text,
                thread_id=state.message.thread_id,
                keyboard=(
                    keyboards.form_keyboard(state.session_id, fields, active)
                    if active is not None
                    else None
                ),
            )
            self.store.set_pending_message(pending_id, message_id)
