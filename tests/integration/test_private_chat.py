from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.telegram.app import BotApp
from app.telegram.runner import Runner
from tests.helpers import wait_for
from tests.telegram_factory import BOT_ID, BOT_USERNAME, update


@pytest.fixture
def bot_app(settings, store, client, runner, health, messenger) -> BotApp:
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    runner.set_messenger(messenger)
    runner.set_interaction_hook(app.on_interaction)
    return app


async def send(bot_app: BotApp, telegram_update) -> None:
    context = SimpleNamespace(bot=None)
    await bot_app.on_message(telegram_update, context)


async def test_prompt_is_answered_from_a_private_chat(
    bot_app: BotApp, messenger, store, fake_server
) -> None:
    await send(bot_app, update("run the tests", chat_type="private", chat_id=111, update_id=1))

    await wait_for(lambda: any("Hello" in e.text for e in messenger.edited or messenger.sent))
    assert fake_server.prompts
    _session, prompt, metadata = fake_server.prompts[0]
    assert "[speaker] name: Ana" in prompt
    assert metadata["telegram"]["user_id"] == 111
    assert store.recent_turns(1)[0].status in ("succeeded", "running")


async def test_second_prompt_reuses_the_session(bot_app: BotApp, fake_server, messenger) -> None:
    await send(bot_app, update("first", chat_type="private", chat_id=111, update_id=1))
    await wait_for(lambda: len(fake_server.prompts) == 1)
    await send(bot_app, update("second", chat_type="private", chat_id=111, update_id=2))
    await wait_for(lambda: len(fake_server.prompts) == 2)

    first_session = fake_server.prompts[0][0]
    second_session = fake_server.prompts[1][0]
    assert first_session == second_session


async def test_message_while_busy_is_steered_into_the_running_turn(
    bot_app: BotApp, fake_server, messenger, store
) -> None:
    fake_server.gate = asyncio.Event()
    await send(bot_app, update("first", chat_type="private", chat_id=111, update_id=201))
    await wait_for(lambda: len(fake_server.prompts) == 1)

    await send(bot_app, update("second", chat_type="private", chat_id=111, update_id=202))
    await wait_for(lambda: len(fake_server.prompts) == 2)

    assert fake_server.deliveries == [None, "steer"]
    assert fake_server.prompts[0][0] == fake_server.prompts[1][0]
    await wait_for(
        lambda: any("steer into the running turn" in item.text for item in messenger.sent)
    )
    assert store.recent_turns(2)[0].status == "steered"

    fake_server.gate.set()
    await wait_for(
        lambda: any(
            "I'm done" in item.text for item in messenger.sent + messenger.edited
        )
    )


async def test_silent_run_times_out_is_stopped_and_reported(
    settings, store, client, bus, health, messenger, fake_server
) -> None:
    fake_server.gate = asyncio.Event()
    runner = Runner(
        settings=settings,
        store=store,
        client=client,
        bus=bus,
        messenger=messenger,
        run_timeout=0.05,
    )
    app = BotApp(settings=settings, store=store, client=client, runner=runner, state=health)
    app.bot_id = BOT_ID
    app.bot_username = BOT_USERNAME
    app.messenger = messenger
    runner.set_messenger(messenger)
    runner.set_interaction_hook(app.on_interaction)

    await send(app, update("long task", chat_type="private", chat_id=111, update_id=150))
    await wait_for(
        lambda: any(
            "did not answer in time" in item.text for item in messenger.sent + messenger.edited
        )
    )

    session_id = store.get_conversation(111, 0).active_session_id
    assert session_id is not None
    assert fake_server.interrupts == [session_id]
    assert store.recent_turns(1)[0].status == "failed"
    assert not any("timed out waiting for the agent" in item.text for item in messenger.edited)
    fake_server.gate.set()


async def test_abort_interrupts_the_bound_session(bot_app: BotApp, fake_server, store, messenger) -> None:
    await send(bot_app, update("long task", chat_type="private", chat_id=111, update_id=1))
    await wait_for(lambda: len(fake_server.prompts) == 1)

    session_id = store.get_conversation(111, 0).active_session_id
    assert session_id is not None

    message = update("stop", chat_type="private", chat_id=111, update_id=3)
    inbound = bot_app._inbound(message)
    assert await bot_app.runner.abort(inbound) is True
    assert fake_server.interrupts == [session_id]


