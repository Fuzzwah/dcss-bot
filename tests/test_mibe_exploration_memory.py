from collections import Counter
from dataclasses import replace

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_exploration import ExplorationMemory, anchor_translation
from dcss_bot.models import Cell, Observation


def floor(*, depth=1, turn=10, position=(0, 0)):
    return Observation(
        hp=10, hp_max=20, place="Dungeon", depth=depth, turn=turn, position=position,
        cells=(Cell(-4, 1, 60), Cell(5, 5, 42), Cell(0, 0, 33), Cell(1, 0, 33)),
    )


def shifted(obs, offset, **changes):
    dx, dy = offset
    return replace(obs, cells=tuple(replace(cell, x=cell.x + dx, y=cell.y + dy)
                                    for cell in obs.cells), **changes)


def fill_cache(memory):
    memory.visits[(1, 0)] = 7
    memory.blocked.add((2, 1))
    memory.exhausted_frontiers[(1, 0)] = frozenset({(2, 0), (2, -1)})
    memory.pickup_attempts.add((3, 0))
    memory.equipment_ground.update({(3, 0), (4, 0)})


def record_rebases(agent, monkeypatch):
    calls = []
    original = agent.tactics.rebase

    def rebase(location, offset):
        calls.append((location, offset))
        original(location, offset)

    monkeypatch.setattr(agent.tactics, "rebase", rebase)
    return calls


async def test_returning_floor_uses_its_own_public_anchors_and_transforms_every_cache(monkeypatch):
    agent = RuleBasedAgent()
    calls = record_rebases(agent, monkeypatch)
    source = floor()
    await agent.decide(source)
    fill_cache(agent.exploration.current)
    source_memory = agent.exploration.current
    other = replace(floor(depth=2, turn=11), cells=(Cell(0, 0, 46), Cell(1, 0, 42)))
    await agent.decide(other)
    assert calls == []  # A new floor is not a translated version of the old one.
    returned = shifted(source, (20, -8), turn=12, position=(25, -3))
    await agent.decide(returned)
    memory = agent.exploration.current
    assert memory is source_memory
    assert calls == [(source.location, (20, -8))]
    assert memory.visits == Counter({(21, -8): 7, (20, -8): 1, (25, -3): 1})
    assert memory.blocked == {(22, -7)}
    assert memory.exhausted_frontiers == {(21, -8): frozenset({(22, -8), (22, -9)})}
    assert memory.pickup_attempts == {(23, -8)}
    assert memory.equipment_ground == {(23, -8), (24, -8)}
    assert memory.anchors == {60: frozenset({(16, -7)}), 42: frozenset({(25, -3)})}
    assert memory.known_positions == frozenset((cell.x, cell.y) for cell in returned.cells)
    assert agent.exploration.floors[other.location] is not memory
    assert agent.exploration.previous_position == returned.position


async def test_same_floor_reconnect_after_possible_move_never_aligns_player_positions(monkeypatch):
    agent = RuleBasedAgent()
    calls = record_rebases(agent, monkeypatch)
    source = floor(position=(2, 0))
    await agent.decide(source)
    fill_cache(agent.exploration.current)
    agent._move(source, "l", "In-flight native move", "explore")
    # The native key may have moved the character from world (2,0) to (3,0)
    # before disconnect. Reconnect centers that new position at native (0,0).
    reconnected = shifted(source, (-3, 0), position=(0, 0), turn=11)
    await agent.decide(reconnected)
    memory = agent.exploration.current
    assert calls == [(source.location, (-3, 0))]
    assert memory.visits[(-1, 0)] == 1
    assert memory.visits[(0, 0)] == 1
    assert memory.visits[(-2, 0)] == 7
    assert memory.blocked == {(-1, 1)}
    assert memory.pickup_attempts == {(0, 0)}
    assert agent.exploration.last_move is None


@pytest.mark.parametrize("ambiguity", ["missing", "conflicting", "duplicate"])
async def test_ambiguous_return_invalidates_positions_and_notifies_tactics(ambiguity, monkeypatch):
    agent = RuleBasedAgent()
    calls = record_rebases(agent, monkeypatch)
    source = floor()
    await agent.decide(source)
    fill_cache(agent.exploration.current)
    await agent.decide(floor(depth=2, turn=11))
    returned = shifted(source, (10, 5), position=(10, 5), turn=12)
    if ambiguity == "missing":
        returned = replace(returned, cells=(Cell(10, 5, 33),))
    elif ambiguity == "conflicting":
        returned = replace(returned, cells=tuple(replace(cell, x=cell.x + 1)
                                                 if cell.feature == 42 else cell
                                                 for cell in returned.cells))
    else:
        returned = replace(returned, cells=returned.cells + (Cell(20, 20, 42),))
    await agent.decide(returned)
    memory = agent.exploration.current
    assert calls == [(source.location, None)]
    assert memory.visits == Counter({returned.position: 1})
    assert not memory.blocked
    assert not memory.exhausted_frontiers
    assert not memory.pickup_attempts
    assert not memory.equipment_ground
    assert agent.exploration.floors[source.location] is memory


