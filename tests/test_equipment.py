from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.characters.mibe_equipment import (
    axe_delay, equipment_action, equipment_score, equipment_upgrade, is_flaming_axe,
)
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def equipped_observation():
    return Observation(hp=25, hp_max=25, turn=20, place="Dungeon", depth=1,
                       input_mode=1, cells=(Cell(0, 0, 33, "@", True),),
                       inventory=(Item(0, "a", "+0 hand axe", equipped=True),
                                  Item(1, "b", "+0 animal skin", equipped=True)))


def equipment_agent(obs):
    """Equipment tests begin after the native public inspection boundary."""
    agent = RuleBasedAgent()
    equipped = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipped)
    agent.development._last_skill_inspection = obs.turn
    return agent


async def test_verified_armour_upgrade_requires_clear_ground_and_observed_success():
    obs = equipped_observation()
    chain = Item(2, "c", "+0 chain mail")
    ring = Item(3, "d", "+0 ring mail")
    obs = replace(obs, inventory=obs.inventory + (chain, ring))
    agent = equipment_agent(obs)
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("wear", "c")
    # Failed wear must not optimistically raise an internal armour score, or
    # repeat the same failed request. A different real upgrade remains usable.
    action = await agent.decide(replace(obs, turn=40))
    assert (action.kind, action.value) == ("wear", "d")
    assert (await agent.decide(replace(obs, turn=60))).kind not in {"wear", "wield"}
    worn = replace(obs, inventory=(obs.inventory[0], replace(obs.inventory[1], equipped=False),
                                   replace(chain, equipped=True), ring))
    assert (await equipment_agent(worn).decide(worn)).kind != "wear"
    unsafe = replace(obs, monsters=(Monster(1, "rat", 1, 0, 0),))
    assert (await equipment_agent(unsafe).decide(unsafe)).kind not in {"wear", "wield", "put_on", "pickup"}


@pytest.mark.parametrize("name", ["+0 hand axe of distortion", "+0 hand axe (distort)"])
async def test_axe_upgrade_uses_only_known_names_and_does_not_unwield_distortion(name):
    obs = equipped_observation()
    obs = replace(obs, inventory=obs.inventory + (Item(2, "c", "+0 war axe"),))
    action = await equipment_agent(obs).decide(obs)
    assert (action.kind, action.value) == ("wield", "c")
    distortion = replace(obs, inventory=(replace(obs.inventory[0], name=name),
                                         *obs.inventory[1:]))
    assert (await equipment_agent(distortion).decide(distortion)).kind != "wield"


@pytest.mark.parametrize("name", ["a glowing war axe", "+9 war axe of distortion", "+9 war axe of chaos",
                                  "+9 war axe of draining", '+9 war axe "Trouble" {Str-5}',
                                  '+9 plate armour "Trouble" {rF+}', "+9 robe {rF+ *Rage}",
                                  "+9 plate armour of ponderousness", "+9 scarf {harm}"])
def test_unknown_or_hazardous_equipment_is_not_a_verified_upgrade(name):
    assert not equipment_upgrade(name, equipped_observation().inventory)


def test_visible_enchantment_brand_and_existing_equipment_affect_ranking():
    assert equipment_score("a glowing broad axe") is None
    inv = (Item(0, "a", "+4 hand axe of electrocution", equipped=True),)
    assert not equipment_upgrade("+0 war axe", inv)
    assert not equipment_upgrade("+0 plate armour", (Item(1, "b", '+2 robe "Unknown" {rF+}', equipped=True),))
    shield = equipped_observation().inventory + (Item(4, "e", "+0 kite shield", equipped=True),)
    assert not equipment_upgrade("+0 battleaxe", shield)


@pytest.mark.parametrize("terse,verbose", [
    ("+0 war axe (flame)", "+0 war axe of flaming"),
    ("+0 war axe (heavy)", "+0 heavy war axe"),
    ("+0 war axe (speed)", "+0 war axe of speed"),
])
def test_native_brands_remain_practical_equipment_upgrades(terse, verbose):
    inventory = (Item(0, "a", "+0 war axe", equipped=True), Item(2, "c", terse))
    obs = replace(equipped_observation(), inventory=inventory)
    assert equipment_score(terse) == equipment_score(verbose)
    assert equipment_score(terse) > equipment_score("+0 war axe")
    assert equipment_upgrade(terse, inventory)
    action = equipment_action(obs)
    assert (action.kind, action.value) == ("wield", "c")


