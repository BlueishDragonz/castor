import datetime

import pytest

from castor.events import TickChanged
from castor.logger import logger
from castor.realtime import ConnectionManager
from castor.routes.api import _websocket_tick_text
from castor.storage.dict import DictHabit


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)


def test_websocket_null_text_maps_to_empty_note() -> None:
    assert _websocket_tick_text({"text": None}) == ""
    assert _websocket_tick_text({"text": "note"}) == "note"
    assert _websocket_tick_text({}) is None


async def test_empty_text_clears_existing_note() -> None:
    habit = DictHabit(
        {"id": "habit-1", "name": "Test", "records": []},
        habit_list=object(),  # type: ignore[arg-type]
    )
    day = datetime.date(2026, 7, 11)
    await habit.tick(day, True, "existing note")

    record = await habit.tick(day, True, text="")

    assert record.text == ""


async def test_broadcast_logs_each_successful_websocket_push() -> None:
    manager = ConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    await manager.connect("user-1", first)  # type: ignore[arg-type]
    await manager.connect("user-1", second)  # type: ignore[arg-type]

    logs: list[str] = []
    sink = logger.add(logs.append, format="{message}")
    try:
        await manager.broadcast(
            TickChanged(
                user_id="user-1",
                habit_id="habit-1",
                day=datetime.date(2026, 7, 11),
                done=True,
                text=None,
                timestamp=1_783_728_000_000,
            )
        )
    finally:
        logger.remove(sink)

    assert first.messages == second.messages
    assert first.messages[0]["type"] == "tick_changed"
    matching_logs = [line for line in logs if "[ws] broadcast tick_changed" in line]
    assert len(matching_logs) == 2
    assert all(
        "user=user-1 habit=habit-1 day=2026-07-11" in line for line in matching_logs
    )


# ---------------------------------------------------------------------------
# F15 — a failed push must be reported to the client, not swallowed
# ---------------------------------------------------------------------------
#
# The audit found both WebSocket handlers ending in a bare
# `except Exception: logger.warning`. The client had already sent a
# request_id and was waiting for an ack; on failure it received nothing, and
# concluded the write had succeeded. Partial completion with no detection on
# either side.
#
# These tests pin the contract: for the same request_id the client gets
# exactly one frame, and when the write fails that frame is the error type,
# not the success type. A silent-drop regression fails here.


def test_f15_source_does_not_swallow_websocket_errors() -> None:
    """No `except Exception` in the WS handler may end at a log call.

    Structural rather than behavioural because the failure path is hard to
    reach through the real socket stack, but the defect itself was structural:
    a bare log-and-continue. Both handlers now send an explicit error frame,
    and this asserts that shape survives future edits.
    """
    import inspect

    from castor.routes import api

    source = inspect.getsource(api)
    # Every broad `except Exception` in the WS handler must be followed by a
    # client-visible error frame.
    assert "tick_error" in source, "push_tick must report failures to the client"
    assert "habit_list_error" in source, (
        "push_habit_list must report failures to the client"
    )
    # The old swallow pattern: a log call as the entire handler body.
    assert "logger.warning(f\"[ws] failed to" not in source, (
        "a log-and-continue handler is exactly the F15 defect"
    )


async def test_f15_failed_push_tick_sends_an_error_frame() -> None:
    """A tick that raises must produce tick_error, not silence.

    Drives the real handler. `views.get_user_habit` is patched to raise, which
    is the shape of failure the audit described (a data-layer error being
    swallowed). The assertion is that the client is told.
    """
    from unittest.mock import patch

    import castor.routes.api as api_module
    from fastapi import WebSocketDisconnect

    # sync_ws runs the real IP rate limiter, which needs its table to exist.
    from castor.app.db import create_db_and_tables

    await create_db_and_tables()

    sent: list[dict] = []

    class Client:
        host = "127.0.0.1"

    class Socket:
        client = Client()

        def __init__(self):
            # Deliver exactly one message, then behave like a peer that has
            # gone away. Without this the handler's `while True` loops forever
            # re-reading the same message.
            self._delivered = False

        async def receive_json(self):
            if self._delivered:
                raise WebSocketDisconnect()
            self._delivered = True
            return {
                "type": "push_tick",
                "request_id": "req-42",
                "habit_id": "habit-1",
                "day": "2026-07-11",
                "done": True,
            }

        async def send_json(self, message):
            sent.append(message)

        async def close(self, code=1000):
            pass

    class User:
        id = "user-1"
        email = "user@example.com"
        is_active = True

    socket = Socket()
    manager = api_module.manager
    connected: list = []

    async def fake_connect(user_id, ws):
        connected.append(user_id)

    def fake_disconnect(user_id, ws):
        pass

    async def raise_on_tick(*args, **kwargs):
        # The realistic failure: a data-layer error (locked/corrupt DB — F9).
        raise RuntimeError("database is locked")

    with (
        patch.object(manager, "connect", fake_connect),
        patch.object(manager, "disconnect", fake_disconnect),
        patch.object(api_module, "_authenticate_ws", _returning(User)),
        patch.object(api_module.views, "get_user_habit", raise_on_tick),
    ):
        # The handler catches WebSocketDisconnect itself and returns, so a
        # clean return IS the expected outcome here.
        await api_module.sync_ws(socket, token="token")

    # The client must be told the tick was not applied, and it must be tied to
    # the request it made.
    errors = [m for m in sent if m.get("type") == "tick_error"]
    assert len(errors) == 1, f"expected exactly one error frame, got {sent}"
    assert errors[0]["request_id"] == "req-42"
    # And crucially, no ack was sent — the client must not believe it worked.
    assert not [m for m in sent if m.get("type") == "tick_ack"]


def _returning(value):
    """Build a zero-arg async callable returning `value`."""

    async def _fn(*args, **kwargs):
        return value

    return _fn