async def test_same_floor_unanchored_map_discontinuity_discards_cache(monkeypatch):
    agent = RuleBasedAgent()
    calls = record_rebases(agent, monkeypatch)
    source = replace(floor(), cells=(Cell(0, 0, 33), Cell(1, 0, 33)))
    await agent.decide(source)
    fill_cache(agent.exploration.current)
    agent._move(source, "l", "Unacknowledged move", "explore")
    reconnected = shifted(source, (-1, 0))
    await agent.decide(reconnected)
    assert calls == [(source.location, None)]
    assert agent.exploration.current.visits == Counter({(0, 0): 1})
    assert not agent.exploration.current.blocked
    assert not agent.exploration.current.exhausted_frontiers


def test_normal_unanchored_observations_preserve_cache_without_frame_work():
    memory = ExplorationMemory()
    obs = replace(floor(), cells=(Cell(0, 0, 33),))
    assert memory.observe(obs) is None
    current = memory.current
    fill_cache(current)
    expanded = replace(obs, turn=11, cells=obs.cells + (Cell(1, 0, 33),))
    assert memory.observe(expanded) is None
    assert memory.current is current
    assert memory.current.visits[(1, 0)] == 7
    assert memory.current.blocked == {(2, 1)}


def test_cached_zero_offset_return_keeps_only_same_floor_memory():
    memory = ExplorationMemory()
    source = floor()
    memory.observe(source)
    fill_cache(memory.current)
    stored = memory.current
    memory.observe(floor(depth=2, turn=11))
    assert memory.current.visits == Counter({(0, 0): 1})
    change = memory.observe(replace(source, turn=12))
    assert change.offset == (0, 0)
    assert memory.current is stored
    assert memory.current.visits[(1, 0)] == 7
    assert memory.current.blocked == {(2, 1)}


@pytest.mark.parametrize("feature", [42, 43, 44, 46, 47, 48, 60, 71, 88])
def test_each_pinned_public_stair_exit_and_branch_variant_can_anchor(feature):
    memory = ExplorationMemory()
    source = replace(floor(), cells=(Cell(2, 3, feature),))
    memory.observe(source)
    changed = memory.observe(shifted(source, (8, -6), turn=11))
    assert changed.offset == (8, -6)


def test_anchor_matching_requires_unique_variants_and_all_offsets_to_agree():
    old = {42: frozenset({(0, 0)}), 46: frozenset({(5, 0)})}
    assert anchor_translation(old, {42: frozenset({(2, 3)}), 46: frozenset({(7, 3)})}) == (2, 3)
    assert anchor_translation(old, {42: frozenset({(2, 3)}), 46: frozenset({(7, 4)})}) is None
    assert anchor_translation(old, {42: frozenset({(2, 3), (7, 4)})}) is None
    assert anchor_translation({42: frozenset({(0, 0), (1, 0)})}, old) is None
    assert anchor_translation(old, {43: frozenset({(0, 0)})}) is None


def test_frontier_consumer_compares_translated_unknown_edge_values():
    agent = RuleBasedAgent()
    source_cells = (Cell(0, 0, 33), Cell(1, 0, 33), Cell(0, 1, 42))
    source = replace(floor(), cells=source_cells)
    agent.exploration.observe(source)
    source_map = {(cell.x, cell.y): cell for cell in source_cells}
    edges = agent._unknown_edges(source_map, (0, 0))
    agent.exploration.current.exhausted_frontiers[(0, 0)] = edges
    returned = shifted(source, (10, -4), turn=11, position=(11, -4))
    agent.exploration.observe(returned)
    cells = {(cell.x, cell.y): cell for cell in returned.cells}
    assert (10, -4) not in agent._frontiers(returned, cells)
    assert agent.exploration.current.exhausted_frontiers[(10, -4)] == agent._unknown_edges(cells, (10, -4))
