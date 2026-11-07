from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.speech.stt import SttRejected
from app.speech.tts import TtsFailed
from app.telegram.app import BotApp
from app.telegram.runner import Runner
from tests.fakes.speech import FakeStt, FakeTts
from tests.helpers import wait_for
from tests.telegram_factory import BOT_ID, BOT_USERNAME, update, voice_update


class FakeFile:
    def __init__(self, data: bytes = b"OggS voice bytes") -> None:
        self.data = data

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.data)


class FakeBot:
    def __init__(self, data: bytes = b"OggS voice bytes") -> None:
        self.data = data
        self.files: list[str] = []

    async def get_file(self, file_id: str) -> FakeFile:
        self.files.append(file_id)
        return FakeFile(self.data)


@pytest.fixture
def voice_app(settings, store, client, bus, health, messenger):
    runner = Runner(settings=settings, store=store, client=client, bus=bus, messenger=messenger)
    app = BotApp(
        settings=settings,
        store=store,
        client=client,
        runner=runner,
        state=health,
        stt=FakeStt(),
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    return app, messenger, runner


async def send_voice(app: BotApp, telegram_update, bot: FakeBot) -> None:
    await app.on_voice(telegram_update, SimpleNamespace(bot=bot))


async def test_private_voice_is_transcribed_and_answered(
    voice_app, fake_server, store
) -> None:
    app, messenger, _runner = voice_app
    bot = FakeBot()

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=1), bot)

    await wait_for(lambda: len(fake_server.prompts) == 1)
    _session, prompt, _metadata = fake_server.prompts[0]
    assert "hola desde la voz" in prompt
    assert "[speaker] name: Ana" in prompt
    assert any("Recognized" in item.text for item in messenger.edited)
    assert bot.files == ["voice-file-1"]


async def test_group_voice_needs_a_mention(voice_app, fake_server) -> None:
    app, _messenger, _runner = voice_app
    bot = FakeBot()

    await send_voice(app, voice_update(update_id=10), bot)
    assert fake_server.prompts == []

    mention = f"@{BOT_USERNAME}"
    await send_voice(
        app,
        voice_update(
            update_id=11,
            caption=mention,
            caption_entities=[
                {"type": "mention", "offset": 0, "length": len(mention)}
            ],
        ),
        bot,
    )
    await wait_for(lambda: len(fake_server.prompts) == 1)
    assert "hola desde la voz" in fake_server.prompts[0][1]


async def test_transcription_failure_is_reported_without_a_prompt(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    stt = FakeStt(error=SttRejected())
    runner = Runner(settings=settings, store=store, client=client, bus=bus, messenger=messenger)
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=stt
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=2), FakeBot())

    await wait_for(lambda: any("Failed to recognize" in item.text for item in messenger.edited))
    assert fake_server.prompts == []


async def test_voice_recognition_is_configurable(settings, store, client, bus, health, messenger, fake_server) -> None:
    runner = Runner(settings=settings, store=store, client=client, bus=bus, messenger=messenger)
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=None
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=3), FakeBot())

    await wait_for(lambda: any("not configured" in item.text for item in messenger.sent))
    assert fake_server.prompts == []


async def test_tts_modes_control_audio_replies(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    tts = FakeTts()
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await app._cmd_tts(app._inbound(update("/tts all", chat_id=111, chat_type="private")), "all")
    await send_text(app, "hello", update_id=20)
    await wait_for(lambda: bool(messenger.audio))

    assert tts.texts == ["Hello from the agent"]
    assert tts.voices == [None]
    assert messenger.audio[0].chat_id == 111
    assert messenger.audio[0].data == b"ID3fake-mp3"


async def test_voice_reply_is_text_only_by_default(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    tts = FakeTts()
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings,
        store=store,
        client=client,
        runner=runner,
        state=health,
        stt=FakeStt(),
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=30), FakeBot())

    await wait_for(
        lambda: any("Hello from the agent" in item.text for item in messenger.edited)
    )
    assert messenger.audio == []
    assert tts.texts == []
    assert fake_server.prompts


async def test_spanish_audio_request_uses_the_spanish_voice(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    fake_server.generate_text = "Hola Javier, esta es una prueba en castellano."
    tts = FakeTts()
    stt = FakeStt(text="responde en castellano")
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=stt
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=50), FakeBot())

    await wait_for(lambda: bool(messenger.audio))
    assert tts.voices == ["es-ES-ElviraNeural"]
    assert "Spanish (Castilian)" in fake_server.generated[0]


