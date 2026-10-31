"""Actual failures: unfinished rage, premature exploration, and unseen magic."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalConfig, TacticalController, WithdrawalGoal
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import CLOSED_DOORS


def data():
    return json.loads((Path(__file__).parent / "fixtures" / "recorded_development8_encounters.json").read_text())


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
    return observation(data()["observations"][name])


def choose(controller, obs, visits=None):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    if visits is None:
        visits = Counter({position: 2 for position in cells})
    return controller.decide(obs, cells, visits, RuleBasedAgent._covered)


def clear_destination(obs, action):
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    target = obs.position[0] + dx, obs.position[1] + dy
    assert any((cell.x, cell.y) == target and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)
    assert all((enemy.x, enemy.y) != target for enemy in obs.monsters)
    return target


def prepared(obs, config=None):
    agent = RuleBasedAgent(tactical_config=config)
    equipment = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipment)
    agent.development._last_skill_inspection = obs.turn
    agent.previous_messages = obs.messages
    return agent


def test_raging_healthy_character_does_not_freeze_before_a_weak_two_contact_pack():
    obs = captured("rage_pack")
    controller = TacticalController()
    action = choose(controller, obs)
    target = clear_destination(obs, action)
    assert action.objective == "combat"
    assert 1 <= sum(enemy.distance(target) <= 1 for enemy in obs.hostiles) <= 2
    assert min(enemy.distance(target) for enemy in obs.hostiles) < min(enemy.distance(obs.position) for enemy in obs.hostiles)


def test_native_thin_air_dart_damage_interrupts_rage_recovery_and_withdraws_on_known_ground():
    before, after = captured("invisible_before"), captured("invisible_after")
    controller = TacticalController()
    controller.routine(before, Action("wait", objective="recover"))
    action = choose(controller, after)
    clear_destination(after, action)
    assert action.objective == "planned retreat"
    plan = action.assessment["moving_withdrawal"]
    assert plan["expected_loss"] >= (before.hp-after.hp)*plan["horizon"]
    assert action.assessment["assessment"]["threats"] == ()  # No invented hidden coordinate.


def test_unseen_damage_is_not_ignored_when_a_visible_trivial_enemy_also_exists():
    before = captured("invisible_before")
    cells = tuple(Cell(x,0,46 if x == -6 else 33,visible=True) for x in range(-6,7))
    rat = Monster(1,"rat",5,0,0,description="Attack  Max Damage\nBite  3\n")
    before = replace(before,hp=50,hp_max=50,statuses=(),status_details=(),position=(0,0),
                     monsters=(rat,),cells=cells,inventory=(),abilities=())
    after = replace(before,turn=before.turn+1,hp=43,messages=captured("invisible_after").messages)
    controller = TacticalController()
    controller.routine(before,Action("wait"))
    action = choose(controller,after)
    target = clear_destination(after,action)
    assert action.objective == "planned retreat" and target[0] < 0
    assert action.assessment["moving_withdrawal"]["expected_loss"] >= 7


async def test_cooldown_only_recovery_precedes_new_exploration_then_ends_when_ready():
    obs = captured("cooldown_clear_ground")
    agent = prepared(obs)
    action = await agent.decide(obs)
    assert action.objective == "recover" and action.kind not in {"move", "explore"}
    ready = replace(obs,turn=obs.turn+1,statuses=(),status_details=())
    action = await agent.decide(ready)
    assert action.objective != "recover" and action.kind not in {"wait", "rest", "pause"}


async def test_stale_cooldown_has_a_finite_bound_instead_of_an_infinite_rest():
    obs = captured("cooldown_clear_ground")
    agent = prepared(obs,TacticalConfig(berserk_recovery_limit=3))
    assert (await agent.decide(obs)).kind != "pause"
    assert (await agent.decide(replace(obs,turn=obs.turn+2))).kind != "pause"
    assert (await agent.decide(replace(obs,turn=obs.turn+3))).kind == "pause"


async def test_visible_combat_preempts_quiet_cooldown_recovery():
    obs = captured("cooldown_clear_ground")
    cell = next(cell for cell in obs.cells if cell.visible and cell.walkable and not cell.cloud
                and cell.feature not in CLOSED_DOORS
                and max(abs(cell.x-obs.position[0]),abs(cell.y-obs.position[1])) == 1)
    enemy = Monster(999,"rat",cell.x,cell.y,0,description="Attack  Max Damage\nBite  3\n")
    obs = replace(obs,monsters=(enemy,))
    action = await prepared(obs).decide(obs)
    assert action.kind == "move"
    dx,dy = DIRECTIONS[action.value]
    assert (obs.position[0]+dx,obs.position[1]+dy) == (enemy.x,enemy.y)


def test_reaching_cover_continues_the_abandoned_encounter_to_known_upstairs():
    values = data()
    before, after = observation(values["observations"]["cover_before"]), observation(values["observations"]["cover_after"])
    goal = dict(values["cover_goal"])
    goal["position"] = tuple(goal["position"])
    controller = TacticalController()
    controller.encounter_start = before.turn-2
    controller.encounter_last_seen = before.turn
    controller.routine(before,Action("move","h",objective="planned retreat"))
    controller.withdrawal = WithdrawalGoal(**goal)
    visits = Counter({tuple(position):1 for position in values["cover_visited_positions"]})
    action = choose(controller,after,visits)
    clear_destination(after,action)
    assert action.objective == "planned retreat"
    assert action.assessment["moving_withdrawal"]["goal_kind"] == "upstairs"


def test_complete_public_spell_data_replaces_an_unclassified_creatures_native_threat_guess():
    enemy = Monster(1,"unclassified caster",4,0,3,spells=("Magic Dart","Slow"),
                    description="Attack  Max Damage\nHit  6\nSpells:\nMagic Dart: (3d4)\nSlow: (25%)\n")
    info = mechanics(enemy)
    assert info.ranged_max == 12 and info.ranged_mean == 7.5
    assert info.disables and info.will_check


def test_will_defence_is_prepared_before_exposing_the_next_retreat_step():
    obs = captured("cover_before")
    obs = replace(obs,statuses=(),status_details=(),will=0,inventory=(),
                  abilities=(Ability("Trog's Hand","b",True,0),))
    action = choose(TacticalController(),obs)
    assert (action.kind,action.value) == ("ability","trogs_hand")


def test_vanishing_without_hp_loss_keeps_the_threat_and_does_not_mislabel_a_bump_as_escape():
    raw = json.loads((Path(__file__).parent / "fixtures" / "recorded_development5_pauses.json").read_text())
    visible, vanished = (observation(value) for value in raw["wizard_transition"][:2])
    vanished = replace(vanished,hp=visible.hp,hp_max=visible.hp_max)
    controller = TacticalController()
    controller.routine(visible,Action("move","b",objective="combat"))
    action = choose(controller,vanished)
    assert action.kind == "move"
    dx,dy = DIRECTIONS[action.value]
    target = vanished.position[0]+dx,vanished.position[1]+dy
    enemy, = visible.hostiles
    if action.objective == "planned retreat":
        assert target != (enemy.x,enemy.y)
    else:
        assert action.objective == "defend unseen" and target == (enemy.x,enemy.y)
    assert action.assessment["assessment"]["recent_damage"] == 0
    assert action.assessment["assessment"]["threats"] == ()


def test_recorded_invisible_damage_keeps_defence_active_without_inventing_a_current_monster():
    raw = json.loads((Path(__file__).parent / "fixtures" / "recorded_development5_pauses.json").read_text())
    visible, *transitions = (observation(value) for value in raw["wizard_transition"])
    controller = TacticalController()
    controller.routine(visible,Action("move","b",objective="combat"))
    for obs in transitions:
        action = choose(controller,obs)
        assert action.kind == ("attack" if action.objective == "defend unseen" else "move")
        assert action.objective in {"defend unseen","retreat","planned retreat"}
        dx,dy = DIRECTIONS[action.value]
        target = obs.position[0]+dx,obs.position[1]+dy
        assert any((cell.x,cell.y)==target and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)
        assert action.assessment["assessment"]["recent_damage"] > 0
        assert action.assessment["assessment"]["threats"] == ()
    assert controller.unseen_harm[1] == transitions[-1].turn


def test_free_observation_cannot_refresh_unseen_damage_but_expiry_does_not_cancel_a_real_escape_goal():
    raw = json.loads((Path(__file__).parent / "fixtures" / "recorded_development5_pauses.json").read_text())
    visible, vanished = (observation(value) for value in raw["wizard_transition"][:2])
    controller = TacticalController()
    controller.routine(visible,Action("move","b",objective="combat"))
    choose(controller,vanished)
    harm = controller.unseen_harm
    action = choose(controller,vanished)
    assert controller.unseen_harm == harm
    later = replace(vanished,turn=vanished.turn+4)
    action = choose(controller,later)
    clear_destination(later,action)
    assert controller.unseen_harm is None
    assert action.objective == "planned retreat"
    assert action.assessment["assessment"]["recent_damage"] == 0


def test_sigmunds_pinned_generated_scythe_is_not_assumed_adjacent_only_before_inspection():
    obs = Observation(hp=39,hp_max=39,position=(0,0),
                      monsters=(Monster(1,"Sigmund",2,0,3),))
    controller = TacticalController()
    risk = controller.assess(obs,{},lambda *_:False)
    assert risk.simultaneous_attackers == 1
    assert mechanics(obs.hostiles[0]).reach == 2
