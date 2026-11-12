from __future__ import annotations

from app.telegram.streaming import StreamEditor, escape, split_message
from tests.fakes.telegram import FakeMessenger


def test_split_respects_limit_and_boundaries() -> None:
    text = ("paragraph one\n\n" * 500).strip()
    chunks = split_message(text, limit=200)
    assert chunks
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert all(chunk for chunk in chunks)


def test_split_short_text_is_single_chunk() -> None:
    assert split_message("hello") == ["hello"]


def test_escape_neutralises_html() -> None:
    assert escape("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"


async def test_editor_coalesces_edits() -> None:
    messenger = FakeMessenger()
    clock = {"now": 100.0}

    def now() -> float:
        return clock["now"]

    editor = StreamEditor(
        messenger.send_text, messenger.edit_text, chat_id=1, min_interval=1.0, now=now
    )
    await editor.start()
    # The three dots go out at once: the chat must show it is working even when
    # the client hides the typing action.
    assert [sent.text for sent in messenger.sent] == ["…"]

    editor.append("hello")
    await editor.flush()
    assert [sent.text for sent in messenger.sent] == ["…"]
    assert messenger.last_text == "hello"  # the dots became the answer
    clock["now"] += 0.1
    editor.append(" world")
    await editor.flush()
    # Too soon: no second edit yet.
    assert len(messenger.edited) == 1

    clock["now"] += 1.5
    await editor.flush()
    assert len(messenger.edited) == 2
    assert "hello world" in messenger.last_text


async def test_placeholder_is_replaced_in_the_same_message() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text, messenger.edit_text, chat_id=1, min_interval=5.0, now=lambda: 0.0
    )
    await editor.start()
    placeholder = messenger.sent[0]
    editor.append("answer")
    await editor.flush()
    assert messenger._next_id == placeholder.message_id  # no second bubble
    assert messenger.edited[-1].message_id == placeholder.message_id
    assert messenger.edited[-1].text == "answer"


async def test_finish_without_text_leaves_the_notice_instead_of_the_dots() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text, messenger.edit_text, chat_id=1, min_interval=0.0, now=lambda: 0.0
    )
    await editor.start()
    await editor.finish("The agent did not answer in time.")
    assert messenger.last_text == "The agent did not answer in time."


async def test_answer_is_resent_when_the_bubble_cannot_be_edited() -> None:
    messenger = FakeMessenger()

    async def broken_edit(*_args, **_kwargs) -> None:
        raise RuntimeError("message to edit not found")

    editor = StreamEditor(
        messenger.send_text, broken_edit, chat_id=1, min_interval=0.0, now=lambda: 0.0
    )
    await editor.start()
    editor.append("answer")
    await editor.flush()
    # The dots were deleted (or the edit was refused): the answer still lands.
    assert [sent.text for sent in messenger.sent] == ["…", "answer"]


async def test_placeholder_can_be_disabled() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text,
        messenger.edit_text,
        chat_id=1,
        min_interval=0.0,
        now=lambda: 0.0,
        placeholder=None,
    )
    await editor.start()
    assert messenger.sent == []


async def test_editor_splits_growing_answer_into_new_messages() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text, messenger.edit_text, chat_id=1, min_interval=0.0, now=lambda: 0.0
    )
    await editor.start()
    editor.append("y" * 9000)
    await editor.flush(force=True)
    assert len(messenger.sent) >= 3
    assert all(len(sent.text) <= 4096 for sent in messenger.sent)


async def test_deferred_editor_shows_only_the_filtered_final_text() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text,
        messenger.edit_text,
        chat_id=1,
        min_interval=0.0,
        now=lambda: 0.0,
        defer_until_finish=True,
    )
    await editor.start()
    assert [sent.text for sent in messenger.sent] == ["…"]  # dots while it thinks
    editor.append("No puedo generar audio.")
    await editor.flush()
    assert [sent.text for sent in messenger.sent] == ["…"]

    editor.replace('"Small steps still move you forward."')
    await editor.finish()
    assert [sent.text for sent in messenger.sent] == ["…"]
    assert messenger.last_text == '"Small steps still move you forward."'


async def test_editor_prefixes_attribution_once() -> None:
    messenger = FakeMessenger()
    editor = StreamEditor(
        messenger.send_text,
        messenger.edit_text,
        chat_id=1,
        min_interval=0.0,
        now=lambda: 0.0,
        prefix="Ana:",
    )
    await editor.start()
    editor.append("answer")
    await editor.flush(force=True)
    assert messenger.last_text.startswith("Ana:\nanswer")
