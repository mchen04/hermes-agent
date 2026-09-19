"""LOCAL-PATCH kanban-quiet-transient: which block kinds post and which wake the origin.

A supervisor card that parks on `transient` while its coder runs must not post to the
channel or wake the front door: on 2026-09-18 about forty such notices reached #general in
twelve hours and each woke a three-paragraph reply. `needs_input` is a real question and
does both. `capability` is a wall for the operator profile: it posts once, wakes nobody.
"""

from types import SimpleNamespace

from gateway import kanban_watchers_notifier as notifier


def _blocked(kind):
    return SimpleNamespace(kind="blocked", payload={"kind": kind, "reason": f"{kind} reason"})


def _head():
    return SimpleNamespace(head="**card**")


def test_transient_block_is_silent_and_does_not_wake():
    msg, handoff, detail = notifier._EVENT_FORMATTERS["blocked"](_blocked("transient"), _head())
    assert msg is None and handoff is None and detail is None
    assert notifier._wakes_origin(_blocked("transient")) is False


def test_needs_input_block_posts_and_wakes():
    msg, _, _ = notifier._EVENT_FORMATTERS["blocked"](_blocked("needs_input"), _head())
    assert msg and "needs_input reason" in msg
    assert notifier._wakes_origin(_blocked("needs_input")) is True


def test_capability_block_posts_but_does_not_wake():
    msg, _, _ = notifier._EVENT_FORMATTERS["blocked"](_blocked("capability"), _head())
    assert msg and "capability reason" in msg
    assert notifier._wakes_origin(_blocked("capability")) is False


def test_untyped_operator_block_still_posts_and_wakes():
    event = SimpleNamespace(kind="blocked", payload={"reason": "parked by hand"})
    msg, _, _ = notifier._EVENT_FORMATTERS["blocked"](event, _head())
    assert msg and "parked by hand" in msg
    assert notifier._wakes_origin(event) is True
    bare = SimpleNamespace(kind="blocked", payload=None)
    assert notifier._wakes_origin(bare) is True


def test_other_wake_kinds_are_unchanged():
    assert notifier._wakes_origin(SimpleNamespace(kind="completed", payload={})) is True
    assert notifier._wakes_origin(SimpleNamespace(kind="auto_resumed", payload={})) is False
    assert notifier._wakes_origin(SimpleNamespace(kind="unblocked", payload={})) is False
