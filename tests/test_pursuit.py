from dataclasses import replace
import json
from pathlib import Path

import pytest

from legacy_replay import decide_legacy_observation

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Ability, Action, Cell, Item, Monster, Observation


def pursuit():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "pursuit.json").read_text())
    observations = []
    for data in fixture["observations"]:
        data["position"] = tuple(data["position"])
        data["cells"] = tuple(Cell(**cell) for cell in data["cells"])
        data["monsters"] = tuple(Monster(**monster) for monster in data["monsters"])
        data["inventory"] = tuple(Item(**item) for item in data["inventory"])
        data["messages"] = tuple(data["messages"])
        data["statuses"] = tuple(data["statuses"])
        observations.append(Observation(**data))
    agent = RuleBasedAgent()
    agent.exploration.observe(observations[0])
    agent.exploration.current.visits.update({tuple(pos): 1 for pos in fixture["known_retreat_positions"]})
    return agent, observations


async def after_engagement():
    agent, observations = pursuit()
    first = await decide_legacy_observation(agent, observations[0])
    damaged = await decide_legacy_observation(agent, observations[1])
    assert (first.kind, first.value, first.objective) == ("move", "y", "combat")
    # A normal-speed adjacent goblin can follow and make opportunity attacks:
    # reduced HP is not evidence that walking east will escape it.
    assert (damaged.kind, damaged.value, damaged.objective) == ("move", "y", "combat")
    return agent, observations[2]


def after_recorded_failed_retreat():
    """Seed the genuine old-policy walk recorded between turns 191 and 192."""
    agent, observations = pursuit()
    agent.tactics.routine(observations[1], Action("move", "l", objective="retreat"))
    return agent, observations[2]


async def test_real_goblin_encounter_does_not_begin_an_ineffective_retreat_loop():
    agent, followed = await after_engagement()
    action = await decide_legacy_observation(agent, followed)
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")


async def test_observed_berserk_is_used_proactively_in_bounded_dangerous_melee():
    agent, observations = pursuit()
    ready = replace(observations[0], abilities=(Ability("Berserk", "a", True),),
                    monsters=(replace(observations[0].monsters[0], name="gnoll", threat=2),))
    action = await decide_legacy_observation(agent, ready)
    assert (action.kind, action.value) == ("ability", "berserk")


@pytest.mark.parametrize("statuses", [(), ("-Berserk",)])
async def test_low_hp_does_not_offer_repeated_pursuit_attacks_or_late_berserk(statuses):
    agent, followed = after_recorded_failed_retreat()
    action = await decide_legacy_observation(agent, replace(followed, hp=8, statuses=statuses,
                                        abilities=(Ability("Berserk", "a", True),)))
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")


async def test_known_healing_still_precedes_pursuit_commitment():
    agent, followed = await after_engagement()
    action = await decide_legacy_observation(agent, replace(followed, hp=8,
                                       inventory=(Item(3, "d", "a potion of heal wounds"),),
                                       abilities=(Ability("Berserk", "a", True),)))
    assert (action.kind, action.value) == ("quaff", "d")


async def test_actual_separation_does_not_retain_a_forced_pursuit_commitment():
    agent, followed = await after_engagement()
    separated = replace(followed, monsters=(replace(followed.monsters[0], x=-3),),
                        abilities=(Ability("Berserk", "a", True),))
    assert (await decide_legacy_observation(agent, separated)).kind != "ability"
    action = await decide_legacy_observation(agent, replace(followed, turn=193))
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")


@pytest.mark.parametrize("statuses", [("-Berserk",), ("Slow",), ("Pois",)])
async def test_pursuit_never_overrides_observed_berserk_commitment_restrictions(statuses):
    agent, followed = await after_engagement()
    obs = replace(followed, statuses=statuses, abilities=(Ability("Berserk", "a", True),))
    assert (await decide_legacy_observation(agent, obs)).kind != "ability"


async def test_floor_change_does_not_make_merged_slime_a_safe_berserk_target():
    agent, followed = await after_engagement()
    obs = replace(followed, depth=2, abilities=(Ability("Berserk", "a", True),),
                  monsters=(Monster(10, "large slime creature", 0, -10, 3),))
    action = await decide_legacy_observation(agent, obs)
    assert action.kind != "ability"
    assert not (action.kind == "move" and action.value == "h")
