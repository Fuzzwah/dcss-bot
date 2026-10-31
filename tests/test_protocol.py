import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
import re
import zlib

import pytest
from websockets.asyncio.server import serve

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.base import CharacterProfile
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.models import Action
from dcss_bot.state import GameState


ALTERNATE = CharacterProfile(
    identifier="test-elf-conjurer", abbreviation="DECj", species="Deep Elf",
    background="Conjurer", god="", weapon="short sword", preferred_stat="intelligence",
    agent_factory=PROFILE.agent_factory,
    rc_options=(("autopickup", "?"), ("default_autopickup", "false")),
)


def effective_rc(contents):
    return dict(re.findall(r"(?m)^[ \t]*([\w.]+)[ \t]*[+\-^:]?=[ \t]*([^\r\n]*)", contents))


def test_incremental_map_visibility_and_moving_monster():
    state = GameState()
    state.apply({"msg": "map", "clear": True, "cells": [
        {"x": 1, "y": 2, "f": 33, "t": {"bg": 1},
         "mon": {"id": 7, "name": "slime creature", "threat": 2}},
        {"f": 33, "t": {"bg": 1}},
    ]})
    state.apply({"msg": "map", "cells": [
        {"x": 1, "y": 2, "mon": None},
        {"mon": {"id": 7, "name": "large slime creature", "threat": 3}},
    ]})
    obs = state.observation()
    assert [(m.name, m.x, m.y, m.threat) for m in obs.hostiles] == [("large slime creature", 2, 2, 3)]
    state.apply({"msg": "map", "cells": [{"x": 2, "y": 2, "t": {"bg": 0x40001}}]})
    assert state.observation().hostiles == ()
    assert {(c.x, c.y) for c in state.observation().cells} == {(1, 2), (2, 2)}
    state.apply({"msg": "map", "clear": True, "cells": [{"x": 0, "y": 0, "f": 33}]})
    assert [(c.x, c.y) for c in state.observation().cells] == [(0, 0)]


def test_reobserved_monster_new_identity_inherits_unchanged_cell_fields():
    state = GameState()
    state.apply({"msg": "map", "cells": [{"x": 2, "y": -4, "f": 33, "t": {"bg": 1},
                 "mon": {"id": 12, "name": "withered plant", "threat": 0, "att": 0}}]})
    state.apply({"msg": "map", "cells": [{"x": 2, "y": -4, "mon": {"id": 36}}]})
    observed = state.observation()
    assert (observed.monsters[0].id, observed.monsters[0].name, observed.monsters[0].threat) == (
        36, "withered plant", 0)
    assert observed.hostiles == ()


def test_player_delta_inventory_removal_and_hidden_item_fields():
    state = GameState()
    state.apply({"msg": "player", "hp": 20, "hp_max": 25, "pos": {"x": 2, "y": 3},
                 "inv": {"0": {"name": "a smoky potion", "quantity": 1, "letter": 97,
                                 "sub_type": 99, "plus": 14}}, "status": [{"light": "Pois"}]})
    state.apply({"msg": "player", "hp": 12, "pos": {"x": 4}})
    obs = state.observation()
    assert (obs.hp, obs.hp_max, obs.position) == (12, 25, (4, 3))
    assert obs.inventory[0].name == "a smoky potion"
    assert "sub_type" not in obs.to_dict()["inventory"][0]
    assert "plus" not in obs.to_dict()["inventory"][0]
    state.apply({"msg": "player", "inv": {"0": {"quantity": 0}}, "status": []})
    assert state.observation().inventory == ()
    assert state.observation().statuses == ()


def test_messages_rollback_menus_and_death():
    state = GameState()
    state.apply({"msg": "msgs", "messages": [{"text": "<red>Badly hurt.</red>"}]})
    state.apply({"msg": "msgs", "rollback": 1, "messages": [{"text": "You die..."}]})
    state.apply({"msg": "ui-push", "type": "game-over", "title": "Slain by an ogre"})
    assert state.observation().messages == ("You die...",)
    assert state.observation().dead and state.observation().game_over
    assert state.end_reason == "Slain by an ogre"


