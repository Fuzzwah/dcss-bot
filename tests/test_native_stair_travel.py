"""Native map selection, guarded same-floor arrival and control boundaries."""
import asyncio
import json

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.config import Config
from dcss_bot.models import Action
from dcss_bot.recording import Recorder
from dcss_bot.supervisor import Supervisor


class Packets:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.keys = asyncio.Queue()
        self.sent = []

    async def send(self, payload):
        message = json.loads(payload)
        self.sent.append(message)
        if "keycode" in message:
            self.keys.put_nowait(message["keycode"])

    async def recv(self):
        return await self.incoming.get()

    def feed(self, *messages):
        self.incoming.put_nowait(json.dumps({"msgs": list(messages)}))

    async def key(self, expected):
        assert await asyncio.wait_for(self.keys.get(), 2) == expected

    def game_keys(self):
        return [message["keycode"] for message in self.sent if "keycode" in message]


def adapter_at_floor():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE, timeout=1)
    adapter.socket = Packets()
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 30, "hp_max": 30, "turn": 10, "place": "D", "depth": 2,
                         "pos": {"x": 0, "y": 0}})
    adapter.state.apply({"msg": "map", "cells": [
        {"x": 0, "y": 0, "f": 33, "t": {"bg": 1}},
        {"x": 2, "y": 0, "f": 45},
        {"x": 3, "y": 0, "f": 71},
        {"x": 4, "y": 0, "f": 43},
        {"x": 5, "y": 0, "f": 42},
    ]})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return adapter


def cursor(x, y=0, ident=2):
    return {"msg": "cursor", "id": ident, "loc": {"x": x, "y": y}}


async def open_map(adapter):
    await adapter.socket.key(ord("X"))
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "ui_state", "state": 2}, cursor(0))
    await adapter.socket.key(ord(">"))


async def commit_target(adapter):
    await open_map(adapter)
    adapter.socket.feed(cursor(5))
    await adapter.socket.key(13)


def finish_map_cancel(adapter):
    adapter.socket.feed({"msg": "cursor", "id": 2},
                        {"msg": "ui_state", "state": 0},
                        {"msg": "input_mode", "mode": 1})


def arrive(adapter, **player):
    adapter.socket.feed({"msg": "ui_state", "state": 0},
                        {"msg": "cursor", "id": 2},
                        {"msg": "player", "turn": 15, "pos": {"x": 5, "y": 0}, **player},
                        {"msg": "input_mode", "mode": 1})


@pytest.mark.parametrize("value", ["", "1", "1,", "1,2,3", "01,2", "1.0,2", "1,2\n", "a,2", "10000,2"])
def test_travel_requires_bounded_explicit_public_coordinates(value):
    with pytest.raises(ValueError):
        Action("travel_downstairs", value)


@pytest.mark.parametrize("value", ["0,0", "-12,34", "9999,-9999"])
def test_travel_coordinate_validation_preserves_origin_relative_coordinates(value):
    assert Action("travel_downstairs", value, objective="descend").value == value


@pytest.mark.parametrize("unsafe", ["unknown", "hatch_down", "hatch_up", "branch", "exit",
                                    "hostile", "hurt", "status", "cloud", "target_cloud", "menu"])
async def test_invalid_or_unsafe_travel_never_opens_map(unsafe):
    adapter = adapter_at_floor()
    target = "5,0"
    if unsafe == "unknown":
        target = "99,0"
    elif unsafe in {"hatch_down", "hatch_up", "branch", "exit"}:
        feature = {"hatch_down": 45, "hatch_up": 49, "branch": 71, "exit": 60}[unsafe]
        adapter.state.apply({"msg": "map", "cells": [{"x": 5, "y": 0, "f": feature}]})
    elif unsafe == "hostile":
        adapter.state.apply({"msg": "map", "cells": [{"x": 1, "y": 0, "t": {"bg": 1},
                             "mon": {"id": 4, "name": "orc", "att": 0}}]})
    elif unsafe == "hurt":
        adapter.state.apply({"msg": "player", "hp": 29})
    elif unsafe == "status":
        adapter.state.apply({"msg": "player", "status": [{"light": "Slow"}]})
    elif unsafe in {"cloud", "target_cloud"}:
        adapter.state.apply({"msg": "map", "cells": [{"x": 5 if unsafe == "target_cloud" else 0,
                             "y": 0, "t": {"cloud": 1}}]})
    else:
        adapter.state.apply({"msg": "menu", "tag": "inventory", "items": []})
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("travel_downstairs", target))
    assert adapter.socket.sent == []
    assert adapter.state.observation().turn == 10


