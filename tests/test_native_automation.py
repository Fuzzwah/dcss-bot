"""Public 0.34 command boundaries and interruptible native activities."""
import asyncio
import json

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.models import Action


class PublicPackets:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.keys = asyncio.Queue()

    async def send(self, payload):
        message = json.loads(payload)
        self.sent.append(message)
        if "keycode" in message:
            self.keys.put_nowait(message["keycode"])
        if message.get("keycode") in {ord("5"), ord("o")}:
            self.started.set()
        if message.get("keycode") == 18:
            self.cancelled.set()

    async def recv(self):
        return await self.incoming.get()

    def feed(self, *messages):
        self.incoming.put_nowait(json.dumps({"msgs": list(messages)}))


def native_adapter():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE, timeout=1)
    adapter.socket = PublicPackets()
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 30, "hp_max": 30, "turn": 10})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return adapter


@pytest.mark.parametrize("kind", ["rest", "explore"])
@pytest.mark.parametrize("unsafe", ["hostile", "prompt", "cloud", "death"])
async def test_native_activity_rejects_unsafe_start_without_spending_a_turn(kind, unsafe):
    adapter = native_adapter()
    if unsafe == "hostile":
        adapter.state.apply({"msg": "map", "cells": [{"x": 1, "y": 0, "t": {"bg": 1},
                             "mon": {"id": 1, "name": "orc", "att": 0}}]})
    elif unsafe == "prompt":
        adapter.state.apply({"msg": "input_mode", "mode": 8})
    elif unsafe == "cloud":
        adapter.state.apply({"msg": "map", "cells": [{"x": 0, "y": 0, "t": {"bg": 1, "cloud": 1}}]})
    else:
        adapter.state.apply({"msg": "msgs", "messages": [{"text": "You die..."}]})
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action(kind))
    assert adapter.socket.sent == []
    assert adapter.state.observation().turn == 10


@pytest.mark.parametrize("hp,status", [(29, []), (30, [{"light": "Slow"}])])
async def test_exploration_requires_fully_recovered_state(hp, status):
    adapter = native_adapter()
    adapter.state.apply({"msg": "player", "hp": hp, "status": status})
    with pytest.raises(ProtocolError, match="Unsafe auto-exploration"):
        await adapter.dispatch(Action("explore"))
    assert adapter.socket.sent == []


@pytest.mark.parametrize("kind,message,result", [
    ("rest", "You feel yourself speed up.", "complete"),
    ("explore", "Done exploring.", "complete"),
    ("explore", "Partly explored, can't reach some items.", "partial"),
])
async def test_native_activity_waits_past_intermediate_busy_packets(kind, message, result):
    adapter = native_adapter()
    adapter.state.apply({"msg": "player", "hp": 20 if kind == "rest" else 30})
    task = asyncio.create_task(adapter.dispatch(Action(kind)))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 20})
    await asyncio.sleep(.15)
    assert not task.done()
    adapter.socket.feed({"msg": "player", "turn": 35, "hp": 30},
                        {"msg": "msgs", "messages": [{"text": message}]},
                        {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert (observed.turn, observed.hp, observed.input_mode) == (35, 30, 1)
    assert (observed.native_automation, observed.native_result) == (kind, result)
    assert not adapter.socket.cancelled.is_set()


@pytest.mark.parametrize("ending", ["danger", "prompt", "death"])
async def test_native_activity_returns_native_interrupt_without_more_gameplay(ending):
    adapter = native_adapter()
    task = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    messages = [{"msg": "input_mode", "mode": 0}]
    if ending == "danger":
        messages += [{"msg": "map", "cells": [{"x": 1, "y": 0, "t": {"bg": 1},
                      "mon": {"id": 2, "name": "orc", "att": 0}}]},
                     {"msg": "input_mode", "mode": 1}]
    elif ending == "prompt":
        messages += [{"msg": "input_mode", "mode": 8}]
    else:
        messages += [{"msg": "msgs", "messages": [{"text": "You die..."}]}]
    adapter.socket.feed(*messages)
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == "interrupted"
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("o")]


@pytest.mark.parametrize("reason", ["control", "turn_limit", "timeout"])
async def test_busy_native_activity_is_cancelled_and_acknowledged_before_return(reason):
    adapter = native_adapter()
    if reason == "timeout":
        adapter.timeout = .05
    stop = asyncio.Event()
    task = asyncio.create_task(adapter.dispatch(
        Action("rest"), automation_guard=lambda obs: reason if stop.is_set() else ""))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 15})
    if reason != "timeout":
        stop.set()
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    assert not task.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == reason
    assert observed.turn == 15
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("5"), 18]


async def test_old_exploration_message_does_not_report_fresh_completion():
    adapter = native_adapter()
    adapter.state.apply({"msg": "msgs", "messages": [{"text": "Done exploring."}]})
    task = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == "interrupted"


async def test_queued_command_snapshot_cannot_finish_native_activity_before_start_acknowledgement():
    adapter = native_adapter()
    task = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 1})
    await asyncio.sleep(.15)
    assert not task.done()
    assert adapter.native_activity == "explore"
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "player", "turn": 20, "pos": {"x": 8, "y": 0}})
    await asyncio.sleep(.15)
    assert not task.done()
    adapter.socket.feed({"msg": "msgs", "messages": [{"text": "Done exploring."}]},
                        {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == "complete"
    assert observed.turn == 20
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("o")]


async def test_disconnect_during_macro_never_replays_it():
    adapter = native_adapter()
    async def disconnected():
        raise OSError("disconnected")
    adapter.socket.recv = disconnected
    with pytest.raises(OSError):
        await adapter.dispatch(Action("rest"))
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("5")]
    assert adapter.native_activity == "rest"


async def test_native_exploration_runs_beyond_five_seconds_while_making_public_progress():
    adapter = native_adapter()
    task = asyncio.create_task(adapter.dispatch(Action("explore"), automation_delay=.4))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0})
    for turn in range(11, 29):
        await asyncio.sleep(.3)
        adapter.socket.feed({"msg": "player", "turn": turn, "pos": {"x": turn, "y": 0}})
        assert not adapter.socket.cancelled.is_set()
        assert not task.done()
    adapter.socket.feed({"msg": "msgs", "messages": [{"text": "Done exploring."}]},
                        {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == "complete"
    assert observed.turn == 28
    assert observed.position == (28, 0)
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("o")]


@pytest.mark.parametrize("noise", [
    {"msg": "ping"},
    {"msg": "player", "turn": 10},
    {"msg": "input_mode", "mode": 0},
    {"msg": "msgs", "messages": [{"text": "A distant noise."}]},
])
async def test_nonprogress_packets_cannot_renew_native_exploration_deadline(noise):
    adapter = native_adapter()
    adapter.timeout = .2
    task = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0})

    async def keep_transport_active():
        while not adapter.socket.cancelled.is_set():
            adapter.socket.feed(noise)
            await asyncio.sleep(.02)

    producer = asyncio.create_task(keep_transport_active())
    try:
        await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
        assert not task.done()
        adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
        observed = await asyncio.wait_for(task, 1)
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
    assert observed.native_result == "timeout"
    assert observed.turn == 10
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("o"), 18]


@pytest.mark.parametrize("delay", [2.001, 60])
async def test_native_exploration_declines_unsupported_pacing_without_a_game_key(delay):
    adapter = native_adapter()
    observed = await adapter.dispatch(Action("explore"), automation_delay=delay)
    assert observed.native_result == "unsupported_delay"
    assert observed.input_mode == 1 and observed.turn == 10
    assert adapter.socket.sent == []


def pickup_menu(adapter, items):
    adapter.state.apply({"msg": "menu", "tag": "pickup",
                         "title": {"text": "Pick up what? 3/52 gear slots (_ for help)"},
                         "items": items})


@pytest.mark.parametrize("keys", ["", "aa", "a?", "a\n"])
def test_pickup_selection_rejects_unbounded_or_ambiguous_keys(keys):
    with pytest.raises(ValueError):
        Action("pickup_select", keys)


@pytest.mark.parametrize("bad", ["selected", "disabled", "ambiguous", "wrong_menu"])
async def test_pickup_selection_validates_entire_menu_before_toggling_anything(bad):
    adapter = native_adapter()
    row = {"text": " a - a scroll of identify", "q": 1, "level": 2, "hotkeys": [97, 63]}
    if bad == "selected":
        row["text"] = " a + a scroll of identify"
    if bad == "disabled":
        row["colour"] = 8
    pickup_menu(adapter, [row, dict(row)] if bad == "ambiguous" else [row])
    if bad == "wrong_menu":
        adapter.state.menus[-1]["title"] = {"text": "What do you want to examine?"}
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("pickup_select", "a"))
    assert adapter.socket.sent == []


async def test_pickup_selection_commits_multiple_supplies_and_equipment_together():
    adapter = native_adapter()
    first = {"text": " a - a scroll of identify", "q": 1, "level": 2, "hotkeys": [97, 63]}
    second = {"text": " b - a +1 hand axe", "q": 1, "level": 2, "hotkeys": [98, 41]}
    pickup_menu(adapter, [first, second])
    task = asyncio.create_task(adapter.dispatch(Action("pickup_select", "ab")))
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == 97
    adapter.socket.feed({"msg": "update_menu_items", "chunk_start": 0, "items": [
        {**first, "text": " a + a scroll of identify"}]})
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == 98
    adapter.socket.feed({"msg": "update_menu_items", "chunk_start": 1, "items": [
        {**second, "text": " b + a +1 hand axe"}]})
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == 13
    adapter.socket.feed({"msg": "close_menu"}, {"msg": "player", "turn": 11, "inv": {
        "0": {"name": "a scroll of identify", "quantity": 1, "letter": 97},
        "1": {"name": "a +1 hand axe", "quantity": 1, "letter": 98}}},
        {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert {item.name for item in observed.inventory} == {"a scroll of identify", "a +1 hand axe"}
    assert observed.turn == 11 and not observed.menu
    assert [message["keycode"] for message in adapter.socket.sent] == [97, 98, 13]


async def test_pickup_danger_between_selection_and_commit_never_picks_up():
    adapter = native_adapter()
    row = {"text": " a - a scroll of identify", "q": 1, "level": 2, "hotkeys": [97, 63]}
    pickup_menu(adapter, [row])
    task = asyncio.create_task(adapter.dispatch(Action("pickup_select", "a")))
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == 97
    adapter.socket.feed(
        {"msg": "update_menu_items", "chunk_start": 0, "items": [
            {**row, "text": " a + a scroll of identify"}]},
        {"msg": "map", "cells": [{"x": 1, "y": 0, "t": {"bg": 1},
         "mon": {"id": 3, "name": "orc", "att": 0}}]})
    with pytest.raises(ProtocolError, match="interrupted before commit"):
        await asyncio.wait_for(task, 1)
    assert adapter.state.observation().turn == 10
    assert [message["keycode"] for message in adapter.socket.sent] == [97]


async def test_unacknowledged_native_cancellation_is_bounded_without_replay():
    adapter = native_adapter()
    adapter.timeout = .05
    task = asyncio.create_task(adapter.dispatch(Action("rest")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0})
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    with pytest.raises(TimeoutError, match="after automation cancellation"):
        await asyncio.wait_for(task, 1)
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("5"), 18]


async def test_task_cancellation_acknowledges_redraw_before_releasing_native_activity():
    adapter = native_adapter()
    task = asyncio.create_task(adapter.dispatch(Action("rest")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0})
    await asyncio.sleep(.15)
    task.cancel()
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    assert not task.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "input_mode", "mode": 1}, {"msg": "player", "turn": 15})
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert adapter.state.observation().input_mode == 1
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [ord("5"), 18]


async def test_page_acknowledgement_continues_guarded_macro_without_replaying_o():
    adapter = native_adapter()
    first = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 5})
    assert (await asyncio.wait_for(first, 1)).native_result == "interrupted"
    stop = asyncio.Event()
    continuation = asyncio.create_task(adapter.dispatch(
        Action("more"), automation_guard=lambda obs: "control" if stop.is_set() else ""))
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == ord("o")
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == ord(" ")
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 15})
    await asyncio.sleep(.15)
    assert not continuation.done()
    stop.set()
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(continuation, 1)
    assert observed.native_result == "control"
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [
        ord("o"), ord(" "), 18]


async def test_resumed_busy_native_activity_is_cancelled_instead_of_replayed():
    adapter = native_adapter()
    adapter.native_activity = "explore"
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    task = asyncio.create_task(adapter._resumed_observation())
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.input_mode == 1
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [18]


