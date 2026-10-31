"""Captured source12 poison refusal and an ordinary foe across a moat."""
import asyncio
from collections import Counter
from dataclasses import replace
import json
from math import ceil
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics, visible_dangerous_weapon
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development12_combat.json"


def captured(name):
    values = dict(json.loads(FIXTURE.read_text())["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def choose(policy, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return policy.decide(obs, cells, Counter(), RuleBasedAgent._covered)


def exhausted_policy(obs):
    policy = TacticalController()
    # Explicitly synthetic stationary responses before the captured endpoint:
    # exercise the actual wait-budget transitions, not a counter overwritten by
    # the first observation's initialization.
    for turns_ago in range(policy.encounter_progress.limit, 0, -1):
        before = replace(obs, turn=obs.turn-turns_ago, time=obs.time-10*turns_ago)
        policy.routine(before, Action("wait", objective="defend choke"))
    return policy


@pytest.mark.parametrize("wait_budget", ["fresh", "exhausted"])
def test_poisoned_public_multiarrival_can_reform_on_clear_ground_without_rage(wait_budget):
    # The capture supplies only this decision's public inputs. No recorded
    # response is an outcome for the newly selected formation-preserving move.
    obs = captured("mild_poison_wave")
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    policy = TacticalController() if wait_budget == "fresh" else exhausted_policy(obs)
    action = choose(policy, obs)
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    target = obs.position[0]+dx, obs.position[1]+dy
    assert cells[target].visible and cells[target].walkable and not cells[target].cloud
    assert all((enemy.x, enemy.y) != target for enemy in obs.monsters)
    # Reserve arrivals, not just bodies already adjacent in the captured frame.
    # This is a one-action bound; terrain unknown to the public stays passable.
    walking = policy.walking_geometry(obs)
    def arrivals(position):
        return sum(walking.distance(enemy, info, position,
                                    info.reach+ceil(info.movement_speed-1e-9))
                   <= info.reach+ceil(info.movement_speed-1e-9)
                   for enemy in obs.hostiles for info in (mechanics(enemy),))
    assert arrivals(obs.position) > 2
    assert arrivals(target) <= 1
    selected = next(row for row in action.assessment["candidates"]
                    if (row["kind"], row["value"]) == (action.kind, action.value))
    assert selected["expected_loss"] < obs.poison_survival*.2
    assert selected["burst_loss"] < obs.poison_survival*.6


def test_identified_curing_still_preempts_the_poisoned_ordinary_fallback():
    obs = captured("mild_poison_wave")
    obs = replace(obs, inventory=(*obs.inventory, Item(25, "z", "potion of curing", category="potion")))
    action = choose(exhausted_policy(obs), obs)
    assert (action.kind, action.value) == ("quaff", "z")


@pytest.mark.parametrize("survival", [None, 0, 1])
def test_unknown_or_critical_poison_does_not_authorize_the_ordinary_commitment(survival):
    obs = captured("mild_poison_wave")
    obs = replace(obs, poison_survival=survival, status_details=())
    action = choose(exhausted_policy(obs), obs)
    assert action.kind == "pause"


def test_native_moated_gnoll_is_examined_plain_whip_not_a_reaching_or_distortion_threat():
    obs = captured("moated_whip_gnoll")
    enemy, = obs.hostiles
    assert tuple(enemy.equipment) == ("+0 whip",)
    assert mechanics(enemy).melee == 11 and mechanics(enemy).reach == 1
    assert not visible_dangerous_weapon(enemy)
    action = choose(exhausted_policy(obs), obs)
    # Tactics alone has no legal distance-reducing step: the policy caller needs
    # actual enclosure evidence and an exploration goal, not fabricated pursuit.
    assert action.kind == "pause"
    assert any(cell.feature == 31 and cell.visible for cell in obs.cells)


def test_agent_bypasses_the_actual_moat_on_dry_ground_without_claiming_a_full_cage():
    obs = captured("moated_whip_gnoll")
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    # This evidence is deliberately partial: the second interior tile and
    # southern/eastern boundary were not all observed. Preserve that fact.
    assert not cells[(8, -20)].visible and not cells[(9, -19)].visible
    assert cells[(6, -19)].feature == 0
    agent = RuleBasedAgent()
    agent.tactics = exhausted_policy(obs)
    action = asyncio.run(agent.decide(obs))
    assert action.kind == "move" and action.objective not in {"combat", "lure", "blocked"}
    dx, dy = DIRECTIONS[action.value]
    target = obs.position[0]+dx, obs.position[1]+dy
    assert cells[target].visible and cells[target].walkable and not cells[target].cloud
    assert obs.hostiles[0].distance(target) >= obs.hostiles[0].distance(obs.position)
    assert len(obs.hostiles) == 1 and tuple(obs.hostiles[0].equipment) == ("+0 whip",)
    assert not cells[(8, -20)].visible and cells[(6, -19)].feature == 0


def test_moated_gnoll_does_not_lose_hard_weapon_avoidance_when_inspected():
    obs = captured("moated_whip_gnoll")
    enemy, = obs.hostiles
    enemy = replace(enemy, equipment=("+0 whip of distortion",),
                    description=enemy.description.replace("+0 whip", "+0 whip of distortion"))
    obs = replace(obs, monsters=(enemy,))
    policy = exhausted_policy(obs)
    action = choose(policy, obs)
    assert policy.latest_assessment.threats[0].avoid_melee
    assert action.kind not in {"attack", "ability"}
    if action.kind == "move":
        dx, dy = DIRECTIONS[action.value]
        target = obs.position[0]+dx, obs.position[1]+dy
        assert enemy.distance(target) >= enemy.distance(obs.position)
        assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud
                   for cell in obs.cells)