async def test_floor_map_reset_cannot_reuse_a_foreign_floor_destination():
    adapter = adapter_at_floor()
    adapter.state.apply({"msg": "player", "depth": 3})
    adapter.state.apply({"msg": "map", "clear": True, "cells": [{"x": 0, "y": 0, "f": 33}]})
    with pytest.raises(ProtocolError, match="known ordinary downstairs"):
        await adapter.dispatch(Action("travel_downstairs", "5,0"))
    assert adapter.socket.sent == []


async def test_native_map_cycles_past_hatch_branch_and_other_stair_then_arrives_without_descending():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0"), automation_delay=.02))
    await open_map(adapter)
    for selected in (2, 3, 4):
        adapter.socket.feed(cursor(selected))
        await adapter.socket.key(ord(">"))
        assert 13 not in adapter.socket.game_keys()
        assert adapter.state.observation().position == (0, 0)
    adapter.socket.feed(cursor(5))
    await adapter.socket.key(13)
    # Enter leaves an already-NORMAL map; its explicit UI exit acknowledges it.
    adapter.socket.feed({"msg": "ui_state", "state": 0}, {"msg": "cursor", "id": 2},
                        {"msg": "player", "turn": 12, "pos": {"x": 2, "y": 0}})
    await asyncio.sleep(.15)
    assert not task.done()
    arrive(adapter)
    observed = await asyncio.wait_for(task, 2)
    assert (observed.location, observed.position, observed.turn) == ("D:2", (5, 0), 15)
    assert (observed.native_automation, observed.native_result) == ("travel_downstairs", "complete")
    assert not adapter.native_activity
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), ord(">"), ord(">"), ord(">"), 13]
    assert {message["line"] for message in adapter.socket.sent if message["msg"] == "set_option"} >= {
        "travel_delay = 20", "explore_delay = 20", "travel_key_stop = true"}


async def test_mouse_cursor_or_old_command_packets_cannot_authorize_enter():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await open_map(adapter)
    adapter.socket.feed(cursor(5, ident=0), {"msg": "input_mode", "mode": 1})
    await asyncio.sleep(.15)
    assert adapter.socket.game_keys() == [ord("X"), ord(">")]
    assert not task.done()
    adapter.socket.feed(cursor(5))
    await adapter.socket.key(13)
    adapter.socket.feed({"msg": "input_mode", "mode": 1})
    await asyncio.sleep(.15)
    assert not task.done()  # Map's own UI exit has not acknowledged Enter yet.
    arrive(adapter)
    assert (await asyncio.wait_for(task, 2)).native_result == "complete"


@pytest.mark.parametrize("ending", ["short", "wrong_stair", "different_floor", "hurt", "status", "hostile"])
async def test_ready_without_safe_exact_same_floor_arrival_is_not_complete(ending):
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await commit_target(adapter)
    delta = {}
    if ending == "short":
        delta["pos"] = {"x": 2, "y": 0}
    elif ending == "wrong_stair":
        delta["pos"] = {"x": 4, "y": 0}
    elif ending == "different_floor":
        delta["depth"] = 3
    elif ending == "hurt":
        delta["hp"] = 28
    elif ending == "status":
        delta["status"] = [{"light": "Pois"}]
    else:
        adapter.socket.feed({"msg": "map", "cells": [{"x": 6, "y": 0, "t": {"bg": 1},
                             "mon": {"id": 8, "name": "orc", "att": 0}}]})
    arrive(adapter, **delta)
    observed = await asyncio.wait_for(task, 2)
    assert observed.native_result == "interrupted"
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13]


