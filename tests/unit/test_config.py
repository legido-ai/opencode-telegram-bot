from __future__ import annotations

import pytest

from app.config import ConfigError, Settings


def test_missing_variables_are_named_but_values_are_not_echoed() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({})
    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "OPENCODE_SERVER_PASSWORD" in message


def test_parses_allowlists_and_trigger(settings: Settings) -> None:
    assert settings.allowed_user_ids == frozenset({111, 222})
    assert settings.trigger == "mention"
    assert settings.is_user_allowed(111)
    assert not settings.is_user_allowed(999)


def test_private_chat_follows_user_allowlist(settings: Settings) -> None:
    assert settings.is_chat_allowed(111)
    assert not settings.is_chat_allowed(999)


def test_group_chat_allowed_without_chat_allowlist(settings: Settings) -> None:
    assert settings.is_chat_allowed(-100999)


def test_group_chat_requires_explicit_id_when_configured(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
            "TELEGRAM_ALLOWED_USER_IDS": "111",
            "TELEGRAM_ALLOWED_CHAT_IDS": "-100777",
            "OPENCODE_SERVER_PASSWORD": "server-secret-value",
            "DATA_DIR": str(tmp_path),
        }
    )
    assert settings.is_chat_allowed(-100777)
    assert not settings.is_chat_allowed(-100888)


def test_rejects_unknown_trigger(tmp_path) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
                "TELEGRAM_ALLOWED_USER_IDS": "111",
                "TELEGRAM_TRIGGER": "shout",
                "OPENCODE_SERVER_PASSWORD": "server-secret-value",
                "DATA_DIR": str(tmp_path),
            }
        )


def test_speech_defaults_are_off_and_safe(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
            "TELEGRAM_ALLOWED_USER_IDS": "111",
            "OPENCODE_SERVER_PASSWORD": "server-secret-value",
            "DATA_DIR": str(tmp_path),
        }
    )
    assert settings.stt_configured is False
    assert settings.stt_request_format == "multipart"
    assert settings.tts_provider == "openai"
    assert settings.tts_configured is False


def test_parses_stt_and_tts_settings(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
            "TELEGRAM_ALLOWED_USER_IDS": "111",
            "OPENCODE_SERVER_PASSWORD": "server-secret-value",
            "DATA_DIR": str(tmp_path),
            "STT_API_URL": "http://opencode-stt:9000/v1/",
            "STT_API_KEY": "stt-secret",
            "STT_MODEL": "small",
            "STT_LANGUAGE": "es",
            "STT_REQUEST_FORMAT": "json",
            "TTS_PROVIDER": "edge",
            "TTS_VOICE": "en-US-AriaNeural",
        }
    )
    assert settings.stt_url == "http://opencode-stt:9000/v1"
    assert settings.stt_configured is True
    assert settings.stt_request_format == "json"
    assert settings.tts_configured is True
    assert "stt-secret" in settings.secrets


def test_default_and_explicit_audio_voices_are_separate(tmp_path) -> None:
    base = {
        "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "OPENCODE_SERVER_PASSWORD": "server-secret-value",
        "DATA_DIR": str(tmp_path),
        "TTS_PROVIDER": "edge",
        "TTS_VOICE": "es-ES-ElviraNeural",
        "TTS_VOICE_AUDIO": "en-US-AriaNeural",
    }
    settings = Settings.from_env(base)
    assert settings.tts_voice == "es-ES-ElviraNeural"
    assert settings.tts_voice_audio == "en-US-AriaNeural"

    fallback = Settings.from_env({**base, "TTS_VOICE_AUDIO": ""})
    assert fallback.tts_voice_audio == "es-ES-ElviraNeural"


def test_rejects_bad_speech_settings(tmp_path) -> None:
    base = {
        "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "OPENCODE_SERVER_PASSWORD": "server-secret-value",
        "DATA_DIR": str(tmp_path),
    }
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "STT_REQUEST_FORMAT": "xml"})
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "TTS_PROVIDER": "carrier-pigeon"})


def test_rejects_bad_attachment_limit(tmp_path) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
                "TELEGRAM_ALLOWED_USER_IDS": "111",
                "MAX_ATTACHMENT_MB": "900",
                "OPENCODE_SERVER_PASSWORD": "server-secret-value",
                "DATA_DIR": str(tmp_path),
            }
        )


def test_run_timeout_defaults_and_validates(tmp_path) -> None:
    base = {
        "TELEGRAM_BOT_TOKEN": "123456:TESTTOKENVALUE",
        "TELEGRAM_ALLOWED_USER_IDS": "111",
        "OPENCODE_SERVER_PASSWORD": "server-secret-value",
        "DATA_DIR": str(tmp_path),
    }
    assert Settings.from_env(base).run_timeout_seconds == 1800
    assert Settings.from_env({**base, "RUN_TIMEOUT_SECONDS": "60"}).run_timeout_seconds == 60
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "RUN_TIMEOUT_SECONDS": "soon"})
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "RUN_TIMEOUT_SECONDS": "0"})
