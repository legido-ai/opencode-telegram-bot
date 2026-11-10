"""Runtime configuration: parsed once, validated eagerly, never logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TRIGGER_POLICIES = ("mention", "all")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
STT_REQUEST_FORMATS = ("multipart", "json")
TTS_PROVIDERS = ("edge", "openai", "elevenlabs")


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid.

    Messages name the offending variables and never include their values.
    """


def _int_list(raw: str, var: str) -> frozenset[int]:
    values: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(f"{var} must be a comma-separated list of numeric ids") from exc
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    trigger: str
    opencode_url: str
    opencode_username: str
    opencode_password: str
    data_dir: Path
    health_host: str
    health_port: int
    max_attachment_mb: int
    run_timeout_seconds: float
    log_level: str
    stt_url: str
    stt_api_key: str
    stt_model: str
    stt_language: str
    stt_request_format: str
    stt_note_prompt: str
    tts_provider: str
    tts_url: str
    tts_api_key: str
    tts_model: str
    tts_voice: str
    tts_voice_audio: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in logs, errors or health payloads."""

        return (self.bot_token, self.opencode_password, self.stt_api_key, self.tts_api_key)

    @property
    def stt_configured(self) -> bool:
        return bool(self.stt_url and self.stt_api_key)

    @property
    def tts_configured(self) -> bool:
        if self.tts_provider == "edge":
            return True
        return bool(self.tts_url and self.tts_api_key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [
            name
            for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "OPENCODE_SERVER_PASSWORD")
            if not (env.get(name) or "").strip()
        ]
        if missing:
            raise ConfigError("missing required configuration: " + ", ".join(missing))

        users = _int_list(env["TELEGRAM_ALLOWED_USER_IDS"], "TELEGRAM_ALLOWED_USER_IDS")
        if not users:
            raise ConfigError("TELEGRAM_ALLOWED_USER_IDS must contain at least one id")
        chats = _int_list(env.get("TELEGRAM_ALLOWED_CHAT_IDS", ""), "TELEGRAM_ALLOWED_CHAT_IDS")

        trigger = (env.get("TELEGRAM_TRIGGER") or "mention").strip().lower()
        if trigger not in TRIGGER_POLICIES:
            raise ConfigError("TELEGRAM_TRIGGER must be one of: " + ", ".join(TRIGGER_POLICIES))

        level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
        if level not in LOG_LEVELS:
            raise ConfigError("LOG_LEVEL must be one of: " + ", ".join(LOG_LEVELS))

        try:
            port = int(env.get("HEALTH_PORT") or 8080)
        except ValueError as exc:
            raise ConfigError("HEALTH_PORT must be an integer") from exc

        try:
            max_mb = int(env.get("MAX_ATTACHMENT_MB") or 20)
        except ValueError as exc:
            raise ConfigError("MAX_ATTACHMENT_MB must be an integer") from exc
        if max_mb <= 0 or max_mb > 50:
            raise ConfigError("MAX_ATTACHMENT_MB must be between 1 and 50")

        try:
            run_timeout = float(env.get("RUN_TIMEOUT_SECONDS") or 1800)
        except ValueError as exc:
            raise ConfigError("RUN_TIMEOUT_SECONDS must be a number of seconds") from exc
        if run_timeout <= 0:
            raise ConfigError("RUN_TIMEOUT_SECONDS must be greater than zero")

        request_format = (env.get("STT_REQUEST_FORMAT") or "multipart").strip().lower()
        if request_format not in STT_REQUEST_FORMATS:
            raise ConfigError(
                "STT_REQUEST_FORMAT must be one of: " + ", ".join(STT_REQUEST_FORMATS)
            )

        provider = (env.get("TTS_PROVIDER") or "openai").strip().lower()
        if provider not in TTS_PROVIDERS:
            raise ConfigError("TTS_PROVIDER must be one of: " + ", ".join(TTS_PROVIDERS))

        default_voice = {
            "edge": "en-US-EmmaMultilingualNeural",
            "elevenlabs": "21m00Tcm4TlvDq8ikWAM",
        }.get(provider, "alloy")
        default_model = "eleven_flash_v2_5" if provider == "elevenlabs" else "gpt-4o-mini-tts"

        return cls(
            bot_token=env["TELEGRAM_BOT_TOKEN"].strip(),
            allowed_user_ids=users,
            allowed_chat_ids=chats,
            trigger=trigger,
            opencode_url=(env.get("OPENCODE_API_URL") or "http://127.0.0.1:49374").rstrip("/"),
            opencode_username=(env.get("OPENCODE_SERVER_USERNAME") or "opencode").strip(),
            opencode_password=env["OPENCODE_SERVER_PASSWORD"],
            data_dir=Path(env.get("DATA_DIR") or "/data"),
            health_host=(env.get("HEALTH_HOST") or "127.0.0.1").strip(),
            health_port=port,
            max_attachment_mb=max_mb,
            run_timeout_seconds=run_timeout,
            log_level=level,
            stt_url=(env.get("STT_API_URL") or "").strip().rstrip("/"),
            stt_api_key=(env.get("STT_API_KEY") or "").strip(),
            stt_model=(env.get("STT_MODEL") or "whisper-large-v3-turbo").strip(),
            stt_language=(env.get("STT_LANGUAGE") or "").strip(),
            stt_request_format=request_format,
            stt_note_prompt=(env.get("STT_NOTE_PROMPT") or "").strip(),
            tts_provider=provider,
            tts_url=(env.get("TTS_API_URL") or "").strip().rstrip("/"),
            tts_api_key=(env.get("TTS_API_KEY") or "").strip(),
            tts_model=(env.get("TTS_MODEL") or default_model).strip(),
            tts_voice=(env.get("TTS_VOICE") or default_voice).strip(),
            # Explicit "generate an audio" requests are rare and use a separate
            # voice; fall back to the default so one variable still works.
            tts_voice_audio=(
                env.get("TTS_VOICE_AUDIO") or env.get("TTS_VOICE") or default_voice
            ).strip(),
        )

    def is_user_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids

    def is_chat_allowed(self, chat_id: int) -> bool:
        """Private chats follow the user allowlist; groups need an explicit id
        only when the operator configured a chat allowlist."""

        if chat_id > 0:
            return chat_id in self.allowed_user_ids
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids
