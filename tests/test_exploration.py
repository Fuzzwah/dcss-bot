from dataclasses import replace

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Cell, DIRECTIONS, Monster, Observation


def enclosed(floors):
    """Known walls around a small floor layout; callers can conceal openings."""
    xs, ys = zip(*floors)
    cells = {(x, y): Cell(x, y, 7, "#", True)
             for x in range(min(xs) - 1, max(xs) + 2)
             for y in range(min(ys) - 1, max(ys) + 2)}
    cells.update({pos: Cell(*pos, feature, ">" if feature == 42 else ".", True)
                  for pos, feature in floors.items()})
    return cells


def observation(cells, **kwargs):
    return Observation(hp=20, hp_max=20, place="Dungeon", depth=1, turn=1,
                       cells=tuple(cells.values()), **kwargs)


def moved(obs, action):
    dx, dy = DIRECTIONS[action.value]
    return replace(obs, position=(obs.position[0] + dx, obs.position[1] + dy), turn=obs.turn + 1)


async def test_revealing_a_room_does_not_require_walking_its_interior():
    world = enclosed({(x, y): 42 if (x, y) == (4, 4) else 33
                      for x in range(1, 8) for y in range(1, 8)})
    remembered = {}
    position = (4, 4)
    visited = {position}
    agent = RuleBasedAgent()
    for turn in range(80):
        visible = {pos for pos in world
                   if max(abs(pos[0] - position[0]), abs(pos[1] - position[1])) <= 3}
        remembered.update({pos: world[pos] for pos in visible})
        cells = {pos: replace(cell, visible=pos in visible) for pos, cell in remembered.items()}
        obs = replace(observation(cells, position=position), turn=turn)
        action = await agent.decide(obs)
        if action.kind == "stairs_down":
            break
        assert action.kind == "move"
        next_obs = moved(obs, action)
        assert next_obs.position in remembered
        assert world[next_obs.position].walkable
        position = next_obs.position
        visited.add(position)
    else:
        pytest.fail("The fully revealed room never finished exploration")
    assert len(visited) < 49
    assert all(pos in remembered for pos, cell in world.items() if cell.walkable)
    assert position == (4, 4)


async def test_fully_known_enclosed_room_can_descend_with_unvisited_interior():
    cells = enclosed({(x, y): 42 if (x, y) == (0, 0) else 33
                      for x in range(5) for y in range(5)})
    agent = RuleBasedAgent()
    assert (await agent.decide(observation(cells))).kind == "stairs_down"
    assert len(agent.visits) == 1


@pytest.mark.parametrize("explicit_unknown", [False, True])
async def test_concealed_passage_uses_known_frontier_not_unknown_steps(explicit_unknown):
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    if explicit_unknown:
        cells[(3, 0)] = Cell(3, 0)
    else:
        del cells[(3, 0)]
    agent = RuleBasedAgent()
    obs = observation(cells)
    for expected in ((1, 0), (2, 0)):
        action = await agent.decide(obs)
        assert (action.kind, action.objective) == ("move", "explore")
        obs = moved(obs, action)
        assert obs.position == expected
    # Neither approaching the endpoint nor a visit to the previous square
    # retires it. Actually standing there with no reveal does retire it.
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "h", "descend")
    assert (3, 0) not in agent.visits


async def test_closed_door_opens_then_reaches_newly_revealed_passage():
    cells = enclosed({(0, 0): 42, (1, 0): 1})
    del cells[(2, 0)]
    agent = RuleBasedAgent()
    obs = observation(cells)
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")
    # Bumping opens the door and consumes a turn without moving the player.
    cells[(1, 0)] = Cell(1, 0, 34, "'", True)
    obs = replace(obs, turn=2, cells=tuple(cells.values()))
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("move", "l")
    assert (1, 0) not in agent.blocked
    cells[(2, 0)] = Cell(2, 0, 33, ".", True)
    obs = replace(moved(obs, action), cells=tuple(cells.values()))
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")


async def test_unreachable_boundary_does_not_prevent_safe_descent():
    cells = enclosed({(0, 0): 42, (4, 0): 33})
    del cells[(5, 0)]
    assert (await RuleBasedAgent().decide(observation(cells))).kind == "stairs_down"


