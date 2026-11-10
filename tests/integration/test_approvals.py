from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.telegram.app import BotApp
from tests.helpers import wait_for
from tests.telegram_factory import BOT_ID, BOT_USERNAME, callback_update, update

MENTION = [{"type": "mention", "offset": 0, "length": 7}]


@pytest.fixture
def bot_app(settings, store, client, runner, health, messenger) -> BotApp:
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    runner.set_messenger(messenger)
    runner.set_interaction_hook(app.on_interaction)
    return app


async def test_permission_is_asked_and_answered_by_the_second_human(
    bot_app: BotApp, fake_server, messenger, store
) -> None:
    fake_server.permission_request = ("req_1", "bash: rm -rf build")
    await bot_app.on_message(
        update("@oc3bot clean the build", entities=MENTION, user_id=111, first_name="Ana", update_id=1),
        SimpleNamespace(bot=None),
    )

    await wait_for(lambda: any(item.keyboard is not None for item in messenger.sent))
    session_id = store.get_conversation(-100123, 0).active_session_id
    assert session_id is not None
    pending = store.get_pending("permission:req_1")
    assert pending is not None and pending.state == "pending"

    await bot_app.on_callback(
        callback_update(f"p:{session_id}:req_1:once", update_id=90, user_id=222, first_name="Luis"),
        SimpleNamespace(bot=None),
    )
    await wait_for(lambda: fake_server.permission_replies != [])

    assert fake_server.permission_replies == [(session_id, "req_1", "once")]
    assert store.get_pending("permission:req_1").decided_by == 222
    assert any("Allowed by Luis" in item.text for item in messenger.edited)


async def test_second_press_is_refused(bot_app: BotApp, fake_server, messenger, store) -> None:
    fake_server.permission_request = ("req_2", "bash")
    await bot_app.on_message(
        update("@oc3bot go", entities=MENTION, update_id=1), SimpleNamespace(bot=None)
    )
    await wait_for(lambda: store.get_pending("permission:req_2") is not None)
    session_id = store.get_conversation(-100123, 0).active_session_id

    await bot_app.on_callback(
        callback_update(f"p:{session_id}:req_2:reject", update_id=91, user_id=111, first_name="Ana"),
        SimpleNamespace(bot=None),
    )
    await wait_for(lambda: len(fake_server.permission_replies) == 1)

    await bot_app.on_callback(
        callback_update(f"p:{session_id}:req_2:once", update_id=92, user_id=222, first_name="Luis"),
        SimpleNamespace(bot=None),
    )
    await wait_for(lambda: any("Already decided" in text for _, text in messenger.callbacks))
    assert len(fake_server.permission_replies) == 1
    assert fake_server.permission_replies[0][2] == "reject"


async def test_abort_cancels_pending_interactions(bot_app: BotApp, fake_server, store, messenger) -> None:
    fake_server.permission_request = ("req_3", "bash")
    await bot_app.on_message(
        update("@oc3bot run", entities=MENTION, update_id=1), SimpleNamespace(bot=None)
    )
    await wait_for(lambda: store.get_pending("permission:req_3") is not None)

    inbound = bot_app._inbound(update("@oc3bot stop", entities=MENTION, update_id=2))
    await bot_app.runner.abort(inbound)
    assert store.get_pending("permission:req_3").state == "cancelled"


async def test_agent_question_is_surfaced_and_answered_from_buttons(
    bot_app: BotApp, fake_server, messenger, store
) -> None:
    fake_server.form_request = {
        "id": "frm_1",
        "title": "Questions",
        "metadata": {"kind": "question"},
        "fields": [
            {
                "key": "q0",
                "title": 'Clarify "OZI"',
                "description": "What do you mean by OZI?",
                "type": "string",
                "options": [
                    {"value": "OCI compute instances", "label": "OCI compute instances"},
                    {"value": "Running containers", "label": "Running containers"},
                ],
            }
        ],
    }
    await bot_app.on_message(
        update("@oc3bot what are the OZI instances?", entities=MENTION, update_id=1),
        SimpleNamespace(bot=None),
    )

    await wait_for(lambda: any(item.keyboard is not None for item in messenger.sent))
    session_id = store.get_conversation(-100123, 0).active_session_id
    assert session_id is not None
    pending = store.get_pending("form:frm_1")
    assert pending is not None and pending.state == "pending"
    assert any('Clarify "OZI"' in item.text for item in messenger.sent)

    await bot_app.on_callback(
        callback_update(f"f:{session_id}:0:0", update_id=93, user_id=222, first_name="Luis"),
        SimpleNamespace(bot=None),
    )
    await wait_for(lambda: fake_server.form_replies != [])

    assert fake_server.form_replies == [
        (session_id, "frm_1", {"answer": {"q0": "OCI compute instances"}})
    ]
    assert store.get_pending("form:frm_1").state == "answered"
    await wait_for(
        lambda: any("Hello" in item.text for item in messenger.sent + messenger.edited)
    )
