from dataclasses import replace
import json
from pathlib import Path

import pytest

from legacy_replay import decide_legacy_observation

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.danger import assess_danger
from dcss_bot.models import Action, Cell, DIRECTIONS, Item, Monster, Observation


def encounter(name):
    data = json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())
    data["position"] = tuple(data["position"])
    data["cells"] = tuple(Cell(**c) for c in data["cells"])
    data["monsters"] = tuple(Monster(**m) for m in data["monsters"])
    return Observation(place="Dungeon", depth=8, turn=20, input_mode=1, **data)


def prepared_agent(obs):
    """Skip only the already-completed public development inspection."""
    agent = RuleBasedAgent()
    equipment = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipment)
    agent.development._last_skill_inspection = obs.turn
    return agent


@pytest.mark.parametrize("fixture", ["merging_slimes", "low_hp", "unsafe_stairs"])
async def test_emergency_tries_one_known_separation_before_likely_fatal_exchange(fixture):
    obs = encounter(fixture)
    agent = prepared_agent(obs)
    agent.exploration.observe(obs)
    agent.exploration.current.visits[(-1, 0)] = 1
    decision = await decide_legacy_observation(agent, obs)
    assert (decision.kind, decision.value, decision.objective) == ("move", "h", "retreat")


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


async def test_stairs_require_clear_healthy_floor_after_recovery():
    obs = Observation(hp=15, hp_max=30, turn=10, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 42, ">", True),), input_mode=1)
    agent = prepared_agent(obs)
    recovery = await agent.decide(obs)
    assert recovery.objective == "recover"
    assert recovery.kind != "stairs_down"
    healthy = replace(obs, hp=30, turn=25)
    assert (await agent.decide(healthy)).kind == "explore"
    finished = replace(healthy, native_automation="explore", native_result="complete")
    assert (await agent.decide(finished)).kind == "stairs_down"
    threatened = replace(obs, hp=30, monsters=(Monster(1, "rat", 1, 0, 0),))
    assert (await prepared_agent(threatened).decide(threatened)).kind != "stairs_down"


async def test_blocked_movement_is_not_repeated_forever():
    obs = Observation(hp=20, hp_max=20, turn=1, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, ".", True), Cell(1, 0, 33, ".", True)))
    agent = prepared_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    obs = replace(obs, native_automation="explore", native_result="partial")
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
    agent = prepared_agent(obs)
    assert (await agent.decide(obs)).kind == "pickup"
    assert (await agent.decide(replace(obs, turn=2))).kind == "explore"
    stopped = replace(obs, turn=2, native_automation="explore", native_result="partial")
    step = await agent.decide(stopped)
    assert (step.kind, step.value) == ("move", "l")
    moved = replace(obs, position=(1, 0), turn=3)
    assert (await agent.decide(moved)).kind != "pickup"


async def test_passive_flora_does_not_trap_exploration_but_trivial_enemies_remain_threats():
    obs = Observation(hp=19, hp_max=19, place="Dungeon", depth=1, input_mode=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, "P", True)),
                      monsters=(Monster(1, "withered plant", 1, 0, 0),))
    action = await prepared_agent(obs).decide(obs)
    assert (action.kind, action.objective) == ("explore", "explore")
    for name in ("rat", "oklob plant", "active ballistomycete"):
        enemy = Monster(2, name, 1, 0, 0)
        threatened = replace(obs, monsters=(enemy,))
        assert threatened.hostiles == (enemy,)
        assert (await prepared_agent(threatened).decide(threatened)).kind != "explore"


def ranged_encounter(threat=1):
    return Observation(hp=25, hp_max=25, turn=20, place="Dungeon", depth=1, input_mode=1,
                       cells=tuple(Cell(x, 0, 33, ".", True) for x in range(-1, 5)),
                       monsters=(Monster(1, "dart slug", 4, 0, threat, description="dart slug"),))


