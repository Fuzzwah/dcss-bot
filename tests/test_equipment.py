from dataclasses import replace

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent, equipment_score, equipment_upgrade
from dcss_bot.models import Action, Cell, Item, Monster, Observation


def equipped_observation():
    return Observation(hp=25, hp_max=25, turn=20, place="Dungeon", depth=1,
                       input_mode=1, cells=(Cell(0, 0, 33, "@", True),),
                       inventory=(Item(0, "a", "+0 hand axe", equipped=True),
                                  Item(1, "b", "+0 animal skin", equipped=True)))


async def test_verified_armour_upgrade_requires_clear_ground_and_observed_success():
    obs = equipped_observation()
    chain = Item(2, "c", "+0 chain mail")
    ring = Item(3, "d", "+0 ring mail")
    obs = replace(obs, inventory=obs.inventory + (chain, ring))
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("wear", "c")
    # Failed wear must not optimistically raise an internal armour score, or
    # repeat the same failed request. A different real upgrade remains usable.
    action = await agent.decide(replace(obs, turn=40))
    assert (action.kind, action.value) == ("wear", "d")
    assert (await agent.decide(replace(obs, turn=60))).kind not in {"wear", "wield"}
    worn = replace(obs, inventory=(obs.inventory[0], replace(obs.inventory[1], equipped=False),
                                   replace(chain, equipped=True), ring))
    assert (await RuleBasedAgent().decide(worn)).kind != "wear"
    unsafe = replace(obs, monsters=(Monster(1, "rat", 1, 0, 0),))
    assert (await RuleBasedAgent().decide(unsafe)).objective == "combat"


async def test_axe_upgrade_uses_only_known_names_and_does_not_unwield_distortion():
    obs = equipped_observation()
    obs = replace(obs, inventory=obs.inventory + (Item(2, "c", "+0 war axe"),))
    action = await RuleBasedAgent().decide(obs)
    assert (action.kind, action.value) == ("wield", "c")
    distortion = replace(obs, inventory=(replace(obs.inventory[0], name="+0 hand axe of distortion"),
                                         *obs.inventory[1:]))
    assert (await RuleBasedAgent().decide(distortion)).kind != "wield"


@pytest.mark.parametrize("name", ["a glowing war axe", "+9 war axe of distortion", "+9 war axe of chaos",
                                  "+9 war axe of draining", '+9 war axe "Trouble" {Str-5}',
                                  "+9 plate armour of ponderousness"])
def test_unknown_or_hazardous_equipment_is_not_a_verified_upgrade(name):
    assert not equipment_upgrade(name, equipped_observation().inventory)


def test_visible_enchantment_brand_and_existing_equipment_affect_ranking():
    assert equipment_score("a glowing broad axe") is None
    inv = (Item(0, "a", "+4 hand axe of electrocution", equipped=True),)
    assert not equipment_upgrade("+0 war axe", inv)
    assert not equipment_upgrade("+0 plate armour", (Item(1, "b", '+2 robe "Unknown" {rF+}', equipped=True),))
    shield = equipped_observation().inventory + (Item(4, "e", "+0 kite shield", equipped=True),)
    assert not equipment_upgrade("+0 battleaxe", shield)


async def test_pickup_pile_selects_useful_visible_item_and_skips_junk():
    obs = replace(equipped_observation(), menu={"tag": "pickup", "items": [
        {"text": " a - a stone", "hotkeys": [97]},
        {"text": " b - a +0 hand axe", "hotkeys": [98]},
        {"text": " c - a +0 war axe", "hotkeys": [99]},
        {"text": " d - a +8 war axe of chaos", "hotkeys": [100]},
    ]})
    action = await RuleBasedAgent().decide(obs)
    assert (action.kind, action.value) == ("menu", "c")
    junk = replace(obs, menu={"tag": "pickup", "items": obs.menu["items"][:2]})
    assert (await RuleBasedAgent().decide(junk)).kind == "cancel"
    assert (await RuleBasedAgent().decide(replace(obs, monsters=(Monster(1, "rat", 2, 0),)))).kind == "cancel"