@pytest.mark.parametrize("kind,value", [("move", "hh"), ("move", "Q"), ("read", "\n"),
                                         ("quit", ""), ("confirm", "yes"), ("wait", "10")])
def test_actions_reject_unbounded_or_unknown_commands(kind, value):
    with pytest.raises(ValueError):
        Action(kind, value)


def test_compressed_protocol_stream_retains_dictionary():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    for hp in (20, 18):
        message = {"msgs": [{"msg": "player", "name": "Minotaur", "hp": hp}]}
        data = compressor.compress(json.dumps(message).encode()) + compressor.flush(zlib.Z_SYNC_FLUSH)
        assert adapter.decode(data[:-4]) == message["msgs"]
    with pytest.raises(ProtocolError):
        adapter.decode('{"msgs": [3]}')


def test_ui_housekeeping_is_safe_and_selects_character_by_label():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "ui-push", "type": "newgame-choice", "main-items": {
        "buttons": [{"label": "Mi - Minotaur", "hotkey": "m"}]}})
    assert adapter.housekeeping(adapter.state.observation()).value == "m"
    adapter.state.apply({"msg": "ui-pop"})
    adapter.state.apply({"msg": "input_mode", "mode": 8})
    response = adapter.housekeeping(adapter.state.observation())
    assert (response.kind, response.value) == ("confirm", "n")
    adapter.state.apply({"msg": "input_mode", "mode": 3})
    assert adapter.housekeeping(adapter.state.observation()).kind == "cancel"


@pytest.mark.parametrize("prompt", [
    "Increase (S)trength, (I)ntelligence, or (D)exterity? ",
    "Increase (s)trength, (i)ntelligence, or (d)exterity?",
    "Increase strength, intelligence, or dexterity?",
])
def test_native_level_up_hotkeys_are_recognized_only_while_prompting(prompt):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "msgs", "messages": [{"text": prompt}]})
    adapter.state.apply({"msg": "input_mode", "mode": 7})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == ("menu", "S")
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    assert adapter.housekeeping(adapter.state.observation()) is None
    adapter.state.apply({"msg": "input_mode", "mode": 8})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == ("confirm", "n")


def test_unknown_attribute_prompt_is_not_answered_as_level_advancement():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "msgs", "messages": [{"text": "Sacrifice strength or dexterity?"}]})
    adapter.state.apply({"msg": "input_mode", "mode": 7})
    assert adapter.housekeeping(adapter.state.observation()).kind == "pause"


@pytest.mark.parametrize("legacy,selected,section", [
    ("Mi - Minotaur", "DE - Deep Elf", "main-items"),
    ("Be - Berserker", "Cj - Conjurer", "sub-items"),
    ("hand axe", "short sword", "main-items"),
])
def test_alternate_profile_controls_creation_instead_of_legacy_choices(legacy, selected, section):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=ALTERNATE)
    adapter.state.apply({"msg": "ui-push", "type": "newgame-choice", section: {
        "buttons": [{"label": legacy, "hotkey": "a"}, {"label": selected, "hotkey": ord("b")}]}})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == ("menu", "b")


@pytest.mark.parametrize("stat,key", [("intelligence", "I"), ("dexterity", "D")])
def test_alternate_profile_controls_advancement_without_overriding_prompt_safety(stat, key):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret",
                              character=replace(ALTERNATE, preferred_stat=stat))
    adapter.state.apply({"msg": "msgs", "messages": [
        {"text": "Increase (S)trength, (I)ntelligence, or (D)exterity?"}]})
    adapter.state.apply({"msg": "input_mode", "mode": 7})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == ("menu", key)
    adapter.state.apply({"msg": "input_mode", "mode": 8})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == ("confirm", "n")
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    assert adapter.housekeeping(adapter.state.observation()) is None


