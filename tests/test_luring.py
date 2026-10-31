from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Cell, DIRECTIONS, Monster, Observation


def recorded_lure(hp=20, threat=1):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "ranged_lure.json").read_text())
    for row in fixture["observations"]:
        visible = {tuple(position) for position in row["visible_cells"]}
        yield Observation(
            hp=hp, hp_max=20, place="Dungeon", depth=1, turn=row["turn"],
            position=tuple(row["position"]),
            cells=tuple(Cell(**{**cell, "visible": (cell["x"], cell["y"]) in visible})
                        for cell in fixture["cells"]),
            monsters=tuple(Monster(**{**monster, "threat": threat, "description": monster["name"]})
                           for monster in row["monsters"]),
        )


def prepared_lure_agent():
    agent = RuleBasedAgent()
    agent.development._inspection_key = (1, None, ())
    agent.development._last_skill_inspection = 2478
    return agent


async def encounter_decision(agent, **kwargs):
    for observation in recorded_lure(**kwargs):
        action = await agent.decide(observation)
        if observation.turn == 2492:
            return observation, action
    raise AssertionError("Missing recorded encounter boundary")


async def test_real_dart_slug_circle_closes_after_bounded_lure_despite_los_and_id_churn():
    agent = prepared_lure_agent()
    closing = []
    for observation in recorded_lure():
        action = await agent.decide(observation)
        if action.kind == "move" and action.objective == "combat":
            dx, dy = DIRECTIONS[action.value]
            target = observation.position[0] + dx, observation.position[1] + dy
            assert observation.hostiles[0].distance(target) < observation.hostiles[0].distance(observation.position)
            assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud
                       for cell in observation.cells)
            closing.append((observation, action))
    assert closing, "A complete recorded lure circuit must eventually close safely despite brief LOS/ID churn"


async def test_lure_deadline_does_not_force_a_dangerous_ranged_charge():
    observation, action = await encounter_decision(prepared_lure_agent(), threat=2)
    assert action.kind in {"move", "wait", "pause"}
    if action.kind == "move":
        dx, dy = DIRECTIONS[action.value]
        target = observation.position[0] + dx, observation.position[1] + dy
        assert all(monster.distance(target) >= monster.distance(observation.position)
                   for monster in observation.hostiles)


async def test_a_later_separate_encounter_can_try_cover_again():
    agent = prepared_lure_agent()
    observation, _ = await encounter_decision(agent)
    action = await agent.decide(replace(observation, turn=2510))
    assert (action.kind, action.objective) == ("move", "lure")
