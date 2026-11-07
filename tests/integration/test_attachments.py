from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.telegram.attachments import AttachmentError, collect


class FakeFile:
    def __init__(self, size: int = 100) -> None:
        self.file_id = "file-1"
        self.file_unique_id = "uniq-1"
        self.file_size = size

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"payload")


class FakeBot:
    def __init__(self, size: int = 100) -> None:
        self._size = size

    async def get_file(self, file_id: str) -> FakeFile:
        return FakeFile(self._size)


def photo_message(size: int = 100):
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id="small", file_unique_id="u", file_size=size)],
        document=None,
    )


def document_message(name: str, mime: str | None, size: int = 100):
    return SimpleNamespace(
        photo=None,
        document=SimpleNamespace(
            file_id="doc", file_unique_id="d", file_size=size, file_name=name, mime_type=mime
        ),
    )


async def test_photo_is_accepted_as_a_data_uri(settings) -> None:
    refs = await collect(FakeBot(), photo_message(), settings)
    assert refs
    prefix, encoded = refs[0]["uri"].split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    assert base64.b64decode(encoded) == b"payload"


async def test_oversized_file_is_refused_with_the_limit(settings) -> None:
    too_big = settings.max_attachment_mb * 1024 * 1024 + 1
    with pytest.raises(AttachmentError) as excinfo:
        await collect(FakeBot(too_big), photo_message(too_big), settings)
    assert str(settings.max_attachment_mb) in str(excinfo.value)


async def test_unsupported_type_is_refused(settings) -> None:
    with pytest.raises(AttachmentError):
        await collect(FakeBot(), document_message("archive.zip", "application/zip"), settings)


async def test_text_document_is_accepted(settings) -> None:
    refs = await collect(FakeBot(), document_message("notes.md", None), settings)
    assert refs[0]["uri"].startswith("data:text/markdown;base64,")
    assert refs[0]["name"] == "notes.md"


async def test_message_without_files_returns_empty(settings) -> None:
    empty = SimpleNamespace(photo=None, document=None)
    assert await collect(FakeBot(), empty, settings) == []
