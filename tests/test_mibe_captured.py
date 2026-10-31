from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from legacy_replay import decide_legacy_tactics

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalConfig, TacticalController
from dcss_bot.models import Ability, Cell, Item, Monster, Observation, Resistance, Skill, Status


def captured(name):
    data = json.loads((Path(__file__).parent / "fixtures" / (name + ".json")).read_text())["observation"]
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        data[field] = tuple(factory(**entry) for entry in data[field])
    data["position"] = tuple(data["position"])
    for field in ("messages", "statuses"):
        data[field] = tuple(data[field])
    return Observation(**data)


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return decide_legacy_tactics(controller, obs, cells, Counter({position: 1 for position in cells}), RuleBasedAgent._covered)


def test_actually_visible_priest_is_not_zero_damage_because_corner_heuristic_disagrees():
    obs = captured("recorded_priest_visibility")
    controller = TacticalController()
    # The recorded eight damage is evidence that the putative cover did not stop attacks.
    controller.previous = replace(obs, turn=obs.turn-1, hp=obs.hp+8)
    action = choose(controller, obs)
    assert action is not None and action.kind != "pause"
    assert action.kind != "stairs_down"
    assessment = action.assessment["assessment"]
    assert assessment["recent_damage"] == 8
    assert any(threat["ranged"] and threat["expected_loss"] > 0 for threat in assessment["threats"])
    if action.kind == "move":
        assert action.objective == "retreat"


def test_recorded_corridor_melee_group_can_be_awaited_instead_of_spurious_pause():
    obs = captured("recorded_melee_choke")
    action = choose(TacticalController(), obs)
    assert action is not None and action.kind not in {"pause", "stairs_up", "stairs_down"}
    if action.kind == "move":
        from dcss_bot.models import DIRECTIONS
        dx, dy = DIRECTIONS[action.value]
        cell = next(cell for cell in obs.cells if (cell.x,cell.y) == (obs.position[0]+dx,obs.position[1]+dy))
        assert cell.visible and cell.walkable and not cell.cloud


def test_observed_kill_releases_skipped_enemy_zone_without_erasing_other_floor_memory():
    controller = TacticalController()
    cells = tuple(Cell(x,0,33,".",True) for x in range(4))
    old = Observation(place="Dungeon",depth=2,xl=5,hp=40,hp_max=40,cells=cells,
                      monsters=(Monster(1,"orc wizard",3,0),), messages=("You see an orc wizard.",))
    controller._remember(old, "disabling caster")
    controller.previous = old
    assert (2,0) in controller.avoided_positions(old,{(cell.x,cell.y):cell for cell in cells})
    now = replace(old,turn=1,monsters=(),messages=old.messages+("You kill the orc wizard!",))
    controller.observe(now)
    assert not controller.avoided_positions(now,{(cell.x,cell.y):cell for cell in cells})


@pytest.mark.parametrize("options", [
    {"horizon_actions":float("nan")}, {"horizon_actions":0},
    {"dangerous_fraction":.8,"emergency_fraction":.3}, {"berserk_min_hp":0},
    {"lure_step_limit":0},
])
def test_invalid_tactical_configuration_cannot_silently_disable_safety(options):
    with pytest.raises(ValueError):
        TacticalConfig(**options)


def test_resistance_jewellery_is_not_mistaken_for_a_drinkable_resistance_potion():
    obs = Observation(hp=10,hp_max=40,place="Dungeon",depth=3,
                      inventory=(Item(0,"a","ring of cold resistance",equipped=True),),
                      monsters=(Monster(1,"ice beast",1,0),))
    action = choose(TacticalController(),obs)
    assert action.kind != "quaff"


async def test_actual_enchantment_menu_chooses_equipped_axe_instead_of_abort_confirmation_loop():
    obs = captured("recorded_enchant_weapon")
    action = await RuleBasedAgent().decide(obs)
    assert action.kind == "menu"
    selected = next(item for item in obs.inventory if item.equipped and item.letter == action.value)
    assert "hand axe" in selected.name
    assert any(ord(action.value) in entry.get("hotkeys",[]) for entry in obs.menu["items"])


def test_actual_pursuit_feedback_stops_backpedalling_and_allows_a_bounded_fight():
    data = json.loads((Path(__file__).parent/"fixtures"/"recorded_priest_pursuit.json").read_text())["observations"]
    observations = []
    for values in data:
        for field,factory in (("cells",Cell),("monsters",Monster),("inventory",Item),("abilities",Ability),
                              ("resistances",Resistance),("skills",Skill),("status_details",Status)):
            values[field] = tuple(factory(**item) for item in values[field])
        values["position"] = tuple(values["position"])
        for field in ("messages","statuses"):
            values[field] = tuple(values[field])
        observations.append(Observation(**values))
    first,second,third,after_attack = observations
    controller = TacticalController()
    controller.encounter_start = first.turn
    controller.encounter_last_seen = first.turn
    from dcss_bot.models import Action, DIRECTIONS
    controller.routine(first,Action("move","b",objective="retreat"))
    choose(controller,second)
    controller.routine(second,Action("move","b",objective="retreat"))
    action = choose(controller,third)
    assert action.objective == "combat"
    if action.kind == "ability":
        assert action.value == "berserk"
    else:
        assert action.kind == "move"
        dx,dy = DIRECTIONS[action.value]
        assert any(enemy.name == "orc priest" and (enemy.x,enemy.y) ==
                   (third.position[0]+dx,third.position[1]+dy) for enemy in third.monsters)
        controller.routine(third,action)
        action = choose(controller,after_attack)
        assert (action.kind,action.value) == ("ability","berserk")


@pytest.mark.parametrize("name,distance", [("recorded_endoplasm_seed2", 7),
                                         ("recorded_endoplasm_seed3", 6)])
async def test_recorded_freeze_endoplasm_allows_a_legal_contact_response(name, distance):
    from dcss_bot.characters.mibe_mechanics import mechanics
    from dcss_bot.models import DIRECTIONS

    obs = captured(name)
    assert obs.hp == obs.hp_max and obs.depth == 1
    endoplasm, = obs.hostiles
    assert endoplasm.name == "endoplasm" and endoplasm.spells == ["Freeze"]
    assert endoplasm.distance(obs.position) == distance
    assert not mechanics(endoplasm).ranged
    action = await RuleBasedAgent().decide(obs)
    assert action.kind in {"wait", "move"}
    assert action.objective in {"combat", "lure"}
    assert not any(threat["ranged"] for threat in action.assessment["assessment"]["threats"])
    if action.kind == "move":
        dx, dy = DIRECTIONS[action.value]
        destination = obs.position[0] + dx, obs.position[1] + dy
        cell = next(cell for cell in obs.cells if (cell.x, cell.y) == destination)
        assert cell.visible and cell.walkable and not cell.cloud
        assert all((enemy.x, enemy.y) != destination for enemy in obs.hostiles)
