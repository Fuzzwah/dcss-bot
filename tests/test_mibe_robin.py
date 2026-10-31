"""Development16 seed3 public decisions, not counterfactual survival claims."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development16_robin.json"


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


def boundary(sequence):
    return observation(next(frame["observation"] for frame in capture()["frames"]
                            if frame["sequence"] == sequence))


def recorded_history_before(sequence):
    """Only historical actions receive historical responses; never roll out a new choice."""
    controller = TacticalController()
    visits = Counter()
    previous = None
    for frame in capture()["frames"]:
        if frame["sequence"] >= sequence:
            break
        obs = observation(frame["observation"])
        if previous is None or (obs.location, obs.turn, obs.position) != previous:
            visits[obs.position] += 1
        previous = obs.location, obs.turn, obs.position
        controller.routine(obs, Action(**frame["recorded_action"]))
    return controller, visits


def choose(obs, controller, visits):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return controller.decide(obs, cells, visits, RuleBasedAgent._covered)


def test_real_public_multiarrival_has_a_legal_bounded_hold_instead_of_walking_into_three_contacts():
    obs = boundary(2127)
    controller, visits = recorded_history_before(2127)
    # The east step is legal ground, but makes three ALREADY PUBLIC bodies able
    # to reach adjacency in one normal movement reaction. Do not use Sigmund or
    # Robin, neither of whom had been seen at this decision.
    east = (1, 13)
    arriving = [enemy for enemy in obs.hostiles if enemy.distance(east) == 2]
    assert sorted(enemy.name for enemy in arriving) == ["frilled lizard", "goblin", "hobgoblin"]
    cell = next(cell for cell in obs.cells if (cell.x, cell.y) == obs.position)
    assert cell.visible and cell.walkable and not cell.cloud
    action = choose(obs, controller, visits)
    assert (action.kind, action.value, action.objective) == ("wait", "", "defend choke")
    assert action.assessment["assessment"]["category"] != "safe"
    # This is one credible formation-preserving action, not an assertion that
    # waiting wins this encounter or that the recorded east-step response recurs.


@pytest.mark.parametrize("hazard", ["lethal projectile", "hard disable", "lethal poison", "unresolved unique"])
def test_multiarrival_hold_keeps_real_danger_and_unresolved_enemy_bounds(hazard):
    obs = boundary(2127)
    controller, visits = recorded_history_before(2127)
    if hazard == "lethal projectile":
        enemies = tuple(replace(enemy, description="Attacks  Max Damage\nHit: +0 club  6\nThrow: stone  60\n")
                        if enemy.name == "kobold" else enemy for enemy in obs.monsters)
        obs = replace(obs, monsters=enemies)
    elif hazard == "hard disable":
        enemies = tuple(replace(enemy, spells=("Paralyse",),
                                description=(enemy.description or "") + "\nSpells:\nParalyse: \n")
                        if enemy.name == "frilled lizard" else enemy for enemy in obs.monsters)
        obs = replace(obs, monsters=enemies)
    elif hazard == "lethal poison":
        obs = replace(obs, statuses=("Pois",), poison_survival=1)
    else:
        controller.recent_hostiles["sigmund"] = (1, obs.turn)
    action = choose(obs, controller, visits)
    assert (action.kind, action.objective) != ("wait", "defend choke")


def test_examined_robin_throw_is_low_direct_damage_and_ally_displacement_not_its_mace():
    obs = boundary(2146)
    robin = next(enemy for enemy in obs.hostiles if enemy.name == "Robin")
    info = mechanics(robin)
    assert info.melee == 9
    assert info.ranged and info.ranged_max == 3 and info.ranged_mean == 1.5
    assert info.ally_reposition and info.ally_might
    assert not info.summons and not info.disables
    # These are capabilities; the capture does not prove a current buff or
    # permit inserting a hidden companion or a guessed throw destination.
    assert all(not enemy.statuses for enemy in obs.hostiles)


def test_battlecry_is_might_not_a_direct_projectile_or_haste():
    enemy = Monster(1, "examined leader", 4, 0, description="Attack  Max Damage\nHit  8\n",
                    spells=("Battlecry",))
    info = mechanics(enemy)
    assert info.ally_might and not info.ally_reposition
    assert not info.ranged and info.speed == info.movement_speed == 1
    assert not info.escalates


def test_actual_public_might_table_is_not_multiplied_twice_or_treated_as_haste():
    obs = boundary(2159)
    goblin = next(enemy for enemy in obs.hostiles if enemy.statuses)
    assert "Strong:" in goblin.statuses
    assert "6 (base 4)" in goblin.description
    info = mechanics(goblin)
    assert info.melee == 6
    assert info.speed == info.movement_speed == 1


def test_unknown_ally_thrower_does_not_inherit_robins_small_fixed_damage_bound():
    enemy = Monster(1, "unclassified thrower", 4, 0, 3,
                    description="Attack  Max Damage\nHit  8\nSpells:\nThrow Ally: \n",
                    spells=("Throw Ally",))
    info = mechanics(enemy)
    assert info.ally_reposition and info.ranged
    assert info.ranged_max > 3


@pytest.mark.parametrize("name,statuses,expected", [
    ("clockroach", (), True),
    ("unclassified body", ("Damage-immune at range:", "   Non-damaging side-effects happen normally."), True),
    ("unclassified body", ("Strong:",), False),
    ("unclassified body", ("Not damage-immune at range",), False),
])
def test_only_native_innate_or_exact_current_public_warding_marks_adjacent_damage(name, statuses, expected):
    info = mechanics(Monster(1, name, 3, 0, statuses=statuses,
                             description="Attack  Max Damage\nBite  4\n"))
    assert info.damage_requires_adjacency is expected
    # This trait says nothing about a foe's offensive control or ranged damage;
    # nor does it turn useful non-damaging player control into an illegal action.
    assert not info.disables and not info.melee_prohibited


def test_public_clockroach_table_keeps_real_body_and_pure_sandblast_bounds():
    # Synthetic transcription of the parent's development16 seed2 examination;
    # damage immunity is separate from both offensive attacks and control.
    enemy = Monster(1, "clockroach", 3, 0, statuses=("Damage-immune at range:",),
                    speed_description="Speed: 120%",
                    description="Max HP: ~11\nAttack  Max Damage\nBite  4\nSpells:\nSandblast: (3d7)",
                    spells=("Sandblast",), spell_ranges=(("Sandblast", 4),))
    info = mechanics(enemy)
    assert info.melee == 4 and info.ranged_max == 21 and info.ranged_range == 4
    assert info.speed == info.movement_speed == 1.2
    assert info.damage_requires_adjacency
    assert not (info.poison or info.disables or info.summons or info.melee_prohibited)


def test_public_warding_does_not_erase_independent_control():
    enemy = Monster(1, "examined warded caster", 3, 0,
                    statuses=("Damage-immune at range:",),
                    description="Attack  Max Damage\nHit  4\n",
                    spells=("Slow",))
    info = mechanics(enemy)
    assert info.damage_requires_adjacency and info.disables and info.will_check


def warded_clockroach_with_items(*items):
    """Native public geometry/enemy, counterfactual identified resources."""
    values = json.loads((FIXTURE.parent / "recorded_development16_shooter_interruption.json").read_text())
    obs = observation(values["clockroach_terminal"])
    return replace(obs, abilities=(), inventory=(obs.inventory[0], *items))


@pytest.mark.parametrize("name,category", [
    ("wand of mindburst (4)", "wand"),
    ("wand of flame (4)", "wand"),
    ("wand of iceblast (4)", "wand"),
    ("javelin", "missile"),
    ("boomerang", "missile"),
    ("poisoned dart", "missile"),
])
def test_range_warded_target_does_not_waste_direct_damage_resources(name, category):
    obs = warded_clockroach_with_items(Item(22, "w", name, category=category))
    action = choose(obs, TacticalController(), Counter({obs.position: 1}))
    assert action.kind == "move"
    from dcss_bot.models import DIRECTIONS
    dx, dy = DIRECTIONS[action.value]
    destination = obs.position[0]+dx, obs.position[1]+dy
    assert obs.hostiles[0].distance(destination) < obs.hostiles[0].distance(obs.position)
    assert any((cell.x, cell.y) == destination and cell.visible and cell.walkable
               and not cell.cloud for cell in obs.cells)


@pytest.mark.parametrize("name", ["wand of roots (4)", "wand of acid (4)"])
def test_range_warding_keeps_available_non_damage_control_when_damage_wand_is_first(name):
    obs = warded_clockroach_with_items(
        Item(22, "w", "wand of mindburst (4)", category="wand"),
        Item(23, "x", name, category="wand"))
    action = choose(obs, TacticalController(), Counter({obs.position: 1}))
    target = obs.hostiles[0]
    assert (action.kind, action.value) == ("zap", f"x:{target.x},{target.y}")


@pytest.mark.parametrize("name", ["curare-tipped dart", "atropa-tipped dart"])
def test_range_warding_keeps_status_missiles(name):
    obs = warded_clockroach_with_items(Item(22, "w", name, category="missile"))
    obs = replace(obs, hp=int(obs.hp_max*.75))
    action = choose(obs, TacticalController(), Counter({obs.position: 1}))
    target = obs.hostiles[0]
    assert (action.kind, action.value) == ("throw", f"w:{target.x},{target.y}")


def test_adjacent_original_source_can_still_use_damage_wand_against_warding():
    obs = warded_clockroach_with_items(Item(22, "w", "wand of mindburst (4)", category="wand"))
    adjacent = next(cell for cell in obs.cells if cell.visible and cell.walkable and not cell.cloud
                    and max(abs(cell.x-obs.position[0]), abs(cell.y-obs.position[1])) == 1)
    target = replace(obs.hostiles[0], x=adjacent.x, y=adjacent.y)
    # Only these two currently visible floor cells are available; a separate
    # credible withdrawal must not mask whether the damage action is legal.
    obs = replace(obs, monsters=(target,), cells=tuple(
        cell for cell in obs.cells if (cell.x,cell.y) in {obs.position,(target.x,target.y)}))
    action = choose(obs, TacticalController(), Counter({obs.position: 1}))
    assert (action.kind, action.value) == ("zap", f"w:{target.x},{target.y}")


def solo_support():
    values = json.loads((FIXTURE.parent / "recorded_development6_pauses.json").read_text())
    return observation(values["observations"]["3"])




@pytest.mark.parametrize("hazard", ["low hp","poison","cooldown","unresolved","companion","hard spell","missing staging"])
def test_support_preparation_does_not_turn_into_a_blind_panic_rage(hazard):
    obs = solo_support()
    controller = TacticalController()
    if hazard=="low hp":
        obs = replace(obs,hp=10)
    elif hazard=="poison":
        obs = replace(obs,statuses=("Pois",),poison_survival=obs.hp-1)
    elif hazard=="cooldown":
        obs = replace(obs,statuses=("-Berserk",))
    elif hazard=="unresolved":
        controller.recent_hostiles["hobgoblin"]=(1,obs.turn)
    elif hazard=="companion":
        obs = replace(obs,monsters=obs.monsters+(Monster(999,"goblin",obs.position[0],obs.position[1]+1),))
    elif hazard=="hard spell":
        obs = replace(obs,monsters=(replace(obs.hostiles[0],spells=(*obs.hostiles[0].spells,"Paralyse")),))
    else:
        enemy = obs.hostiles[0]
        obs = replace(obs,cells=tuple(cell for cell in obs.cells
                       if enemy.distance((cell.x,cell.y))>1 or (cell.x,cell.y)==(enemy.x,enemy.y)))
    action = choose(obs,controller,Counter({obs.position:1}))
    assert (action.kind,action.value)!=("ability","berserk")
