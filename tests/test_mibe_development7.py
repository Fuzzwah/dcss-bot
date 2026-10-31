"""Observed damage alternatives, wasted rage, and prematurely abandoned retreats."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from legacy_replay import decide_legacy_tactics

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController, WithdrawalGoal
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def captured(name):
    path = Path(__file__).parent / "fixtures" / "recorded_development7_encounters.json"
    return observation(json.loads(path.read_text())["observations"][name])


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return decide_legacy_tactics(controller, obs, cells, Counter({position: 2 for position in cells}), RuleBasedAgent._covered)


def destination(obs, action):
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    return obs.position[0] + dx, obs.position[1] + dy


def established(obs):
    controller = TacticalController()
    controller.encounter_start = obs.turn - 20
    controller.encounter_last_seen = obs.turn
    controller.ineffective_retreats = controller.config.ineffective_retreat_limit
    controller.lure_steps = controller.config.lure_step_limit
    return controller


def test_native_whip_and_stone_are_alternative_attacks_not_an_eight_damage_projectile():
    obs = captured("slowed_kobold")
    enemy, = obs.hostiles
    info = mechanics(enemy)
    assert info.melee == 6 and info.ranged_max == 2
    action = choose(established(obs), obs)
    target = destination(obs, action)
    assert enemy.distance(target) < enemy.distance(obs.position)
    assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)


def test_slowed_approach_still_refuses_a_plausibly_lethal_projectile():
    obs = captured("slowed_kobold")
    enemy, = obs.hostiles
    # Explicit synthetic corridor: a closing step must still leave a ranged
    # gap. Ordinary throwers cannot make the lethal projectile at adjacency.
    enemy = replace(enemy, x=obs.position[0]+3, y=obs.position[1],
                    description="Attacks  Max Damage\nHit: +0 whip  6\nThrow: stones  20\n")
    cells = tuple(Cell(x, obs.position[1]+dy, 33, visible=True)
                  for x in range(obs.position[0]-2, obs.position[0]+5) for dy in (-1,0,1))
    obs = replace(obs, monsters=(enemy,), cells=cells)
    action = choose(established(obs), obs)
    assert action.kind != "move" or enemy.distance(destination(obs, action)) >= enemy.distance(obs.position)


def test_active_rage_strikes_the_adjacent_ordinary_target_instead_of_backpedalling():
    obs = captured("berserk_kobold")
    enemy, = obs.hostiles
    action = choose(TacticalController(), obs)
    assert destination(obs, action) == (enemy.x, enemy.y)
    assert action.objective == "combat"


def corridor_controller():
    arrival = captured("gnoll_arrival")
    controller = TacticalController()
    controller.observe(arrival)
    controller.routine(arrival, Action("move", "h", objective="retreat"))
    return controller


def test_known_ordinary_wave_in_one_attack_front_uses_rage_before_losing_hp():
    obs = captured("gnoll_corridor")
    action = choose(corridor_controller(), obs)
    assert (action.kind, action.value) == ("ability", "berserk")
    assert obs.hp == obs.hp_max
    assert action.assessment["assessment"]["simultaneous_attackers"] == 1


@pytest.mark.parametrize("hazard", ["poison", "unresolved unique", "extra reaching attacker"])
def test_corridor_commitment_does_not_erase_aftermath_or_additional_threats(hazard):
    obs = captured("gnoll_corridor")
    controller = corridor_controller()
    if hazard == "poison":
        obs = replace(obs, statuses=("Pois",))
    elif hazard == "unresolved unique":
        controller.recent_hostiles["sigmund"] = (1, obs.turn)
    else:
        enemies = list(obs.monsters)
        index = next(i for i, enemy in enumerate(enemies) if enemy.distance(obs.position) == 2)
        enemies[index] = replace(enemies[index], equipment=("+0 spear",))
        obs = replace(obs, monsters=tuple(enemies))
    action = choose(controller, obs)
    assert (action.kind, action.value) != ("ability", "berserk")


def withdrawal_observation():
    cells = tuple(Cell(x, y, feature=46 if x == -6 and y == 0 else 33 if y == 0 else 1,
                       glyph="<" if x == -6 and y == 0 else ".", visible=True)
                  for x in range(-6, 6) for y in range(-1, 2))
    return Observation(turn=100, hp=50, hp_max=60, species="Minotaur", god="Trog", place="Dungeon", depth=4,
                       position=(0, 0), cells=cells, monsters=(Monster(1, "Eustachio", 3, 0, 3,
                       description="An apprentice summoner.", spells=("Summon Small Mammal", "Blink")),))


def test_equal_speed_pursuit_continues_toward_a_real_goal_after_two_nonseparating_steps():
    obs = withdrawal_observation()
    controller = established(obs)
    for _ in range(4):
        action = choose(controller, obs)
        target = destination(obs, action)
        assert action.objective == "planned retreat"
        assert target[0] < obs.position[0]
        assert action.assessment["moving_withdrawal"]["goal"] == (-6, 0)
        assert action.assessment["moving_withdrawal"]["burst_loss"] == 0
        obs = replace(obs, turn=obs.turn + 1, position=target,
                      monsters=tuple(replace(enemy, x=enemy.x - 1) for enemy in obs.monsters))


def test_failed_movement_retires_the_goal_instead_of_disabling_the_cycle_guard():
    obs = withdrawal_observation()
    controller = established(obs)
    action = choose(controller, obs)
    assert action.objective == "planned retreat"
    blocked = replace(obs, turn=obs.turn + 1)
    action = choose(controller, blocked)
    assert action.objective != "planned retreat"
    assert controller.withdrawal is None


def test_public_support_abilities_do_not_acquire_the_casters_weapon_damage():
    enemy = withdrawal_observation().hostiles[0]
    info = mechanics(enemy)
    assert info.summons and not info.ranged
    robin = mechanics(Monster(2, "Robin", 3, 0, 3, spells=("Battlecry", "Throw Ally"),
                              description="Attack  Max Damage\nHit  8\n"))
    assert robin.ranged and robin.melee == 8 and robin.ranged_max == 3
    assert robin.ally_reposition and robin.ally_might


def test_distortion_scythe_damage_does_not_become_the_damage_of_sigmunds_spells():
    enemy, = captured("sigmund_pursuit").hostiles
    info = mechanics(enemy)
    assert info.melee == 43
    assert info.ranged_max == 15
    assert info.ranged_mean == 9


def test_unmodelled_spell_keeps_uncertainty_despite_a_known_weak_projectile():
    enemy = Monster(1, "caster", 4, 0, 3, spells=("Unclassified attack",),
                    description="Attack  Max Damage\nThrow: stone  2\nSpells:\nUnclassified attack: \n")
    info = mechanics(enemy)
    assert info.ranged_max >= 45


def test_actual_long_stair_return_remains_an_actionable_goal_after_reactive_retreat_exhaustion():
    obs = captured("sigmund_pursuit")
    data = json.loads((Path(__file__).parent / "fixtures" / "recorded_development7_encounters.json").read_text())
    visits = Counter({tuple(position): 1 for position in data["sigmund_visited_positions"]})
    controller = established(obs)
    action = controller.decide(obs, {(cell.x,cell.y):cell for cell in obs.cells}, visits, RuleBasedAgent._covered)
    target = destination(obs,action)
    plan = action.assessment["moving_withdrawal"]
    assert action.objective == "planned retreat"
    assert any((cell.x,cell.y) == plan["goal"] and cell.feature in {46,47,48} for cell in obs.cells)
    assert len(plan["path"]) > plan["horizon"]
    assert plan["path"][0] == target
    assert all(enemy.distance(target) > mechanics(enemy).reach for enemy in obs.hostiles)


def test_new_native_identity_refreshes_public_information_without_retiring_the_escape_goal():
    path = Path(__file__).parent / "fixtures" / "recorded_development7_encounters.json"
    data = json.loads(path.read_text())
    previous = observation(data["observations"]["sigmund_before_reappearance"])
    obs = observation(data["observations"]["sigmund_reappearance"])
    goal = dict(data["sigmund_reappearance_goal"])
    goal["position"] = tuple(goal["position"])
    visits = Counter({tuple(position):1 for position in data["sigmund_native_visited_positions"]})
    cells = {(cell.x,cell.y):cell for cell in obs.cells}
    controller = established(obs)
    controller.routine(previous,Action("move","j",objective="planned retreat"))
    controller.withdrawal = WithdrawalGoal(**goal)
    action = controller.decide(obs,cells,visits,RuleBasedAgent._covered)
    assert action.kind == "inspect_monster"
    assert controller.withdrawal is not None
    assert (obs.location,goal["position"]) not in controller.failed_withdrawals
    enemy, = obs.hostiles
    assert action.value == f"{enemy.x},{enemy.y}"
    known, = captured("sigmund_pursuit").hostiles
    refreshed = replace(enemy,description=known.description,equipment=known.equipment,
                        speed_description=known.speed_description,spells=known.spells,
                        spell_ranges=known.spell_ranges)
    action = controller.decide(replace(obs,monsters=(refreshed,)),cells,visits,RuleBasedAgent._covered)
    assert action.objective == "planned retreat"
    target = destination(obs,action)
    assert any((cell.x,cell.y)==target and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)


def test_return_stair_does_not_invent_a_distant_distortion_scythe_hit_before_departure():
    data = json.loads((Path(__file__).parent / "fixtures" / "recorded_development6_pauses.json").read_text())
    departure = observation(data["stair_entry"]["departure"])
    arrival = observation(data["stair_entry"]["arrival"])
    controller = TacticalController()
    controller.routine(departure,Action("stairs_down"))
    controller.observe(arrival)
    controller.routine(arrival,Action("wait"))
    obs = observation(data["stair_emergency"])
    enemy, = captured("sigmund_pursuit").hostiles
    enemy = replace(enemy,x=obs.position[0]+5,y=obs.position[1])
    cells = tuple(Cell(obs.position[0]+x,obs.position[1]+y,47 if x==y==0 else 33,visible=True)
                  for x in range(-6,7) for y in range(-2,3))
    obs = replace(obs,hp=39,hp_max=39,statuses=(),status_details=(),monsters=(enemy,),
                  cells=cells,inventory=(),abilities=(),will=None)
    action = choose(controller,obs)
    assert action.kind == "escape_upstairs"
    assert action.assessment["stair_escape"]["largest_possible_departure_hit"] == 15
    assert mechanics(enemy).melee > obs.hp  # That attack cannot arrive in this tick.