async def test_english_audio_request_uses_the_english_voice(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    fake_server.generate_text = "Hello Javier, this is an English test."
    tts = FakeTts()
    stt = FakeStt(text="responde en inglés")
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=stt
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=51), FakeBot())

    await wait_for(lambda: bool(messenger.audio))
    assert tts.voices == ["en-US-AriaNeural"]
    assert "English" in fake_server.generated[0]


async def test_audio_request_generates_the_phrase_instead_of_using_tools(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    fake_server.generate_text = '"Small steps still move you forward."'
    tts = FakeTts()
    stt = FakeStt(text="genera un audio corto con una frase.")
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=stt
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=40), FakeBot())

    await wait_for(lambda: bool(messenger.audio))
    assert tts.texts == ['"Small steps still move you forward."']
    assert tts.voices == ["en-US-AriaNeural"]
    assert fake_server.prompts == []
    assert fake_server.generated
    assert "genera un audio corto con una frase" in fake_server.generated[0]
    shown = "\n".join(item.text for item in messenger.sent + messenger.edited)
    assert "Small steps still move you forward." in shown
    assert "No puedo generar audio" not in shown


async def test_tts_off_keeps_voice_answers_silent(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    tts = FakeTts()
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings,
        store=store,
        client=client,
        runner=runner,
        state=health,
        stt=FakeStt(),
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await app._cmd_tts(app._inbound(update("/tts off", chat_id=111, chat_type="private")), "off")
    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=31), FakeBot())

    await wait_for(lambda: any("I'm done" in item.text for item in messenger.sent))
    assert messenger.audio == []
    assert tts.texts == []


async def test_auto_speaks_voice_requests_only(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    tts = FakeTts()
    stt = FakeStt()
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(
        settings=settings, store=store, client=client, runner=runner, state=health, stt=stt
    )
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await app._cmd_tts(app._inbound(update("/tts auto", chat_id=111, chat_type="private")), "auto")

    await send_text(app, "hello", update_id=21)
    await wait_for(lambda: len(fake_server.prompts) == 1)
    await wait_for(
        lambda: any("I'm done" in item.text for item in messenger.sent + messenger.edited)
    )
    assert messenger.audio == []

    await send_voice(app, voice_update(chat_id=111, chat_type="private", update_id=22), FakeBot())
    await wait_for(lambda: bool(messenger.audio))
    assert tts.texts == ["Hello from the agent"]


async def test_off_sends_no_audio(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    tts = FakeTts()
    runner = Runner(
        settings=settings, store=store, client=client, bus=bus, messenger=messenger, tts=tts
    )
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    await send_text(app, "hello", update_id=23)
    await wait_for(lambda: len(fake_server.prompts) == 1)
    await wait_for(lambda: any("I'm done" in item.text for item in messenger.sent))
    assert messenger.audio == []
    assert tts.texts == []


async def test_tts_failure_does_not_lose_the_answer(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    runner = Runner(
        settings=settings,
        store=store,
        client=client,
        bus=bus,
        messenger=messenger,
        tts=FakeTts(error=TtsFailed()),
    )
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    store.touch_conversation(111, 0, "private", None)
    store.set_tts_mode(111, 0, "all")

    await send_text(app, "hello", update_id=24)
    await wait_for(lambda: any("Failed to generate" in item.text for item in messenger.sent))
    assert any("Hello from the agent" in item.text for item in messenger.edited)


async def test_status_and_tts_command_show_the_mode(
    settings, store, client, bus, health, messenger
) -> None:
    runner = Runner(settings=settings, store=store, client=client, bus=bus, messenger=messenger)
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger

    inbound = app._inbound(update("/tts auto", chat_id=111, chat_type="private"))
    await app._cmd_tts(inbound, "auto")
    assert "voice/audio" in messenger.sent[-1].text
    assert app.runner.status_line(inbound).endswith("audio replies: auto\nqueued: 0")


async def send_text(app: BotApp, text: str, *, update_id: int) -> None:
    await app.on_message(
        update(text, chat_id=111, chat_type="private", update_id=update_id),
        SimpleNamespace(bot=FakeBot()),
    )
