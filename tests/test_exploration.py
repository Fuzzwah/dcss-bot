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



def exploration_agent():
    """Start after initial public development inspection; test navigation only."""
    agent = RuleBasedAgent()
    agent.development._inspection_key = (1, None, ())
    agent.development._last_skill_inspection = 0
    return agent


async def native_no_progress(agent, obs, *, result="partial"):
    """Synthetic native reply with unchanged public map, turn and position."""
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("explore", "explore")
    return replace(obs, native_automation="explore", native_result=result)


def moved(obs, action):
    if action.kind == "travel_downstairs":
        target = tuple(map(int, action.value.split(",")))
        elapsed = max(abs(target[0] - obs.position[0]), abs(target[1] - obs.position[1]))
        return replace(obs, position=target, turn=obs.turn + elapsed,
                       native_automation="travel_downstairs", native_result="complete")
    dx, dy = DIRECTIONS[action.value]
    return replace(obs, position=(obs.position[0] + dx, obs.position[1] + dy), turn=obs.turn + 1)


async def test_revealing_a_room_does_not_require_walking_its_interior():
    world = enclosed({(x, y): 42 if (x, y) == (4, 4) else 33
                      for x in range(1, 8) for y in range(1, 8)})
    remembered = {}
    position = (4, 4)
    visited = {position}
    agent = exploration_agent()
    for turn in range(80):
        visible = {pos for pos in world
                   if max(abs(pos[0] - position[0]), abs(pos[1] - position[1])) <= 3}
        remembered.update({pos: world[pos] for pos in visible})
        cells = {pos: replace(cell, visible=pos in visible) for pos, cell in remembered.items()}
        obs = replace(observation(cells, position=position), turn=turn)
        action = await agent.decide(obs)
        if action.kind == "explore":
            # This manual-navigation simulation supplies a synthetic stopped
            # native command whenever a newly revealed map permits another o.
            obs = replace(obs, native_automation="explore", native_result="partial")
            action = await agent.decide(obs)
        if action.kind == "stairs_down":
            break
        assert action.kind in {"move", "travel_downstairs"}
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
    agent = exploration_agent()
    finished = await native_no_progress(agent, observation(cells), result="complete")
    assert (await agent.decide(finished)).kind == "stairs_down"


@pytest.mark.parametrize("explicit_unknown", [False, True])
async def test_concealed_passage_uses_known_frontier_not_unknown_steps(explicit_unknown):
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    if explicit_unknown:
        cells[(3, 0)] = Cell(3, 0)
    else:
        del cells[(3, 0)]
    agent = exploration_agent()
    obs = await native_no_progress(agent, observation(cells))
    for expected in ((1, 0), (2, 0)):
        action = await agent.decide(obs)
        assert (action.kind, action.objective) == ("move", "explore")
        obs = moved(obs, action)
        assert obs.position == expected
    # Neither approaching the endpoint nor a visit to the previous square
    # retires it. Actually standing there with no reveal does retire it.
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("travel_downstairs", "0,0", "descend")
    assert (3, 0) not in agent.exploration.current.visits


async def test_closed_door_opens_then_reaches_newly_revealed_passage():
    cells = enclosed({(0, 0): 42, (1, 0): 1})
    del cells[(2, 0)]
    agent = exploration_agent()
    obs = await native_no_progress(agent, observation(cells))
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")
    # Bumping opens the door and consumes a turn without moving the player.
    cells[(1, 0)] = Cell(1, 0, 34, "'", True)
    obs = replace(obs, turn=2, cells=tuple(cells.values()))
    action = await agent.decide(obs)
    assert (action.kind, action.value) == ("move", "l")
    assert (1, 0) not in agent.exploration.current.blocked
    cells[(2, 0)] = Cell(2, 0, 33, ".", True)
    obs = await native_no_progress(
        agent, replace(moved(obs, action), cells=tuple(cells.values())))
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")


async def test_unreachable_boundary_does_not_prevent_safe_descent():
    cells = enclosed({(0, 0): 42, (4, 0): 33})
    del cells[(5, 0)]
    agent = exploration_agent()
    finished = await native_no_progress(agent, observation(cells), result="complete")
    assert (await agent.decide(finished)).kind == "stairs_down"


async def test_unknown_diagonal_behind_two_walls_is_not_a_frontier():
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 1)]
    agent = exploration_agent()
    finished = await native_no_progress(agent, observation(cells), result="complete")
    assert (await agent.decide(finished)).kind == "stairs_down"


@pytest.mark.parametrize("hazard", ["blocked", "occupied"])
async def test_no_safe_reachable_frontier_allows_known_stairs(hazard):
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 0)]
    agent = exploration_agent()
    obs = observation(cells)
    agent.exploration.observe(obs)
    if hazard == "blocked":
        agent.exploration.current.blocked.add((1, 0))
    else:
        obs = replace(obs, monsters=(Monster(1, "friendly orc", 1, 0, attitude=1),))
        obs = await native_no_progress(agent, obs, result="complete")
    assert (await agent.decide(obs)).kind == "stairs_down"