async def test_dart_slug_requires_closing_in_open_terrain_not_indefinite_waiting():
    obs = ranged_encounter()
    agent = prepared_agent(obs)
    for x in range(4):
        action = await agent.decide(replace(obs, position=(x, 0), turn=23 + x))
        assert (action.kind, action.value, action.objective) == ("move", "l", "combat")


async def test_nearby_cover_beats_distance_only_retreat_in_a_shooting_lane():
    obs = ranged_encounter(threat=2)
    obs = replace(obs, cells=obs.cells + (Cell(0, 1, 33, ".", True),
                                        Cell(0, 2, 33, ".", True),
                                        Cell(2, 1, 7, "#", True)))
    agent = prepared_agent(obs)
    agent.exploration.observe(obs)
    agent.exploration.current.visits[(-1, 0)] = 1  # Farther away, but still directly exposed.
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("move", "j")


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
    agent = prepared_agent(obs)
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
    assert (await prepared_agent(unseen).decide(unseen)).kind in {"pause","wait"}
    guarded = replace(obs, monsters=obs.monsters + (Monster(2, "ogre", 2, 1, 1, description="ogre"),))
    guarded_action = await prepared_agent(guarded).decide(guarded)
    assert guarded_action.objective != "explore"
    if guarded_action.kind == "move":
        dx, dy = DIRECTIONS[guarded_action.value]
        target = obs.position[0] + dx, obs.position[1] + dy
        assert all(monster.distance(target) >= monster.distance(obs.position)
                   for monster in guarded.hostiles)
        assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud
                   for cell in guarded.cells)
    else:
        assert guarded_action.kind in {"wait", "pause"}


@pytest.mark.parametrize("feature", [45, 49, 60])
async def test_crossing_escape_hatches_and_exit_is_allowed_but_never_activates_them(feature):
    obs = Observation(hp=20, hp_max=20, turn=0, place="Dungeon", depth=1, input_mode=1,
                      cells=(Cell(0, 0, 60, "@", True), Cell(1, 0, feature, ">", True),
                             Cell(2, 0, 33, ".", True)))
    agent = prepared_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    obs = replace(obs, native_automation="explore", native_result="partial")
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("move", "l")
    action = await agent.decide(replace(obs, position=(1, 0), turn=1))
    assert (action.kind, action.value) == ("move", "l")
    on_hatch = replace(obs, position=(0, 0), cells=(Cell(0, 0, feature, ">", True),))
    hatch_agent = prepared_agent(on_hatch)
    assert (await hatch_agent.decide(on_hatch)).kind == "explore"
    finished = replace(on_hatch, native_automation="explore", native_result="complete")
    assert (await hatch_agent.decide(finished)).kind == "pause"


async def test_demonic_plant_does_not_hold_up_exploration():
    obs = Observation(hp=20, hp_max=20, place="Dungeon", depth=1,
                      cells=(Cell(0, 0, 33, "@", True), Cell(1, 0, 33, ".", True)),
                      monsters=(Monster(1, "demonic plant", 7, 0, 0),))
    assert not obs.hostiles
    assert (await prepared_agent(obs).decide(obs)).objective == "explore"




async def test_harmless_flying_swarm_does_not_become_a_surrounding_combat_emergency():
    # Native butterfly.yaml: no attacks, flight and 250% speed. Surrounding
    # hostile wire attitudes must not manufacture eight fast melee attackers.
    obs = Observation(hp=19, hp_max=19, place="Dungeon", depth=1, input_mode=1,
                      cells=tuple(Cell(x,y,33,".",True) for x in range(-2,3) for y in range(-2,3)),
                      monsters=tuple(Monster(index,"butterfly",dx,dy,0,
                                     speed_description="Speed: 250%")
                                     for index,(dx,dy) in enumerate(DIRECTIONS.values(),1)))
    agent = prepared_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    stopped = replace(obs, native_automation="explore", native_result="partial")
    action = await agent.decide(stopped)
    assert action.kind == "move"
    destination = DIRECTIONS[action.value]
    assert any((cell.x,cell.y)==destination and cell.visible and cell.walkable
               and not cell.cloud for cell in obs.cells)
