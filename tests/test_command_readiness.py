import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from dcss_bot.adapter import WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.models import Action


@pytest.mark.parametrize("completion", ["command", "prompt", "menu", "dead"])
async def test_key_waits_through_busy_render_until_a_real_input_or_terminal_boundary(completion):
    keys = []
    async def handler(socket):
        await socket.send(json.dumps({"msgs": [
            {"msg": "player", "hp": 20, "hp_max": 20, "turn": 10, "place": "Dungeon", "depth": 2,
             "species": PROFILE.species, "god": PROFILE.god},
            {"msg": "input_mode", "mode": 1},
        ]}))
        async for raw in socket:
            message = json.loads(raw)
            keys.append(message["keycode"])
            await socket.send(json.dumps({"msg": "input_mode", "mode": 0}))
            # Native level generation can outlast the 120ms packet-coalescing gap.
            await asyncio.sleep(.3)
            packets = [{"msg": "player", "turn": 12, "depth": 3}]
            if completion == "command":
                packets.append({"msg": "input_mode", "mode": 1})
            elif completion == "prompt":
                packets += [{"msg": "msgs", "messages": [{"text": "Increase (S)trength, (I)ntelligence, or (D)exterity?"}]},
                            {"msg": "input_mode", "mode": 7}]
            elif completion == "menu":
                packets.append({"msg": "menu", "tag": "inventory", "items": []})
            else:
                packets.append({"msg": "player", "hp": 0})
            await socket.send(json.dumps({"msgs": packets}))

    async with serve(handler, "127.0.0.1", 0, subprotocols=["no-compression"]) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebTilesAdapter(f"ws://127.0.0.1:{port}", "bot", "secret", timeout=2, character=PROFILE)
        adapter.socket = await connect(adapter.url, subprotocols=["no-compression"])
        try:
            await adapter.receive()
            observation = await adapter.dispatch(Action("stairs_down"))
            assert (observation.turn, observation.depth) == (12, 3)
            assert keys == [ord(">")]
            if completion == "command":
                assert observation.input_mode == 1
            elif completion == "prompt":
                action = adapter.housekeeping(observation)
                assert (action.kind, action.value) == ("menu", "S")
            elif completion == "menu":
                assert observation.menu["tag"] == "inventory"
            else:
                assert observation.dead
        finally:
            await adapter.close()


async def test_busy_engine_without_completion_times_out_without_replaying_the_key():
    keys = []
    async def handler(socket):
        await socket.send(json.dumps({"msgs": [
            {"msg": "player", "hp": 20, "hp_max": 20, "species": PROFILE.species, "god": PROFILE.god},
            {"msg": "input_mode", "mode": 1},
        ]}))
        async for raw in socket:
            keys.append(json.loads(raw)["keycode"])
            await socket.send(json.dumps({"msg": "input_mode", "mode": 0}))

    async with serve(handler, "127.0.0.1", 0, subprotocols=["no-compression"]) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebTilesAdapter(f"ws://127.0.0.1:{port}", "bot", "secret", timeout=.3, character=PROFILE)
        adapter.socket = await connect(adapter.url, subprotocols=["no-compression"])
        try:
            await adapter.receive()
            with pytest.raises(TimeoutError):
                await adapter.dispatch(Action("stairs_down"))
            assert keys == [ord(">")]
        finally:
            await adapter.close()