@pytest.mark.parametrize("mode,kind,value", [(8, "confirm", "n"), (5, "more", "")])
def test_native_prompt_safety_precedes_character_creation_choices(mode, kind, value):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=ALTERNATE)
    adapter.state.apply({"msg": "ui-push", "type": "newgame-choice", "main-items": {
        "buttons": [{"label": "Deep Elf", "hotkey": "y"}]}})
    adapter.state.apply({"msg": "input_mode", "mode": mode})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action.kind, action.value) == (kind, value)


async def test_real_websocket_registration_reconnect_and_dispatch():
    accounts = set()
    turn = 0
    rc = ""
    async def handler(ws):
        nonlocal turn, rc
        await ws.send(json.dumps({"msgs": [{"msg": "lobby_complete"}]}))
        async for data in ws:
            request = json.loads(data)
            kind = request["msg"]
            if kind == "login":
                result = "login_success" if request["username"] in accounts else "login_fail"
                reply = {"msg": result}
            elif kind == "register":
                accounts.add(request["username"])
                reply = {"msg": "login_success"}
            elif kind == "get_rc":
                reply = {"msg": "rcfile_contents", "game_id": "dcss", "contents": rc}
            elif kind == "set_rc":
                rc = request["contents"]
                continue
            elif kind == "play":
                await ws.send(json.dumps({"msgs": [
                    {"msg": "player", "name": "bot", "species": PROFILE.species,
                     "god": PROFILE.god, "hp": 20, "hp_max": 20, "turn": turn},
                    {"msg": "msgs", "messages": [
                        {"text": f"Welcome, bot the {PROFILE.species} {PROFILE.background}."}]},
                    {"msg": "input_mode", "mode": 1}]}))
                continue
            elif kind == "key":
                assert request["keycode"] == ord("l")
                turn += 1
                reply = {"msg": "player", "turn": turn, "pos": {"x": turn, "y": 0}}
            else:
                raise AssertionError(request)
            await ws.send(json.dumps({"msgs": [reply]}))
    async with serve(handler, "127.0.0.1", 0, subprotocols=["no-compression"]) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebTilesAdapter(f"ws://127.0.0.1:{port}", "bot", "secret", character=PROFILE)
        await adapter.connect()
        obs = await adapter.dispatch(Action("move", "l"))
        assert obs.turn == 1 and obs.position == (1, 0)
        await adapter.close()
        obs = await adapter.reconnect()
        assert obs.turn == 1
        obs = await adapter.dispatch(Action("move", "l"))
        assert obs.turn == 2 and obs.position == (2, 0)
        await adapter.close()


@pytest.mark.parametrize("frame", [b"not deflate", "{broken", '{"msgs":[null]}'])
def test_malformed_frames_fail_explicitly(frame):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    with pytest.raises(ProtocolError):
        adapter.decode(frame)


async def test_gameplay_cannot_be_sent_into_a_confirmation_prompt():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg":"player", "hp":20,"hp_max":20})
    adapter.state.apply({"msg":"input_mode", "mode":8})
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("move", "y"))


def test_modal_confirmation_uses_menu_title_not_previous_game_message():
    state = GameState()
    state.apply({"msg": "msgs", "messages": [{"text": "Found a staircase."}]})
    state.apply({"msg": "menu", "tag": "prompt", "title": {"text": "<white>Save game and exit? "},
                 "items": [{"text": "Y - Yes", "hotkeys": [89, 121]}]})
    state.apply({"msg": "input_mode", "mode": 8})
    assert state.observation().prompt == "Save game and exit? "


