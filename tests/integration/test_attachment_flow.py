from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from telegram import Update

from app.telegram.app import BotApp
from tests.fakes.opencode import FakeOpenCode
from tests.helpers import wait_for
from tests.telegram_factory import BOT_ID, BOT_USERNAME, bot, message_payload


class FakeFile:
    file_size = 1024
    file_unique_id = "uniq"

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"png")


class FakeBot:
    async def get_file(self, file_id: str) -> FakeFile:
        return FakeFile()


@pytest.fixture
def bot_app(settings, store, client, runner, health, messenger) -> BotApp:
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    runner.set_messenger(messenger)
    runner.set_interaction_hook(app.on_interaction)
    return app


async def test_photo_with_caption_reaches_the_agent_as_a_file(
    bot_app: BotApp, fake_server: FakeOpenCode, settings
) -> None:
    payload = message_payload(text="what is wrong here?", chat_type="private", chat_id=111)
    payload["photo"] = [
        {"file_id": "photo-1", "file_unique_id": "uniq", "width": 800, "height": 600, "file_size": 1024}
    ]
    telegram_update = Update.de_json({"update_id": 31, "message": payload}, bot())

    await bot_app.on_message(telegram_update, SimpleNamespace(bot=FakeBot()))
    await wait_for(lambda: len(fake_server.prompts) == 1)

    files = fake_server.files[0]
    assert files and files[0]["mime"] == "image/jpeg"
    assert files[0]["source"] == {"type": "inline"}
    assert base64.b64decode(files[0]["data"]) == b"png"
    _, prompt, _ = fake_server.prompts[0]
    assert prompt.endswith("what is wrong here?")