async def test_temporary_cloud_blockage_is_not_floor_completion():
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    del cells[(3, 0)]
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    agent = exploration_agent()
    obs = observation(cells)
    assert (await agent.decide(obs)).kind == "wait"
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=False)
    cleared = replace(obs, turn=2, cells=tuple(cells.values()))
    stopped = await native_no_progress(agent, cleared)
    action = await agent.decide(stopped)
    assert (action.kind, action.value, action.objective) == ("move", "l", "explore")


async def test_cloud_on_frontier_itself_is_waited_out_not_stepped_into():
    cells = enclosed({(0, 0): 42, (1, 0): 33})
    del cells[(2, 0)]
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    assert (await exploration_agent().decide(observation(cells))).kind == "wait"


async def test_cloud_between_exhausted_floor_and_alternative_safe_stairs_is_waited_out():
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 43})
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    obs = observation(cells)
    agent = exploration_agent()
    agent.tactics.unsafe_stairs[(obs.location, 42)] = obs.xl + 2
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("wait", "wait for terrain")
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=False)
    cleared = replace(obs, turn=2, cells=tuple(cells.values()))
    finished = await native_no_progress(agent, cleared, result="complete")
    action = await agent.decide(finished)
    assert (action.kind, action.value, action.objective) == ("travel_downstairs", "2,0", "descend")
    assert agent.tactics.unsafe_stairs[(obs.location, 42)] > obs.xl


@pytest.mark.parametrize("barrier", ["unsafe_destination", "blocked_route", "avoided_route", "occupied_route"])
async def test_cloud_wait_does_not_relax_noncloud_stair_safeguards(barrier):
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 43})
    cells[(1, 0)] = replace(cells[(1, 0)], cloud=True)
    obs = observation(cells)
    agent = exploration_agent()
    agent.exploration.observe(obs)
    agent.tactics.unsafe_stairs[(obs.location, 42)] = obs.xl + 2
    if barrier == "unsafe_destination":
        agent.tactics.unsafe_stairs[(obs.location, 43)] = obs.xl + 2
    elif barrier == "blocked_route":
        agent.exploration.current.blocked.add((1, 0))
    elif barrier == "avoided_route":
        from dcss_bot.characters.mibe_tactics import Avoidance
        agent.tactics.avoided[obs.location] = [Avoidance((3, 0), obs.xl, "abandoned encounter", "ogre")]
    else:
        obs = replace(obs, monsters=(Monster(1, "friendly orc", 1, 0, attitude=1),))
    assert (await agent.decide(obs)).kind == "pause"


async def test_stagnant_frontier_does_not_leave_an_unvisited_target_loop():
    cells = enclosed({(0, 0): 42, (1, 0): 33, (2, 0): 33})
    del cells[(3, 0)]
    agent = exploration_agent()
    obs = await native_no_progress(agent, observation(cells))
    for _ in range(5):
        action = await agent.decide(obs)
        assert action.objective in {"explore", "descend"}
        if action.kind == "stairs_down":
            break
        assert action.kind in {"move", "travel_downstairs"}
        obs = moved(obs, action)
    assert action.kind == "stairs_down"
    assert obs.position == (0, 0)


async def test_failed_move_is_not_retried_and_new_location_resets_frontiers():
    cells = {(0, 0): Cell(0, 0, 33, ".", True), (1, 0): Cell(1, 0, 33, ".", True)}
    obs = observation(cells)
    agent = exploration_agent()
    obs = await native_no_progress(agent, obs)
    assert (await agent.decide(obs)).value == "l"
    assert (await agent.decide(obs)).kind == "pause"
    assert (1, 0) in agent.exploration.current.blocked
    assert (0, 0) in agent.exploration.current.exhausted_frontiers
    # This coordinate was an exhausted viewpoint on the old floor, but the
    # same coordinate on another floor is a fresh candidate.
    obs = replace(obs, depth=2, position=(1, 0), turn=2)
    obs = await native_no_progress(agent, obs)
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("move", "h", "explore")
    assert not agent.exploration.current.blocked
    assert (0, 0) not in agent.exploration.current.exhausted_frontiers


def test_frontier_is_reconsidered_when_local_unknown_boundary_changes():
    cells = enclosed({(0, 0): 33, (1, 0): 33})
    del cells[(2, 0)]
    del cells[(2, 1)]
    agent = exploration_agent()
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
    agent = exploration_agent()
    agent.exploration.observe(obs)
    agent.exploration.current.visits[(1, 0)] = 100
    assert (await agent.decide(obs)).objective == "explore"


@pytest.mark.parametrize("unsafe", ["injured", "hostile"])
async def test_exhausted_exploration_never_overrides_combat_or_recovery(unsafe):
    cells = enclosed({(0, 0): 42})
    obs = observation(cells)
    if unsafe == "injured":
        obs = replace(obs, hp=10)
    else:
        obs = replace(obs, monsters=(Monster(1, "rat", 1, 0, threat=0),))
    action = await exploration_agent().decide(obs)
    assert action.kind != "stairs_down"
    assert action.objective not in {"explore", "descend", "equipment"}


