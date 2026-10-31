from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.danger import assess_danger, is_ranged
from dcss_bot.models import Cell, DIRECTIONS, Item, Monster, Observation


def recorded(name):
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())
    observations = []
    for data in fixture["observations"]:
        data["position"] = tuple(data["position"])
        data["cells"] = tuple(Cell(**cell) for cell in data["cells"])
        data["monsters"] = tuple(Monster(**monster) for monster in data["monsters"])
        data["inventory"] = tuple(Item(**item) for item in data["inventory"])
        data["messages"] = tuple(data["messages"])
        data["statuses"] = tuple(data["statuses"])
        observations.append(Observation(**data))
    return observations


def destination(observation, action):
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    target = observation.position[0] + dx, observation.position[1] + dy
    assert any((cell.x, cell.y) == target and cell.walkable and cell.visible
               and not cell.cloud and cell.feature != 1 for cell in observation.cells)
    assert all((monster.x, monster.y) != target for monster in observation.hostiles)
    return target


@pytest.mark.parametrize("index", [0, 1, 2])
async def test_real_reach_attacker_is_not_given_free_hits_or_wasted_berserk(index):
    obs = recorded("reaching_gnoll")[index]
    action = await RuleBasedAgent().decide(obs)
    target = destination(obs, action)
    assert min(m.distance(target) for m in obs.hostiles) < min(m.distance(obs.position) for m in obs.hostiles)
    assert sum(m.distance(target) <= 1 for m in obs.hostiles) <= 1


async def test_unwitnessed_ordinary_foe_is_not_assumed_to_have_a_hidden_polearm():
    obs = replace(recorded("reaching_gnoll")[2], hp=41, statuses=(), messages=())
    assert (await RuleBasedAgent().decide(obs)).kind == "wait"


async def test_reach_response_keeps_known_healing_priority_and_rejects_cloud_routes():
    obs = recorded("reaching_gnoll")[2]
    injured = replace(obs, hp=8, inventory=(Item(2, "c", "a potion of heal wounds"),))
    action = await RuleBasedAgent().decide(injured)
    assert (action.kind, action.value) == ("quaff", "c")
    blocked = replace(obs, cells=tuple(replace(cell, cloud=True) for cell in obs.cells))
    assert (await RuleBasedAgent().decide(blocked)).kind == "pause"


@pytest.mark.parametrize("enemies,berserk", [(True, False), (False, False), (True, True)])
async def test_liquid_fire_requires_safe_movement_even_without_visible_enemies(enemies, berserk):
    obs = recorded("liquid_fire")[0]
    if not enemies:
        obs = replace(obs, monsters=())
    if berserk:
        obs = replace(obs, statuses=obs.statuses + ("Berserk",))
    assert assess_danger(obs).emergency
    action = await RuleBasedAgent().decide(obs)
    destination(obs, action)
    assert action.objective == "emergency recovery"


async def test_liquid_fire_does_not_authorize_stepping_into_clouds_or_guessing_a_door():
    obs = recorded("liquid_fire")[0]
    cells = (Cell(*obs.position, 33, "@", True),
             Cell(obs.position[0] + 1, obs.position[1], 1, "+", True),
             Cell(obs.position[0] - 1, obs.position[1], 33, ".", True, True))
    assert (await RuleBasedAgent().decide(replace(obs, cells=cells))).kind == "pause"


def test_bombardier_beetles_are_known_ranged_threats():
    assert is_ranged(Monster(1, "bombardier beetle", 4, 0))