async def test_unauthorized_user_never_reaches_the_agent(bot_app: BotApp, fake_server, store) -> None:
    await send(bot_app, update("let me in", chat_type="private", chat_id=999, user_id=999, update_id=5))
    await wait_for(lambda: bot_app.authorizer.rejections == 1)
    assert fake_server.prompts == []
    assert store.recent_turns() == []


async def test_duplicate_update_runs_once(bot_app: BotApp, fake_server) -> None:
    telegram_update = update("do it", chat_type="private", chat_id=111, update_id=7)
    await send(bot_app, telegram_update)
    await wait_for(lambda: len(fake_server.prompts) == 1)
    await send(bot_app, telegram_update)
    await wait_for(lambda: bot_app.store.mark_seen(7) is False)
    assert len(fake_server.prompts) == 1


async def test_sign_off_is_its_own_message(bot_app: BotApp, messenger) -> None:
    await send(bot_app, update("sign off", chat_type="private", chat_id=111, update_id=121))
    await wait_for(lambda: any(item.text.startswith("✅ I'm done") for item in messenger.sent))

    answers = [m for m in messenger.sent + messenger.edited if "Hello" in m.text]
    done = [m for m in messenger.sent if m.text.startswith("✅ I'm done")]
    assert answers, "no answer"
    assert len(done) == 1, "the sign-off must be exactly one message of its own"
    assert "I'm done" not in answers[-1].text
    # That message is also what relabels the reply keyboard, so it must carry
    # the current consumption instead of a bare check mark.
    assert done[0].keyboard is not None
    assert "42K/200K (21%)" in done[0].text


async def test_consumption_lives_in_the_card_not_in_chat_noise(
    bot_app: BotApp, messenger
) -> None:
    await send(bot_app, update("how are you", chat_type="private", chat_id=111, update_id=41))
    await wait_for(lambda: any("context 42K/200K (21%)" in item.text for item in messenger.sent))

    answer = next(item for item in messenger.edited if "Hello" in item.text)
    assert "%" not in answer.text and "$" not in answer.text


async def test_run_updates_and_pins_a_dashboard(bot_app: BotApp, messenger, store) -> None:
    await send(bot_app, update("ping", chat_type="private", chat_id=111, update_id=51))
    await wait_for(lambda: bool(messenger.pinned))

    dashboard = [item for item in messenger.sent if item.text.startswith("📊")]
    assert dashboard, "no dashboard message"
    assert "context 42K/200K (21%)" in dashboard[0].text
    assert store.pinned_message_id(111, 0) == messenger.pinned[0][1]
    # No extra "keyboard updated" chatter: the card itself carries the numbers.
    assert not any("Keyboard updated" in item.text for item in messenger.sent)


async def test_second_run_edits_the_same_dashboard(bot_app: BotApp, messenger) -> None:
    await send(bot_app, update("one", chat_type="private", chat_id=111, update_id=61))
    await wait_for(lambda: bool(messenger.pinned))
    pinned_once = len(messenger.pinned)

    await send(bot_app, update("two", chat_type="private", chat_id=111, update_id=62))
    await wait_for(lambda: sum("📊" in item.text for item in messenger.edited) > 0)
    assert len(messenger.pinned) == pinned_once


def test_menu_labels_map_to_commands() -> None:
    from app.telegram.keyboards import menu_command

    assert menu_command("🧠 Model") == "/model"
    assert menu_command("📊 Context") == "/context"
    assert menu_command("hello") is None


async def test_context_command_mirrors_the_status_bar(bot_app: BotApp, messenger) -> None:
    await send(bot_app, update("hello", chat_type="private", chat_id=111, update_id=71))
    await wait_for(lambda: len(bot_app.runner.active_sessions) == 0 and store_has_session(bot_app))

    inbound = bot_app._inbound(update("/context", chat_type="private", chat_id=111, update_id=72))
    await bot_app._cmd_context(inbound, "")
    line = messenger.sent[-1].text
    assert "42K/200K (21%)" in line
    assert "queued 0" in line


