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
            monsters=tuple(Monster(**{**monster, "threat": threat}) for monster in row["monsters"]),
        )


async def encounter_decision(agent, **kwargs):
    for observation in recorded_lure(**kwargs):
        action = await agent.decide(observation)
        if observation.turn == 2492:
            return observation, action
    raise AssertionError("Missing recorded encounter boundary")


async def test_real_dart_slug_circle_closes_after_bounded_lure_despite_los_and_id_churn():
    observation, action = await encounter_decision(RuleBasedAgent())
    assert (action.kind, action.objective) == ("move", "combat")
    dx, dy = DIRECTIONS[action.value]
    target = observation.position[0] + dx, observation.position[1] + dy
    assert observation.hostiles[0].distance(target) < observation.hostiles[0].distance(observation.position)
    assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud
               for cell in observation.cells)


@pytest.mark.parametrize("change", [{"hp": 10}, {"threat": 2}])
async def test_lure_deadline_never_forces_an_injured_or_dangerous_ranged_charge(change):
    _, action = await encounter_decision(RuleBasedAgent(), **change)
    assert action.objective in {"lure", "retreat"}


async def test_a_later_separate_encounter_can_try_cover_again():
    agent = RuleBasedAgent()
    observation, _ = await encounter_decision(agent)
    action = await agent.decide(replace(observation, turn=2510))
    assert (action.kind, action.objective) == ("move", "lure")
