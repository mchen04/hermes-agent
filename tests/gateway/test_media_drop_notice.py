"""LOCAL-PATCH media-drop-notice: a MEDIA: attachment the delivery policy drops is named, never silent.

The policy (strict mode, allowlisted folders) is unchanged. What changes: the user sees
"Not attached: <name>" next to the reply, and the agent's next turn carries a note naming
the dropped path, so neither believes the file was attached.
"""

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType, SessionSource
from tests.gateway.test_run_progress_topics import ProgressCaptureAdapter, _make_runner

_SESSION_KEY = "agent:main:discord:group:-1001:17585"


class _Entry:
    session_id = "s1"


def _event():
    source = SessionSource(platform=Platform.DISCORD, chat_id="-1001", chat_type="group", thread_id="17585")
    return source, MessageEvent(text="hi", message_type=MessageType.TEXT, source=source, message_id="1")


@pytest.fixture
def strict_policy(monkeypatch, tmp_path):
    """Strict delivery: only the Hermes caches and an operator folder may be attached."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "1")
    monkeypatch.setenv("HERMES_MEDIA_TRUST_RECENT_FILES", "0")
    monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(allowed))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return allowed, workspace


async def _deliver(runner, response, *, already_sent=False):
    source, event = _event()
    return await runner._hmwa_deliver_turn_response(
        event, source, _Entry(), _SESSION_KEY, None,
        {"already_sent": already_sent, "failed": False}, [], response, None, False,
    )


@pytest.mark.asyncio
async def test_dropped_attachment_is_named_to_the_user_and_the_next_turn(strict_policy):
    _allowed, workspace = strict_policy
    zip_path = workspace / "private-article-preview-r2.zip"
    zip_path.write_bytes(b"PK")
    runner = _make_runner(ProgressCaptureAdapter(platform=Platform.DISCORD))
    text = await _deliver(runner, f"Final preview attached.\n\nMEDIA:{zip_path}")

    assert text.endswith(
        "⚠️ Not attached: `private-article-preview-r2.zip` (denied by the delivery policy).")
    (note,) = runner._consume_pending_turn_sidecar_notes(_SESSION_KEY)
    assert str(zip_path) in note and "NOT delivered" in note and "Do not say they were attached" in note
    # One-shot: consumed once.
    assert runner._consume_pending_turn_sidecar_notes(_SESSION_KEY) == []


@pytest.mark.asyncio
async def test_allowed_attachment_adds_nothing(strict_policy):
    allowed, _workspace = strict_policy
    ok = allowed / "chart.png"
    ok.write_bytes(b"\x89PNG")
    runner = _make_runner(ProgressCaptureAdapter(platform=Platform.DISCORD))
    response = f"Chart attached.\n\nMEDIA:{ok}"
    assert await _deliver(runner, response) == response
    assert runner._consume_pending_turn_sidecar_notes(_SESSION_KEY) == []


@pytest.mark.asyncio
async def test_streamed_reply_gets_the_notice_as_a_trailing_message(strict_policy):
    _allowed, workspace = strict_policy
    missing = workspace / "never-written.pdf"
    adapter = ProgressCaptureAdapter(platform=Platform.DISCORD)
    runner = _make_runner(adapter)
    assert await _deliver(runner, f"Report attached.\n\nMEDIA:{missing}", already_sent=True) is None
    assert [m["content"] for m in adapter.sent] == [
        "⚠️ Not attached: `never-written.pdf` (not found on this host)."]


def test_next_turn_notes_keep_the_carried_note():
    runner = _make_runner(ProgressCaptureAdapter(platform=Platform.DISCORD))
    runner._append_pending_turn_sidecar_note(_SESSION_KEY, "carried")
    # The next turn stages its own notes before the agent runs; the carried note survives.
    runner._set_pending_turn_sidecar_notes(_SESSION_KEY, ["first contact"])
    assert runner._consume_pending_turn_sidecar_notes(_SESSION_KEY) == ["carried", "first contact"]
    runner._set_pending_turn_sidecar_notes(_SESSION_KEY, ["later"])
    assert runner._consume_pending_turn_sidecar_notes(_SESSION_KEY) == ["later"]
