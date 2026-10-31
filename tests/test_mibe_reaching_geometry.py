"""Captured development13 seed8; every altered response below is synthetic."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe_encounter import enclosed_melee_bypass, enclosed_melee_component
from dcss_bot.characters.mibe_geometry import WalkingDistances
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_retreat import plan_withdrawal
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


DATA = json.loads((Path(__file__).parent / "fixtures" / "recorded_development14_reaching_barrier.json").read_text())


def captured():
    raw = dict(DATA["snapshot"])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        raw[field] = tuple(factory(**entry) for entry in raw.get(field, ()))
    raw["position"] = tuple(raw["position"])
    for field in ("messages", "statuses"):
        raw[field] = tuple(raw[field])
    return Observation(**raw)


def mapping(obs):
    return {(cell.x, cell.y): cell for cell in obs.cells}


def uncovered(*_):
    return False


def synthetic_nonranged():
    """Synthetic: remove the net and other gnoll; place the polearm four away.

    Keep every captured terrain cell. The new occupied square is the original
    whip gnoll's observed dry square, not an invented traversable water cell.
    """
    obs = captured()
    enemy = obs.hostiles[1]
    description = "\n".join(line for line in enemy.description.splitlines()
                            if not line.startswith(("Throw:", "It has an unusual item:")))
    enemy = replace(enemy, x=5, description=description)
    return replace(obs, monsters=(enemy,))


def synthetic_crossing(*, player_y, feature=31):
    """Synthetic broad moat; reachable near-side squares may still reach across."""
    obs = synthetic_nonranged()
    enemy = replace(obs.hostiles[0], x=0, y=0)
    cells = {(x, y): Cell(x, y, 33, ".", True)
             for x in range(-6, 7) for y in range(-2, 6)}
    for x in range(-4, 5):
        cells[(x, player_y-1)] = Cell(x, player_y-1, feature, "~", True)
    return replace(obs, position=(0, player_y), monsters=(enemy,), cells=tuple(cells.values()))


def test_actual_zero_damage_net_keeps_its_control_risk_and_will_independence():
    obs = captured()
    enemy = obs.hostiles[1]
    info = mechanics(enemy)
    assert info.reach == 2 and info.ranged and info.entangles and not info.will_check
    assert info.ranged_max == 0  # The public row already has the correct damage.
    risk = TacticalController().assess(obs, mapping(obs), uncovered)
    threat = next(threat for threat in risk.threats if threat.position == (enemy.x, enemy.y))
    assert threat.walking_steps_lower_bound > enemy.distance(obs.position)
    assert threat.ranged and threat.entangling
    assert threat.burst_loss == 0  # No fictitious halberd damage added to the net.
    assert all(not enclosed_melee_component(foe, mapping(obs)) for foe in obs.hostiles)

    # Synthetic high Will does not protect against this independent restraint.
    high_will = replace(obs, will=3)
    high_will_risk = TacticalController().assess(high_will, mapping(high_will), uncovered)
    assert next(threat for threat in high_will_risk.threats
                if threat.position == (enemy.x, enemy.y)).entangling


def test_synthetic_nonranged_polearm_four_away_allows_only_a_finite_dry_nonapproach_step():
    obs = synthetic_nonranged()
    cells = mapping(obs)
    enemy = obs.hostiles[0]
    info = mechanics(enemy)
    assert enemy.distance(obs.position) == 4 and info.reach == 2 and not info.ranged
    assert WalkingDistances(cells).distance(enemy, info, obs.position, 6) == 7
    action = enclosed_melee_bypass(obs, cells, {(1, -7)})
    assert action is not None and (action.kind, action.value, action.objective) == ("move", "k", "bypass barrier")
    dx, dy = DIRECTIONS[action.value]
    destination = obs.position[0]+dx, obs.position[1]+dy
    assert cells[destination].visible and cells[destination].walkable and not cells[destination].cloud
    assert enemy.distance(destination) >= enemy.distance(obs.position)
    assert action.assessment["separation_window_actions"] == 4
    assert "enclosed_hostile_ids" not in action.assessment
    assert not enclosed_melee_component(enemy, cells)
    risk = TacticalController().assess(obs, cells, uncovered)
    assert risk.expected_loss == risk.burst_loss == 0


def test_synthetic_nonranged_known_barrier_also_permits_a_bounded_moving_retreat():
    obs = synthetic_nonranged()
    # Synthetic stair anchor on an already observed clear, dry first step.
    cells = mapping(obs)
    cells[(1, -5)] = replace(cells[(1, -5)], feature=46)
    obs = replace(obs, cells=tuple(cells.values()))
    plan = plan_withdrawal(obs, cells, {point: 1 for point in cells}, uncovered, target=(1, -5))
    assert plan is not None and plan.path == ((1, -5),)
    assert plan.horizon == 1 and plan.expected_loss == plan.burst_loss == 0


def test_synthetic_polearm_already_two_away_across_water_remains_contact_risk():
    obs = synthetic_crossing(player_y=2)
    enemy = obs.hostiles[0]
    info = mechanics(enemy)
    cells = mapping(obs)
    assert cells[(0, 1)].feature == 31 and cells[obs.position].walkable
    for cap in (2, 4, 8):
        assert WalkingDistances(cells).distance(enemy, info, obs.position, cap) == 2
    risk = TacticalController().assess(obs, cells, uncovered)
    assert risk.simultaneous_attackers == 1 and risk.burst_loss > 0
    assert enclosed_melee_bypass(obs, cells, {(0, 3)}) is None
    cells[(0, 3)] = replace(cells[(0, 3)], feature=46)
    assert plan_withdrawal(replace(obs, cells=tuple(cells.values())), cells,
                           {point: 1 for point in cells}, uncovered, target=(0, 3)) is None


@pytest.mark.parametrize("feature", [11, 31])
def test_synthetic_attack_square_before_barrier_prevents_naive_target_bfs_safety(feature):
    obs = synthetic_crossing(player_y=3, feature=feature)
    enemy = obs.hostiles[0]
    info = mechanics(enemy)
    cells = mapping(obs)
    walking = WalkingDistances(cells)
    # Target walking requires a long detour; one near-side step is already in
    # polearm range. Ignore line of fire even for the wall: optimistic, not safe.
    assert walking.distance(enemy, replace(info, reach=1), obs.position, 6) == 7
    assert walking.distance(enemy, info, obs.position, 6) == 3
    assert walking.distance(enemy, info, obs.position, 6)-info.reach == 1
    risk = TacticalController().assess(obs, cells, uncovered)
    assert risk.burst_loss > 0 and risk.threats[0].walking_steps_lower_bound == 3
    assert enclosed_melee_bypass(obs, cells, {(0, 4)}) is None


@pytest.mark.parametrize("feature", [0, 1, 33])
def test_synthetic_unknown_or_door_shortcut_and_occupant_never_increase_walking_cost(feature):
    obs = synthetic_nonranged()
    cells = mapping(obs)
    for point in ((2, -4), (3, -4), (4, -4)):
        cells[point] = replace(cells[point], feature=feature, visible=feature != 0)
    # Synthetic friendly occupant on the shortcut cannot turn it into a barrier.
    obs = replace(obs, cells=tuple(cells.values()),
                  monsters=obs.monsters+(Monster(99, "human", 3, -4, attitude=1),))
    enemy = obs.hostiles[0]
    info = mechanics(enemy)
    assert WalkingDistances(cells).distance(enemy, info, obs.position, 6) <= 4
    assert enclosed_melee_bypass(obs, cells, {(1, -7)}) is None
    assert TacticalController().assess(obs, cells, uncovered).burst_loss > 0


@pytest.mark.parametrize("change", [
    {"name": "unfamiliar creature"},
    {"statuses": ("flying",)},
    {"spells": ("Blink",)},
    {"description": None},
])
def test_synthetic_unknown_flying_blinking_or_unexamined_mover_keeps_chebyshev_fallback(change):
    obs = synthetic_nonranged()
    enemy = replace(obs.hostiles[0], **change)
    obs = replace(obs, monsters=(enemy,))
    cells = mapping(obs)
    assert WalkingDistances(cells).distance(enemy, mechanics(enemy), obs.position, 6) == 4
    assert enclosed_melee_bypass(obs, cells, {(1, -7)}) is None
    assert TacticalController().assess(obs, cells, uncovered).burst_loss > 0


@pytest.mark.parametrize("reach", [1, 2])
@pytest.mark.parametrize("cap,expected", [(0, 1), (3, 4), (6, 7), (7, 7), (9, 7)])
def test_synthetic_open_route_preserves_target_distance_cap_for_existing_consumers(reach, cap, expected):
    obs = synthetic_nonranged()
    enemy = replace(obs.hostiles[0], x=0, y=0)
    info = replace(mechanics(enemy), reach=reach)
    # Unobserved cells are open, not a false wall at the edge of the capture.
    walking = WalkingDistances({})
    assert walking.distance(enemy, info, (0, 7), cap) == expected
