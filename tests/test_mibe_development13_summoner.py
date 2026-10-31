"""Recorded source6/4 bounded commitments; altered responses are synthetic."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development13_summoner.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def recorded():
    data = json.loads(FIXTURE.read_text())
    policy = TacticalController()
    # Replay recorded public action transitions, not rewritten retreat counters.
    for row in data["history"]:
        policy.routine(observation(row["observation"]), Action(**row["action"]))
    return policy, observation(data["observation"])


def choose(policy, obs):
    return policy.decide(obs, {(cell.x, cell.y): cell for cell in obs.cells},
                         Counter(), RuleBasedAgent._covered)


def legal_approach(obs, action):
    assert action.kind == "move" and action.objective == "bounded summoner approach"
    dx, dy = DIRECTIONS[action.value]
    position = obs.position[0]+dx, obs.position[1]+dy
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    assert cells[position].visible and cells[position].walkable and not cells[position].cloud
    assert position not in {(enemy.x, enemy.y) for enemy in obs.monsters}
    assert next(enemy for enemy in obs.hostiles if enemy.id == 4).distance(position) == 1
    selected = next(row for row in action.assessment["candidates"]
                    if (row["kind"], row["value"]) == (action.kind, action.value))
    assert obs.hp-selected["burst_loss"] >= 24
    return position


async def test_actual_source6_consumer_prepares_then_takes_one_legal_bounded_approach():
    policy, obs = recorded()
    agent = RuleBasedAgent()
    agent.tactics = policy
    action = await agent.decide(obs)
    assert obs.turn == 3451 and obs.hp == 49 and obs.hp_max == 51 and not obs.statuses
    assert policy.ineffective_retreats == 3
    assert (action.kind, action.value) == ("ability", "trogs_hand")
    assert any(entry.name == "Trog's Hand" and entry.available and entry.failure == 6
               for entry in obs.abilities)
    zombie = next(threat for threat in policy.latest_assessment.threats if threat.name == "human zombie")
    assert zombie.walking_steps_lower_bound == 5
    assert zombie.expected_loss == zombie.burst_loss == 0
    assert action.assessment["minor_summoner_commitment"]["approach_available"]
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])

    # Synthetic failed Hand response: no regeneration appeared, but a failed
    # native power must not authorize another preparation attempt forever.
    response = replace(obs, turn=obs.turn+1, time=obs.time+10)
    approach = await agent.decide(response)
    position = legal_approach(response, approach)
    assert policy.minor_summoner_preparations == {(obs.location, "eustachio")}
    assert policy.minor_summoner_approaches == {(obs.location, "eustachio")}
    # Synthetic successful movement with unchanged visible enemies. Ordinary
    # adjacent attack remains legal; this is not a mandate to rage/chase.
    contact = replace(response, turn=response.turn+1, time=response.time+10, position=position)
    strike = await agent.decide(contact)
    assert strike.kind == "move" and strike.objective == "combat"
    dx, dy = DIRECTIONS[strike.value]
    assert (position[0]+dx, position[1]+dy) == (-1, -2)


def test_synthetic_active_regeneration_or_unavailable_hand_still_permits_the_one_step():
    for active in (False, True):
        policy, obs = recorded()
        obs = replace(obs, statuses=("Regen",) if active else (),
                      abilities=tuple(entry for entry in obs.abilities if entry.name != "Trog's Hand"))
        legal_approach(obs, choose(policy, obs))


def test_synthetic_blink_and_failed_movement_cannot_renew_the_approach_budget():
    policy, obs = recorded()
    obs = replace(obs, abilities=())
    legal_approach(obs, choose(policy, obs))
    # Synthetic blocked move leaves the same observation at the next turn.
    for offset in range(1, 4):
        action = choose(policy, replace(obs, turn=obs.turn+offset, time=obs.time+offset*10))
        assert action.objective != "bounded summoner approach"
        assert (action.kind, action.value) != ("ability", "berserk")
        assert not action.assessment["minor_summoner_commitment"]["approach_available"]
    # Synthetic Blink is an actual relocation, not damage/progress proof. The
    # same encounter and visible identity retain their spent approach budget.
    target = next(enemy for enemy in obs.monsters if enemy.id == 4)
    occupied = {(enemy.x, enemy.y) for enemy in obs.monsters}
    landing = next(cell for cell in obs.cells if cell.visible and cell.walkable and not cell.cloud
                   and max(abs(cell.x-obs.position[0]), abs(cell.y-obs.position[1])) == 2
                   and (cell.x, cell.y) not in occupied)
    blinked = replace(obs, turn=obs.turn+4, time=obs.time+40,
                      monsters=tuple(replace(enemy, x=landing.x, y=landing.y) if enemy is target else enemy
                                     for enemy in obs.monsters))
    action = choose(policy, blinked)
    assert action.objective != "bounded summoner approach"
    assert not action.assessment["minor_summoner_commitment"]["approach_available"]


async def test_synthetic_consumer_new_native_id_does_not_renew_preparation_or_pursuit():
    policy, obs = recorded()
    agent = RuleBasedAgent()
    agent.tactics = policy
    first = await agent.decide(obs)
    assert first.objective == "prepare minor summoner"
    # Synthetic LOS/Blink-style identity refresh with the same displayed book.
    refreshed = replace(obs, turn=obs.turn+1, time=obs.time+10,
                        monsters=tuple(replace(enemy, id=104) if enemy.id == 4 else enemy
                                       for enemy in obs.monsters))
    approach = await agent.decide(refreshed)
    assert approach.objective == "bounded summoner approach"
    assert approach.assessment["minor_summoner_commitment"]["target_id"] == 104
    # Another replacement cannot refill the spent approach or Hand attempt.
    refreshed_again = replace(refreshed, turn=obs.turn+2, time=obs.time+20,
                              monsters=tuple(replace(enemy, id=204) if enemy.id == 104 else enemy
                                             for enemy in refreshed.monsters))
    action = await agent.decide(refreshed_again)
    assert action.objective not in {"bounded summoner approach", "prepare minor summoner"}
    assert not action.assessment["minor_summoner_commitment"]["approach_available"]
    assert not action.assessment["minor_summoner_commitment"]["preparation_available"]


@pytest.mark.parametrize("change", ["unknown_book", "greater_summon", "disable", "distortion",
                                   "unknown_body", "strong_weapon", "near_companion", "open_barrier",
                                   "low_hp", "poison", "hydra"])
def test_synthetic_danger_does_not_inherit_minor_summoner_permission(change):
    policy, obs = recorded()
    target = next(enemy for enemy in obs.monsters if enemy.id == 4)
    if change in {"unknown_book", "greater_summon", "disable"}:
        spell = {"unknown_book": "Unrecognized spell", "greater_summon": "Summon Greater Demon",
                 "disable": "Paralyse"}[change]
        target = replace(target, spells=(*target.spells, spell))
    elif change == "distortion":
        target = replace(target, equipment=("+0 rapier of distortion",),
                         description=target.description.replace("+0 rapier", "+0 rapier of distortion"))
    elif change == "unknown_body":
        target = replace(target, description=None)
    elif change == "strong_weapon":
        target = replace(target, description=target.description.replace("Hit: +0 rapier  12", "Hit: +0 rapier  30"))
    elif change == "hydra":
        target = replace(target, name="hydra")
    obs = replace(obs, monsters=tuple(target if enemy.id == 4 else enemy for enemy in obs.monsters))
    if change == "near_companion":
        obs = replace(obs, monsters=tuple(replace(enemy, x=2, y=-1) if enemy.id != 4 else enemy
                                         for enemy in obs.monsters))
    elif change == "open_barrier":
        obs = replace(obs, cells=tuple(replace(cell, feature=33) if cell.feature in range(5, 14) else cell
                                      for cell in obs.cells))
    elif change == "low_hp":
        obs = replace(obs, hp=15)
    elif change == "poison":
        obs = replace(obs, statuses=("Pois",), poison_survival=35)
    action = choose(policy, obs)
    assert action.objective not in {"bounded summoner approach", "prepare minor summoner"}
    assert not action.assessment["minor_summoner_commitment"]["approach_available"]
    assert action.assessment["minor_summoner_commitment"]["rejected"]
    assert (action.kind, action.value) != ("ability", "berserk")


def test_synthetic_emergency_healing_and_curing_keep_priority_over_the_new_permission():
    for poison in (False, True):
        policy, obs = recorded()
        obs = replace(obs, hp=15, statuses=("Pois",) if poison else (),
                      poison_survival=10 if poison else obs.poison_survival,
                      inventory=(*obs.inventory,
                                 Item(24, "y", "potion of heal wounds", category="potion"),
                                 Item(25, "z", "potion of curing", category="potion")))
        action = choose(policy, obs)
        assert action.kind == "quaff"
        assert action.value == ("z" if poison else "y")


def recorded_entangler():
    data = json.loads((FIXTURE.parent / "recorded_development13_entangler.json").read_text())
    policy = TacticalController()
    for row in data["history"]:
        policy.routine(observation(row["observation"]), Action(**row["action"]))
    return policy, observation(data["observation"]), data["public_examinations"]


async def test_actual_source4_inspects_before_rage_then_known_distant_web_prevents_commitment():
    policy, obs, examinations = recorded_entangler()
    agent = RuleBasedAgent()
    agent.tactics = policy
    assert obs.turn == 1279 and obs.hp == 23 and not obs.statuses
    assert all(enemy.description is None for enemy in obs.monsters)
    assert any(enemy.distance(obs.position) == 1 for enemy in obs.hostiles)
    action = await agent.decide(obs)
    assert action.kind == "inspect_monster"
    # Explicitly synthetic turn-free examination responses: actual recorded
    # public descriptions/books, composed onto the same source4 pre-rage pose.
    # This does not pretend unexamined worms initially exposed their book.
    inspected = set()
    for _ in range(len(obs.monsters)):
        if action.kind != "inspect_monster":
            break
        position = tuple(map(int, action.value.split(",")))
        target = next(enemy for enemy in obs.monsters if (enemy.x, enemy.y) == position)
        assert target.id not in inspected
        inspected.add(target.id)
        known = Monster(**examinations[target.name])
        target = replace(target, description=known.description, spells=known.spells,
                         spell_ranges=known.spell_ranges, equipment=known.equipment,
                         speed_description=known.speed_description)
        obs = replace(obs, monsters=tuple(target if enemy.id == target.id else enemy
                                         for enemy in obs.monsters))
        action = await agent.decide(obs)
    assert action.kind != "inspect_monster"
    assert (action.kind, action.value) != ("ability", "berserk")
    assert obs.turn == 1279 and obs.position == (-2, 0)
    assert any(threat.entangling and threat.distance > 1 for threat in policy.latest_assessment.threats)
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])


@pytest.mark.parametrize("restraint", ["Held", "Netted", "Webbed"])
def test_synthetic_current_restraint_blocks_new_rage_even_with_an_adjacent_ordinary_foe(restraint):
    policy, obs, examinations = recorded_entangler()
    # Explicitly synthetic reduced encounter. The adjacent endoplasm is a
    # bounded ordinary fight; restraint alone must block new commitment.
    enemy = next(enemy for enemy in obs.monsters if enemy.name == "endoplasm")
    known = Monster(**examinations["endoplasm"])
    enemy = replace(enemy, x=obs.position[0]+1, y=obs.position[1], description=known.description,
                    spells=known.spells, spell_ranges=known.spell_ranges)
    obs = replace(obs, hp=obs.hp_max, statuses=(restraint,), monsters=(enemy,))
    action = choose(policy, obs)
    assert action.objective == "escape restraint"
    assert (action.kind, action.value) != ("ability", "berserk")
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])
