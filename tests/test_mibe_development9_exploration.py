"""Public source9 no-path pauses: traversal lifecycle and cloud-cut stairs."""
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import WithdrawalGoal
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import DOWNSTAIRS


def captured(name):
    path = Path(__file__).parent / "fixtures" / "recorded_development9_exploration.json"
    values = dict(json.loads(path.read_text())["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def prepared(obs):
    agent = RuleBasedAgent()
    equipment = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipment)
    agent.development._last_skill_inspection = obs.turn
    agent.previous_messages = obs.messages
    return agent


async def test_recorded_upstairs_arrival_completes_withdrawal_without_banning_a_clear_chain():
    before, after = captured("withdrawal_departure"), captured("withdrawal_arrival")
    assert before.location != after.location and not after.hostiles
    agent = prepared(after)
    agent.exploration.observe(before)
    agent.tactics.withdrawal = WithdrawalGoal(before.location, before.position, "upstairs", before.turn-10, 0)
    agent.tactics.routine(before, Action("stairs_up", objective="planned retreat"))
    action = await agent.decide(after)
    assert agent.tactics.withdrawal is None
    assert action.objective != "planned retreat"
    # Completing the plan does not reopen the abandoned downstairs.
    arrival_stair = next(cell for cell in after.cells if (cell.x, cell.y) == after.position)
    assert arrival_stair.feature in DOWNSTAIRS
    assert after.position not in agent.tactics.safe_stairs(after, {after.position})


async def test_recorded_fog_cut_waits_for_alternative_stairs_without_reentering_yiuf():
    obs = captured("cloud_blocked_seed5")
    assert not obs.hostiles and obs.hp == obs.hp_max
    agent = prepared(obs)
    agent.exploration.observe(obs)
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    # Recreate an exhausted floor, not a new exploration run: visiting known
    # viewpoints cannot reveal a new route through the currently visible fog.
    agent.exploration.current.exhausted_frontiers = {
        position: agent._unknown_edges(cells, position)
        for position, cell in cells.items() if cell.walkable
    }
    here = cells[obs.position]
    agent.tactics.unsafe_stairs[(obs.location, here.feature)] = obs.xl + 2
    downstairs = {position for position, cell in cells.items() if cell.feature in DOWNSTAIRS}
    alternatives = agent.tactics.safe_stairs(obs, downstairs)
    assert obs.position not in alternatives and len(alternatives) == 2
    assert agent._path(obs, cells, alternatives) is None
    assert agent._path(obs, cells, alternatives, allow_clouds=True) is not None
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("wait", "wait for terrain")
    # Publicly observing the fog disappear permits a real known-ground step.
    clear = replace(obs, turn=obs.turn+1,
                    cells=tuple(replace(cell, cloud=False) for cell in obs.cells))
    action = await agent.decide(clear)
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    target = clear.position[0]+dx, clear.position[1]+dy
    assert cells[target].visible and cells[target].walkable
    assert obs.position not in agent.tactics.safe_stairs(clear, {obs.position})


def test_recorded_seed1_is_stair_banned_not_disconnected_or_permission_to_force_descent():
    obs = captured("exhausted_seed1")
    agent = prepared(obs)
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    downstairs = {position for position, cell in cells.items() if cell.feature in DOWNSTAIRS}
    assert len(downstairs) == 3
    assert all(agent._path(obs, cells, {position}) is not None for position in downstairs)
    # Recorded bad arrival (42) and reverse withdrawal links (43,44) all
    # require XL8 under the retained XL+2 protection; XL6 cannot force them.
    for position in downstairs:
        agent.tactics.unsafe_stairs[(obs.location, cells[position].feature)] = obs.xl + 2
    assert not agent.tactics.safe_stairs(obs, downstairs)
