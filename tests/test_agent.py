from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.danger import assess_danger
from dcss_bot.models import Action, Cell, Item, Monster, Observation


def encounter(name):
    data = json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())
    data["position"] = tuple(data["position"])
    data["cells"] = tuple(Cell(**c) for c in data["cells"])
    data["monsters"] = tuple(Monster(**m) for m in data["monsters"])
    return Observation(place="Dungeon", depth=8, turn=20, input_mode=1, **data)


@pytest.mark.parametrize("fixture", ["merging_slimes", "low_hp", "unsafe_stairs"])
async def test_emergency_retreats_instead_of_stairs_explore_or_autoattack(fixture):
    obs = encounter(fixture)
    agent = RuleBasedAgent()
    agent.last_location = obs.location
    agent.visits[(-1, 0)] = 1
    decision = await agent.decide(obs)
    assert decision.kind == "move" and decision.value == "h"
    assert decision.objective == "retreat"


def test_merged_slime_escalates_even_when_alone_and_healthy():
    obs = encounter("merging_slimes")
    obs = replace(obs, monsters=(Monster(3, "titanic slime creature", 3, 0, 1),))
    assert assess_danger(obs).emergency
    assert not assess_danger(replace(obs, monsters=(Monster(3, "rat", 3, 0, 0),))).emergency


def test_damage_status_and_multiple_attackers_affect_danger():
    obs = replace(encounter("low_hp"), hp=25)
    assert assess_danger(obs, previous_hp=30).emergency is False
    assert assess_danger(obs, previous_hp=35).incoming_damage == 10
    assert assess_danger(obs, previous_hp=35).emergency
    assert assess_danger(replace(obs, statuses=("Conf",))).emergency
    enemies = tuple(Monster(i, "rat", x, y, 0) for i, (x, y) in enumerate([(1, 0), (0, 1), (-1, 0)]))
    assert assess_danger(replace(obs, monsters=enemies)).adjacent == 3
    assert assess_danger(replace(obs, monsters=enemies)).emergency


async def test_identified_healing_precedes_fleeing_but_unknown_items_are_not_guessed():
    obs = encounter("low_hp")
    agent = RuleBasedAgent()
    obs = replace(obs, inventory=(Item(1, "b", "a potion of heal wounds"), Item(2, "c", "a smoky potion")))
    decision = await agent.decide(obs)
    assert (decision.kind, decision.value) == ("quaff", "b")
    decision = await RuleBasedAgent().decide(replace(obs, inventory=(Item(2, "c", "a smoky potion"),)))
    assert decision.kind != "quaff"


async def test_recovery_is_one_turn_and_stairs_require_clear_healthy_floor():
    obs = Observation(hp=15, hp_max=30, turn=10, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 42, ">", True),), input_mode=1)
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).kind == "wait"
    assert (await agent.decide(replace(obs, hp=30, turn=25))).kind == "stairs_down"


async def test_blocked_movement_is_not_repeated_forever():
    obs = Observation(hp=20, hp_max=20, turn=1, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, ".", True), Cell(1, 0, 33, ".", True)))
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).value == "l"
    assert (await agent.decide(obs)).kind == "pause"


async def test_unsafe_stair_action_is_rejected_at_adapter_boundary():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg":"player", "hp":8,"hp_max":30,
                         "species": PROFILE.species, "god": PROFILE.god})
    adapter.state.apply({"msg":"input_mode", "mode":1})
    adapter.state.apply({"msg":"map", "cells":[{"x":1,"y":0,"t":{"bg":1},"mon":{"id":1,"name":"ogre"}}]})
    for kind in ("stairs_up", "stairs_down", "explore"):
        with pytest.raises(ProtocolError):
            await adapter.dispatch(Action(kind))


async def test_death_never_requests_another_combat_action():
    obs = replace(encounter("low_hp"), hp=0, dead=True)
    assert (await RuleBasedAgent().decide(obs)).kind == "pause"


async def test_berserk_cooldown_does_not_prevent_healing():
    obs = replace(encounter("low_hp"), statuses=("-Berserk",),
                  inventory=(Item(0, "a", "a potion of heal wounds"),))
    assert (await RuleBasedAgent().decide(obs)).kind == "quaff"


async def test_equipment_message_is_not_reused_after_moving():
    obs = Observation(hp=20, hp_max=20, turn=1, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, ".", True)),
                      messages=("You see here a +0 war axe.",))
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).kind == "pickup"
    assert (await agent.decide(replace(obs, turn=2))).kind == "move"


async def test_passive_flora_does_not_trap_exploration_but_trivial_enemies_remain_threats():
    obs = Observation(hp=19, hp_max=19, place="Dungeon", depth=1, input_mode=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, "P", True)),
                      monsters=(Monster(1, "withered plant", 1, 0, 0),))
    action = await RuleBasedAgent().decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")
    for name in ("rat", "oklob plant", "active ballistomycete"):
        enemy = Monster(2, name, 1, 0, 0)
        assert replace(obs, monsters=(enemy,)).hostiles == (enemy,)


