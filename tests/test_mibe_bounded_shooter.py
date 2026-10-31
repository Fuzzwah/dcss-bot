"""Recorded native early shooter poses; response mutations are explicit synthetics."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from legacy_replay import decide_legacy_observation

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_approach import BoundedShooterApproach
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import CLOSED_DOORS


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development13_shooter.json"
ALIASES_FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development14_shooter_aliases.json"
INTERRUPTION_FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development16_shooter_interruption.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def recorded(key):
    return observation(json.loads(FIXTURE.read_text())[key])


def recorded_alias(key):
    return observation(json.loads(ALIASES_FIXTURE.read_text())[key])


def examined_centaur():
    obs = recorded("centaur_first_visible")
    public = Monster(**json.loads(FIXTURE.read_text())["centaur_public_examination"])
    # An explicitly synthetic turn-free inspection response: the later actual
    # public book is composed at the original, earliest healthy native pose.
    return replace(obs, monsters=tuple(replace(public, x=m.x, y=m.y) if m.name == "centaur" else m
                                       for m in obs.monsters))


def open_centaur(hp=51, distance=7):
    """Synthetic open arena, retaining the actual recorded public equipment/book."""
    obs = examined_centaur()
    return replace(obs, hp=hp, position=(0, 0), statuses=(), status_details=(), abilities=(),
                   inventory=(), messages=(),
                   monsters=(replace(obs.hostiles[0], x=distance, y=0),),
                   cells=tuple(Cell(x, y, 33, ".", True) for x in range(-12, 13) for y in range(-12, 13)))


def choose(policy, obs, covered=RuleBasedAgent._covered):
    return policy.decide(obs, {(cell.x, cell.y): cell for cell in obs.cells}, Counter(), covered)


def destination(obs, action):
    dx, dy = DIRECTIONS[action.value]
    return obs.position[0]+dx, obs.position[1]+dy


def assert_legal_advance_or_real_defence(obs, action):
    assert action.kind != "pause"
    if action.kind == "move":
        position = destination(obs, action)
        cell = next(cell for cell in obs.cells if (cell.x, cell.y) == position)
        assert cell.visible and cell.walkable and not cell.cloud and cell.feature not in CLOSED_DOORS
        assert position not in {(enemy.x, enemy.y) for enemy in obs.monsters}
        if action.objective == "bounded shooter approach":
            assert obs.hostiles[0].distance(position) < obs.hostiles[0].distance(obs.position)
        else:
            assert action.objective in {"planned retreat", "retreat", "lure"}
            if "moving_withdrawal" in action.assessment:
                assert action.assessment["moving_withdrawal"]
            else:
                policy = TacticalController()
                cells = {(cell.x, cell.y): cell for cell in obs.cells}
                standing = policy.assess(obs, cells, RuleBasedAgent._covered)
                after = policy.assess(obs, cells, RuleBasedAgent._covered, position)
                assert (after.expected_loss < standing.expected_loss
                        or after.simultaneous_attackers < standing.simultaneous_attackers)
    else:
        assert action.kind in {"read", "quaff", "ability", "escape_upstairs"}
        assert (action.kind, action.value) != ("ability", "berserk")


async def test_actual_first_healthy_centaur_consumer_inspects_then_advances_or_uses_real_defence():
    obs = recorded("centaur_first_visible")
    before = recorded("centaur_precontact_full_hp")
    assert before.turn == 4481 and before.hp == before.hp_max == 51 and not before.hostiles
    assert obs.turn == 4482 and obs.hp == 43 and obs.hp_max == 51
    assert obs.hostiles[0].distance(obs.position) == 7
    assert obs.hostiles[0].description is None
    agent = RuleBasedAgent()
    first = await agent.decide(obs)
    assert first.kind == "inspect_monster"
    inspected = examined_centaur()
    action = await agent.decide(inspected)
    assert_legal_advance_or_real_defence(inspected, action)
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])


async def test_actual_healthy_initial_slug_consumer_acts_before_the_recorded_hp_decline():
    obs = recorded("slug_first_healthy_examined")
    assert obs.turn == 128 and obs.hp == 16 and obs.hp_max == 19
    assert obs.hostiles[0].distance(obs.position) == 2
    action = await RuleBasedAgent().decide(obs)
    assert_legal_advance_or_real_defence(obs, action)


async def test_actual_postdamage_preview_is_not_blindly_charged():
    obs = recorded("centaur_low_hp_preview")
    assert obs.turn == 4501 and obs.hp == 23 and obs.hp_max == 51
    assert obs.hostiles[0].distance(obs.position) == 7
    action = await RuleBasedAgent().decide(obs)
    assert action.objective != "bounded shooter approach"
    assert (action.kind, action.value) != ("ability", "berserk")
    if action.kind == "move":
        assert action.objective in {"retreat", "planned retreat", "lure"}


def test_forecast_pressure_not_static_range_controls_cover_and_retreat():
    obs = open_centaur()
    policy = TacticalController()
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    at_eight = policy.assess(obs, cells, lambda *_: False, (-1, 0))
    assert at_eight.threats[0].distance == 8
    assert at_eight.threats[0].ranged and at_eight.expected_loss > 0
    assert policy._corner_step(obs, cells, Counter(), lambda *_: False,
                               policy.assess(obs, cells, lambda *_: False)) is None
    action = choose(policy, obs, lambda *_: False)
    assert action.objective == "bounded shooter approach"


def test_genuine_opaque_cover_and_truly_separated_slow_shooter_remain_relief():
    obs = open_centaur()
    policy = TacticalController()
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    # Explicit terrain-backed cover callback, with a known opaque wall in the ray.
    cells[(3, 1)] = Cell(3, 1, 5, "#", True)
    covered = lambda terrain, enemy, position: position == (0, 2) and terrain[(3, 1)].feature == 5
    risk = policy.assess(obs, cells, covered, (0, 2))
    assert not risk.threats[0].ranged
    slug = recorded("slug_first_healthy_examined").hostiles[0]
    distant = replace(obs, monsters=(replace(slug, x=10, y=0),))
    separated = policy.assess(distant, cells, lambda *_: False)
    assert not separated.threats[0].ranged
    assert separated.threats[0].expected_loss == 0


def test_single_move_exposure_and_separately_logged_absolute_burst_and_stress():
    obs = open_centaur(hp=43)
    policy = TacticalController()
    action = choose(policy, obs, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    evidence = action.assessment["shooter_approach"]
    assert evidence["available"] and evidence["remaining_steps"] == 6
    assert evidence["remaining_expected_loss"] + evidence["hp_reserve"] < obs.hp
    assert evidence["remaining_absolute_burst"] > obs.hp
    assert evidence["one_action_absolute_burst"] >= obs.hp
    assert evidence["stress_heuristic"] > evidence["remaining_expected_loss"]
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    one = policy.assess(obs, cells, lambda *_: False, destination(obs, action),
                        duration=1/policy.config.horizon_actions)
    assert one.horizon == 1
    assert evidence["one_action_expected_loss"] == one.expected_loss
    selected = next(row for row in action.assessment["candidates"]
                    if row["kind"] == action.kind and row["value"] == action.value)
    assert selected["expected_loss"] == one.expected_loss


@pytest.mark.parametrize("change", ["blocked", "kiting", "los_loss",
                                   "poison", "timeout", "unseen_contact", "new_book", "reserve_lost"])
def test_approach_cancellation_cannot_be_refilled_by_native_id_or_routine_fallback(change):
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    position = destination(obs, first)
    response = replace(obs, turn=obs.turn+1, time=obs.time+10, position=position)
    if change == "blocked":
        response = replace(response, position=obs.position)
    elif change == "kiting":
        response = replace(response, monsters=(replace(obs.hostiles[0], x=8),))
    elif change == "los_loss":
        response = replace(response, monsters=())
    elif change == "poison":
        response = replace(response, statuses=("Pois",), poison_survival=40)
    elif change == "timeout":
        response = replace(response, turn=obs.turn+9, time=obs.time+90)
    elif change == "new_book":
        response = replace(response, monsters=(replace(obs.hostiles[0], spells=("Blink",)),))
    elif change == "reserve_lost":
        response = replace(response, hp=10)
    elif change == "unseen_contact":
        policy.unseen_displacements.origin = response.location, response.position
        policy.unseen_displacements.attempts[(position[0], position[1]+1)] = response.turn
    action = choose(policy, response, lambda *_: False)
    assert action is None or action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None
    # A restored healthy body/book and another ID cannot restart this allowance.
    restored = replace(obs, turn=response.turn+1, time=response.time+10, position=position,
                       monsters=(replace(obs.hostiles[0], id=1000),))
    action = choose(policy, restored, lambda *_: False)
    assert action.objective != "bounded shooter approach"


def test_distance_progress_preserves_action_time_and_hp_budgets_across_synthetic_native_aliases():
    obs = open_centaur()
    policy = TacticalController()
    started = obs.turn
    for offset in range(6):
        action = choose(policy, obs, lambda *_: False)
        assert action.objective == "bounded shooter approach"
        evidence = action.assessment["shooter_approach"]
        assert evidence["actions_used"] == offset
        assert evidence["remaining_steps"] == 6-offset
        # Synthetic responses execute only the selected step. Each new ID is
        # explicitly a control, not a claim about persistent native identity.
        obs = replace(obs, position=destination(obs, action), turn=obs.turn+1, time=obs.time+10,
                      hp=obs.hp-1, monsters=(replace(obs.hostiles[0], id=1000+offset),))
        assert policy.shooter_approach.active.started == started
        assert policy.shooter_approach.active.actions == offset+1
    assert obs.hostiles[0].distance(obs.position) == 1
    assert policy.shooter_approach.active.actions == 6
    assert obs.hp == 45 and obs.turn-started == 6
    action = choose(policy, obs, lambda *_: False)
    assert action.objective != "bounded shooter approach"


@pytest.mark.parametrize("change", ["unknown_book", "blink", "invisible", "net", "poison",
                                   "unknown_hp", "heavy_body", "heavy_shot", "heavy_melee",
                                   "fast", "distortion", "cloud", "door", "unknown_corner",
                                   "low_hp", "held", "slow", "corrosion"])
def test_unbounded_target_or_restricted_pose_never_gets_new_shooter_permission(change):
    obs = open_centaur()
    target = obs.hostiles[0]
    if change in {"unknown_book", "blink", "invisible", "poison"}:
        spell = {"unknown_book": "Unrecognized spell", "blink": "Blink",
                 "invisible": "Invisibility", "poison": "Sting"}[change]
        target = replace(target, spells=(spell,))
    elif change == "net":
        target = replace(target, description=target.description.replace("orcbow  22", "orcbow  22 ensnare in a net"))
    elif change == "unknown_hp":
        target = replace(target, description=target.description.replace("Max HP: ~22", "HP unknown"))
    elif change == "heavy_body":
        target = replace(target, description=target.description.replace("Max HP: ~22", "Max HP: ~50"))
    elif change == "heavy_shot":
        target = replace(target, description=target.description.replace("orcbow  22", "orcbow  40"))
    elif change == "heavy_melee":
        target = replace(target, description=target.description.replace("Hit               7", "Hit               20"))
    elif change == "fast":
        target = replace(target, speed_description="Speed: 200%")
    elif change == "distortion":
        target = replace(target, equipment=("+0 orcbow of distortion",))
    obs = replace(obs, monsters=(target,))
    if change in {"cloud", "door", "unknown_corner"}:
        # Close every possible first-step lane, not only one arbitrary direction.
        obs = replace(obs, cells=tuple(replace(cell, cloud=True) if change == "cloud" and cell.x == 1
                                      else replace(cell, feature=1) if change == "door" and cell.x == 1
                                      else replace(cell, visible=False) if change == "unknown_corner" and cell.x == 2
                                      else cell for cell in obs.cells))
    elif change == "low_hp":
        obs = replace(obs, hp=23)
    elif change in {"held", "slow", "corrosion"}:
        obs = replace(obs, statuses=({"held": "Held", "slow": "Slow", "corrosion": "Corr"}[change],))
    action = choose(TacticalController(), obs, lambda *_: False)
    assert action.objective != "bounded shooter approach"


def test_emergency_resources_and_real_known_upstairs_remain_higher_priority():
    healthy = open_centaur()
    fog = replace(healthy, inventory=(Item(1, "b", "scroll of fog", category="scroll"),))
    action = choose(TacticalController(), fog, lambda *_: False)
    assert (action.kind, action.value) == ("read", "b")
    wounded = replace(healthy, hp=15, inventory=(Item(1, "c", "potion of heal wounds", category="potion"),))
    action = choose(TacticalController(), wounded, lambda *_: False)
    assert (action.kind, action.value) == ("quaff", "c")
    # With normal attack speed the existing strict absolute-burst withdrawal
    # budget admits a nearby real stair; no approach may supersede that plan.
    stair = replace(healthy, monsters=(replace(healthy.hostiles[0], speed_description="Speed: 100% (travel: 150%)"),),
                    cells=tuple(replace(cell, feature=46) if (cell.x, cell.y) == (-1, 0) else cell
                                for cell in healthy.cells))
    action = choose(TacticalController(), stair, lambda *_: False)
    assert action.objective == "planned retreat"
    assert action.assessment["moving_withdrawal"]["goal_kind"] == "upstairs"


async def test_fresh_failed_normal_displacement_without_incoming_hp_loss_stops_exploration():
    obs = replace(open_centaur(), monsters=(), input_mode=1)
    agent = RuleBasedAgent()
    agent.tactics.routine(obs, Action("move", "l", "known clear move", "explore"))
    response = replace(obs, turn=obs.turn+1, time=obs.time+10,
                       messages=("You slash something!",))
    action = await decide_legacy_observation(agent, response)
    assert action.kind in {"pause", "attack", "read", "quaff", "ability"}
    assert action.objective != "bounded shooter approach"
    assert agent.tactics.unseen_harm is None
    assessment = agent.tactics.latest_assessment
    assert assessment.recent_damage == 0 and assessment.expected_loss == assessment.burst_loss == 0
    assert not assessment.threats
    assert action.assessment["unseen_displacements"]


async def test_actual_source4_alias_transition_is_inspected_without_inventing_another_body():
    data = json.loads(ALIASES_FIXTURE.read_text())
    before = recorded_alias("source4_before_alias")
    missing = recorded_alias("source4_after_alias_unexamined")
    examined = recorded_alias("source4_after_free_examination")
    assert (before.turn, before.time, before.hostiles[0].id) == (40, 390, 3)
    assert (missing.turn, missing.time, missing.hostiles[0].id) == (41, 400, 4)
    assert len(before.hostiles) == len(missing.hostiles) == len(examined.hostiles) == 1
    assert before.hostiles[0].name == missing.hostiles[0].name == "kobold"
    assert missing.hostiles[0].description is None
    assert examined.hostiles[0].description is not None
    assert (examined.turn, examined.time, examined.position) == (
        missing.turn, missing.time, missing.position)
    # Seed the actual historical move, not a newly chosen action. Only the
    # recorded turn-free examination is supplied after a policy decision.
    agent = RuleBasedAgent()
    agent.tactics.routine(before, Action(**data["source4_historical_move"]))
    inspect = await agent.decide(missing)
    assert (inspect.kind, inspect.value) == ("inspect_monster", "6,11")
    action = await agent.decide(examined)
    assert_legal_advance_or_real_defence(examined, action)
    assert agent.tactics.recent_hostiles["kobold"][0] == 1
    assert {3, 4} <= agent.tactics.last_seen_hostiles.keys()
    assert action.assessment["policy_state"]["recent_unresolved_hostiles"] == {"kobold": 1}


@pytest.mark.parametrize("frame,turn", [("source4_final", 50), ("source1_final", 423)])
def test_actual_final_public_geometry_has_a_costed_executable_response(frame, turn):
    obs = recorded_alias(frame)
    assert obs.turn == turn and len(obs.hostiles) == 1
    policy = TacticalController()
    if frame == "source4_final":
        # These are actual earlier public sightings, not extra bodies and not
        # responses to actions selected by the new policy.
        policy.observe(recorded_alias("source4_before_alias"))
        policy.observe(recorded_alias("source4_after_free_examination"))
    action = choose(policy, obs)
    assert_legal_advance_or_real_defence(obs, action)
    assert action.assessment["policy_state"]["recent_unresolved_hostiles"] == {
        obs.hostiles[0].name: 1}
    if action.objective == "bounded shooter approach":
        evidence = action.assessment["shooter_approach"]
        assert evidence["available"] and evidence["actions_used"] == 0
        assert evidence["remaining_expected_loss"] + evidence["hp_reserve"] < obs.hp
        assert evidence["one_action_expected_loss"] >= 0
        assert policy.shooter_approach.active.actions == 1


@pytest.mark.parametrize("threat", [0, 4])
async def test_synthetic_missing_description_alias_gets_free_inspection_without_refilling(threat):
    obs = open_centaur()
    agent = RuleBasedAgent()
    first = await decide_legacy_observation(agent, obs)
    assert first.objective == "bounded shooter approach"
    # Explicit synthetic response to the selected move, with a new native ID
    # and no book. The high-threat control must not turn fallback damage bounds
    # into newly observed offensive mechanics.
    missing = replace(obs, turn=obs.turn+1, time=obs.time+10,
                      position=destination(obs, first),
                      monsters=(replace(obs.hostiles[0], id=999, threat=threat,
                                        description=None, equipment=(), speed_description=None,
                                        spells=(), spell_ranges=()),))
    inspect = await agent.decide(missing)
    assert inspect.kind == "inspect_monster"
    attempt = agent.tactics.shooter_approach.active
    assert attempt is not None and attempt.actions == 1 and attempt.started == obs.turn
    assert (obs.location, obs.hostiles[0].name.casefold()) not in agent.tactics.shooter_approach.cancelled
    assert agent.tactics.recent_hostiles[obs.hostiles[0].name.casefold()][0] == 1
    # Explicit synthetic free description response: same pose, turn and time;
    # the public book comes from the recorded examined centaur.
    examined = replace(missing, monsters=(replace(obs.hostiles[0], id=999, threat=threat),))
    action = await agent.decide(examined)
    assert action.objective == "bounded shooter approach"
    assert_legal_advance_or_real_defence(examined, action)
    assert action.assessment["shooter_approach"]["actions_used"] == 1
    assert agent.tactics.shooter_approach.active.actions == 2
    assert agent.tactics.shooter_approach.active.started == obs.turn
    assert (examined.turn, examined.time) == (missing.turn, missing.time)


@pytest.mark.parametrize("exhausted", ["time", "hp"])
def test_synthetic_alias_cannot_reopen_expired_time_or_lost_hp_allowance(exhausted):
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first),
                       turn=obs.turn+(9 if exhausted == "time" else 1),
                       time=obs.time+(90 if exhausted == "time" else 10),
                       hp=10 if exhausted == "hp" else obs.hp,
                       monsters=(replace(obs.hostiles[0], id=999),))
    action = choose(policy, response, lambda *_: False)
    assert action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None
    restored = replace(response, hp=obs.hp, turn=response.turn+1, time=response.time+10,
                       monsters=(replace(obs.hostiles[0], id=1000),))
    action = choose(policy, restored, lambda *_: False)
    assert action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None


@pytest.mark.parametrize("extra", ["visible_body", "same_name_unresolved", "other_name_unresolved"])
def test_actual_observed_additional_bodies_and_unresolved_counts_still_forbid_approach(extra):
    obs = open_centaur()
    policy = TacticalController()
    # Explicit synthetic two-body observation, unlike an ID-only alias.
    second = replace(obs.hostiles[0], id=999, x=-7)
    if extra == "other_name_unresolved":
        second = Monster(999, "orc", -7, 0)
    two = replace(obs, monsters=(*obs.monsters, second))
    policy.routine(two, Action("wait"))
    current = replace(two if extra == "visible_body" else obs,
                      turn=obs.turn+1, time=obs.time+10)
    action = choose(policy, current, lambda *_: False)
    assert sum(count for count, _ in policy.recent_hostiles.values()) == 2
    assert action is None or action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None


def synthetic_visible_shooter_lane():
    """Synthetic one-cell route with the actual recorded public centaur book."""
    return replace(open_centaur(), cells=tuple(Cell(x, 0, 33, ".", True) for x in range(8)))


@pytest.mark.parametrize("neighbor", ["missing", "unseen", "unknown"])
@pytest.mark.parametrize("door", [None, 34, 177, 196, 197])
def test_visible_clear_route_executes_beside_unknown_cells_and_through_known_open_doors(neighbor, door):
    obs = synthetic_visible_shooter_lane()
    cells = list(obs.cells)
    if neighbor == "unseen":
        cells.append(Cell(1, 1, 33, ".", False))
    elif neighbor == "unknown":
        cells.append(Cell(1, 1, 0, " ", False))
    if door is not None:
        cells = [replace(cell, feature=door) if (cell.x, cell.y) == (1, 0) else cell for cell in cells]
    obs = replace(obs, cells=tuple(cells))
    action = choose(TacticalController(), obs, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert destination(obs, action) == (1, 0)
    assert_legal_advance_or_real_defence(obs, action)
    assert action.assessment["shooter_approach"]["remaining_steps"] == 6
    assert action.assessment["shooter_approach"]["one_action_expected_loss"] > 0


@pytest.mark.parametrize("hazard", ["missing", "unseen", "unknown", "occupied", "cloud",
                                   "closed_door", "closed_clear_door"])
def test_unknown_unseen_occupied_clouded_and_closed_route_cells_are_not_executable(hazard):
    obs = synthetic_visible_shooter_lane()
    cells = list(obs.cells)
    if hazard == "missing":
        cells = [cell for cell in cells if (cell.x, cell.y) != (1, 0)]
    else:
        changes = {"unseen": {"visible": False}, "unknown": {"feature": 0},
                   "cloud": {"cloud": True}, "closed_door": {"feature": 1},
                   "closed_clear_door": {"feature": 176}}.get(hazard, {})
        cells = [replace(cell, **changes) if (cell.x, cell.y) == (1, 0) else cell for cell in cells]
    obs = replace(obs, cells=tuple(cells))
    if hazard == "occupied":
        # A nonhostile body blocks the route without creating a second hostile
        # count; this exercises occupancy rather than the multi-foe gate.
        obs = replace(obs, monsters=(*obs.monsters, Monster(999, "orc", 1, 0, attitude=1)))
    assert len(obs.hostiles) == 1
    policy = TacticalController()
    action = choose(policy, obs, lambda *_: False)
    assert action is None or action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None


def test_synthetic_proven_coordinate_rebase_preserves_the_existing_alias_allowance():
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10)
    dx, dy = 8, -3
    # Explicit synthetic public-anchor translation, not a new encounter or
    # movement. The only action spent was the originally selected first step.
    policy.rebase(obs.location, (dx, dy))
    shifted = replace(response, position=(response.position[0]+dx, response.position[1]+dy),
                      cells=tuple(replace(cell, x=cell.x+dx, y=cell.y+dy) for cell in response.cells),
                      monsters=(replace(response.hostiles[0], id=999,
                                        x=response.hostiles[0].x+dx, y=response.hostiles[0].y+dy),))
    action = choose(policy, shifted, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert_legal_advance_or_real_defence(shifted, action)
    assert action.assessment["shooter_approach"]["actions_used"] == 1
    assert policy.shooter_approach.active.started == obs.turn
    assert policy.shooter_approach.active.actions == 2


def test_synthetic_unproven_coordinate_reset_cancels_only_its_own_floor_allowance():
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10,
                       monsters=(replace(obs.hostiles[0], id=999),))
    policy.rebase("Dungeon:99", None)
    action = choose(policy, response, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert action.assessment["shooter_approach"]["actions_used"] == 1
    policy.rebase(obs.location, None)
    restored = replace(response, turn=response.turn+1, time=response.time+10,
                       position=destination(response, action),
                       monsters=(replace(obs.hostiles[0], id=1000),))
    action = choose(policy, restored, lambda *_: False)
    assert action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None


def recorded_reversal(key):
    fixture = Path(__file__).parent / "fixtures" / "recorded_development15_shooter_reversal.json"
    return observation(json.loads(fixture.read_text())[key])


def test_recorded_approach_result_is_not_reinterpreted_after_a_later_deliberate_withdrawal():
    before = recorded_reversal("before_approach")
    after = recorded_reversal("after_approach")
    withdrawn = recorded_reversal("after_withdrawal")
    assert after.position != before.position and withdrawn.position == before.position
    approach = BoundedShooterApproach()
    approach.commit(before)
    approach.observe(after)
    approach.observe(withdrawn)
    assert approach.active is not None and approach.active.actions == 1
    assert (before.location, before.hostiles[0].name.casefold()) not in approach.cancelled
    # Restoring these old-policy facts does not refund the issued move: contact
    # is again six cells away with only five approach actions remaining.
    policy = TacticalController()
    cells = {(cell.x, cell.y): cell for cell in withdrawn.cells}
    step, _ = approach.plan(withdrawn, cells, status="", uncertain_contact=False, unresolved=False,
                           assess=lambda point: policy.assess(withdrawn, cells, lambda *_: False,
                                                             point, duration=1/3))
    assert step is None


def test_synthetic_admitted_approach_does_not_reverse_back_to_its_old_stair():
    obs = open_centaur()
    obs = replace(obs, cells=tuple(replace(cell, feature=46) if (cell.x, cell.y) == obs.position else cell
                                  for cell in obs.cells))
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10)
    action = choose(policy, response, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert destination(response, action) != obs.position
    assert obs.hostiles[0].distance(destination(response, action)) < obs.hostiles[0].distance(response.position)
    assert policy.shooter_approach.active.actions == 2


def test_synthetic_healthy_ordinary_thrower_is_not_a_reason_to_evacuate_a_manageable_floor():
    native = recorded_alias("source4_final")
    obs = replace(open_centaur(hp=19, distance=3), hp_max=19,
                  monsters=(replace(native.hostiles[0], x=3, y=0),),
                  cells=tuple(replace(cell, feature=46) if (cell.x, cell.y) == (-1, 0) else cell
                              for cell in open_centaur().cells))
    action = choose(TacticalController(), obs, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert native.hostiles[0].name == "kobold"
    assert destination(obs, action) != (-1, 0)
    assert obs.hostiles[0].distance(destination(obs, action)) < obs.hostiles[0].distance(obs.position)


def test_synthetic_emergency_resource_still_preempts_an_active_approach():
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10,
                       hp=10, inventory=(Item(1, "r", "scroll of teleportation", category="scroll"),))
    action = choose(policy, response, lambda *_: False)
    assert (action.kind, action.value) == ("read", "r")
    assert policy.shooter_approach.active is None


def test_synthetic_last_contact_step_uses_ordinary_throwers_melee_not_a_point_blank_javelin():
    native = recorded_reversal("before_approach")
    obs = replace(open_centaur(hp=31, distance=3), hp_max=31, abilities=(),
                  monsters=(replace(native.hostiles[0], x=3, y=0),))
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10, hp=18)
    action = choose(policy, response, lambda *_: False)
    assert action.objective == "bounded shooter approach"
    assert response.hostiles[0].distance(destination(response, action)) == 1
    proof = action.assessment["shooter_approach"]
    assert proof["one_action_absolute_burst"] + proof["hp_reserve"] < response.hp
    assert policy.shooter_approach.active.actions == 2


def recorded_interruption(key):
    return observation(json.loads(INTERRUPTION_FIXTURE.read_text())[key])


def seed_recorded_interrupted_approach():
    """Seed only executed historical actions, not replies to new policy choices."""
    data = json.loads(INTERRUPTION_FIXTURE.read_text())
    policy = TacticalController()
    keys = ("first_approach", "after_first_approach", "before_third_approach")
    for key, row in zip(keys, data["executed_actions"][:3]):
        obs = observation(data[key])
        action = Action(**row["action"])
        assert action.objective == "bounded shooter approach"
        policy.routine(obs, action)
        policy.shooter_approach.commit(obs)
    policy.observe(observation(data["additional_foe_unexamined"]))
    return policy


def test_captured_seed5_healthy_shooter_has_an_executable_action_before_the_real_interruption():
    obs = recorded_interruption("first_approach")
    assert (obs.turn, obs.position, obs.hp, obs.hp_max) == (69, (3, 8), 19, 19)
    assert obs.hostiles[0].name == "dart slug"
    assert obs.hostiles[0].distance(obs.position) == 7
    action = choose(TacticalController(), obs)
    assert action.objective == "bounded shooter approach"
    assert_legal_advance_or_real_defence(obs, action)
    assert action.assessment["shooter_approach"]["actions_used"] == 0
    assert action.assessment["shooter_approach"]["remaining_steps"] == 6


def test_captured_additional_foe_suspends_only_the_three_remaining_actions_and_consumes_the_move_receipt():
    policy = seed_recorded_interrupted_approach()
    approach = policy.shooter_approach
    attempt = approach.active
    assert attempt is not None and attempt.actions == 3 and attempt.started == 69
    assert not attempt.result_pending
    assert (attempt.location, attempt.name) not in approach.cancelled
    extra = recorded_interruption("additional_foe_examined")
    assert extra.turn == 72 and extra.position == (6, 8) and extra.hp == 19
    assert {enemy.name for enemy in extra.hostiles} == {"dart slug", "hobgoblin"}
    hobgoblin = next(enemy for enemy in extra.hostiles if enemy.name == "hobgoblin")
    assert (hobgoblin.x, hobgoblin.y) == (13, 9)
    assert any(status.startswith("Asleep:") for status in hobgoblin.statuses)
    action = choose(policy, extra)
    assert action.objective != "bounded shooter approach"
    assert action.kind in {"move", "wait", "read", "quaff", "ability", "escape_upstairs"}
    assert approach.active is attempt and attempt.actions == 3 and attempt.started == 69
    # This is a recorded withdrawal, independently of the newly selected action.
    historical = json.loads(INTERRUPTION_FIXTURE.read_text())["executed_actions"]
    withdrawal = next(row for row in historical if row["sequence"] == 92)
    policy.routine(extra, Action(**withdrawal["action"]))
    lone = recorded_interruption("after_withdrawal")
    action = choose(policy, lone)
    assert lone.turn == 73 and lone.position == (5, 7) and lone.hp == 19
    assert len(lone.hostiles) == 1
    assert policy.recent_hostiles["hobgoblin"][0] == 1
    assert action.objective != "bounded shooter approach"
    assert approach.active is attempt and attempt.actions == 3 and attempt.started == 69
    assert "unresolved additional foe; remaining allowance suspended" in (
        action.assessment["shooter_approach"]["rejected"])


def test_captured_seed5_terminal_has_a_genuinely_unresolved_foe_and_no_refilled_allowance():
    policy = seed_recorded_interrupted_approach()
    data = json.loads(INTERRUPTION_FIXTURE.read_text())
    extra = observation(data["additional_foe_examined"])
    policy.routine(extra, Action(**next(row["action"] for row in data["executed_actions"]
                                      if row["sequence"] == 92)))
    lone = observation(data["after_withdrawal"])
    policy.routine(lone, Action(**next(row["action"] for row in data["executed_actions"]
                                     if row["sequence"] == 93)))
    hidden = observation(data["after_retreat"])
    policy.observe(hidden)
    assert not hidden.hostiles
    assert policy.shooter_approach.active is None
    key = extra.location, "dart slug"
    assert policy.shooter_approach.cancelled[key] == "target lost LOS"
    policy.routine(hidden, Action("wait", objective="lure"))
    policy.observe(observation(data["reappeared_examined"]))
    terminal = observation(data["terminal"])
    assert (terminal.turn, terminal.position, terminal.hp) == (81, (5, 7), 19)
    assert terminal.hostiles[0].distance(terminal.position) == 4
    assert terminal.turn-69 > 8
    assert not any("kill the hobgoblin" in message.casefold() for message in terminal.messages)
    action = choose(policy, terminal)
    assert action.objective != "bounded shooter approach"
    assert policy.recent_hostiles["hobgoblin"][0] == 1
    assert policy.shooter_approach.active is None
    assert policy.shooter_approach.cancelled[key] == "target lost LOS"


def interrupted_centaur():
    """Synthetic legal first move followed by a newly visible adjacent body."""
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    assert first.objective == "bounded shooter approach"
    extra = Monster(999, "orc", *obs.position)
    response = replace(obs, position=destination(obs, first), turn=obs.turn+1, time=obs.time+10,
                       monsters=(extra, obs.hostiles[0]))
    policy.observe(response)
    assert policy.shooter_approach.active is not None
    assert not policy.shooter_approach.active.result_pending
    # The declared synthetic attack, rather than an unexecuted policy choice,
    # supplies the next frame's public kill receipt.
    direction = next(key for key, (dx, dy) in DIRECTIONS.items()
                     if (response.position[0]+dx, response.position[1]+dy) == obs.position)
    policy.routine(response, Action("attack", direction, objective="combat"))
    return obs, policy, response


def test_publicly_killed_extra_foe_resumes_remaining_allowance_without_refunding_time_or_actions():
    original, policy, interrupted = interrupted_centaur()
    resolved = replace(interrupted, turn=interrupted.turn+1, time=interrupted.time+10,
                       monsters=(replace(original.hostiles[0], id=1000),),
                       messages=("You kill the orc!",))
    action = choose(policy, resolved, lambda *_: False)
    assert policy.recent_hostiles.get("orc", (0, 0))[0] == 0
    assert action.objective == "bounded shooter approach"
    assert_legal_advance_or_real_defence(resolved, action)
    evidence = action.assessment["shooter_approach"]
    assert evidence["actions_used"] == 1 and evidence["remaining_steps"] == 5
    assert evidence["action_limit"] == 6 and evidence["time_limit"] == 8
    assert policy.shooter_approach.active.started == original.turn
    assert policy.shooter_approach.active.actions == 2
    assert resolved.turn-original.turn == 2


@pytest.mark.parametrize("resolution", ["still_visible", "left_los", "vanished"])
def test_extra_foe_without_a_public_kill_cannot_resume_suspended_allowance(resolution):
    original, policy, interrupted = interrupted_centaur()
    current = replace(interrupted, turn=interrupted.turn+1, time=interrupted.time+10,
                      monsters=interrupted.monsters if resolution == "still_visible"
                      else (replace(original.hostiles[0], id=1000),),
                      messages=("The orc vanishes!",) if resolution == "vanished" else ())
    action = choose(policy, current, lambda *_: False)
    assert action is None or action.objective != "bounded shooter approach"
    assert policy.recent_hostiles["orc"][0] == 1
    if resolution == "vanished":
        # An adjacent vanishing body is unknown contact, not a safe suspension.
        assert policy.shooter_approach.active is None
    else:
        assert policy.shooter_approach.active.actions == 1
        assert policy.shooter_approach.active.started == original.turn


@pytest.mark.parametrize("failure", ["blocked", "kiting", "ambiguous_target"])
def test_interruption_does_not_mask_the_issued_moves_true_failure(failure):
    obs = open_centaur()
    policy = TacticalController()
    first = choose(policy, obs, lambda *_: False)
    target = replace(obs.hostiles[0], x=8) if failure == "kiting" else obs.hostiles[0]
    other = replace(target, id=999, x=-7) if failure == "ambiguous_target" else Monster(999, "orc", -7, 0)
    response = replace(obs, turn=obs.turn+1, time=obs.time+10,
                       position=obs.position if failure == "blocked" else destination(obs, first),
                       monsters=(other, target))
    policy.observe(response)
    assert policy.shooter_approach.active is None
    reason = policy.shooter_approach.cancelled[obs.location, target.name.casefold()]
    assert reason == {"blocked": "approach failed to displace the player",
                      "kiting": "kiting or no observed distance progress",
                      "ambiguous_target": "different or ambiguous visible target"}[failure]


@pytest.mark.parametrize("exhausted", ["time", "hp", "status", "unknown_book", "unknown_route", "actions"])
def test_interruption_resolution_cannot_override_original_exhaustion_or_safety_bounds(exhausted):
    original, policy, interrupted = interrupted_centaur()
    target = replace(original.hostiles[0], id=1000)
    current = replace(interrupted, turn=interrupted.turn+1, time=interrupted.time+10,
                      monsters=(target,), messages=("You kill the orc!",))
    if exhausted == "time":
        current = replace(current, turn=original.turn+9, time=original.time+90)
    elif exhausted == "hp":
        current = replace(current, hp=10,
                          inventory=(Item(1, "r", "scroll of teleportation", category="scroll"),))
    elif exhausted == "status":
        current = replace(current, statuses=("Slow",))
    elif exhausted == "unknown_book":
        current = replace(current, monsters=(replace(target, spells=("Blink",)),))
    elif exhausted == "unknown_route":
        current = replace(current, cells=tuple(replace(cell, visible=False) if cell.x == 2 else cell
                                              for cell in current.cells))
    else:
        # No fabricated spent counter: consume all six publicly progressing
        # issued moves in a separate synthetic ordinary encounter first.
        policy = TacticalController()
        progress = original
        for _ in range(6):
            move = choose(policy, progress, lambda *_: False)
            assert move.objective == "bounded shooter approach"
            progress = replace(progress, position=destination(progress, move),
                               turn=progress.turn+1, time=progress.time+10)
        policy.observe(progress)
        assert policy.shooter_approach.active.actions == 6
        extra = Monster(999, "orc", progress.position[0]-1, progress.position[1])
        interrupted = replace(progress, monsters=(extra, *progress.monsters))
        policy.routine(interrupted, Action("attack", "h", objective="combat"))
        current = replace(progress, turn=progress.turn+1, time=progress.time+10,
                          monsters=(replace(target, x=target.x+1),), messages=("You kill the orc!",))
    action = choose(policy, current, lambda *_: False)
    assert action is None or action.objective != "bounded shooter approach"
    assert policy.shooter_approach.active is None
    if exhausted == "hp":
        assert (action.kind, action.value) == ("read", "r")
    restored = replace(current, hp=original.hp, statuses=(), inventory=(),
                       turn=current.turn+1, time=current.time+10,
                       monsters=(replace(original.hostiles[0], id=1001),), cells=original.cells)
    action = choose(policy, restored, lambda *_: False)
    assert action is None or action.objective != "bounded shooter approach"


def test_captured_clockroach_sandblast_is_bounded_but_requires_an_executable_close_not_ranged_damage():
    obs = recorded_interruption("clockroach_terminal")
    assert (obs.turn, obs.hp, obs.hp_max) == (3398, 44, 51)
    target = obs.hostiles[0]
    assert target.name == "clockroach" and target.distance(obs.position) == 3
    assert tuple(target.spells) == ("Sandblast",)
    assert ("Sandblast", 4) in map(tuple, target.spell_ranges)
    assert "Sandblast: (3d7)" in target.description
    assert any(status.startswith("Damage-immune at range:") for status in target.statuses)
    assert BoundedShooterApproach.bounded_target(target)
    action = choose(TacticalController(), obs)
    assert action.objective == "bounded shooter approach"
    assert_legal_advance_or_real_defence(obs, action)
    assert action.assessment["shooter_approach"]["remaining_steps"] == 2
    assert action.assessment["shooter_approach"]["action_limit"] == 6
    assert action.kind == "move"


@pytest.mark.parametrize("change", ["unknown_damage", "heavy_shot", "extra_control", "poison"])
def test_sandblast_admission_does_not_admit_unknown_damage_or_unbounded_secondary_effects(change):
    obs = recorded_interruption("clockroach_terminal")
    target = obs.hostiles[0]
    if change == "unknown_damage":
        target = replace(target, description=target.description.replace("Sandblast: (3d7)", "Sandblast: unknown"))
    elif change == "heavy_shot":
        target = replace(target, description=target.description.replace("Sandblast: (3d7)", "Sandblast: (3d9)"))
    elif change == "extra_control":
        target = replace(target, spells=(*target.spells, "Slow"))
    else:
        target = replace(target, spells=("Sting",))
    assert not BoundedShooterApproach.bounded_target(target)