class SeedServer:
    """Loopback implementation of upstream's no-ack set_rc protocol."""

    def __init__(self):
        self.accounts = set()
        self.rc = "# keep this comment\nspecies = Minotaur\ngame_seed = 17\npregen_dungeon = false\n"
        self.requests = []
        self.readback = "ok"
        self.rc_reads = 0
        self.saved = True
        self.delay_save = False
        self.login_fails = False
        self.registration_fails = False
        self.seed_selection = False
        self.delay_player = False
        self.saved_label = "[SeedBot, a level 1 Minotaur Berserker of Trog]"
        self.profile = PROFILE
        self.welcome = True
        self.welcome_background = None

    def ready_packets(self):
        packets = [
            {"msg": "version", "text": "Dungeon Crawl Stone Soup 0.34.1"},
            {"msg": "player", "hp": 20, "hp_max": 20, "turn": 0,
             "species": self.profile.species, "god": self.profile.god},
            {"msg": "input_mode", "mode": 1},
        ]
        if self.welcome:
            background = self.welcome_background or self.profile.background
            packets.append({"msg": "msgs", "messages": [
                {"text": f"Welcome, SeedBot the {self.profile.species} {background}."}]})
        return packets

    async def handler(self, ws):
        await ws.send(json.dumps({"msg": "lobby_complete"}))
        async for data in ws:
            request = json.loads(data)
            self.requests.append(request)
            kind = request["msg"]
            if kind == "login":
                known = request["username"] in self.accounts and not self.login_fails
                reply = {"msg": "login_success" if known else "login_fail"}
                await ws.send(json.dumps(reply))
                if known:
                    await ws.send(json.dumps({"msg": "set_game_links",
                                              "content": '<a href="#play-dcss">DCSS</a>'}))
                    if self.saved:
                        if self.delay_save:
                            await asyncio.sleep(.2)
                        await ws.send(json.dumps({"msg": "set_game_links",
                                                  "content": f'<a href="#play-dcss">{self.saved_label}</a>'}))
                continue
            elif kind == "register":
                if request["username"] in self.accounts or self.registration_fails:
                    reply = {"msg": "register_fail", "reason": "Account unavailable"}
                else:
                    self.accounts.add(request["username"])
                    reply = {"msg": "login_success"}
            elif kind == "get_rc":
                self.rc_reads += 1
                reply = {"msg": "rcfile_contents", "game_id": "dcss", "contents": self.rc}
                if self.rc_reads > 1:
                    if self.readback == "mismatch":
                        reply["contents"] += "# not what was written\n"
                    elif self.readback == "wrong_game":
                        reply["game_id"] = "other"
                    elif self.readback == "malformed":
                        reply["contents"] = None
                    elif self.readback == "error":
                        reply = {"msg": "auth_error", "reason": "RC rejected"}
                    elif self.readback == "silent":
                        continue
            elif kind == "set_rc":
                self.rc = request["contents"]
                continue  # Upstream does not send an acknowledgement.
            elif kind == "play":
                if self.delay_player:
                    await ws.send(json.dumps({"msg": "version", "text": "Dungeon Crawl Stone Soup 0.34.1"}))
                    await asyncio.sleep(.25)
                reply = {"msgs": self.ready_packets()}
                if self.seed_selection:
                    reply = {"msgs": [{"msg": "ui-push", "type": "seed-selection"},
                                      {"msg": "input_mode", "mode": 7}]}
            elif kind == "key":
                reply = {"msg": "player", "hp": 0, "hp_max": 20}
                if self.seed_selection:
                    assert request["keycode"] == 13
                    reply = {"msgs": [{"msg": "ui-pop"}, *self.ready_packets()]}
            else:
                raise AssertionError(request)
            await ws.send(json.dumps(reply))

    @asynccontextmanager
    async def adapter(self, seed=12345, event=None, character=PROFILE):
        async with serve(self.handler, "127.0.0.1", 0, subprotocols=["no-compression"]) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = WebTilesAdapter(f"ws://127.0.0.1:{port}", "SeedBot", "secret",
                                      timeout=.6, seed=seed, event=event, character=character)
            try:
                yield adapter
            finally:
                await adapter.close()


