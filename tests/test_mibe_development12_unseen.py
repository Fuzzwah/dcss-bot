"""Actual quiet pursuit transitions, plus explicitly synthetic signal boundaries."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development12_unseen.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells",Cell),("monsters",Monster),("inventory",Item),
                           ("abilities",Ability),("resistances",Resistance),
                           ("skills",Skill),("status_details",Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field,()))
    for field in ("position","messages","statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def choose(policy, obs):
    return policy.decide(obs,{(c.x,c.y):c for c in obs.cells},Counter(),RuleBasedAgent._covered)


def test_actual_two_quiet_moves_allow_one_observation_wait_not_permanent_unseen_pause():
    data = json.loads(FIXTURE.read_text())
    policy = TacticalController()
    for row in data["quiet_retreat"]:
        before,after = observation(row["before"]),observation(row["after"])
        policy.routine(before,Action(**row["action"]))
        policy.observe(after)
        policy.previous = after
    action = choose(policy,after)
    assert action.kind == "wait" and action.objective == "verify unseen"
    # Synthetic quiet world responses. The short post-contact hold is also
    # legitimate; neither timer may turn stale evidence into a permanent pause.
    quiet = after
    for _ in range(4):
        policy.routine(quiet,action)
        quiet = replace(quiet,turn=quiet.turn+1,time=quiet.time+10)
        action = choose(policy,quiet)
        if action is None:
            break
        assert action.kind == "wait"
    assert action is None
    assert policy.unseen_harm is None


def test_blocked_invisible_attack_is_fresh_contact_even_without_player_hit_feedback():
    raw = json.loads(FIXTURE.read_text())["blocked"]
    before,after = observation(raw["before"]),observation(raw["after"])
    # Synthetic wait response using the actual native shield-block wording.
    after = replace(after,messages=before.messages+("You block something's attack.",))
    policy = TacticalController()
    policy.routine(before,Action("wait"))
    action = choose(policy,after)
    assert action.kind == "attack" and action.value in DIRECTIONS
    assert action.objective == "defend unseen" and policy.unseen_contact is None


def test_fresh_unlocated_attack_can_prepare_hand_at_full_current_hp_without_guessing_a_caster():
    raw = json.loads(FIXTURE.read_text())["blocked"]
    before = observation(raw["before"])
    # Synthetic fresh attack after the native Hand buff has expired. Availability
    # comes from the captured actual ability menu; no monster identity is added.
    before = replace(before,statuses=(),status_details=(),will=0)
    after = replace(before,turn=before.turn+1,time=before.time+10,
                    messages=before.messages+("Something hits you but does no damage.",))
    policy = TacticalController()
    policy.routine(before,Action("wait"))
    action = choose(policy,after)
    assert after.hp == after.hp_max
    assert (action.kind,action.value) == ("ability","trogs_hand")
