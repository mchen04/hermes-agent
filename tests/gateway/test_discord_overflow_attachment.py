"""LOCAL-PATCH discord-overflow-attachment: a reply over the split cap keeps the flood guard and
carries its full text as a Markdown file, so nothing past the cap is lost in the channel."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.gateway.test_discord_split_cap import CAP, _huge_content, _make_adapter
import plugins.platforms.discord.adapter as adapter_module


class _File:
    def __init__(self, stream, filename):
        self.data = stream.read().decode("utf-8")
        self.filename = filename


@pytest.mark.asyncio
async def test_capped_reply_attaches_the_full_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(adapter_module.discord, "File", _File)
    adapter = _make_adapter()
    sends = []

    async def fake_send(*, content, reference=None, file=None):
        sends.append((content, file))
        return SimpleNamespace(id=9000 + len(sends))

    channel = SimpleNamespace(id=555, send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    content = _huge_content()

    result = await adapter.send("555", content)

    assert result.success is True
    assert len(sends) == CAP
    assert all(file is None for _, file in sends[:-1])
    notice, attached = sends[-1]
    assert "attached as full-reply.md" in notice
    assert attached.filename == "full-reply.md"
    assert attached.data == content


@pytest.mark.asyncio
async def test_reply_under_the_cap_has_no_attachment(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(adapter_module.discord, "File", _File)
    adapter = _make_adapter()
    sends = []

    async def fake_send(*, content, reference=None, file=None):
        sends.append(file)
        return SimpleNamespace(id=9000 + len(sends))

    channel = SimpleNamespace(id=555, send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())

    result = await adapter.send("555", "short reply " * 300)

    assert result.success is True
    assert 1 < len(sends) <= CAP
    assert sends == [None] * len(sends)