@pytest.mark.parametrize("reason", ["control", "turn_limit", "yielded", "danger"])
async def test_busy_travel_cancellation_waits_for_consumed_redraw_not_only_travel_stop(reason):
    adapter = adapter_at_floor()
    stop = asyncio.Event()
    guard = lambda obs: reason if stop.is_set() and reason != "danger" else ""
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0"), automation_guard=guard))
    await commit_target(adapter)
    adapter.socket.feed({"msg": "ui_state", "state": 0},
                        {"msg": "player", "turn": 11, "pos": {"x": 1, "y": 0}})
    await asyncio.sleep(.15)
    if reason == "danger":
        adapter.socket.feed({"msg": "player", "hp": 20})
    else:
        stop.set()
    await adapter.socket.key(18)
    adapter.socket.feed({"msg": "input_mode", "mode": 1})
    await asyncio.sleep(.15)
    assert not task.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 2)
    assert observed.native_result == ("interrupted" if reason == "danger" else reason)
    assert observed.location == "D:2" and observed.position == (1, 0)
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13, 18]


async def test_map_selection_control_closes_map_without_committing_travel():
    adapter = adapter_at_floor()
    stop = asyncio.Event()
    task = asyncio.create_task(adapter.dispatch(
        Action("travel_downstairs", "5,0"), automation_guard=lambda _: "control" if stop.is_set() else ""))
    await open_map(adapter)
    stop.set()
    adapter.socket.feed(cursor(5))
    await adapter.socket.key(27)
    assert not task.done()
    finish_map_cancel(adapter)
    observed = await asyncio.wait_for(task, 2)
    assert observed.native_result == "control" and observed.turn == 10
    assert observed.position == (0, 0) and observed.input_mode == 1
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 27]


async def test_changed_selected_terrain_is_cancelled_without_enter():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await open_map(adapter)
    adapter.socket.feed({"msg": "map", "cells": [{"x": 5, "y": 0, "f": 45}]}, cursor(5))
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    with pytest.raises(ProtocolError, match="no longer ordinary downstairs"):
        await asyncio.wait_for(task, 2)
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 27]


async def test_map_selection_wrap_is_finite_and_never_uses_the_wrong_stair():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await open_map(adapter)
    for selected in (2, 3):
        adapter.socket.feed(cursor(selected))
        await adapter.socket.key(ord(">"))
    adapter.socket.feed(cursor(2))
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    with pytest.raises(ProtocolError, match="selection cycle"):
        await asyncio.wait_for(task, 2)
    assert 13 not in adapter.socket.game_keys()


async def test_missing_cursor_response_times_out_and_closes_map_without_retrying_selection():
    adapter = adapter_at_floor()
    adapter.timeout = .3
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await open_map(adapter)
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    with pytest.raises(TimeoutError, match="fresh native map cursor"):
        await asyncio.wait_for(task, 2)
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 27]


async def test_unexpected_menu_during_map_selection_is_cancelled_without_a_selection_hotkey():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await open_map(adapter)
    adapter.socket.feed({"msg": "menu", "tag": "unexpected", "items": []},
                        {"msg": "input_mode", "mode": 7})
    await adapter.socket.key(27)
    adapter.socket.feed({"msg": "close_menu"}, {"msg": "input_mode", "mode": 0})
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    with pytest.raises(ProtocolError, match="unexpected interface"):
        await asyncio.wait_for(task, 2)
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 27, 27]


