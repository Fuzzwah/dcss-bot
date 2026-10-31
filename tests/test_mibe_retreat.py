"""Moving withdrawal forecasts use only visible or explicitly remembered terrain."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import OPPORTUNITY_CHANCE
from dcss_bot.characters.mibe_retreat import plan_withdrawal
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def corridor(*, length=6, enemy=None, statuses=(), hp=100):
    cells = {(x, 0): Cell(x, 0, 46 if x == length else 33, visible=True)
             for x in range(length + 1)}
    obs = Observation(hp=hp, hp_max=hp, position=(0, 0), statuses=statuses,
                      monsters=(enemy,) if enemy is not None else (),
                      cells=tuple(cells.values()))
    return obs, cells, {point: 1 for point in cells}


def uncovered(*_):
    return False


def test_known_upstairs_preserves_a_nonadjacent_equal_speed_pursuit_gap():
    enemy = Monster(1, "gnoll", -2, 0)
    obs, cells, visits = corridor(enemy=enemy)
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None
    assert plan.goal == (6, 0) and plan.goal_kind == "upstairs"
    assert plan.path == tuple((x, 0) for x in range(1, 7))
    assert plan.horizon == 3
    assert plan.expected_loss == plan.burst_loss == 0
    # Three successive reobservations preserve the gap, not a failed escape
    # just because the pursuer's distance remains two.
    for player_x in range(1, 4):
        moved = replace(obs, position=(player_x, 0),
                        monsters=(replace(enemy, x=player_x - 2),))
        again = plan_withdrawal(moved, cells, visits, uncovered, target=plan.goal)
        assert again is not None
        assert again.path[0] == (player_x + 1, 0)
        assert again.burst_loss == 0


@pytest.mark.parametrize("enemy,statuses", [
    (Monster(1, "jackal", -3, 0), ()),
    (Monster(1, "gnoll", -3, 0), ("Slow",)),
    (Monster(1, "gnoll", -1, 0), ()),
    (Monster(1, "gnoll", -1, 0), ("Haste",)),
    (Monster(1, "gnoll", -2, 0, equipment=("+0 spear",)), ()),
])
def test_fast_slowed_adjacent_and_reaching_pursuit_is_not_a_walking_escape(enemy, statuses):
    obs, cells, visits = corridor(enemy=enemy, statuses=statuses)
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


@pytest.mark.parametrize("statuses,duration", [
    ((), 3.), (("Slow",), 4.5), (("Haste",), 2.), (("Berserk",), 2.),
    (("Slow", "Haste"), 3.), (("-Berserk",), 3.),
])
def test_forecast_horizon_uses_player_movement_time(statuses, duration):
    obs, cells, visits = corridor(statuses=statuses)
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None
    assert plan.horizon == pytest.approx(duration)


def test_haste_can_preserve_a_gap_against_a_normally_faster_melee_enemy():
    obs, cells, visits = corridor(enemy=Monster(1, "jackal", -3, 0), statuses=("Haste",))
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None and plan.burst_loss == 0


def test_displayed_attack_and_travel_speeds_are_not_interchanged():
    enemy = Monster(1, "gnoll", -2, 0, speed_description="Speed: 200% (travel: 100%)")
    obs, cells, visits = corridor(enemy=enemy)
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None and plan.burst_loss == 0
    # Slow attack speed does not make fast movement an escapeable pursuer.
    fast = replace(enemy, speed_description="Speed: 50% (travel: 150%)")
    assert plan_withdrawal(replace(obs, monsters=(fast,)), cells, visits, uncovered) is None


def test_ranged_exposure_uses_attack_speed_and_actual_moving_horizon():
    enemy = Monster(1, "dart slug", -4, 0,
                    speed_description="Speed: 200% (travel: 40%)")
    obs, cells, visits = corridor(length=2, enemy=enemy)
    fast = plan_withdrawal(obs, cells, visits, uncovered)
    slow_enemy = replace(enemy, speed_description="Speed: 50% (travel: 40%)")
    slow = plan_withdrawal(replace(obs, monsters=(slow_enemy,)), cells, visits, uncovered)
    assert fast is not None and slow is not None
    assert fast.horizon == slow.horizon == 2
    assert fast.expected_loss > slow.expected_loss > 0
    assert fast.burst_loss > slow.burst_loss > 0


def test_walking_away_is_not_three_stationary_actions_at_the_first_tile():
    # A one-action forecast ends after the actual first move. The enemy must
    # spend its energy closing, rather than attacking at that tile for 3 turns.
    obs, cells, visits = corridor(enemy=Monster(1, "gnoll", -2, 0))
    plan = plan_withdrawal(obs, cells, visits, uncovered, max_steps=1)
    assert plan is not None
    assert plan.horizon == 1 and plan.burst_loss == 0
    assert len(plan.path) == 6


@pytest.mark.parametrize("feature,cloud,visible", [
    (0, False, True), (11, False, True), (31, False, True),
    (1, False, True), (176, False, True), (33, True, True),
    (33, False, False),
])
def test_unknown_hazardous_closed_or_nonvisible_next_step_is_rejected(feature, cloud, visible):
    obs, cells, visits = corridor()
    cells[(1, 0)] = Cell(1, 0, feature, visible=visible, cloud=cloud)
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


@pytest.mark.parametrize("attitude", [0, 1, 2])
def test_no_occupied_next_step_even_for_a_friendly_monster(attitude):
    obs, cells, visits = corridor(enemy=Monster(1, "plant", 1, 0, attitude=attitude))
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


def test_only_visited_remembered_ground_can_extend_a_visible_next_step():
    obs, cells, visits = corridor()
    for x in range(2, 7):
        cells[(x, 0)] = replace(cells[(x, 0)], visible=False)
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None and plan.goal == (6, 0)
    assert plan.path[0] == (1, 0)
    visits.pop((2, 0))
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


def test_upstairs_is_the_feature_id_not_a_renderer_glyph():
    obs, cells, visits = corridor()
    cells[(6, 0)] = Cell(6, 0, 33, "<", True)
    assert plan_withdrawal(obs, cells, visits, uncovered) is None
    cells[(6, 0)] = Cell(6, 0, 49, "<", True)  # hatch is not ordinary stairs
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


def test_pinned_goal_is_not_replaced_by_an_easier_anchor():
    obs, cells, visits = corridor()
    cells[(1, 1)] = Cell(1, 1, 46, visible=True)
    visits[(1, 1)] = 1
    unpinned = plan_withdrawal(obs, cells, visits, uncovered)
    pinned = plan_withdrawal(obs, cells, visits, uncovered, target=(6, 0))
    assert unpinned is not None and unpinned.goal == (1, 1)
    assert pinned is not None and pinned.goal == (6, 0)
    assert plan_withdrawal(obs, cells, visits, uncovered, target=(3, 0)) is None
    cells[(6, 0)] = replace(cells[(6, 0)], cloud=True)
    assert plan_withdrawal(obs, cells, visits, uncovered, target=(6, 0)) is None


def test_visited_route_is_preferred_when_anchor_exposure_is_equal():
    obs, cells, visits = corridor(length=3)
    cells[(0, 1)] = Cell(0, 1, 46, visible=True)
    # The new closer stair is visible but unvisited. Known ground wins.
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None and plan.goal == (3, 0)


def test_apparent_cover_needs_nearby_known_opaque_terrain_and_retains_shot_risk():
    enemy = Monster(1, "dart slug", 4, 0)
    obs, cells, visits = corridor(length=2, enemy=enemy)
    cells[(2, 0)] = Cell(2, 0, 33, visible=True)  # remove the upstairs anchor
    cells[(0, 1)] = Cell(0, 1, 33, visible=True)
    visits[(0, 1)] = 1
    hint = lambda mapping, shooter, point: point == (0, 1)
    assert plan_withdrawal(obs, cells, visits, hint) is None
    cells[(1, 1)] = Cell(1, 1, 2, "#", True)
    plan = plan_withdrawal(obs, cells, visits, hint)
    assert plan is not None and plan.goal_kind == "cover" and plan.goal == (0, 1)
    assert plan.burst_loss > 0  # a heuristic wall is not verified LOS


def test_observed_potentially_lethal_projectile_refuses_the_route():
    enemy = Monster(1, "archer", -4, 0,
                    description="Attack  Max Damage\nShoot: +0 shortbow  20\n")
    obs, cells, visits = corridor(length=1, hp=20, enemy=enemy)
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


def test_alternative_melee_and_throw_rows_are_not_added_to_each_ranged_hit():
    enemy = Monster(1, "kobold", -4, 0,
                    description="Attack  Max Damage\nHit: +0 whip  6\nThrow: stone  2\n")
    obs, cells, visits = corridor(length=1, enemy=enemy)
    plan = plan_withdrawal(obs, cells, visits, uncovered)
    assert plan is not None and plan.burst_loss == 2




def test_empty_clear_ground_is_not_an_anchor_or_an_unplanned_wander():
    obs, cells, visits = corridor()
    cells[(6, 0)] = replace(cells[(6, 0)], feature=33)
    assert plan_withdrawal(obs, cells, visits, uncovered) is None


def test_leaving_range_does_not_let_a_shooter_both_chase_and_fire_every_action():
    enemy = Monster(1,"archer",-6,0,
                    description="Attack  Max Damage\nShoot: +0 shortbow  6\n",
                    speed_description="Speed: 100% (travel: 100%)")
    obs,cells,visits = corridor(length=3,enemy=enemy)
    plan = plan_withdrawal(obs,cells,visits,uncovered)
    assert plan is not None and plan.horizon == 3
    assert plan.burst_loss == 6  # One shot at range seven; closing costs later energy.


def recorded_bat_encounter():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "recorded_development13_bat_retreat.json").read_text())
    values = dict(fixture["observation"])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    obs = Observation(**values)
    return obs, {(cell.x, cell.y): cell for cell in obs.cells}, Counter({tuple(point): 1 for point in fixture["visited"]})


def test_recorded_natasha_bat_and_imp_allow_a_costed_legal_withdrawal_from_multiple_enemies():
    obs, cells, visits = recorded_bat_encounter()
    action = TacticalController().decide(obs, cells, visits, RuleBasedAgent._covered)
    assert action.kind == "move" and action.objective == "planned retreat"
    dx, dy = DIRECTIONS[action.value]
    destination = obs.position[0]+dx, obs.position[1]+dy
    assert cells[destination].walkable and cells[destination].visible and not cells[destination].cloud
    assert destination not in {(enemy.x, enemy.y) for enemy in obs.monsters}
    caster = next(enemy for enemy in obs.hostiles if enemy.name == "Natasha")
    assert caster.distance(destination) > caster.distance(obs.position)
    proof = action.assessment["moving_withdrawal"]
    assert 0 < proof["expected_loss"] < obs.hp * .35
    assert 0 < proof["burst_loss"] < obs.hp * .8


@pytest.mark.parametrize("enemy_x", [-3, -1])
def test_examined_weak_interception_costs_opportunity_damage_even_after_spending_movement_energy(enemy_x):
    recorded, _, _ = recorded_bat_encounter()
    bat = replace(next(enemy for enemy in recorded.hostiles if enemy.name == "bat"), x=enemy_x, y=0)
    obs, cells, visits = corridor(length=1, enemy=bat, hp=39)
    plan = plan_withdrawal(obs, cells, visits, uncovered, max_steps=1)
    assert plan is not None and plan.path == ((1, 0),) and plan.goal_kind == "upstairs"
    assert plan.expected_loss > 0 and plan.burst_loss >= 1
    if enemy_x == -3:
        # All regular attack energy is spent closing; an opportunity strike is
        # still possible. Its full damage bound must not disappear.
        assert plan.expected_loss == pytest.approx(round(OPPORTUNITY_CHANCE, 2))
        assert plan.burst_loss == 1


@pytest.mark.parametrize("variant", ["strong", "unknown", "spell", "bonus", "two", "low_hp"])
def test_weak_interception_never_licenses_unknown_effects_packs_or_low_hp_exposure(variant):
    recorded, _, _ = recorded_bat_encounter()
    bat = replace(next(enemy for enemy in recorded.hostiles if enemy.name == "bat"), x=-1, y=0)
    if variant == "strong":
        bat = replace(bat, description="Max HP: ~3\nAttack  Max Damage\nHit  10\n")
    elif variant == "unknown":
        bat = replace(bat, description="")
    elif variant == "spell":
        bat = replace(bat, spells=("Magic Dart",))
    elif variant == "bonus":
        bat = replace(bat, description="Max HP: ~3\nAttack  Max Damage  Bonus\nHit  1  draining\n")
    obs, cells, visits = corridor(length=1, enemy=bat, hp=9 if variant == "low_hp" else 39)
    if variant == "two":
        obs = replace(obs, monsters=(bat, replace(bat, id=bat.id+1, x=0, y=-1)))
    assert plan_withdrawal(obs, cells, visits, uncovered, max_steps=1) is None
