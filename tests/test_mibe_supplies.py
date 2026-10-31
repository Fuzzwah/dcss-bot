"""Safe public supply destinations and native pile selection, without item use."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_development import pickup_menu_action, supply_name
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import UPSTAIRS


SUPPLY_ACTIONS = {"pickup", "pickup_select", "menu", "read", "quaff"}


def clear_observation():
    return Observation(hp=25, hp_max=25, turn=20, place="Dungeon", depth=1,
                       input_mode=1, cells=(Cell(0, 0, 33, "@", True),),
                       inventory=(Item(0, "a", "+0 hand axe", equipped=True, category="weapon"),
                                  Item(1, "b", "+0 animal skin", equipped=True, category="armour")))


def agent_for(obs):
    agent = RuleBasedAgent()
    equipped = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipped)
    agent.development._last_skill_inspection = obs.turn
    return agent


def pile(*entries):
    return {"tag": "pickup", "title": {"text": "Pick up what? 2/52 gear slots (_ for help)"},
            "items": list(entries)}


def row(key, name, *, category="?", quantity=1, **extra):
    return {"text": f" {key} - {name}", "q": quantity,
            "hotkeys": [ord(key), ord(category)], "level": 2, **extra}


def corridor(position=(3, 0), *, glyph="?", turn=20, target_visible=True, equipment=False):
    cells = tuple(Cell(x, 0, next(iter(UPSTAIRS)) if x == 3 else 33,
                       "@" if (x, 0) == position else glyph if x == 0 else ")" if equipment and x == 4 else ".",
                       target_visible if x == 0 else True) for x in range(5))
    walls = tuple(Cell(x, y, 7, "#", True) for x in range(-1, 6) for y in (-1, 1))
    return replace(clear_observation(), position=position, turn=turn,
                   cells=cells + walls + (Cell(-1, 0, 7, "#", True),))


@pytest.mark.parametrize("name", ["a scroll labelled KAMOOK LUTYEC", "2 scrolls labelled JOL EXIROCIUL",
                                  "a bubbling dark potion", "3 potions of curing", "a wand of flame (4)",
                                  "12 gold pieces"])
def test_supplies_are_classified_from_rendered_appearance_only(name):
    assert supply_name(name)


@pytest.mark.parametrize("name", ["a stone", "a +0 hand axe", "a kobold corpse", "a parchment of Summon Hydra"])
def test_non_supplies_are_not_relabelled_as_consumables(name):
    assert not supply_name(name)


def test_scroll_only_menu_collects_unknown_appearance_not_an_inventory_letter():
    # Native bag and gear letters overlap: floor keys are menu keys, not slots.
    obs = replace(clear_observation(), inventory=clear_observation().inventory + (
        Item(52, "a", "bubbling dark potion", category="potion"),
        Item(104, "a", "scroll labelled OLD LABEL", category="scroll"),
    ), menu=pile({"text": "Scrolls", "level": 1}, row("d", "2 scrolls labelled KAMOOK LUTYEC", quantity=2)))
    action = pickup_menu_action(obs)
    assert (action.kind, action.value) == ("menu", "d")


def test_berserk_cooldown_alone_does_not_make_a_safe_supply_menu_illegal():
    obs = replace(clear_observation(), statuses=("-Berserk",),
                  menu=pile(row("d", "a scroll labelled KAMOOK LUTYEC")))
    action = pickup_menu_action(obs)
    assert (action.kind, action.value) == ("menu", "d")


def test_mixed_pile_selects_all_unknown_supplies_and_best_verified_equipment_once():
    obs = replace(clear_observation(), menu=pile(
        row("a", "a scroll labelled KAMOOK LUTYEC"),
        row("b", "a +0 dagger", category=")"),
        row("c", "a bubbling dark potion", category="!"),
        row("d", "a +0 war axe", category=")"),
        row("e", "a +9 war axe of chaos", category=")"),
        row("f", "a +0 hand axe", category=")"),
    ))
    action = pickup_menu_action(obs)
    assert (action.kind, action.value) == ("pickup_select", "acd")
    assert action.kind not in {"read", "quaff"}


def test_pickup_does_not_infer_identity_or_use_bag_quantity_from_hidden_wire_fields():
    unknown = row("c", "a scroll labelled KAMOOK LUTYEC", sub_type=999, plus=99, flags=0)
    obs = replace(clear_observation(), menu=pile(unknown))
    assert (pickup_menu_action(obs).kind, pickup_menu_action(obs).value) == ("menu", "c")


@pytest.mark.parametrize("entries", [
    (),
    (row("a", "a scroll labelled KAMOOK LUTYEC", quantity=0),),
    (row("a", "a scroll labelled KAMOOK LUTYEC", colour=8),),
    (row("a", "a scroll labelled KAMOOK LUTYEC"), row("a", "a potion of curing", category="!")),
    ({"text": " a + a scroll labelled KAMOOK LUTYEC", "q": 1, "hotkeys": [97, 63], "level": 2},),
    ({"text": " a # 2 scrolls labelled KAMOOK LUTYEC", "q": 2, "hotkeys": [97, 63], "level": 2},),
])
def test_empty_disabled_ambiguous_or_preselected_piles_are_cancelled(entries):
    assert pickup_menu_action(replace(clear_observation(), menu=pile(*entries))).kind == "cancel"


@pytest.mark.parametrize("change", [
    {"hp": 20},
    {"monsters": (Monster(1, "rat", 1, 0),)},
    {"statuses": ("Berserk",)},
    {"statuses": ("Confused",)},
    {"cells": (Cell(0, 0, 33, "@", True, cloud=True),)},
])
def test_pickup_menu_cancels_before_selection_when_safety_changes(change):
    obs = replace(clear_observation(), menu=pile(row("a", "a scroll labelled KAMOOK LUTYEC")), **change)
    assert pickup_menu_action(obs).kind == "cancel"


@pytest.mark.parametrize("glyph", ["?", "!"])
async def test_spotted_supplies_beat_nearer_equipment_and_further_exploration(glyph):
    obs = corridor(glyph=glyph, equipment=True)
    action = await agent_for(obs).decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "h", "supplies")



async def test_remembered_native_map_supply_is_a_destination_after_reconnect():
    obs = corridor(target_visible=False)
    action = await agent_for(obs).decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "h", "supplies")

@pytest.mark.parametrize("glyph", ["?", "!"])
async def test_supply_destination_survives_los_loss_and_native_coordinate_rebase(glyph):
    start = corridor(glyph=glyph)
    agent = agent_for(start)
    assert (await agent.decide(start)).objective == "supplies"
    hidden = corridor((2, 0), glyph=glyph, turn=21, target_visible=False)
    hidden = replace(hidden, position=(12, -7),
                     cells=tuple(replace(cell, x=cell.x + 10, y=cell.y - 7) for cell in hidden.cells))
    action = await agent.decide(hidden)
    assert (action.kind, action.value, action.objective) == ("move", "h", "supplies")
    dx, dy = DIRECTIONS[action.value]
    next_cell = next(cell for cell in hidden.cells
                     if (cell.x, cell.y) == (hidden.position[0] + dx, hidden.position[1] + dy))
    assert next_cell.visible and next_cell.walkable and not next_cell.cloud


@pytest.mark.parametrize("glyph", ["?", "!"])
@pytest.mark.parametrize("failure", ["There are no items here.", "You can't carry that many items.", "Okay, then."])
async def test_native_empty_full_or_failed_pickup_does_not_retry_the_same_pile(glyph, failure):
    obs = corridor((0, 0), glyph=glyph)
    obs = replace(obs, messages=("You see here a scroll labelled KAMOOK LUTYEC." if glyph == "?"
                                  else "You see here a bubbling dark potion.",))
    agent = agent_for(obs)
    assert (await agent.decide(obs)).kind == "pickup"
    failed = replace(obs, messages=obs.messages + (failure,))
    for turn in (20, 21, 22):
        action = await agent.decide(replace(failed, turn=turn))
        assert action.kind not in SUPPLY_ACTIONS
        assert action.objective != "supplies"


@pytest.mark.parametrize("hazard", [{"cloud": True}, {"visible": False}])
async def test_supply_route_refuses_cloud_or_invisible_next_step_without_forgetting_goal(hazard):
    start = corridor()
    agent = agent_for(start)
    assert (await agent.decide(start)).objective == "supplies"
    waypoint = corridor((2, 0), turn=21, target_visible=False)
    blocked = replace(waypoint, cells=tuple(
        replace(cell, **hazard) if (cell.x, cell.y) == (1, 0) else cell
        for cell in waypoint.cells))
    action = await agent.decide(blocked)
    assert not (action.kind == "move" and action.value == "h")
    assert action.kind not in SUPPLY_ACTIONS
    assert (0, 0) in agent.exploration.current.supply_ground


def recorded_scroll():
    fixture = Path(__file__).parent / "fixtures" / "recorded_spotted_scroll.json"
    observations = []
    for record in json.loads(fixture.read_text())["records"]:
        data = record["observation"]
        for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item)):
            data[field] = tuple(factory(**entry) for entry in data[field])
        for field in ("position", "messages", "statuses"):
            data[field] = tuple(data[field])
        observations.append(Observation(**data))
    return observations


async def test_recorded_visible_scroll_is_a_destination_but_hostile_arrival_does_not_pick_up():
    spotted, arrived = recorded_scroll()
    agent = agent_for(spotted)
    action = await agent.decide(spotted)
    assert (action.kind, action.value, action.objective) == ("move", "n", "supplies")
    action = await agent.decide(arrived)
    assert action.kind not in SUPPLY_ACTIONS
    assert (48, 0) in agent.exploration.current.supply_ground


async def pickup_after_bounded_quiet(agent, obs):
    # Synthetic receipts model real one-turn waits, not repeated decisions on
    # an unchanged world or a jump past the post-contact safety hold.
    held = False
    for _ in range(4):
        action = await agent.decide(obs)
        if action.kind == "pickup":
            assert held
            assert action.objective == "supplies"
            return
        assert (action.kind, action.objective) == ("wait", "lure")
        held = True
        obs = replace(obs, turn=obs.turn + 1, time=obs.time + 10)
    pytest.fail("A cleared supply pile was not retried after bounded quiet waits")


async def test_supply_spotted_during_danger_is_collected_only_after_ground_is_clear():
    obs = replace(corridor((0, 0)), messages=("You see here a scroll labelled KAMOOK LUTYEC.",),
                  monsters=(Monster(1, "rat", 1, 0),))
    agent = agent_for(obs)
    assert (await agent.decide(obs)).kind not in SUPPLY_ACTIONS
    cleared = replace(obs, turn=21, monsters=(), messages=obs.messages + ("You kill the rat!",))
    await pickup_after_bounded_quiet(agent, cleared)


async def test_danger_cancelling_an_open_pile_does_not_permanently_abandon_its_scroll():
    obs = replace(corridor((0, 0)), messages=("You see here a scroll labelled KAMOOK LUTYEC.",))
    agent = agent_for(obs)
    assert (await agent.decide(obs)).kind == "pickup"
    interrupted = replace(obs, menu=pile(row("d", "a scroll labelled KAMOOK LUTYEC")),
                          monsters=(Monster(1, "rat", 1, 0),))
    assert (await agent.decide(interrupted)).kind == "cancel"
    cleared = replace(obs, turn=21, messages=obs.messages + ("You kill the rat!",))
    await pickup_after_bounded_quiet(agent, cleared)


async def test_synthetic_scroll_relabel_of_recorded_withdrawal_does_not_reopen_rejected_route():
    # The encounter and movement evidence are recorded; only the optional loot
    # glyph is synthetically changed from armour to a scroll at the same square.
    data = json.loads((Path(__file__).parent / "fixtures"
                       / "recorded_development13_loot_cycle.json").read_text())
    agent = RuleBasedAgent()
    target = (-3, 12)
    for sequence in range(1, 47):
        transition = data["transitions"][sequence - 1]
        raw = data["snapshot"] | transition["before_changes"]
        cells = {(cell["x"], cell["y"]): cell for cell in data["snapshot"]["cells"]}
        for point in transition["removed_cells"]:
            cells.pop(tuple(point))
        cells.update({(cell["x"], cell["y"]): cell for cell in transition["cell_changes"]})
        raw = raw | {"cells": [cell | {"glyph": "?"} if position == target else cell
                              for position, cell in cells.items()]}
        for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                               ("abilities", Ability), ("resistances", Resistance),
                               ("skills", Skill), ("status_details", Status)):
            raw[field] = tuple(factory(**entry) for entry in raw.get(field, ()))
        for field in ("position", "messages", "statuses"):
            raw[field] = tuple(raw[field])
        obs = Observation(**raw)
        action = await agent.decide(obs)
        if sequence in (18, 19):
            assert action.kind == "move" and action.objective == "supplies"
        if sequence in (21, 22, 23):
            assert action.kind == "move" and action.objective == "bypass barrier"
        if sequence >= 26 and action.objective in {"supplies", "equipment"}:
            # Other remembered supplies remain valid. Only the rejected
            # destination must stay closed after this encounter's withdrawal.
            assert agent.exploration.current.equipment_target != target
        if action.kind == "move":
            dx, dy = DIRECTIONS[action.value]
            step = (obs.position[0] + dx, obs.position[1] + dy)
            cell = next(cell for cell in obs.cells if (cell.x, cell.y) == step)
            assert cell.visible and cell.walkable and not cell.cloud
            assert step not in {(enemy.x, enemy.y) for enemy in obs.hostiles}
    assert target in agent.exploration.current.supply_ground


def test_square_description_sharing_pickup_tag_is_not_treated_as_pickup():
    menu = pile(row("a", "a scroll labelled KAMOOK LUTYEC"))
    menu["title"] = {"text": "Visible Items<lightgray> (select to examine)</lightgray>"}
    assert pickup_menu_action(replace(clear_observation(), menu=menu)).kind == "cancel"
