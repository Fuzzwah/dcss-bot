"""Consumer regressions cropped from development5 public encounter traces."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def recorded():
    data = json.loads((Path(__file__).parent / "fixtures" / "recorded_development5_pauses.json").read_text())

    def observation(values):
        for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                               ("abilities", Ability), ("resistances", Resistance),
                               ("skills", Skill), ("status_details", Status)):
            values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
        values["position"] = tuple(values["position"])
        for field in ("messages", "statuses"):
            values[field] = tuple(values[field])
        return Observation(**values)

    return ({name: observation(values) for name, values in data["observations"].items()},
            tuple(observation(values) for values in data["wizard_transition"]),
            observation(data["unwitnessed_reach"]))


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return controller.decide(obs, cells, Counter({position: 2 for position in cells}), RuleBasedAgent._covered)


def destination(obs, action):
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    return obs.position[0] + dx, obs.position[1] + dy


def clear_destination(obs, action):
    target = destination(obs, action)
    assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud
               for cell in obs.cells)
    assert all((monster.x, monster.y) != target for monster in obs.monsters)
    return target


def established_encounter(obs):
    controller = TacticalController()
    controller.encounter_start = obs.turn - 8
    controller.encounter_last_seen = obs.turn
    controller.ineffective_retreats = controller.config.ineffective_retreat_limit
    controller.lure_steps = 2
    return controller


def test_recorded_injured_lone_slow_shooter_can_be_closed_without_waiting_under_fire():
    observations, _, _ = recorded()
    obs = observations["lone_dart_slug"]
    controller = established_encounter(obs)
    action = choose(controller, obs)
    target = clear_destination(obs, action)
    slug, = obs.hostiles
    assert .5 <= obs.hp_ratio < .75 and slug.distance(obs.position) == 3
    assert slug.distance(target) < slug.distance(obs.position)
    assert any(threat["ranged"] for threat in action.assessment["assessment"]["threats"])


def test_recorded_iguana_and_contact_freeze_can_approach_the_defended_corridor():
    observations, _, _ = recorded()
    obs = observations["iguana_endoplasm"]
    action = choose(established_encounter(obs), obs)
    assert action.kind == "wait"
    assert all(not threat["ranged"] for threat in action.assessment["assessment"]["threats"])
    assert action.assessment["assessment"]["simultaneous_attackers"] == 0


def test_recorded_full_hp_orc_priest_group_has_a_bounded_cover_route_before_contact():
    observations, _, _ = recorded()
    obs = observations["orc_priest_group"]
    controller = established_encounter(obs)
    action = choose(controller, obs)
    target = clear_destination(obs, action)
    assert obs.hp == obs.hp_max
    assert all(enemy.distance(target) >= min(3, enemy.distance(obs.position)) for enemy in obs.hostiles)
    assert controller.lure_steps <= controller.config.lure_step_limit
    # The exception is for a nearby cover/range endpoint, not endless retreat.
    exhausted = established_encounter(obs)
    exhausted.lure_steps = exhausted.config.lure_step_limit
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    choose(exhausted, obs)
    assert exhausted._corner_step(obs, cells, Counter(), RuleBasedAgent._covered,
                                  exhausted.latest_assessment) is None


@pytest.mark.parametrize("name", ["slowed_reaching_dead_end", "injured_reaching_pair"])
def test_recorded_no_credible_option_does_not_become_a_reach_charge_or_unsafe_wait(name):
    observations, _, _ = recorded()
    obs = observations[name]
    action = choose(established_encounter(obs), obs)
    assert action.kind == "pause"
    assert action.assessment["category"] == "emergency"
    assert action.assessment["assessment"]["simultaneous_attackers"] > 0
    assert not any(candidate["kind"] == "wait" for candidate in action.assessment["candidates"])


def test_public_halberd_reach_prevents_the_recorded_free_hit_before_first_from_afar_message():
    _, _, obs = recorded()
    assert not any("from afar" in message for message in obs.messages)
    action = choose(established_encounter(obs), obs)
    assert action.kind not in {"wait", "ability"}
    assert action.kind != "move" or destination(obs, action) != (1, 0)
    assert action.assessment["assessment"]["simultaneous_attackers"] == 1






def test_berserk_hp_scale_change_is_not_inferred_as_a_new_invisible_attack():
    _, transition, _ = recorded()
    base = replace(transition[1], hp=45, hp_max=46, statuses=("Berserk",),
                   messages=("Something hits you.",))
    controller = TacticalController()
    controller.routine(base, Action("wait"))
    expired = replace(base, turn=base.turn + 1, hp=30, hp_max=31, statuses=("Slow", "-Berserk"))
    choose(controller, expired)
    assert controller.latest_assessment.recent_damage == 0
    assert controller.unseen_harm is None


@pytest.mark.parametrize("spell,attack_range", [("Sting", 4), ("Throw Frost", 6), ("Slug Dart", 7)])
def test_tactical_range_boundary_uses_candidate_position_and_native_short_range(spell, attack_range):
    enemy = Monster(1, "dart slug", attack_range + 1, 0, spells=(spell,))
    obs = Observation(species="Minotaur", hp=100, hp_max=100, ac=0, ev=8,
                      position=(0, 0), monsters=(enemy,), attack_delay=1.)
    controller = TacticalController()
    outside = controller.assess(obs, {}, lambda *_: False)
    inside = controller.assess(obs, {}, lambda *_: False, position=(1, 0))
    far = controller.assess(obs, {}, lambda *_: False, position=(-4, 0))
    assert outside.threats[0].ranged and inside.threats[0].ranged
    assert 0 < outside.expected_loss < inside.expected_loss
    assert not far.threats[0].ranged and far.expected_loss == 0
    covered = controller.assess(obs, {}, lambda *_: True, position=(1, 0))
    assert not covered.threats[0].ranged






def test_unseen_last_seen_evidence_rebases_only_with_its_own_level_and_clears_on_unproved_origin():
    _, transition, _ = recorded()
    visible, vanished, _, _ = transition
    controller = TacticalController()
    controller.routine(visible, Action("move", "b", objective="combat"))
    choose(controller, vanished)
    contact = controller.unseen_contact
    controller.rebase("Dungeon:99", (10, 10))
    assert controller.unseen_contact == contact
    controller.rebase(vanished.location, (10, 10))
    enemy, seen = controller.unseen_contact
    assert (enemy.x, enemy.y, seen) == (contact[0].x+10, contact[0].y+10, contact[1])
    controller.rebase(vanished.location, None)
    assert controller.unseen_contact is None and controller.unseen_harm is None


def test_known_escape_preempts_last_seen_invisible_bump_and_pending_teleport_is_not_restarted():
    _, transition, _ = recorded()
    visible, vanished, _, _ = transition
    vanished = replace(vanished, inventory=vanished.inventory + (Item(50, "z", "scroll of teleportation"),))
    controller = TacticalController()
    controller.routine(visible, Action("move", "b", objective="combat"))
    action = choose(controller, vanished)
    assert (action.kind, action.value) == ("read", "z")
    pending = replace(vanished, turn=vanished.turn+1, statuses=("Tele",))
    action = choose(controller, pending)
    assert (action.kind, action.value) != ("read", "z")


def test_deadly_slug_shot_uses_identified_healing_instead_of_the_new_closing_candidate():
    observations, _, _ = recorded()
    obs = replace(observations["lone_dart_slug"],hp=6,
                  inventory=(Item(100,"z","potion of heal wounds",category="potion"),))
    action = choose(established_encounter(obs),obs)
    assert (action.kind,action.value) == ("quaff","z")


def test_closing_below_half_hp_uses_actual_single_step_exposure_not_an_hp_ratio_gate():
    observations, _, _ = recorded()
    obs = replace(observations["lone_dart_slug"],hp=12)
    action = choose(established_encounter(obs),obs)
    target = clear_destination(obs,action)
    assert obs.hp_ratio < .5
    assert obs.hostiles[0].distance(target) < obs.hostiles[0].distance(obs.position)
