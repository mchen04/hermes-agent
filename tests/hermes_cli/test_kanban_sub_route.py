"""LOCAL-PATCH kanban-sub-route: explicit subscriptions land in the chat's own session."""

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "route.db"))
    kb.init_db()
    c = kbc.connect()
    yield c
    c.close()


def _config(monkeypatch, kanban):
    import hermes_cli.config as config
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": kanban})


def _sub(conn, **kwargs):
    tid = kb.create_task(conn, title="route", assignee="worker")
    kbn.add_notify_sub(conn, task_id=tid, platform=kwargs.pop("platform", "discord"),
                       chat_id="123", **kwargs)
    return kbn.list_notify_subs(conn, tid)[0]


def test_discord_channel_is_stored_as_group(conn, monkeypatch):
    _config(monkeypatch, {})
    assert _sub(conn, chat_type="channel")["chat_type"] == "group"


def test_telegram_channel_is_kept(conn, monkeypatch):
    _config(monkeypatch, {})
    assert _sub(conn, platform="telegram", chat_type="channel")["chat_type"] == "channel"


def test_resubscribe_does_not_downgrade_discord_group(conn, monkeypatch):
    _config(monkeypatch, {})
    tid = kb.create_task(conn, title="route", assignee="worker")
    kbn.add_notify_sub(conn, task_id=tid, platform="discord", chat_id="123", chat_type="group")
    kbn.add_notify_sub(conn, task_id=tid, platform="discord", chat_id="123", chat_type="channel",
                       delivery_mode="notify+wake")
    assert kbn.list_notify_subs(conn, tid)[0]["chat_type"] == "group"


def test_configured_wake_overrides_explicit_notify_wake(conn, monkeypatch):
    _config(monkeypatch, {"notify_delivery_mode": "wake"})
    assert _sub(conn, chat_type="group", delivery_mode="notify+wake")["delivery_mode"] == "wake"


def test_default_config_keeps_explicit_notify_wake(conn, monkeypatch):
    _config(monkeypatch, {})
    assert _sub(conn, chat_type="group", delivery_mode="notify+wake")["delivery_mode"] == "notify+wake"
