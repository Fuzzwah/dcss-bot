"""Native invisible death feedback and public attack-roll interpretation."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.recording import Recorder


FIXTURES = Path(__file__).parent / "fixtures"


def captured(name):
    data = json.loads((FIXTURES / "recorded_development10_feedback.json").read_text())
    values = dict(data["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def test_native_unseen_melee_death_ends_contact_and_stale_harm_without_inventing_identity():
    before, after = captured("unseen_kill_before"), captured("unseen_kill_after")
    old = json.loads((FIXTURES / "recorded_development10_unseen_bump.json").read_text())
    # The recorded public reconnect offset is (-3,+14), not a guessed player origin.
    remembered = replace(Monster(**old["visible_changes"]["monsters"][0]),x=0,y=1)
    policy = TacticalController()
    policy.previous = before
    policy.last_action = Action("move","j",objective="defend unseen")
    policy.unseen_contact = remembered,before.turn
    policy.unseen_harm = before.location,before.turn,1
    policy.unseen_attempted = True
    action = policy.decide(after,{(c.x,c.y):c for c in after.cells},Counter(),RuleBasedAgent._covered)
    assert policy.unseen_contact is None and policy.unseen_harm is None
    assert action.kind == "wait" and action.objective != "defend unseen"
    assert not after.monsters


def test_native_unseen_xp_death_is_recorded_once_not_recounted_by_free_ui(tmp_path):
    before, after = captured("unseen_kill_before"), captured("unseen_kill_after")
    recorder = Recorder(tmp_path,{})
    try:
        recorder.record(before,Action("move","j",objective="defend unseen"),after,.1)
        recorder.record(after,Action("inspect","abilities"),after,.01)
        assert recorder.finish("stopped",after)["kills"] == 1
    finally:
        recorder.close()


def test_public_natural_attack_rolls_have_a_mean_below_the_unchanged_peak():
    one = mechanics(Monster(1,"unfamiliar creature",1,0,3,description="Bite  2\n"))
    assert one.melee == 2 and one.melee_mean == 1.5
    multiple = mechanics(Monster(2,"unfamiliar creature",1,0,3,description="Bite  4\nClaw  2\n"))
    assert multiple.melee == 6 and multiple.melee_mean == 4


def test_unseen_kill_does_not_erase_fresh_damage_from_another_hidden_attacker():
    before, after = captured("unseen_kill_before"), captured("unseen_kill_after")
    after = replace(after,hp=after.hp-3,messages=after.messages+("Something hits you!",))
    old = json.loads((FIXTURES / "recorded_development10_unseen_bump.json").read_text())
    policy = TacticalController()
    policy.previous = before
    policy.last_action = Action("move","j",objective="defend unseen")
    policy.unseen_contact = replace(Monster(**old["visible_changes"]["monsters"][0]),x=0,y=1),before.turn
    policy.unseen_harm = before.location,before.turn,1
    policy.unseen_attempted = True
    action = policy.decide(after,{(c.x,c.y):c for c in after.cells},Counter(),RuleBasedAgent._covered)
    assert policy.unseen_contact is None and policy.unseen_harm is not None
    assert action.objective not in {"explore","equipment","development"}