@pytest.mark.parametrize("feature", [45, 49, 60])
async def test_exhausted_frontier_does_not_authorize_hatch_or_exit_activation(feature):
    obs = observation(enclosed({(0, 0): feature}))
    agent = exploration_agent()
    finished = await native_no_progress(agent, obs, result="complete")
    assert (await agent.decide(finished)).kind == "pause"


@pytest.mark.parametrize("place", ["Dungeon", "Lair"])
async def test_cleared_floor_travels_to_exact_safe_stair_then_observes_before_descent(place):
    cells = enclosed({(x, 0): 42 if x == 4 else 33 for x in range(5)})
    agent = exploration_agent()
    obs = await native_no_progress(agent, replace(observation(cells), place=place), result="complete")
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == ("travel_downstairs", "4,0", "descend")
    assert obs.position == (0, 0)
    assert agent.exploration.last_move is None
    arrival = moved(obs, action)
    assert arrival.position == (4, 0)
    assert (await agent.decide(arrival)).kind == "stairs_down"


@pytest.mark.parametrize("position,result", [((2, 0), "interrupted"), ((4, 0), "complete")])
async def test_new_hostile_preempts_native_stair_travel_or_arrival_descent(position, result):
    cells = enclosed({(x, y): 42 if (x, y) == (4, 0) else 33
                      for x in range(5) for y in range(2)})
    agent = exploration_agent()
    obs = await native_no_progress(agent, observation(cells), result="complete")
    assert (await agent.decide(obs)).kind == "travel_downstairs"
    danger = replace(obs, position=position, turn=obs.turn + 2,
                     native_automation="travel_downstairs", native_result=result,
                     monsters=(Monster(1, "rat", position[0], 1, threat=0),))
    action = await agent.decide(danger)
    assert action.kind not in {"travel_downstairs", "stairs_down", "explore"}
    assert action.objective != "descend"


async def test_native_stair_target_excludes_banned_nearer_stair():
    cells = enclosed({(x, 0): {1: 42, 4: 43}.get(x, 33) for x in range(5)})
    agent = exploration_agent()
    obs = observation(cells)
    agent.tactics.unsafe_stairs[(obs.location, 42)] = obs.xl + 2
    finished = await native_no_progress(agent, obs, result="complete")
    action = await agent.decide(finished)
    assert (action.kind, action.value) == ("travel_downstairs", "4,0")
    assert agent.tactics.unsafe_stairs[(obs.location, 42)] > obs.xl


@pytest.mark.parametrize("feature", [0, 45, 49, 60, 71, 72])
async def test_native_stair_target_never_selects_unknown_hatch_exit_or_branch(feature):
    cells = enclosed({(x, 0): {1: feature, 4: 42}.get(x, 33) for x in range(5)})
    # Unknown is not a traversable route, so provide an ordinary known bypass.
    cells.update({(x, 1): Cell(x, 1, 33, ".", True) for x in range(5)})
    cells.update({(x, 2): Cell(x, 2, 7, "#", True) for x in range(-1, 6)})
    agent = exploration_agent()
    finished = await native_no_progress(agent, observation(cells), result="complete")
    action = await agent.decide(finished)
    assert (action.kind, action.value) == ("travel_downstairs", "4,0")


@pytest.mark.parametrize("hazard", ["cloud", "blocked", "avoided", "pursuit"])
async def test_native_stair_route_does_not_bypass_policy_hazards_and_resumes_when_clear(hazard):
    from dcss_bot.characters.mibe_tactics import Avoidance

    cells = enclosed({(x, y): 42 if (x, y) == (6, 0) else 33
                      for x in range(7) for y in range(4)})
    agent = exploration_agent()
    obs = await native_no_progress(agent, observation(cells), result="complete")
    if hazard == "cloud":
        cells[(3, 0)] = replace(cells[(3, 0)], cloud=True)
        obs = replace(obs, cells=tuple(cells.values()))
    elif hazard == "blocked":
        agent.exploration.current.blocked.add((3, 0))
    elif hazard == "avoided":
        agent.tactics.avoided[obs.location] = [
            Avoidance((3, 0), obs.xl, "abandoned encounter", "ogre")]
    else:
        agent.tactics.recent_hostiles["ogre"] = 1, obs.turn
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("move", "descend")
    next_position = moved(obs, action).position
    assert next_position not in agent.tactical_avoided
    assert next_position not in agent.exploration.current.blocked
    assert not cells[next_position].cloud

    # The fallback is for the actual restriction, not a permanent manual mode.
    cells[(3, 0)] = replace(cells[(3, 0)], cloud=False)
    agent.exploration.current.blocked.clear()
    agent.tactics.avoided.clear()
    agent.tactics.recent_hostiles.clear()
    cleared = replace(moved(obs, action), cells=tuple(cells.values()))
    resumed = await agent.decide(cleared)
    assert (resumed.kind, resumed.value) == ("travel_downstairs", "6,0")