async def test_prompt_continuation_keeps_exact_travel_goal_without_replaying_map_transaction():
    adapter = adapter_at_floor()
    first = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await commit_target(adapter)
    adapter.socket.feed({"msg": "ui_state", "state": 0}, {"msg": "input_mode", "mode": 5})
    assert (await asyncio.wait_for(first, 2)).native_result == "interrupted"
    assert adapter.native_activity == "travel_downstairs"
    continued = asyncio.create_task(adapter.dispatch(Action("more")))
    await adapter.socket.key(ord(" "))
    adapter.socket.feed({"msg": "input_mode", "mode": 0})
    arrive(adapter)
    assert (await asyncio.wait_for(continued, 2)).native_result == "complete"
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13, ord(" ")]


async def test_disconnect_after_enter_keeps_goal_and_reconnect_cancels_busy_activity_without_replay():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await commit_target(adapter)
    old_recv = adapter.socket.recv
    async def disconnected():
        raise OSError("connection lost")
    adapter.socket.recv = disconnected
    adapter.socket.feed({"msg": "ui_state", "state": 0})
    with pytest.raises(OSError):
        await asyncio.wait_for(task, 2)
    assert adapter.native_activity == "travel_downstairs"
    adapter.socket.recv = old_recv
    adapter._map_ui_state = 0  # A reconnect's complete public UI snapshot.
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    resumed = asyncio.create_task(adapter._resumed_observation())
    await adapter.socket.key(18)
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(resumed, 2)
    assert observed.input_mode == 1 and not adapter.native_activity
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13, 18]


async def test_task_cancellation_before_enter_response_waits_for_map_exit_then_redraw_ack():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await commit_target(adapter)
    task.cancel()
    await asyncio.sleep(.05)
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13]
    adapter.socket.feed({"msg": "ui_state", "state": 0})
    await adapter.socket.key(18)
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert not adapter.native_activity
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13, 18]


class StairAgent:
    async def decide(self, observation):
        return Action("travel_downstairs", "5,0", objective="descend")


