"""Development15 seed5 public boundaries, not simulated counterfactual outcomes."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development15_committed_caster.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def capture():
    return json.loads(FIXTURE.read_text())


def boundary(sequence=None):
    data = capture()
    if sequence is None:
        return observation(data["paused_observation"])
    return observation(next(frame["observation"] for frame in data["frames"]
                            if frame["sequence"] == sequence))


def recorded_history_before(sequence=None):
    """Feed only actual old actions/outcomes, never responses to changed choices."""
    controller = TacticalController()
    for frame in capture()["frames"]:
        if sequence is not None and frame["sequence"] >= sequence:
            break
        controller.routine(observation(frame["observation"]), Action(**frame["recorded_action"]))
    return controller


def choose(obs, controller=None):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    visits = Counter({position: 2 for position in cells})
    return (controller or TacticalController()).decide(obs, cells, visits, RuleBasedAgent._covered)


def attacks(obs, action):
    if action.kind not in {"move", "attack"} or action.value not in DIRECTIONS:
        return False
    dx, dy = DIRECTIONS[action.value]
    target = obs.position[0] + dx, obs.position[1] + dy
    return any((enemy.x, enemy.y) == target for enemy in obs.hostiles)


def attacking_candidates(obs, action):
    return [row for row in action.assessment["candidates"]
            if attacks(obs, Action(row["kind"], row["value"]))]


def test_capture_preserves_the_whole_commitment_and_menkaure_was_not_known_early():
    data = capture()
    assert [frame["sequence"] for frame in data["frames"]] == list(range(2652, 2663))
    commit = boundary(2653)
    assert commit.turn == 2508 and commit.position == (9, 1)
    assert [enemy.name for enemy in commit.hostiles] == ["black bear"]
    assert commit.hostiles[0].description is not None
    first = boundary(2655)
    menkaure = next(enemy for enemy in first.hostiles if enemy.name == "Menkaure")
    assert first.turn == 2510 and first.statuses == ("Berserk",)
    assert menkaure.description is None and menkaure.distance(first.position) == 7
    assert next(enemy for enemy in boundary(2656).hostiles if enemy.name == "Menkaure").description
    paused = boundary()
    assert (paused.turn, paused.xl, paused.hp, paused.hp_max, paused.position) == (2516, 5, 35, 67, (5, 1))


def test_captured_berserker_rage_is_self_enchantment_not_a_black_bear_projectile():
    bear = boundary(2653).hostiles[0]
    assert bear.spells == ["Berserker Rage"] or bear.spells == ("Berserker Rage",)
    info = mechanics(bear)
    assert info.escalates and info.melee == 19
    assert not info.ranged
    action = choose(boundary(2653))
    threat = action.assessment["assessment"]["threats"][0]
    assert not threat["ranged"]


def test_first_captured_backpedal_can_instead_strike_the_almost_dead_adjacent_bear():
    obs = boundary(2656)
    action = choose(obs, recorded_history_before(2656))
    assert (action.kind, action.value, action.objective) == ("move", "l", "combat")
    assert attacks(obs, action)
    menkaure = next(row for row in action.assessment["assessment"]["threats"] if row["name"] == "Menkaure")
    assert menkaure["avoid_melee"] and not menkaure["melee_prohibited"]
    assert not any(row["kind"] in {"quaff", "read", "ability", "blink", "zap", "throw"}
                   for row in action.assessment["candidates"])


@pytest.mark.parametrize("sequence", [2661, 2662])
def test_captured_existing_rage_can_close_on_the_examined_caster(sequence):
    obs = boundary(sequence)
    action = choose(obs, recorded_history_before(sequence))
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")
    dx, dy = DIRECTIONS[action.value]
    destination = obs.position[0] + dx, obs.position[1] + dy
    cell = next(cell for cell in obs.cells if (cell.x, cell.y) == destination)
    assert cell.visible and cell.walkable and not cell.cloud
    assert obs.hostiles[0].distance(destination) < obs.hostiles[0].distance(obs.position)


@pytest.mark.parametrize("history", [False, True])
def test_captured_adjacent_menkaure_has_a_legal_attack_without_reclassifying_risk(history):
    obs = boundary()
    action = choose(obs, recorded_history_before() if history else None)
    assert (action.kind, action.value, action.objective) == ("move", "h", "combat")
    assert attacks(obs, action)
    threat = action.assessment["assessment"]["threats"][0]
    assert threat["avoid_melee"] and not threat["melee_prohibited"]
    assert action.assessment["category"] in {"avoid", "emergency"}
    assert "no kill or survival guarantee" in action.reason


def test_synthetic_ordinary_caster_identity_does_not_change_the_mechanics_decision():
    obs = boundary()
    caster = replace(obs.hostiles[0], name="examined caster")
    action = choose(replace(obs, monsters=(caster,)))
    assert attacks(replace(obs, monsters=(caster,)), action)


def test_synthetic_unexamined_menkaure_is_read_not_granted_a_name_only_rage_bypass():
    obs = boundary()
    enemy = replace(obs.hostiles[0], description=None, spells=(), spell_ranges=())
    obs = replace(obs, monsters=(enemy,))
    action = choose(obs)
    assert (action.kind, action.value) == ("inspect_monster", "4,1")
    assert not attacking_candidates(obs, action)


@pytest.mark.parametrize("resource", ["healing", "teleport"])
def test_synthetic_uncommitted_caster_keeps_early_resource_priority_and_refuses_rage(resource):
    obs = boundary()
    name = "potion of heal wounds" if resource == "healing" else "scroll of teleportation"
    obs = replace(obs, statuses=(), hp=20,
                  inventory=(*obs.inventory, Item(25, "z", name)),
                  abilities=(*obs.abilities, Ability("Berserk", "a", True)))
    action = choose(obs)
    assert (action.kind, action.value) == ("quaff" if resource == "healing" else "read", "z")
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])
    assert not attacking_candidates(obs, action)


@pytest.mark.parametrize("victim", ["five-headed hydra", "large slime creature", "distortion caster", "chaos caster"])
@pytest.mark.parametrize("cleave", [False, True])
def test_synthetic_hard_melee_prohibitions_survive_rage_for_target_and_axe_cleave(victim, cleave):
    obs = boundary()
    caster = obs.hostiles[0]
    extra = {"equipment": ("a +0 dagger of distortion",)} if victim == "distortion caster" else (
        {"equipment": ("a +0 dagger of chaos",)} if victim == "chaos caster" else {})
    enemy = replace(caster, name=victim, **extra)
    monsters = (enemy,)
    if cleave:
        enemy = replace(enemy, y=2)
        monsters = (replace(caster, id=131, name="orc", spells=(), spell_ranges=()), enemy)
    obs = replace(obs, monsters=monsters)
    action = choose(obs)
    assert not attacking_candidates(obs, action)
    assert not attacks(obs, action)
    assert any(row["melee_prohibited"] for row in action.assessment["assessment"]["threats"])


def test_synthetic_two_unmerged_slimes_still_forbid_cleave_escalation():
    obs = boundary()
    caster = obs.hostiles[0]
    obs = replace(obs, monsters=(replace(caster, name="slime creature"),
                                replace(caster, id=131, name="slime creature", y=2)))
    action = choose(obs)
    assert not attacking_candidates(obs, action)
    assert all(row["melee_prohibited"] for row in action.assessment["assessment"]["threats"])


@pytest.mark.parametrize("name,spell", [("Sigmund", "Confuse"), ("Grinder", "Paralyse"),
                                      ("orc sorcerer", "Summon Demon"), ("hornet", "Paralyse")])
def test_synthetic_named_control_and_summoning_encounters_do_not_inherit_the_caster_exception(name, spell):
    obs = boundary()
    enemy = replace(obs.hostiles[0], name=name, spells=(spell,))
    obs = replace(obs, monsters=(enemy,), will=5)
    action = choose(obs)
    assert not attacking_candidates(obs, action)
    assert not attacks(obs, action)


def test_synthetic_high_risk_noncasters_do_not_inherit_the_examined_caster_exception():
    obs = boundary()
    enemy = replace(obs.hostiles[0], name="two-headed ogre", spells=(), spell_ranges=())
    obs = replace(obs, monsters=(enemy,))
    action = choose(obs)
    assert not attacking_candidates(obs, action)
    assert not attacks(obs, action)