def test_native_current_speed_brand_is_not_discarded_for_a_worse_weapon():
    inventory = (Item(0, "a", "+0 war axe (speed)", equipped=True),)
    assert not equipment_upgrade("+1 war axe", inventory)
    assert axe_delay("+0 war axe (heavy)", 0) == 22.5
    assert is_flaming_axe("+0 war axe (flame) (weapon)")


def test_native_ordinary_ego_armour_is_a_usable_upgrade():
    name = "+0 chain mail {rF+}"
    obs = equipped_observation()
    obs = replace(obs, inventory=obs.inventory + (Item(2, "c", name),))
    assert equipment_score(name) == equipment_score("+0 chain mail of fire resistance")
    assert equipment_upgrade(name, obs.inventory)
    action = equipment_action(obs)
    assert (action.kind, action.value) == ("wear", "c")


def test_amulet_fills_an_empty_slot_but_never_offers_occupied_slot_replacement():
    obs = equipped_observation()
    candidate = Item(2, "c", "amulet of regeneration")
    empty = replace(obs, inventory=obs.inventory + (candidate,))
    assert equipment_upgrade(candidate.name, empty.inventory)
    action = equipment_action(empty)
    assert (action.kind, action.value) == ("put_on", "c")
    occupied = replace(empty, inventory=empty.inventory + (
        Item(3, "d", "amulet of guardian spirit", equipped=True),
    ))
    assert not equipment_upgrade(candidate.name, occupied.inventory)
    assert equipment_action(occupied) is None


async def test_pickup_pile_selects_useful_visible_item_and_skips_junk():
    obs = replace(equipped_observation(), menu={"tag": "pickup", "items": [
        {"text": " a - a stone", "hotkeys": [97]},
        {"text": " b - a +0 hand axe", "hotkeys": [98]},
        {"text": " c - a +0 war axe", "hotkeys": [99]},
        {"text": " d - a +8 war axe of chaos", "hotkeys": [100]},
    ]})
    action = await equipment_agent(obs).decide(obs)
    assert (action.kind, action.value) == ("menu", "c")
    junk = replace(obs, menu={"tag": "pickup", "items": obs.menu["items"][:2]})
    assert (await equipment_agent(junk).decide(junk)).kind == "cancel"
    assert (await equipment_agent(obs).decide(replace(obs, monsters=(Monster(1, "rat", 2, 0),)))).kind == "cancel"


async def test_equipment_pile_is_inspected_only_once_even_if_nothing_is_picked_up():
    obs = replace(equipped_observation(), cells=(Cell(0, 0, 33, ")", True),))
    agent = equipment_agent(obs)
    assert (await agent.decide(obs)).kind == "pickup"
    for _ in range(3):
        assert (await agent.decide(obs)).kind != "pickup"


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
    agent = equipment_agent(obs)
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "equipment")
    arrived = replace(obs, position=(1, 0), turn=21,
                      cells=(Cell(0, 0, 33, ".", True), Cell(1, 0, 33, "@", True)),
                      messages=("There are several objects here.",))
    assert (await agent.decide(arrived)).kind == "pickup"


async def test_described_nonupgrade_does_not_fill_inventory():
    obs = replace(equipped_observation(), cells=(Cell(0, 0, 33, ")", True),),
                  messages=("You see here a +0 dagger.",))
    agent = equipment_agent(obs)
    for _ in range(3):
        assert (await agent.decide(obs)).kind != "pickup"


@pytest.mark.parametrize("offset", [(0, 0), (20, -8)])
async def test_recorded_equipment_route_keeps_hidden_destination_across_waypoint_and_rebase(offset):
    fixture = Path(__file__).parent / "fixtures" / "recorded_equipment_visibility_cycle.json"
    observations = []
    for data in json.loads(fixture.read_text())["observations"]:
        for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                               ("abilities", Ability), ("resistances", Resistance),
                               ("skills", Skill), ("status_details", Status)):
            data[field] = tuple(factory(**entry) for entry in data[field])
        data["position"] = tuple(data["position"])
        for field in ("messages", "statuses"):
            data[field] = tuple(data[field])
        observations.append(Observation(**data))
    visible, hidden = observations
    goal = (0, 14)
    assert any((cell.x, cell.y) == goal and cell.visible and cell.glyph == ")" for cell in visible.cells)
    assert any((cell.x, cell.y) == goal and not cell.visible for cell in hidden.cells)
    agent = equipment_agent(visible)
    first = await agent.decide(visible)
    assert (first.kind, first.value, first.objective) == ("move", "h", "equipment")
    dx, dy = offset
    hidden = replace(hidden, position=(hidden.position[0] + dx, hidden.position[1] + dy),
                     cells=tuple(replace(cell, x=cell.x + dx, y=cell.y + dy) for cell in hidden.cells))
    second = await agent.decide(hidden)
    assert second.kind == "move" and second.objective == "equipment"
    assert second.value != "l"  # The recorded exploration action returned to the starting viewpoint.
    step_x, step_y = DIRECTIONS[second.value]
    next_cell = next(cell for cell in hidden.cells
                     if (cell.x, cell.y) == (hidden.position[0] + step_x, hidden.position[1] + step_y))
    assert next_cell.visible and next_cell.walkable and not next_cell.cloud
    assert (goal[0] + dx, goal[1] + dy) in agent.exploration.current.equipment_ground