@pytest.mark.parametrize("seed", [1, 12345, 2**64 - 1])
async def test_seeded_registration_sets_and_verifies_rc_before_play(seed):
    server = SeedServer()
    # Even an earlier nonzero option must be replaced, because upstream retains
    # the first parsed nonzero seed_from_rc. Unrelated RC bytes are preserved.
    server.rc += " game_seed = 99\npregen_dungeon = full\ntravel_delay = 0"
    events = []
    async with server.adapter(seed, lambda kind, data: events.append((kind, data))) as adapter:
        assert not adapter.seed_confirmed and adapter.seed_rc is None
        observation = await adapter.connect()
        assert observation.hp == 20
        assert adapter.engine_version == "Dungeon Crawl Stone Soup 0.34.1"
        assert ("engine_version", {"text": adapter.engine_version}) in events
        assert [r["msg"] for r in server.requests] == [
            "login", "register", "get_rc", "set_rc", "get_rc", "play"]
        settings = effective_rc(server.rc)
        assert settings == {**PROFILE.rc_settings(), "game_seed": str(seed),
                            "travel_delay": "0", "pregen_dungeon": "incremental"}
        assert "# keep this comment\n" in server.rc
        assert sum(line.startswith("game_seed = ") for line in server.rc.splitlines()) == 1
        expected = server.rc
        assert adapter.seed_rc == expected
        assert adapter.seed_confirmed
        assert adapter.seed_rc_sha256 == hashlib.sha256(expected.encode()).hexdigest()
        event = next(data for kind, data in events if kind == "seed_configured")
        assert event == {
            "requested_seed": seed, "confirmed_seed": seed, "confirmation": "rc_readback",
            "rc_sha256": adapter.seed_rc_sha256, "contents": expected, "pregen_dungeon": "incremental"}


async def test_seeded_existing_account_is_untouched():
    server = SeedServer()
    server.accounts.add("SeedBot")
    original = server.rc
    async with server.adapter() as adapter:
        with pytest.raises(ProtocolError, match="newly registered"):
            await adapter.connect()
        assert [r["msg"] for r in server.requests] == ["login"]
        assert server.rc == original
        assert not adapter.seed_confirmed and adapter.seed_rc is None
        assert adapter.socket is None


async def test_seeded_existing_account_wrong_password_cannot_register_or_play():
    server = SeedServer()
    server.accounts.add("SeedBot")
    server.login_fails = True
    original = server.rc
    async with server.adapter() as adapter:
        with pytest.raises(ProtocolError, match="rejected"):
            await adapter.connect()
        assert [r["msg"] for r in server.requests] == ["login", "register"]
        assert server.rc == original


async def test_seeded_reconnect_resumes_without_registration_or_rc_changes():
    server = SeedServer()
    server.delay_save = True
    async with server.adapter() as adapter:
        await adapter.connect()
        original = adapter.seed_rc
        count = len(server.requests)
        observation = await adapter.reconnect()
        assert observation.hp == 20
        assert [r["msg"] for r in server.requests[count:]] == ["login", "play"]
        assert adapter.seed_confirmed and adapter.seed_rc == original


async def test_seeded_missing_save_never_starts_replacement_character():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        server.saved = False
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="No resumable"):
            await adapter.reconnect()
        assert [r["msg"] for r in server.requests[count:]] == ["login"]


async def test_seeded_reconnect_never_registers_if_login_fails():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        server.login_fails = True
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="Cannot register"):
            await adapter.reconnect()
        assert [r["msg"] for r in server.requests[count:]] == ["login"]


async def test_seeded_death_is_terminal_even_on_reconnect():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        assert (await adapter.dispatch(Action("wait"))).dead
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="finished seeded run"):
            await adapter.reconnect()
        assert len(server.requests) == count


@pytest.mark.parametrize("readback", ["mismatch", "wrong_game", "malformed", "error", "silent"])
async def test_seed_rc_readback_failure_never_plays_or_confirms(readback):
    server = SeedServer()
    server.readback = readback
    async with server.adapter() as adapter:
        with pytest.raises((ProtocolError, TimeoutError)):
            await adapter.connect()
        assert "play" not in [r["msg"] for r in server.requests]
        assert not adapter.seed_confirmed and adapter.seed_rc is None
        assert adapter.socket is None
        # The newly created account cannot be silently reused as a fresh run
        # after an ambiguous or failed setup transaction.
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="newly registered"):
            await adapter.connect()
        assert [r["msg"] for r in server.requests[count:]] == ["login"]


