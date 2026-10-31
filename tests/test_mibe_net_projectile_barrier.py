"""Captured development13 seed8 plus explicitly synthetic projectile geometry.

No native line of fire is reconstructed: a possible optimistic path refuses a
certificate, while a known-wall cut can exclude ordinary net control finitely.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe_encounter import enclosed_melee_bypass
from dcss_bot.characters.mibe_geometry import WalkingDistances, possible_projectile_path
from dcss_bot.characters.mibe_mechanics import (
    PHYSICAL_NET_RANGE_UPPER_BOUND, mechanics, ordinary_net_thrower,
)
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


DATA = json.loads((Path(__file__).parent / "fixtures" / "recorded_development13_net_pocket.json").read_text())


def captured():
    raw = dict(DATA["snapshot"])
    for name, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                          ("abilities", Ability), ("resistances", Resistance),
                          ("skills", Skill), ("status_details", Status)):
        raw[name] = tuple(factory(**entry) for entry in raw.get(name, ()))
    raw["position"] = tuple(raw["position"])
    for name in ("messages", "statuses"):
        raw[name] = tuple(raw[name])
    return Observation(**raw)


def mapping(obs):
    return {(cell.x, cell.y): cell for cell in obs.cells}


def net_enemy():
    """Synthetic placement, retaining the actual public examination verbatim."""
    return replace(captured().hostiles[0], x=0, y=4)


def wall_cut(feature=12):
    """Synthetic long finite wall, not a closed component or a permanent cage."""
    cells = {(x, y): Cell(x, y, 33, ".", True)
             for x in range(-12, 13) for y in range(-2, 11)}
    for x in range(-10, 11):
        cells[(x, 2)] = Cell(x, 2, feature, "#", True)
    return replace(captured(), position=(0, 0), monsters=(net_enemy(),), cells=tuple(cells.values()))


def water_pocket(*, north_wall=True):
    """Synthetic isolated land origin; water is NOT a projectile barrier.

    Only the hero and its left neighbor are dry reachable player destinations,
    making a refusal unambiguous rather than permitting a different first step.
    """
    cells = {(0, 0): Cell(0, 0, 33, ".", True),
             (-1, 0): Cell(-1, 0, 33, ".", True),
             (0, 4): Cell(0, 4, 33, ".", True)}
    for x in range(-1, 2):
        for y in range(3, 6):
            if (x, y) != (0, 4):
                cells[(x, y)] = Cell(x, y, 31, "~", True)
    if north_wall:
        cells[(0, 3)] = Cell(0, 3, 12, "#", True)
    return replace(captured(), position=(0, 0), monsters=(net_enemy(),), cells=tuple(cells.values()))


def test_captured_unknown_south_exit_refuses_source_safe_eight_range_certificate():
    obs = captured()
    enemy = obs.hostiles[0]
    cells = mapping(obs)
    info = mechanics(enemy)
    assert obs.turn == 3409 and obs.position == (-12, 3) and obs.hp == obs.hp_max == 57
    assert (enemy.x, enemy.y) == (-11, 7)
    assert ordinary_net_thrower(enemy, info)
    assert info.ranged and info.entangles and not info.will_check
    assert info.ranged_mean == info.ranged_max == 0
    assert PHYSICAL_NET_RANGE_UPPER_BOUND == 8

    # An explicit OPTIMISTIC witness, not an actual native shot or hidden exit.
    # All four intermediate land cells are unknown, so none can be closed off.
    walking_witness = ((-11, 7), (-12, 8), (-12, 9), (-12, 10), (-13, 11))
    assert all(cells.get(point) is None or cells[point].feature == 0
               for point in walking_witness[1:])
    walking = WalkingDistances(cells)
    assert walking.reachable_origins(enemy, 4)[walking_witness[-1]] == 4
    projectile_witness = tuple((-13, y) for y in range(11, 3, -1)) + (obs.position,)
    assert all(cells.get(point) is None or cells[point].feature not in range(5, 14)
               for point in projectile_witness)
    assert possible_projectile_path(walking_witness[-1], obs.position, cells)
    assert not walking.excludes_projectile_exposure(enemy, (obs.position,), 4, 8)
    assert walking.excludes_projectile_exposure(enemy, (obs.position,), 1, 8)
    # Departure is protected, but BOTH legal dry next endpoints have possible
    # exposure in their four-action runway. The tempting north step is a wall.
    assert cells[(-12, 2)].feature == 7
    east_witness = tuple((-13, y) for y in range(11, 4, -1)) + ((-12, 4), (-11, 3))
    assert all(cells.get(point) is None or cells[point].feature not in range(5, 14)
               for point in east_witness)
    assert possible_projectile_path((-13, 11), (-11, 3), cells)
    assert possible_projectile_path((-13, 11), (-13, 3), cells)
    assert not walking.excludes_projectile_exposure(enemy, ((-11, 3),), 4, 8)
    assert not walking.excludes_projectile_exposure(enemy, ((-13, 3),), 4, 8)
    assert enclosed_melee_bypass(obs, cells, {(-11, 3), (-13, 3)}) is None
    # Will does not erase real net restraint or cure the missing geometry proof.
    high_will = replace(obs, will=3)
    assert enclosed_melee_bypass(high_will, cells, {(-11, 3)}) is None
    assert mechanics(high_will.hostiles[0]).entangles


@pytest.mark.parametrize("feature", [5, 6, 9, 11, 12, 13])
def test_synthetic_solid_including_transparent_wall_cut_allows_one_finite_step(feature):
    obs = wall_cut(feature)
    cells = mapping(obs)
    enemy = obs.hostiles[0]
    info = mechanics(enemy)
    walking = WalkingDistances(cells)
    assert ordinary_net_thrower(enemy, info)
    assert walking.distance(enemy, info, obs.position, 6) == 7
    assert walking.excludes_projectile_exposure(enemy, (obs.position, (-1, 0)), 4, 8)
    action = enclosed_melee_bypass(obs, cells, {(-1, 0)}, walking=walking)
    assert action is not None and (action.kind, action.value, action.objective) == ("move", "h", "bypass barrier")
    assert action.assessment["separation_window_actions"] == 4
    proof, = action.assessment["projectile_separation_evidence"]
    assert proof["departure_window_actions"] == 1 and proof["poststep_window_actions"] == 4
    assert proof["optimistic_movement_steps"] == {"departure": 1, "poststep": 4}
    assert proof["player_endpoints"] == [[0, 0], [-1, 0]]
    assert proof["projectile_range_upper_bound"] == 8
    assert "enclosed_hostile_ids" not in action.assessment
    dx, dy = DIRECTIONS[action.value]
    assert cells[(dx, dy)].visible and cells[(dx, dy)].walkable
    assert enemy.distance((dx, dy)) >= enemy.distance(obs.position)
    # Geometry authorization must not mutate the observation/capability model.
    assert mechanics(obs.hostiles[0]) == info and info.entangles and info.ranged_max == 0


def test_synthetic_public_travel_speed_scales_both_projectile_windows():
    obs = wall_cut()
    enemy = replace(obs.hostiles[0], speed_description="Speed: 150% (travel: 200%)")
    obs = replace(obs, monsters=(enemy,))
    action = enclosed_melee_bypass(obs, mapping(obs), {(-1, 0)})
    assert action is not None
    proof, = action.assessment["projectile_separation_evidence"]
    assert proof["optimistic_movement_steps"] == {"departure": 2, "poststep": 8}


def test_synthetic_stair_goal_authorizes_only_a_move_not_stair_activation():
    obs = wall_cut()
    cells = mapping(obs)
    cells[(-1, 0)] = replace(cells[(-1, 0)], feature=46)
    obs = replace(obs, cells=tuple(cells.values()))
    action = enclosed_melee_bypass(obs, cells, {(-1, 0)})
    assert action is not None and action.kind == "move" and action.value == "h"


@pytest.mark.parametrize("feature", [0, 1, 30, 31, 32, 33])
def test_synthetic_unknown_door_water_lava_or_floor_gap_is_projectile_open(feature):
    obs = wall_cut()
    cells = mapping(obs)
    cells[(0, 2)] = replace(cells[(0, 2)], feature=feature, visible=feature != 0)
    enemy = obs.hostiles[0]
    assert possible_projectile_path((0, 4), (0, 0), cells)
    assert not WalkingDistances(cells).excludes_projectile_exposure(enemy, ((0, 0),), 4, 8)
    assert enclosed_melee_bypass(replace(obs, cells=tuple(cells.values())), cells, {(-1, 0)}) is None


def test_synthetic_next_endpoint_is_checked_independently_of_current_wall_cut():
    obs = water_pocket()
    cells = mapping(obs)
    enemy = obs.hostiles[0]
    walking = WalkingDistances(cells)
    assert set(walking.reachable_origins(enemy, 4)) == {(0, 4)}
    assert not possible_projectile_path((0, 4), obs.position, cells)
    assert possible_projectile_path((0, 4), (-1, 0), cells)
    assert walking.excludes_projectile_exposure(enemy, (obs.position,), 4, 8)
    assert not walking.excludes_projectile_exposure(enemy, (obs.position, (-1, 0)), 4, 8)
    assert enclosed_melee_bypass(obs, cells, {(-1, 0)}) is None


def test_synthetic_moving_prefix_can_leave_current_square_before_its_four_action_exposure():
    # Synthetic diagonal land corridor in water: the distant origin (4,8)
    # becomes reachable only after four moves. It threatens the old square at
    # range8, but the actual next square is out of range9. Earlier origins are
    # cut off by the known wall. We do not wait at the old square four actions.
    cells = {(x, y): Cell(x, y, 31, "~", True)
             for x in range(-5, 7) for y in range(-2, 11)}
    for point in ((0, 0), (0, -1), (0, 4), (1, 5), (2, 6), (3, 7), (4, 8)):
        cells[point] = Cell(*point, 33, ".", True)
    for x in range(-3, 4):
        cells[(x, 2)] = Cell(x, 2, 12, "#", True)
    obs = replace(captured(), position=(0, 0), monsters=(net_enemy(),),
                  cells=tuple(cells.values()))
    enemy = obs.hostiles[0]
    walking = WalkingDistances(cells)
    assert walking.reachable_origins(enemy, 4)[(4, 8)] == 4
    assert walking.excludes_projectile_exposure(enemy, (obs.position,), 1, 8)
    assert not walking.excludes_projectile_exposure(enemy, (obs.position,), 4, 8)
    assert walking.excludes_projectile_exposure(enemy, ((0, -1),), 4, 8)
    action = enclosed_melee_bypass(obs, cells, {(0, -1)})
    assert action is not None and (action.kind, action.value) == ("move", "k")
    proof, = action.assessment["projectile_separation_evidence"]
    assert proof["departure_window_actions"] == 1 and proof["poststep_window_actions"] == 4


def test_synthetic_safe_destination_does_not_erase_departure_failure_exposure():
    obs = water_pocket()
    obs = replace(obs, position=(-1, 0))
    cells = mapping(obs)
    enemy = obs.hostiles[0]
    walking = WalkingDistances(cells)
    assert not walking.excludes_projectile_exposure(enemy, (obs.position,), 1, 8)
    assert walking.excludes_projectile_exposure(enemy, ((0, 0),), 4, 8)
    assert enclosed_melee_bypass(obs, cells, {(0, 0)}) is None


def test_synthetic_direct_net_line_across_deep_water_refuses_even_without_physical_arrival():
    obs = water_pocket(north_wall=False)
    cells = mapping(obs)
    enemy = obs.hostiles[0]
    walking = WalkingDistances(cells)
    assert walking.distance(enemy, mechanics(enemy), obs.position, 6) == 7
    assert possible_projectile_path((0, 4), obs.position, cells)
    assert enclosed_melee_bypass(obs, cells, {(-1, 0)}) is None


def test_synthetic_possible_movement_exit_must_be_checked_not_just_initial_shooter():
    obs = water_pocket()
    cells = mapping(obs)
    # Synthetic dry north exit and a further wall: initial direct net line is
    # blocked, but two optimistic moves reach a different firing origin.
    cells[(0, 3)] = Cell(0, 3, 33, ".", True)
    cells[(0, 2)] = Cell(0, 2, 12, "#", True)
    enemy = obs.hostiles[0]
    walking = WalkingDistances(cells)
    assert walking.excludes_projectile_exposure(enemy, (obs.position,), 0, 8)
    assert walking.reachable_origins(enemy, 4)[(1, 2)] == 2
    assert possible_projectile_path((1, 2), obs.position, cells)
    assert not walking.excludes_projectile_exposure(enemy, (obs.position,), 4, 8)
    assert enclosed_melee_bypass(replace(obs, cells=tuple(cells.values())), cells, {(-1, 0)}) is None


@pytest.mark.parametrize("change", [
    {"name": "unknown net creature"},
    {"description": None},
    {"statuses": ("flying",)},
    {"statuses": ("blinking",)},
    {"spells": ("Blink",)},
    {"spells": ("Smiting",)},
    {"spells": ("Confuse",)},
    {"spells": ("Unknown spell",)},
    {"equipment": ("+0 halberd (pain)", "a wand of flame")},
    {"equipment": ("a shortbow",)},
    {"spell_ranges": (("Unknown spell", 7),)},
])
def test_synthetic_unknown_flight_blink_books_and_wands_never_get_net_certificate(change):
    obs = wall_cut()
    enemy = replace(obs.hostiles[0], **change)
    obs = replace(obs, monsters=(enemy,))
    assert not ordinary_net_thrower(enemy, mechanics(enemy))
    assert enclosed_melee_bypass(obs, mapping(obs), {(-1, 0)}) is None


@pytest.mark.parametrize("addition", [
    "\nIt has an unusual item: a wand of flame.",
    "\nIt has an unusual item: a stone.",
    "\nSpells:\nUnknown spell: unknown",
])
def test_synthetic_unparsed_wand_item_or_book_is_not_hidden_by_net_table(addition):
    enemy = net_enemy()
    enemy = replace(enemy, description=enemy.description + addition)
    assert not ordinary_net_thrower(enemy, mechanics(enemy))


@pytest.mark.parametrize("replacement", [
    "Throw: a throwing net   0              Ensnare in a net, exploding",
    "Throw: a throwing net   0              Ensnare in a net, bouncing",
    "Throw: a throwing net   0              Ensnare in a net\nThrow: a stone         2",
])
def test_synthetic_other_projectile_effects_and_rows_are_not_ordinary_net_only(replacement):
    enemy = net_enemy()
    enemy = replace(enemy, description=enemy.description.replace(
        "Throw: a throwing net   0              Ensnare in a net", replacement))
    assert not ordinary_net_thrower(enemy, mechanics(enemy))


def test_synthetic_nearby_polearm_contact_is_not_overridden_by_net_wall_proof():
    obs = wall_cut()
    enemy = replace(obs.hostiles[0], x=0, y=1)
    obs = replace(obs, monsters=(enemy,))
    assert ordinary_net_thrower(enemy, mechanics(enemy))
    assert enclosed_melee_bypass(obs, mapping(obs), {(-1, 0)}) is None


def test_synthetic_projectile_corner_squeeze_and_longer_monotone_paths_stay_possible():
    # Diagonal corner restrictions are deliberately ignored, as with monster
    # movement. A cut must exclude even these optimistic paths.
    cells = {(0, 1): Cell(0, 1, 12), (1, 0): Cell(1, 0, 12)}
    assert possible_projectile_path((0, 0), (1, 1), cells)
    # A monotone path can be longer than direct Chebyshev distance. Keeping it
    # possible avoids assuming native rays have shortest-8-neighbor footprints.
    cells = {(1, 1): Cell(1, 1, 12)}
    assert possible_projectile_path((0, 0), (2, 2), cells)
    assert possible_projectile_path((2, 2), (0, 0), cells)
    assert possible_projectile_path((0, 0), (0, 0), cells)