def loot_corridor(position, *, turn=20, target_visible=True, messages=()):
    """A bounded known route, with its only unexplored frontier beyond x=3."""
    cells = tuple(Cell(x, 0, 33, "@" if (x, 0) == position else ")" if x == 0 else ".",
                       x != 0 or target_visible) for x in range(4))
    walls = tuple(Cell(x, y, 7, "#", True) for x in range(-1, 5) for y in (-1, 1))
    return replace(equipped_observation(), position=position, turn=turn, messages=messages,
                   cells=cells + walls + (Cell(-1, 0, 7, "#", True),))


@pytest.mark.parametrize("name,useful", [
    ("+0 war axe", True), ("+0 dagger", False), ("+0 hand axe", False),
])
async def test_remembered_loot_route_processes_item_then_converges_on_frontier(name, useful):
    agent = equipment_agent(loot_corridor((3, 0)))
    # Losing sight of the actual item at a waypoint must not switch objectives.
    for step, x in enumerate((3, 2, 1)):
        obs = loot_corridor((x, 0), turn=20 + step, target_visible=x != 2)
        action = await agent.decide(obs)
        assert (action.kind, action.value, action.objective) == ("move", "h", "equipment")
    arrived = loot_corridor((0, 0), turn=23, messages=("There are several objects here.",))
    assert (await agent.decide(arrived)).kind == "pickup"
    pile = replace(arrived, menu={"tag": "pickup", "items": [
        {"text": f" a - a {name}", "hotkeys": [97]},
    ]})
    selected = await agent.decide(pile)
    assert selected.kind == ("menu" if useful else "cancel")
    if useful:
        assert selected.value == "a"
        candidate = Item(2, "c", name)
        collected = replace(arrived, inventory=arrived.inventory + (candidate,))
        equipped = await agent.decide(collected)
        assert (equipped.kind, equipped.value) == ("wield", "c")
        arrived = replace(collected, inventory=(
            replace(collected.inventory[0], equipped=False), collected.inventory[1],
            replace(candidate, equipped=True),
        ))
        assert (await agent.decide(arrived)).kind == "inspect"  # Refresh after observed wield success.
    # The same pile/glyph can remain after a rejected item or a partial pickup.
    # Native exploration takes over instead of revisiting the processed pile.
    resumed = replace(loot_corridor((0, 0), turn=24), inventory=arrived.inventory)
    assert (await agent.decide(resumed)).kind == "explore"
    # Synthetic native no-progress reply exercises the manual route as well.
    for step, x in enumerate((0, 1, 2)):
        obs = replace(loot_corridor((x, 0), turn=24 + step), inventory=arrived.inventory,
                      native_automation="explore" if step == 0 else "",
                      native_result="partial" if step == 0 else "")
        action = await agent.decide(obs)
        assert (action.kind, action.value, action.objective) == ("move", "l", "explore")
    finished = replace(loot_corridor((3, 0), turn=27), inventory=arrived.inventory)
    assert (await agent.decide(finished)).kind == "pause"


async def test_remembered_loot_never_authorizes_an_unseen_or_unsafe_next_step():
    start = loot_corridor((3, 0))
    agent = equipment_agent(start)
    assert (await agent.decide(start)).objective == "equipment"
    waypoint = loot_corridor((2, 0), turn=21, target_visible=False)
    cells = {(cell.x, cell.y): cell for cell in waypoint.cells}
    assert agent._path(waypoint, cells, {(0, 0)}, visible_first=True) == "h"
    for unsafe in (replace(cells[(1, 0)], visible=False),
                   replace(cells[(1, 0)], cloud=True),
                   replace(cells[(1, 0)], feature=12)):
        route = {**cells, (1, 0): unsafe}
        assert agent._path(waypoint, route, {(0, 0)}, visible_first=True) is None
    dangerous = replace(waypoint, monsters=(Monster(1, "rat", 1, 0),))
    action = await agent.decide(dangerous)
    assert action.objective != "equipment"
    assert action.kind not in {"pickup", "wear", "wield", "put_on", "menu"}


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
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Wear which item?"}]})
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