@pytest.mark.parametrize("seed", [0, -1, 2**64, True, 1.5, "123", "random"])
def test_adapter_rejects_invalid_seed_before_connecting(seed):
    with pytest.raises(ValueError, match="BOT_SEED"):
        WebTilesAdapter("ws://unused", "SeedBot", "secret", seed=seed, character=PROFILE)


async def test_seed_selection_accepts_enter_only_after_confirmed_seed_configuration():
    server = SeedServer()
    server.seed_selection = True
    async with server.adapter() as adapter:
        observation = await adapter.connect()
        assert observation.menu["type"] == "seed-selection"
        assert adapter.seed == 12345 and adapter.seed_confirmed
        observation = await adapter.create_character()
        assert observation.hp == 20 and not observation.menu
        assert [r["keycode"] for r in server.requests if r["msg"] == "key"] == [13]


@pytest.mark.parametrize("seed,confirmed", [(None, False), (None, True), (12345, False)])
async def test_seed_selection_without_confirmed_seed_refuses_all_keys(seed, confirmed):
    server = SeedServer()
    server.seed_selection = True
    async with server.adapter(seed=seed) as adapter:
        observation = await adapter.connect()
        assert observation.menu["type"] == "seed-selection"
        adapter.seed_confirmed = confirmed
        with pytest.raises(ProtocolError):
            await adapter.create_character()
        assert not any(r["msg"] == "key" for r in server.requests)


async def test_reconnect_waits_for_character_after_delayed_client_initialization():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        server.delay_player = True
        observation = await adapter.reconnect()
        assert observation.hp_max == 20 and observation.input_mode == 1
        assert not any(request["msg"] == "key" for request in server.requests)


async def test_reconnect_never_confirms_character_creation_even_with_stale_save_link():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        server.seed_selection = True
        with pytest.raises(ProtocolError):
            await adapter.reconnect()
        assert not any(request["msg"] == "key" for request in server.requests)


@pytest.mark.parametrize("seed", [None, 12345])
@pytest.mark.parametrize("weapon", ["short sword", None])
async def test_profile_rc_replaces_legacy_defaults_and_preserves_unrelated_bytes(seed, weapon):
    server = SeedServer()
    profile = replace(ALTERNATE, weapon=weapon)
    server.profile = profile
    preserved = "# keep Minotaur notes and CRLF\r\ntravel_delay = 3\r\nbindkey = [.] CMD_WAIT\r\n"
    server.rc = (
        preserved + "species = Minotaur\n Species += Minotaur\n"
        "background = Berserker\nBACKGROUND := Berserker\n"
        "weapon = hand axe\n weapon ^= hand axe\n"
        'autopickup = $?!+"/=\n autopickup += !\n'
        "default_autopickup = true\nDEFAULT_AUTOPICKUP -= false\n"
        "# no final newline"
    )
    async with server.adapter(seed=seed, character=profile) as adapter:
        observation = await adapter.connect()
        assert profile.matches_start(observation)
        settings = effective_rc(server.rc)
        assert {key: settings[key] for key in profile.rc_settings()} == profile.rc_settings()
        assert ("weapon" in settings) == (weapon is not None)
        assert preserved in server.rc
        assert "# no final newline\n" in server.rc
        assert settings["travel_delay"] == "3"
        assert settings["bindkey"] == "[.] CMD_WAIT"
        owned = {"species", "background", "weapon", *profile.rc_settings()}
        assignments = re.findall(r"(?m)^[ \t]*([\w.]+)[ \t]*([+\-^:]?=)", server.rc)
        selected = [(key, operation) for key, operation in assignments if key.casefold() in owned]
        assert len(selected) == len(profile.rc_settings())
        assert dict(selected) == {key: "=" for key in profile.rc_settings()}
        assert [request["msg"] for request in server.requests] == [
            "login", "register", "get_rc", "set_rc", "get_rc", "play"]
        if seed is not None:
            assert adapter.seed_confirmed and adapter.seed_rc == server.rc
            assert settings["game_seed"] == str(seed)
            assert settings["pregen_dungeon"] == "incremental"
        else:
            assert not adapter.seed_confirmed and adapter.seed_rc is None