@pytest.mark.parametrize("mode", [0, 1])
async def test_explicit_native_cancel_never_sends_escape_to_busy_or_command_mode(mode):
    adapter = native_adapter()
    adapter.native_activity = "explore"
    adapter.state.apply({"msg": "input_mode", "mode": mode})
    task = asyncio.create_task(adapter.dispatch(Action("cancel")))
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    assert not task.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.native_result == "control"
    assert observed.turn == 10
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [18]


async def test_reconnect_retains_pending_native_prompt_without_answering_or_replaying():
    adapter = native_adapter()
    adapter.native_activity = "explore"
    adapter.state.apply({"msg": "input_mode", "mode": 5})
    observed = await adapter._resumed_observation()
    assert observed.input_mode == 5
    assert adapter.native_activity == "explore"
    assert adapter.socket.sent == []


async def test_pending_native_prompt_uses_escape_then_safe_busy_interrupt_without_replay():
    adapter = native_adapter()
    first = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "menu", "tag": "pickup", "items": []},
                        {"msg": "input_mode", "mode": 7})
    observed = await asyncio.wait_for(first, 1)
    assert observed.menu and adapter.native_activity == "explore"
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == ord("o")
    cancelling = asyncio.create_task(adapter.dispatch(
        Action("cancel"), automation_guard=lambda _: "control"))
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == 27
    adapter.socket.feed({"msg": "close_all_menus"}, {"msg": "input_mode", "mode": 0})
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    assert not cancelling.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    result = await asyncio.wait_for(cancelling, 1)
    assert result.native_result == "control"
    assert adapter.native_activity == ""
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [
        ord("o"), 27, 18]


@pytest.mark.parametrize("cancel_before_response", [False, True])
async def test_task_cancellation_during_page_continuation_never_replays_native_command(cancel_before_response):
    adapter = native_adapter()
    first = asyncio.create_task(adapter.dispatch(Action("explore")))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 5})
    await asyncio.wait_for(first, 1)
    continuing = asyncio.create_task(adapter.dispatch(Action("more")))
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == ord("o")
    assert await asyncio.wait_for(adapter.socket.keys.get(), 1) == ord(" ")
    if not cancel_before_response:
        adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 20})
        await asyncio.sleep(.15)
    continuing.cancel()
    if cancel_before_response:
        await asyncio.sleep(.03)
        assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [
            ord("o"), ord(" ")]
        adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 20})
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    assert not continuing.done()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(continuing, 1)
    assert adapter.native_activity == ""
    assert adapter.state.observation().turn == 20
    assert [message["keycode"] for message in adapter.socket.sent if "keycode" in message] == [
        ord("o"), ord(" "), 18]


@pytest.mark.parametrize("status", ["Berserk", "-Berserk", "Slow", "Exh"])
async def test_native_rest_accepts_public_rage_and_recovery_cooldowns(status):
    adapter = native_adapter()
    adapter.state.apply({"msg": "player", "status": [{"light": status}]})
    task = asyncio.create_task(adapter.dispatch(Action("rest")))
    await asyncio.wait_for(adapter.socket.started.wait(), 1)
    adapter.socket.feed({"msg": "input_mode", "mode": 0},
                        {"msg": "player", "turn": 30, "status": []},
                        {"msg": "input_mode", "mode": 1})
    observed = await asyncio.wait_for(task, 1)
    assert observed.turn == 30 and observed.statuses == ()
    assert observed.native_result == "complete"


async def test_travel_stopping_ready_cannot_acknowledge_the_still_queued_interrupt():
    adapter = native_adapter()
    stop = asyncio.Event()
    task = asyncio.create_task(adapter.dispatch(
        Action("explore"), automation_guard=lambda _: "control" if stop.is_set() else ""))
    await adapter.socket.started.wait()
    adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "player", "turn": 15})
    stop.set()
    await asyncio.wait_for(adapter.socket.cancelled.wait(), 1)
    # kbhit stops travel without consuming Ctrl-R. This COMMAND belongs to
    # stopping travel, not to processing the queued turn-free redraw.
    adapter.socket.feed({"msg": "input_mode", "mode": 1})
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), .2)
        adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
        observed = await asyncio.wait_for(task, 1)
        assert (observed.turn, observed.native_result) == (15, "control")
        assert not adapter.native_activity
    finally:
        if not task.done():
            task.cancel()
            adapter.socket.feed({"msg": "input_mode", "mode": 0}, {"msg": "input_mode", "mode": 1})
            await asyncio.gather(task, return_exceptions=True)