def store_has_session(bot_app: BotApp) -> bool:
    conversation = bot_app.store.get_conversation(111, 0)
    return bool(conversation and conversation.active_session_id)


async def test_context_always_shows_used_and_window(bot_app: BotApp, messenger, store) -> None:
    await bot_app._cmd_new(bot_app._inbound(update("/new", chat_type="private", chat_id=111, update_id=81)), "")
    inbound = bot_app._inbound(update("/context", chat_type="private", chat_id=111, update_id=82))
    await bot_app._cmd_context(inbound, "")
    line = messenger.sent[-1].text
    assert "0/200K (0%)" in line, line
    assert "server default" not in line


async def test_fresh_session_reports_no_cost(bot_app: BotApp, messenger) -> None:
    await bot_app._cmd_new(bot_app._inbound(update("/new", chat_type="private", chat_id=111, update_id=83)), "")
    inbound = bot_app._inbound(update("/context", chat_type="private", chat_id=111, update_id=84))
    await bot_app._cmd_context(inbound, "")
    assert "$" not in messenger.sent[-1].text


async def test_agent_error_is_shown_instead_of_silence(bot_app: BotApp, messenger, fake_server) -> None:
    fake_server.answer_error = "Free usage limit reached. Connect a provider."
    await send(bot_app, update("hello", chat_type="private", chat_id=111, update_id=91))
    await wait_for(
        lambda: any("Free usage limit" in item.text for item in messenger.sent + messenger.edited)
    )
    assert any("⚠️" in item.text for item in messenger.sent + messenger.edited)


async def test_dots_show_while_thinking_and_then_become_the_answer(
    bot_app: BotApp, messenger, fake_server
) -> None:
    fake_server.gate = asyncio.Event()
    await send(bot_app, update("slow question", chat_type="private", chat_id=111, update_id=101))

    # A placeholder bubble goes out at once: Telegram Web shows nothing while
    # the agent is silent, and that is exactly when the user needs a signal.
    await wait_for(lambda: any(item.text == "…" for item in messenger.sent))
    assert messenger.typing and messenger.typing[0][0] == 111

    fake_server.gate.set()
    await wait_for(lambda: any("Hello" in item.text for item in messenger.edited))
    placeholder = next(item for item in messenger.sent if item.text == "…")
    assert any(edit.message_id == placeholder.message_id for edit in messenger.edited)
    # One answer bubble, not two: the dots were edited, never abandoned.
    assert sum(1 for item in messenger.sent if item.text == "…") == 1


async def test_new_session_resets_the_context_counter(bot_app: BotApp, messenger) -> None:
    await send(bot_app, update("hello", chat_type="private", chat_id=111, update_id=111))
    await wait_for(lambda: bool(messenger.pinned))

    await bot_app._cmd_new(
        bot_app._inbound(update("/new", chat_type="private", chat_id=111, update_id=112)), ""
    )
    await wait_for(lambda: any("context 0/200K (0%)" in item.text for item in messenger.edited))
    keyboard_messages = [item for item in messenger.sent if item.keyboard is not None]
    assert keyboard_messages, "the /new reply must carry the reset keyboard"
    labels = [b.text for row in keyboard_messages[-1].keyboard.keyboard for b in row]
    assert labels == ["🧠 Model", "📊 0 / 200K (0%)"]


async def test_replying_to_an_auto_quoted_answer_does_not_trigger(
    bot_app: BotApp, fake_server, messenger
) -> None:
    await send(bot_app, update("first", chat_type="private", chat_id=111, update_id=141))
    await wait_for(lambda: any("I'm done" in item.text for item in messenger.sent))
    prompts_before = len(fake_server.prompts)

    from tests.telegram_factory import message_payload

    done = message_payload(
        text="✅ I'm done", chat_id=111, chat_type="private", user_id=424242, first_name="Bot", is_bot=True
    )
    await send(
        bot_app,
        update("accidental reply", chat_type="private", chat_id=111, update_id=142, reply_to=done),
    )
    await wait_for(lambda: True, timeout=0.5)
    assert len(fake_server.prompts) == prompts_before