@pytest.mark.parametrize("readback", ["mismatch", "wrong_game", "malformed", "error", "silent"])
async def test_unseeded_character_rc_readback_failure_rejects_before_play(readback):
    server = SeedServer()
    server.readback = readback
    async with server.adapter(seed=None, character=ALTERNATE) as adapter:
        with pytest.raises((ProtocolError, TimeoutError)):
            await adapter.connect()
        assert "play" not in [request["msg"] for request in server.requests]
        assert adapter.socket is None


async def test_matching_saved_character_resumes_without_a_fresh_welcome_or_background_packet():
    server = SeedServer()
    server.accounts.add("SeedBot")
    server.welcome = False
    async with server.adapter(seed=None) as adapter:
        observation = await adapter.connect()
        assert PROFILE.matches_observation(observation)
        assert not PROFILE.matches_start(observation)
        observation = await adapter.create_character()
        assert observation.hp_max == 20
        count = len(server.requests)
        observation = await adapter.reconnect()
        assert PROFILE.matches_observation(observation)
        assert [request["msg"] for request in server.requests[count:]] == ["login", "play"]
        assert not any(request["msg"] == "key" for request in server.requests)


@pytest.mark.parametrize("saved_label", [
    "[SeedBot, a level 3 Minotaur Fighter of Trog]",
    "[SeedBot, a level 12 Deep Elf Conjurer]",
])
async def test_wrong_saved_combination_is_rejected_before_play(saved_label):
    server = SeedServer()
    server.accounts.add("SeedBot")
    server.saved_label = saved_label
    original = server.rc
    async with server.adapter(seed=None) as adapter:
        with pytest.raises(ProtocolError, match="Saved character"):
            await adapter.connect()
        assert [request["msg"] for request in server.requests] == ["login"]
        assert server.rc == original


async def test_seeded_reconnect_rejects_changed_saved_combination_without_rewriting_rc():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        original = server.rc
        server.saved_label = "[SeedBot, a level 3 Minotaur Fighter of Trog]"
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="Saved character"):
            await adapter.reconnect()
        assert [request["msg"] for request in server.requests[count:]] == ["login"]
        assert server.rc == original


@pytest.mark.parametrize("identity", [{"species": "Deep Elf"}, {"god": ""}])
@pytest.mark.parametrize("saved", [False, True])
async def test_public_identity_mismatch_is_rejected_before_any_game_key(identity, saved):
    server = SeedServer()
    server.profile = replace(PROFILE, **identity)
    if saved:
        server.accounts.add("SeedBot")
        server.saved_label = "[playing]"
        server.welcome = False
    async with server.adapter(seed=None) as adapter:
        with pytest.raises(ProtocolError, match="Public character identity"):
            await adapter.connect()
        assert not any(request["msg"] == "key" for request in server.requests)
        assert adapter.socket is None


@pytest.mark.parametrize("background", ["Fighter", None])
async def test_fresh_character_requires_native_welcome_confirming_background(background):
    server = SeedServer()
    server.welcome_background = background
    server.welcome = background is not None
    async with server.adapter(seed=None) as adapter:
        with pytest.raises(ProtocolError, match="Fresh character"):
            await adapter.connect()
        assert not any(request["msg"] == "key" for request in server.requests)


async def test_reconnect_checks_public_identity_when_saved_label_has_no_abbreviation():
    server = SeedServer()
    async with server.adapter() as adapter:
        await adapter.connect()
        server.saved_label = "[playing]"
        server.profile = ALTERNATE
        server.welcome = False
        count = len(server.requests)
        with pytest.raises(ProtocolError, match="Public character identity"):
            await adapter.reconnect()
        assert [request["msg"] for request in server.requests[count:]] == ["login", "play"]


async def test_playable_identity_mismatch_cannot_reach_strategy_dispatch():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=ALTERNATE)
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 20, "hp_max": 20})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    with pytest.raises(ProtocolError, match="Public character identity"):
        await adapter.dispatch(Action("wait"))
    with pytest.raises(ProtocolError, match="Public character identity"):
        await adapter.create_character()
