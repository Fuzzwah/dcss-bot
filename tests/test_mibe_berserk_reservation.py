"""Berserk is a commitment for difficult fights, not the default melee opener."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def test_two_trivial_lizards_get_one_ordinary_melee_action_without_berserk():
    cells = tuple(Cell(x, y, 33, ".", True) for x in range(-4, 5) for y in range(-3, 4))
    obs = Observation(
        species="Minotaur", god="Trog", place="Dungeon", depth=1, xl=2,
        turn=100, hp=26, hp_max=26, ac=4, ev=11, input_mode=1, cells=cells,
        monsters=tuple(Monster(i + 1, "frilled lizard", 1, i, 0,
                               description="Attacks  Max Damage\nBite:  3\n") for i in range(2)),
        abilities=(Ability("Berserk", "a", True, 0),),
    )
    controller = TacticalController()
    action = controller.decide(obs, {(c.x, c.y): c for c in cells},
                               Counter({(c.x, c.y): 2 for c in cells}), RuleBasedAgent._covered)
    assert action.kind == "move" and action.objective == "combat"
    dx, dy = DIRECTIONS[action.value]
    assert any((enemy.x, enemy.y) == (obs.position[0] + dx, obs.position[1] + dy)
               for enemy in obs.hostiles)
    assert not any((row["kind"], row["value"]) == ("ability", "berserk")
                   for row in action.assessment["candidates"])


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return controller.decide(obs, cells, Counter({position: 2 for position in cells}),
                             RuleBasedAgent._covered)


def captured_snake_encounter():
    path = Path(__file__).parent / "fixtures" / "recorded_berserk_snake.json"
    records = json.loads(path.read_text())["records"]
    observations = []
    for record in records:
        values = dict(record["observation"])
        for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                               ("abilities", Ability), ("resistances", Resistance),
                               ("skills", Skill), ("status_details", Status)):
            values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
        values["position"] = tuple(values["position"])
        for field in ("messages", "statuses"):
            values[field] = tuple(values[field])
        observations.append(Observation(**values))
    controller = TacticalController()
    for obs, record in zip(observations[:-1], records[:-1]):
        controller.routine(obs, Action(**record["action"]))
    return controller, observations[-1]


def assert_no_resource_commitment(action):
    assert action.kind in {"move", "wait"}
    assert all(row["resource"] == "none" for row in action.assessment["candidates"])


def test_captured_full_health_high_threat_adder_gets_normal_defence_after_pursuit():
    controller, obs = captured_snake_encounter()
    enemy, = obs.hostiles
    assert obs.turn == 1204 and obs.hp == obs.hp_max == 26
    assert enemy.name == "adder" and enemy.threat == 2 and enemy.distance(obs.position) == 2
    assert "Bite    4" in enemy.description
    action = choose(controller, obs)
    assert_no_resource_commitment(action)
    assert controller.latest_assessment.recent_damage == 0
    assert action.assessment["policy_state"]["ineffective_retreats"] >= 2
    if action.kind == "move":
        dx, dy = DIRECTIONS[action.value]
        target = obs.position[0] + dx, obs.position[1] + dy
        assert any((cell.x, cell.y) == target and cell.visible and cell.walkable
                   and not cell.cloud for cell in obs.cells)
        assert action.objective in {"combat", "retreat", "planned retreat"}
    else:
        assert action.objective in {"defend choke", "lure"}


def test_captured_snake_at_contact_gets_ordinary_melee_not_a_replacement_resource():
    controller, obs = captured_snake_encounter()
    # Synthetic next boundary: the same publicly examined snake reaches contact;
    # no damage, poison or other status has been observed.
    enemy = replace(obs.hostiles[0], x=obs.position[0] + 1, y=obs.position[1])
    obs = replace(obs, turn=obs.turn + 1, monsters=(enemy,))
    action = choose(controller, obs)
    assert_no_resource_commitment(action)
    assert action.kind == "move" and action.objective == "combat"
    assert DIRECTIONS[action.value] == (1, 0)


def bounded_melee(*, group=False):
    cells = tuple(Cell(x, y, 33, ".", True) for x in range(-4, 5) for y in range(-3, 4))
    # Synthetic displayed attack tables: the native Low labels cannot erase a
    # substantial strike, or two smaller strikes that form a heavy melee group.
    enemies = (Monster(1, "gnoll", 1, 0, 0,
                       description="Max HP: ~13\nAttack  Max Damage\nHit  8\n"),
               Monster(2, "gnoll", 1, 1, 0,
                       description="Max HP: ~13\nAttack  Max Damage\nHit  8\n")) if group else (
        Monster(1, "iguana", 1, 0, 0,
                description="Max HP: ~18\nAttack  Max Damage\nBite  14\n"),)
    obs = Observation(
        species="Minotaur", god="Trog", place="Dungeon", depth=2, xl=2,
        turn=100, hp=26, hp_max=26, ac=5, ev=10, input_mode=1,
        cells=cells, monsters=enemies, abilities=(Ability("Berserk", "a", True, 0),),
    )
    controller = TacticalController()
    controller.encounter_start = obs.turn - 4
    controller.encounter_last_seen = obs.turn
    controller.ineffective_retreats = controller.config.ineffective_retreat_limit
    return controller, obs


@pytest.mark.parametrize("group", [False, True])
def test_public_attack_or_group_pressure_still_justifies_early_rage_at_full_health(group):
    controller, obs = bounded_melee(group=group)
    action = choose(controller, obs)
    assert obs.hp == obs.hp_max
    assert (action.kind, action.value) == ("ability", "berserk")


@pytest.mark.parametrize("status", ["Pois", "Slow", "Exh", "-Berserk"])
def test_substantial_public_damage_never_bypasses_rage_aftermath_restrictions(status):
    controller, obs = bounded_melee()
    action = choose(controller, replace(obs, statuses=(status,)))
    assert not any((row["kind"], row["value"]) == ("ability", "berserk")
                   for row in action.assessment["candidates"])


def test_significant_observed_damage_can_justify_rage_against_an_otherwise_weak_duellist():
    controller, obs = bounded_melee()
    enemy = replace(obs.hostiles[0], name="orc", threat=2,
                    description="Max HP: ~9\nAttack  Max Damage\nHit  4\n")
    before = replace(obs, monsters=(enemy,))
    assert_no_resource_commitment(choose(controller, before))
    damaged = replace(before, turn=before.turn + 1, hp=20, messages=("The orc hits you.",))
    action = choose(controller, damaged)
    assert controller.latest_assessment.recent_damage == 6
    assert (action.kind, action.value) == ("ability", "berserk")
