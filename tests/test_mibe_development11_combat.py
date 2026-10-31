"""Fresh source11 failures: native status constraints and attack alternatives."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development11_combat.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def captured(name):
    return observation(json.loads(FIXTURE.read_text())["observations"][name])


def choose(policy, obs):
    return policy.decide(obs,{(c.x,c.y):c for c in obs.cells},Counter(),RuleBasedAgent._covered)


def test_native_confused_rage_uses_one_legal_constrained_wait_not_a_diagnostic_pause():
    obs = captured("forced_confusion")
    policy = TacticalController()
    policy.unseen_harm = obs.location,obs.turn,1
    action = choose(policy,obs)
    assert action.kind == "wait" and action.objective == "recover control"
    assert not obs.hostiles and {"Conf","Berserk"} <= set(obs.statuses)


def test_native_ordinary_javelin_thrower_can_be_closed_on_during_rage_exhaustion():
    obs = captured("exhausted_thrower")
    foe, = obs.hostiles
    info = mechanics(foe)
    assert info.melee == 5 and info.ranged_max == 14
    assert 0 < info.ranged_mean < info.ranged_max
    action = choose(TacticalController(),obs)
    assert action.kind == "move" and action.objective == "combat"
    dx,dy = DIRECTIONS[action.value]
    assert foe.distance((obs.position[0]+dx,obs.position[1]+dy)) == 1


def test_ranged_specialist_keeps_the_adjacent_projectile_threat():
    original = captured("exhausted_thrower")
    specialist = replace(original.hostiles[0],name="deep elf master archer")
    action = choose(TacticalController(),replace(original,monsters=(specialist,)))
    assert action.kind != "move" or specialist.distance((
        original.position[0]+DIRECTIONS[action.value][0],original.position[1]+DIRECTIONS[action.value][1])) != 1


def test_native_small_caster_with_wand_and_bat_cannot_wait_forever_without_approach():
    obs = captured("caster_with_wand")
    policy = TacticalController()
    for _ in range(4):
        action = choose(policy,obs)
        if action.kind != "wait":
            break
        assert action.objective in {"lure","defend choke"}
        # Synthetic stationary follow-ups to the captured native encounter:
        # a bat circling at the same distance cannot replenish the lure budget.
        obs = replace(obs,turn=obs.turn+1,time=obs.time+10)
    assert action.kind == "move" and action.objective in {"combat","retreat","planned retreat"}
    dx,dy = DIRECTIONS[action.value]
    target = obs.position[0]+dx,obs.position[1]+dy
    assert any((c.x,c.y) == target and c.visible and c.walkable and not c.cloud for c in obs.cells)


def test_unlocated_incoming_attacker_survives_named_kills_and_gets_a_nonmoving_search():
    data = json.loads(FIXTURE.read_text())
    policy = TacticalController()
    for row in data["invisible_sequence"]:
        before,after = observation(row["before"]),observation(row["after"])
        policy.routine(before,Action(**row["action"]))
        policy.observe(after)
        if after.turn == 2585:
            assert policy.unseen_harm is not None
        policy.previous = after
    obs = captured("lost_contact")
    policy.ineffective_retreats = 2  # Recorded source11 terminal policy state.
    action = choose(policy,obs)
    assert action.kind == "attack" and action.value in DIRECTIONS and action.objective == "defend unseen"
    assert not obs.hostiles
    dx,dy = DIRECTIONS[action.value]
    assert any((c.x,c.y) == (obs.position[0]+dx,obs.position[1]+dy)
               and c.visible and c.walkable and not c.cloud for c in obs.cells)


def test_contact_damage_spells_are_not_lost_when_projectiles_are_separated():
    foe = Monster(1,"unfamiliar icy creature",1,0,1,
                  description="Attacks  Max Damage\nHit  1\n\nSpells:\nFreeze: 3d5",
                  spells=("Freeze",),spell_ranges=(("Freeze",1),))
    info = mechanics(foe)
    assert info.melee >= 15 and not info.ranged