@pytest.mark.parametrize("control", ["pause", "stop", "explore-delay", "step"])
async def test_supervisor_controls_cancel_real_travel_only_at_acknowledged_boundary(tmp_path, control, monkeypatch):
    adapter = adapter_at_floor()
    recorder = Recorder(tmp_path, {})
    supervisor = Supervisor(Config(delay=.4, explore_delay=.02), StairAgent(), adapter, recorder)
    supervisor.observation = adapter.state.observation()
    supervisor._initial_turn = 10
    supervisor._ready = True
    if control == "step":
        # Leave time for the acknowledged map selection before testing expiry
        # during travel; receive() deliberately coalesces public packet bursts.
        monkeypatch.setattr("dcss_bot.supervisor._NATIVE_STEP_QUANTUM", 1)
        supervisor._paused = True
        supervisor._steps = 1
    task = asyncio.create_task(supervisor._act())
    try:
        await commit_target(adapter)
        adapter.socket.feed({"msg": "ui_state", "state": 0},
                            {"msg": "player", "turn": 11, "pos": {"x": 1, "y": 0}})
        if control == "stop":
            supervisor.request_stop()
        elif control == "step":
            await asyncio.sleep(.05)
        else:
            await supervisor._control({"command": control, "value": .03})
        await adapter.socket.key(18)
        assert not task.done()
        adapter.socket.feed({"msg": "input_mode", "mode": 1})
        await asyncio.sleep(.15)
        assert not task.done()
        adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
        await asyncio.wait_for(task, 2)
        assert supervisor.observation.native_result == ("yielded" if control == "step" else "control")
        assert supervisor.observation.position == (1, 0)
        assert supervisor.actions == 1
    finally:
        if not task.done():
            task.cancel()
            adapter.socket.feed({"msg": "ui_state", "state": 0},
                                {"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
        recorder.close()
    recorded = [json.loads(line) for line in (recorder.path / "actions.jsonl").read_text().splitlines()]
    assert len(recorded) == 1 and recorded[0]["action"]["kind"] == "travel_downstairs"


async def test_ordinary_decision_delay_change_does_not_cancel_native_stair_travel(tmp_path, monkeypatch):
    adapter = adapter_at_floor()
    recorder = Recorder(tmp_path, {})
    supervisor = Supervisor(Config(delay=.4, explore_delay=.02), StairAgent(), adapter, recorder)
    supervisor.observation = adapter.state.observation()
    supervisor._initial_turn = 10
    monkeypatch.setattr("dcss_bot.supervisor._NATIVE_STEP_QUANTUM", .01)
    task = asyncio.create_task(supervisor._act())
    try:
        await commit_target(adapter)
        adapter.socket.feed({"msg": "ui_state", "state": 0},
                            {"msg": "player", "turn": 11, "pos": {"x": 1, "y": 0}})
        await supervisor._control({"command": "delay", "value": 2})
        await asyncio.sleep(.15)
        assert not task.done() and 18 not in adapter.socket.game_keys()
        arrive(adapter)
        await asyncio.wait_for(task, 2)
        assert supervisor.observation.native_result == "complete"
        assert supervisor.delay == 2 and supervisor.explore_delay == .02
    finally:
        recorder.close()


async def test_stalled_native_travel_times_out_without_replaying_enter():
    adapter = adapter_at_floor()
    adapter.timeout = .3
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await commit_target(adapter)
    adapter.socket.feed({"msg": "ui_state", "state": 0},
                        {"msg": "player", "turn": 11, "pos": {"x": 1, "y": 0}})
    await adapter.socket.key(18)
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 2)
    assert observed.native_result == "timeout"
    assert observed.position == (1, 0) and observed.input_mode == 1
    assert adapter.socket.game_keys() == [ord("X"), ord(">"), 13, 18]


async def test_old_normal_ui_snapshot_cannot_replace_fresh_map_open_acknowledgement():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await adapter.socket.key(ord("X"))
    adapter.socket.feed({"msg": "ui_state", "state": 0}, {"msg": "input_mode", "mode": 1})
    await asyncio.sleep(.15)
    assert adapter.socket.game_keys() == [ord("X")]
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "ui_state", "state": 2}, cursor(0))
    await adapter.socket.key(ord(">"))
    adapter.socket.feed(cursor(5))
    await adapter.socket.key(13)
    arrive(adapter)
    assert (await asyncio.wait_for(task, 2)).native_result == "complete"


async def test_task_cancellation_of_ambiguous_map_open_observes_map_before_closing():
    adapter = adapter_at_floor()
    task = asyncio.create_task(adapter.dispatch(Action("travel_downstairs", "5,0")))
    await adapter.socket.key(ord("X"))
    task.cancel()
    await asyncio.sleep(.05)
    assert adapter.socket.game_keys() == [ord("X")]
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "ui_state", "state": 2}, cursor(0))
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert adapter.state.observation().position == (0, 0)
    assert adapter.socket.game_keys() == [ord("X"), 27]


async def test_reconnect_inside_map_closes_it_without_replaying_selection():
    adapter = adapter_at_floor()
    adapter._map_pending = True
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "ui_state", "state": 2}, cursor(3))
    await adapter.receive()
    resumed = asyncio.create_task(adapter._resumed_observation())
    await adapter.socket.key(27)
    finish_map_cancel(adapter)
    observed = await asyncio.wait_for(resumed, 2)
    assert observed.input_mode == 1 and observed.position == (0, 0)
    assert adapter.socket.game_keys() == [27]


async def test_already_at_requested_stair_never_reuses_native_previous_travel_target():
    adapter = adapter_at_floor()
    adapter.state.apply({"msg": "player", "pos": {"x": 5, "y": 0}})
    observed = await adapter.dispatch(Action("travel_downstairs", "5,0"))
    assert observed.native_result == "complete" and observed.position == (5, 0)
    assert observed.location == "D:2" and adapter.socket.sent == []