async def test_unknown_diagonal_behind_two_walls_is_not_a_frontier():
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 1)]
    assert (await RuleBasedAgent().decide(observation(cells))).kind == "stairs_down"


@pytest.mark.parametrize("hazard", ["blocked", "occupied"])
async def test_no_safe_reachable_frontier_allows_known_stairs(hazard):
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 0)]
    agent = RuleBasedAgent()
    obs = observation(cells)
    agent.last_location = obs.location
    if hazard == "blocked":
        agent.blocked.add((1, 0))
    else:
        obs = replace(obs, monsters=(Monster(1, "friendly orc", 1, 0, attitude=1),))
    assert (await agent.decide(obs)).kind == "stairs_down"


async def test_temporary_cloud_blockage_is_not_floor_completion():
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    del cells[(3, 0)]
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    agent = RuleBasedAgent()
    obs = observation(cells)
    assert (await agent.decide(obs)).kind == "wait"
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=False)
    action = await agent.decide(replace(obs, turn=2, cells=tuple(cells.values())))
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")


async def test_cloud_on_frontier_itself_is_waited_out_not_stepped_into():
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 0)]
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    assert (await RuleBasedAgent().decide(observation(cells))).kind == "wait"


async def test_stagnant_frontier_does_not_leave_an_unvisited_target_loop():
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    del cells[(3, 0)]
    agent = RuleBasedAgent()
    obs = observation(cells)
    objectives = []
    for _ in range(5):
        action = await agent.decide(obs)
        objectives.append(action.objective)
        if action.kind == "stairs_down":
            break
        assert action.kind == "move"
        obs = moved(obs, action)
    assert action.kind == "stairs_down"
    assert objectives == ["explore", "explore", "descend", "descend", "descend"]


async def test_failed_move_is_not_retried_and_new_location_resets_frontiers():
    cells = {(0, 0): Cell(0, 0, 33, ".", True), (1, 0): Cell(1, 0, 33, ".", True)}
    obs = observation(cells)
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).value == "l"
    assert (await agent.decide(obs)).kind == "pause"
    assert (1, 0) in agent.blocked
    assert (0, 0) in agent.exhausted_frontiers
    # This coordinate was an exhausted viewpoint on the old floor, but the
    # same coordinate on another floor is a fresh candidate.
    obs = replace(obs, depth=2, position=(1, 0), turn=2)
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "h", "explore")
    assert not agent.blocked
    assert (0, 0) not in agent.exhausted_frontiers


def test_frontier_is_reconsidered_when_local_unknown_boundary_changes():
    cells = enclosed({(0, 0): 33, (1, 0): 33})
    del cells[(2, 0)]
    del cells[(2, 1)]
    agent = RuleBasedAgent()
    obs = observation(cells, position=(1, 0))
    assert (1, 0) not in agent._frontiers(obs, cells)
    away = replace(obs, position=(0, 0))
    assert (1, 0) not in agent._frontiers(away, cells)
    cells[(2, 1)] = Cell(2, 1, 7, "#", True)
    assert (1, 0) in agent._frontiers(away, cells)


async def test_old_visit_counts_do_not_hide_a_new_information_frontier():
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 0)]
    obs = observation(cells)
    agent = RuleBasedAgent()
    agent.last_location = obs.location
    agent.visits[(1, 0)] = 100
    assert (await agent.decide(obs)).objective == "explore"


@pytest.mark.parametrize("unsafe", ["injured", "hostile"])
async def test_exhausted_exploration_never_overrides_combat_or_recovery(unsafe):
    cells = enclosed({(0, 0): 42})
    obs = observation(cells)
    if unsafe == "injured":
        obs = replace(obs, hp=10)
    else:
        obs = replace(obs, monsters=(Monster(1, "rat", 1, 0, threat=0),))
    action = await RuleBasedAgent().decide(obs)
    assert action.kind != "stairs_down"
    assert action.objective in {"recover", "combat", "retreat", "survive"}


@pytest.mark.parametrize("feature", [45, 49, 60])
async def test_exhausted_frontier_does_not_authorize_hatch_or_exit_activation(feature):
    obs = observation(enclosed({(0, 0): feature}))
    assert (await RuleBasedAgent().decide(obs)).kind == "pause"