async def test_equipment_pile_is_inspected_only_once_even_if_nothing_is_picked_up():
    obs = replace(equipped_observation(), cells=(Cell(0, 0, 33, ")", True),))
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).kind == "pickup"
    assert (await agent.decide(obs)).kind == "pause"


async def test_pickup_menu_is_policy_owned_and_selection_commits_once():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "hp": 20, "hp_max": 20,
                         "species": PROFILE.species, "god": PROFILE.god})
    adapter.state.apply({"msg": "menu", "tag": "pickup", "items": [
        {"text": " c - a +0 war axe", "hotkeys": [99]}]})
    assert adapter.housekeeping(adapter.state.observation()) is None
    keys = []

    async def key(value):
        keys.append(value)
        if value == 13:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    await adapter.dispatch(Action("menu", "c"))
    assert keys == ["c", 13]


async def test_pickup_rejects_nonexistent_letter_without_sending_keys():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "menu", "tag": "pickup", "items": []})
    with pytest.raises(ProtocolError, match="not present"):
        await adapter.dispatch(Action("menu", "z"))


async def test_inventory_inspection_reads_worn_state_and_closes_ui_without_a_turn():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "hp": 20, "hp_max": 20, "turn": 0,
                         "species": PROFILE.species, "god": PROFILE.god,
                         "inv": {"1": {"name": "+0 animal skin", "quantity": 1}}})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    keys = []

    async def key(value):
        keys.append(value)
        if value == "i":
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " b - a +0 animal skin (worn)", "hotkeys": [98]}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.create_character()
    assert keys == ["i", 27]
    assert result.turn == 0 and not result.menu and result.inventory[0].equipped


async def test_approached_equipment_pile_is_remembered_when_player_glyph_covers_it():
    obs = replace(equipped_observation(), cells=(Cell(0, 0, 33, "@", True),
                                                Cell(1, 0, 33, ")", True)))
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "equipment")
    arrived = replace(obs, position=(1, 0), turn=21,
                      cells=(Cell(0, 0, 33, ".", True), Cell(1, 0, 33, "@", True)),
                      messages=("There are several objects here.",))
    assert (await agent.decide(arrived)).kind == "pickup"


async def test_described_nonupgrade_does_not_fill_inventory():
    obs = replace(equipped_observation(), cells=(Cell(0, 0, 33, ")", True),),
                  messages=("You see here a +0 dagger.",))
    assert (await RuleBasedAgent().decide(obs)).kind == "pause"


async def test_completed_wear_refreshes_public_worn_markers():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "hp": 20, "hp_max": 20,
                         "species": PROFILE.species, "god": PROFILE.god, "inv": {
        "1": {"name": "+0 animal skin", "quantity": 1},
        "2": {"name": "+0 chain mail", "quantity": 1},
    }})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    keys = []

    async def key(value):
        keys.append(value)
        if value == "W":
            adapter.state.apply({"msg": "input_mode", "mode": 7})
        elif value == "c":
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        elif value == "i":
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " b - a +0 animal skin"}, {"text": " c - a +0 chain mail (worn)"}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("wear", "c"))
    assert keys == ["W", "c", "i", 27]
    assert [item.equipped for item in result.inventory] == [False, True]


@pytest.mark.parametrize("kind,value", [("wear", "b"), ("wield", "a"), ("pickup", "")])
async def test_equipment_actions_are_rejected_at_adapter_boundary_with_hostiles(kind, value):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "hp": 20, "hp_max": 20,
                         "species": PROFILE.species, "god": PROFILE.god})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    adapter.state.apply({"msg": "map", "cells": [
        {"x": 1, "y": 0, "t": {"bg": 1}, "mon": {"id": 1, "name": "rat"}}]})
    with pytest.raises(ProtocolError, match="visible hostiles"):
        await adapter.dispatch(Action(kind, value))