def ranged_encounter(threat=1):
    return Observation(hp=25, hp_max=25, turn=20, place="Dungeon", depth=1, input_mode=1,
                       cells=tuple(Cell(x, 0, 33, ".", True) for x in range(-1, 5)),
                       monsters=(Monster(1, "dart slug", 4, 0, threat),))


async def test_dart_slug_requires_closing_in_open_terrain_not_indefinite_waiting():
    obs = ranged_encounter()
    agent = RuleBasedAgent()
    for x in range(4):
        action = await agent.decide(replace(obs, position=(x, 0), turn=20 + x))
        assert (action.kind, action.value, action.objective) == ("move", "l", "combat")


async def test_nearby_cover_beats_distance_only_retreat_in_a_shooting_lane():
    obs = ranged_encounter(threat=2)
    obs = replace(obs, cells=obs.cells + (Cell(0, 1, 33, ".", True),
                                        Cell(0, 2, 33, ".", True),
                                        Cell(2, 1, 7, "#", True)))
    agent = RuleBasedAgent()
    agent.last_location = obs.location
    agent.visits[(-1, 0)] = 1  # Farther away, but still directly exposed.
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "j", "retreat")


@pytest.mark.parametrize("feature", [0, 11, 12, 13, 14, 31])
def test_unknown_clear_walls_grates_and_water_do_not_count_as_opaque_cover(feature):
    obs = ranged_encounter()
    cells = {(2, 1): Cell(2, 1, feature, "#", True)}
    assert not RuleBasedAgent._covered(cells, obs.monsters[0], (0, 2))


async def test_cover_lure_holds_briefly_then_resumes_instead_of_waiting_forever():
    obs = ranged_encounter()
    obs = replace(obs, cells=obs.cells + (Cell(0, 1, 33, ".", True),
                                        Cell(0, 2, 33, ".", True),
                                        Cell(2, 1, 7, "#", True)))
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).value == "j"
    hidden = replace(obs, turn=21, position=(0, 1), monsters=())
    assert (await agent.decide(hidden)).objective == "lure"
    assert (await agent.decide(replace(hidden, turn=23))).objective == "explore"


def test_multiple_or_dangerous_shooters_escalate_danger_even_when_healthy():
    obs = ranged_encounter()
    assert not assess_danger(obs).emergency
    multiple = replace(obs, monsters=obs.monsters + (Monster(2, "centaur", 4, 2, 1),))
    assert "multiple ranged threats" in assess_danger(multiple).reasons
    assert "dangerous ranged threat" in assess_danger(ranged_encounter(threat=2)).reasons


async def test_ranged_closing_does_not_step_into_unseen_terrain_or_another_enemy():
    obs = ranged_encounter()
    unseen = replace(obs, cells=tuple(replace(c, visible=False) if c.x == 1 else c for c in obs.cells))
    assert (await RuleBasedAgent().decide(unseen)).kind == "wait"
    guarded = replace(obs, monsters=obs.monsters + (Monster(2, "ogre", 2, 1, 1),))
    assert (await RuleBasedAgent().decide(guarded)).kind == "wait"


@pytest.mark.parametrize("feature", [45, 49, 60])
async def test_crossing_escape_hatches_and_exit_is_allowed_but_never_activates_them(feature):
    obs = Observation(hp=20, hp_max=20, turn=0, place="Dungeon", depth=1, input_mode=1,
                      cells=(Cell(0, 0, 60, "@", True), Cell(1, 0, feature, ">", True),
                             Cell(2, 0, 33, ".", True)))
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("move", "l")
    action = await agent.decide(replace(obs, position=(1, 0), turn=1))
    assert (action.kind, action.value) == ("move", "l")
    on_hatch = replace(obs, position=(0, 0), cells=(Cell(0, 0, feature, ">", True),))
    assert (await RuleBasedAgent().decide(on_hatch)).kind == "pause"


async def test_demonic_plant_does_not_hold_up_exploration():
    obs = Observation(hp=20, hp_max=20, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, ".", True)),
                      monsters=(Monster(1, "demonic plant", 7, 0, 0),))
    assert not obs.hostiles
    assert (await RuleBasedAgent().decide(obs)).objective == "explore"


async def test_berserk_cooldown_alone_does_not_waste_healthy_exploration_turns():
    obs = Observation(hp=20, hp_max=20, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, ".", True)),
                      statuses=("-Berserk",))
    assert (await RuleBasedAgent().decide(obs)).objective == "explore"
    for statuses in (("Berserk",), ("-Berserk", "Slow"), ("Exhausted",)):
        assert (await RuleBasedAgent().decide(replace(obs, statuses=statuses))).objective == "recover"
