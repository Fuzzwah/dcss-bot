from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Cell, Item, Monster, Observation


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
    agent.last_location = observations[0].location
    agent.visits.update({tuple(pos): 1 for pos in fixture["known_retreat_positions"]})
    return agent, observations


async def after_retreat():
    agent, observations = pursuit()
    attack = await agent.decide(observations[0])
    retreat = await agent.decide(observations[1])
    assert (attack.kind, attack.value) == ("move", "y")
    assert (retreat.kind, retreat.value, retreat.objective) == ("move", "l", "retreat")
    return agent, observations[2]


async def test_real_goblin_pursuit_uses_berserk_before_repeating_ineffective_escape():
    agent, followed = await after_retreat()
    action = await agent.decide(followed)
    assert (action.kind, action.value) == ("ability", "a")


@pytest.mark.parametrize("statuses", [(), ("-Berserk",)])
async def test_failed_retreat_does_not_offer_repeated_pursuit_attacks_at_low_hp(statuses):
    agent, followed = await after_retreat()
    action = await agent.decide(replace(followed, hp=8, statuses=statuses))
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")


async def test_known_healing_still_precedes_pursuit_commitment():
    agent, followed = await after_retreat()
    action = await agent.decide(replace(followed, hp=8,
                                       inventory=(Item(3, "d", "a potion of heal wounds"),)))
    assert (action.kind, action.value) == ("quaff", "d")


async def test_actual_separation_does_not_trigger_or_retain_pursuit_commitment():
    agent, followed = await after_retreat()
    separated = replace(followed, monsters=(replace(followed.monsters[0], x=-1),))
    assert (await agent.decide(separated)).kind != "ability"
    action = await agent.decide(replace(followed, turn=193))
    assert (action.kind, action.value) == ("move", "h")


@pytest.mark.parametrize("change", [{"statuses": ("-Berserk",)}, {"depth": 2},
                                    {"monsters": (Monster(10, "large slime creature", 0, -10, 3),)}])
async def test_pursuit_does_not_override_cooldown_floor_reset_or_slime_safety(change):
    agent, followed = await after_retreat()
    assert (await agent.decide(replace(followed, **change))).kind != "ability"
