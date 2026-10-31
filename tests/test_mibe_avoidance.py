"""Avoidance must forbid re-entry, not trap a player already inside its footprint."""
from dataclasses import replace

import pytest

from legacy_replay import decide_legacy_observation

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Cell, DIRECTIONS, Monster, Observation


def remembered_encounter(*monsters, cells=None, position=(0, 0)):
    obs = Observation(species="Minotaur",god="Trog",place="Dungeon",depth=4,xl=5,
                      hp=40,hp_max=40,turn=100,input_mode=1,position=position,
                      cells=cells or tuple(Cell(x,0,33,".",True) for x in range(-4,5)))
    agent = RuleBasedAgent()
    agent.exploration.observe(obs)
    # This memory comes from a genuinely visible earlier encounter, not a hidden
    # monster position. The current observation deliberately has no visible foe.
    agent.tactics._remember(replace(obs,monsters=monsters),"previously observed dangerous encounter")
    agent.tactical_avoided = agent.tactics.avoided_positions(obs,{(c.x,c.y):c for c in obs.cells})
    return agent,obs


async def test_player_inside_avoidance_footprint_can_leave_before_development_or_looting():
    agent,obs = remembered_encounter(Monster(1,"orc wizard",1,0,2))
    for turn in (100,101):
        action = await decide_legacy_observation(agent, replace(obs,turn=turn))
        assert action.kind == "move" and action.objective == "retreat"
        dx,dy = DIRECTIONS[action.value]
        destination = obs.position[0]+dx,obs.position[1]+dy
        assert destination[0] < obs.position[0]
        assert any((cell.x,cell.y)==destination and cell.visible and cell.walkable and not cell.cloud
                   for cell in obs.cells)
        obs = replace(obs,position=destination)
    assert obs.position not in agent.tactical_avoided


def test_overlapping_memories_allow_a_clearance_plateau_to_reach_a_real_exit():
    cells = tuple(Cell(0,y,33,".",True) for y in range(-4,5)) + (
        Cell(-1,0,33,".",True),Cell(1,0,33,".",True))
    agent,obs = remembered_encounter(Monster(1,"hydra",-1,0,3),Monster(2,"hydra",1,0,3),cells=cells)
    action = agent._leave_avoided_area(obs,{(cell.x,cell.y):cell for cell in cells})
    assert action is not None and action.kind == "move"
    assert DIRECTIONS[action.value] in {(0,1),(0,-1)}


@pytest.mark.parametrize("blocked", ["cloud","closed_door","unseen"])
def test_memory_escape_does_not_authorize_hazards_or_unknown_ground(blocked):
    cells = tuple(Cell(x,0,33,".",True) for x in range(-4,5))
    cells = tuple(replace(cell,cloud=True) if blocked=="cloud" and cell.x==-1 else
                  replace(cell,feature=1) if blocked=="closed_door" and cell.x==-1 else
                  replace(cell,visible=False) if blocked=="unseen" and cell.x==-1 else cell for cell in cells)
    agent,obs = remembered_encounter(Monster(1,"orc wizard",1,0,2),cells=cells)
    assert agent._leave_avoided_area(obs,{(cell.x,cell.y):cell for cell in cells}) is None


def test_escaping_does_not_disable_ordinary_avoidance_for_later_paths():
    agent,obs = remembered_encounter(Monster(1,"orc wizard",1,0,2),position=(-3,0))
    cells = {(cell.x,cell.y):cell for cell in obs.cells}
    assert agent._leave_avoided_area(obs,cells) is None
    assert agent._path(obs,cells,{(2,0)}) is None


def test_visible_threats_keep_tactical_priority_over_remembered_escape():
    agent,obs = remembered_encounter(Monster(1,"orc wizard",1,0,2))
    visible = replace(obs,monsters=(Monster(2,"titanic slime creature",-1,0,3),))
    assert agent._leave_avoided_area(visible,{(cell.x,cell.y):cell for cell in visible.cells}) is None
