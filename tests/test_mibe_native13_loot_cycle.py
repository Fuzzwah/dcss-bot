"""Actual development13 seed8 loot/bypass cycle; synthetic variants are labeled."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_encounter import enclosed_melee_component
from dcss_bot.characters.mibe_exploration import ExplorationMemory
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import DOWNSTAIRS


DATA = json.loads((Path(__file__).parent / "fixtures" / "recorded_development13_loot_cycle.json").read_text())
TARGET = (-3, 12)


def captured(sequence):
    row = DATA["transitions"][sequence - 1]
    raw = DATA["snapshot"] | row["before_changes"]
    cells = {(cell["x"], cell["y"]): cell for cell in DATA["snapshot"]["cells"]}
    for point in row["removed_cells"]:
        cells.pop(tuple(point))
    cells.update({(cell["x"], cell["y"]): cell for cell in row["cell_changes"]})
    raw = raw | {"cells": list(cells.values())}
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        raw[field] = tuple(factory(**entry) for entry in raw.get(field, ()))
    raw["position"] = tuple(raw["position"])
    for field in ("messages", "statuses"):
        raw[field] = tuple(raw[field])
    return Observation(**raw)


def equipment_only_variant(sequence):
    # Synthetic isolation: the recorded scroll/potion piles have already been
    # collected. Preserve their terrain/visibility and all encounter evidence;
    # never rewrite the recorded fixture or hide supplies in the real consumer.
    obs = captured(sequence)
    return replace(obs, cells=tuple(replace(cell, glyph=".")
                                    if cell.glyph in {"?", "!"} else cell
                                    for cell in obs.cells))


def mapping(obs):
    return {(cell.x, cell.y): cell for cell in obs.cells}


def destination(obs, action):
    dx, dy = DIRECTIONS[action.value]
    return obs.position[0] + dx, obs.position[1] + dy


def assert_clear_move(obs, action):
    assert action.kind == "move"
    cell = mapping(obs)[destination(obs, action)]
    assert cell.visible and cell.walkable and not cell.cloud
    assert destination(obs, action) not in {(enemy.x, enemy.y) for enemy in obs.hostiles}


def choose_after_public_inspections(agent, obs):
    # Synthetic variants reuse an already fully inspected recorded observation.
    # Free initial ability/development inspections may consume decisions, not turns.
    for _ in range(4):
        action = asyncio.run(agent.decide(obs))
        if action.kind != "inspect":
            return action
    pytest.fail("Fully inspected public observation did not finish bounded inspection")


def deferred_agent(obs):
    agent = RuleBasedAgent()
    agent.exploration.observe(obs)
    floor = agent.exploration.current
    floor.equipment_ground.add(TARGET)
    floor.equipment_target = TARGET
    floor.deferred_equipment[TARGET] = floor.known_positions, obs.xl
    return agent




def test_actual_sequence_consumer_keeps_finite_bypass_but_rejects_optional_reversal():
    agent = RuleBasedAgent()
    rejected_target = None
    for sequence in range(1, 27):
        obs = captured(sequence)
        original_cells = obs.cells
        action = asyncio.run(agent.decide(obs))
        if sequence in (3, 5, 6):
            assert_clear_move(obs, action)
            assert action.objective == "bypass barrier"
            assert action.assessment["separation_window_actions"] == 4
            assert action.assessment["separated_hostile_ids"] == [enemy.id for enemy in obs.hostiles]
            assert all(not enclosed_melee_component(enemy, mapping(obs)) for enemy in obs.hostiles)
            assert any(cell.feature == 0 for cell in obs.cells)
        if sequence in (18, 19):
            assert_clear_move(obs, action)
            # Real remembered supplies may outrank the original armour. The
            # safety boundary is withdrawing from whichever optional goal won.
            assert action.objective in {"supplies", "equipment"}
            target = agent.exploration.current.equipment_target
            assert mapping(obs)[target].glyph in {"?", "!", "[", ")", "="}
            if rejected_target is None:
                rejected_target = target
            assert target == rejected_target
        if sequence in (21, 22, 23):
            assert_clear_move(obs, action)
            assert action.objective == "bypass barrier"
        assert obs.cells == original_cells  # No barrier completion or unknown-cell rewriting.
    floor = agent.exploration.current
    assert floor.deferred_equipment[rejected_target] == (floor.known_positions, obs.xl)
    assert_clear_move(obs, action)
    if action.objective in {"equipment", "supplies"}:
        assert floor.equipment_target != rejected_target
    # Continue consuming the recorded inputs after divergence, not a claimed live
    # rollout. Reappearing gnoll IDs/LOS never restart the rejected optional goal.
    for sequence in range(27, 47):
        obs = captured(sequence)
        action = asyncio.run(agent.decide(obs))
        if sequence in (31, 36, 41, 46) and action.objective in {"equipment", "supplies"}:
            assert agent.exploration.current.equipment_target != rejected_target
        if obs.hostiles:
            assert [(enemy.x, enemy.y) for enemy in obs.hostiles] == [(1, 6)]


def test_synthetic_visibility_glyph_and_turn_changes_do_not_restore_rejected_loot():
    obs = equipment_only_variant(26)
    agent = deferred_agent(obs)
    changed = replace(obs, turn=obs.turn + 20, time=obs.time + 200,
                      cells=tuple(replace(cell, visible=not cell.visible, glyph=".")
                                  if (cell.x, cell.y) == TARGET else cell for cell in obs.cells))
    action = choose_after_public_inspections(agent, changed)
    assert action.objective != "equipment"
    assert agent.exploration.current.deferred_equipment[TARGET] == (
        agent.exploration.current.known_positions, obs.xl)


@pytest.mark.parametrize("progress", ["terrain", "xl"])
def test_synthetic_actual_exploration_or_xl_progress_restores_optional_eligibility(progress):
    obs = equipment_only_variant(26)
    agent = deferred_agent(obs)
    if progress == "xl":
        changed = replace(obs, xl=obs.xl + 1)
    else:
        unknown = next(cell for cell in obs.cells if cell.feature == 0)
        changed = replace(obs, cells=tuple(replace(cell, feature=33, glyph=".")
                                          if cell is unknown else cell for cell in obs.cells))
    action = choose_after_public_inspections(agent, changed)
    assert_clear_move(changed, action)
    assert action.objective == "equipment"
    assert agent.exploration.current.equipment_target == TARGET


def test_synthetic_incidental_safe_pickup_is_not_banned_by_optional_route_rejection():
    obs = equipment_only_variant(26)
    agent = deferred_agent(obs)
    changed = replace(obs, position=TARGET, messages=())
    action = choose_after_public_inspections(agent, changed)
    assert action.kind == "pickup" and action.objective == "equipment"
    assert TARGET in agent.exploration.current.pickup_attempts


def test_synthetic_other_safe_equipment_remains_eligible_on_the_same_map():
    obs = equipment_only_variant(26)
    agent = deferred_agent(obs)
    safe_target = (-4, 5)
    changed = replace(obs, cells=tuple(replace(cell, glyph="[")
                                      if (cell.x, cell.y) == safe_target else cell for cell in obs.cells))
    action = choose_after_public_inspections(agent, changed)
    assert_clear_move(changed, action)
    assert action.objective == "equipment"
    assert destination(changed, action) == safe_target
    assert agent.exploration.current.equipment_target == safe_target
    assert agent.exploration.current.deferred_equipment[TARGET] == (
        agent.exploration.current.known_positions, obs.xl)


def test_synthetic_deferred_loot_does_not_hide_a_later_visible_encounter():
    obs = captured(28)  # Actual inspected reappearance, not an invented monster.
    agent = deferred_agent(obs)
    action = asyncio.run(agent.decide(obs))
    assert_clear_move(obs, action)
    assert action.objective == "bypass barrier"
    assert action.assessment["separated_hostile_ids"] == [obs.hostiles[0].id]
    assert len(obs.hostiles) == 1 and obs.hostiles[0].name == "gnoll"


def test_synthetic_loot_rejection_rebases_with_public_anchors_and_resets_without_them():
    memory = ExplorationMemory()
    stairs = next(iter(DOWNSTAIRS))
    before = Observation(place="Dungeon", depth=5, xl=6,
                         cells=(Cell(0, 0, stairs), Cell(1, 0, 33, "[", True)))
    memory.observe(before)
    memory.current.equipment_ground.add((1, 0))
    memory.current.equipment_target = (1, 0)
    memory.current.deferred_equipment[(1, 0)] = memory.current.known_positions, before.xl
    # Leaving and returning uses the same floor-specific rejection, translated
    # from a unique immutable stair, not inferred from the player's position.
    memory.observe(Observation(place="Dungeon", depth=6, cells=(Cell(0, 0, stairs),)))
    shifted = replace(before, cells=tuple(replace(cell, x=cell.x + 10, y=cell.y - 7)
                                          for cell in before.cells))
    change = memory.observe(shifted)
    assert change.offset == (10, -7)
    assert memory.current.equipment_target == (11, -7)
    assert memory.current.equipment_ground == {(11, -7)}
    assert memory.current.deferred_equipment == {(11, -7): (memory.current.known_positions, 6)}
    # A reconnect with no matching public anchor invalidates every cached frame.
    change = memory.observe(replace(before, cells=(Cell(90, 90, 33),)))
    assert change.offset is None
    assert memory.current.equipment_target is None
    assert not memory.current.deferred_equipment
