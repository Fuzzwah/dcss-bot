"""Captured development13 failed walking escape and synthetic evidence boundaries."""
import asyncio
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development13_failed_escape.json"


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def captured():
    raw = json.loads(FIXTURE.read_text())
    values = raw["base"]
    snapshots = [observation(values)]
    actions = []
    for row in raw["transitions"]:
        actions.append(Action(**row["action"]))
        values = dict(values, **row["after_changes"])
        snapshots.append(observation(values))
    return snapshots, actions


def choose(policy, obs, covered=RuleBasedAgent._covered):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return policy.decide(obs, cells, Counter({position: 2 for position in cells}), covered)


def room(*, key="u", only_target=False):
    dx, dy = DIRECTIONS[key]
    cells = ((Cell(0, 0, 33, ".", True), Cell(dx, dy, 33, ".", True)) if only_target else
             tuple(Cell(x, y, 33, ".", True) for x in range(-1, 2) for y in range(-1, 2)))
    return Observation(species="Minotaur", god="Trog", place="Dungeon", depth=4,
                       turn=100, hp=45, hp_max=45, xl=5, input_mode=1, cells=cells)


def response(before):
    return replace(before, turn=before.turn+1, time=before.time+15,
                   messages=before.messages + ("You miss something.", "Something hits you but does no damage."))


def test_exact_captured_normal_walk_did_not_escape_and_does_not_create_a_monster():
    snapshots, actions = captured()
    visible, before, after = snapshots
    assert [obs.turn for obs in snapshots] == [2470, 2471, 2472]
    assert before.position == after.position == (11, -7)
    assert (before.hp, after.hp) == (26, 20)
    assert (actions[1].kind, actions[1].value, actions[1].objective) == ("move", "u", "retreat")
    assert any((monster.x, monster.y) == (12, -8) for monster in visible.hostiles)
    assert not any((monster.x, monster.y) == (12, -8) for monster in before.monsters)
    policy = TacticalController()
    # Consume the real command/result, without installing any policy counters.
    policy.routine(before, actions[1])
    action = choose(policy, after)
    assert policy.unseen_displacements.attempts == {(12, -8): 2472}
    assert action is not None and (action.kind, action.value) != ("move", "u")
    assert not any(row["kind"] == "move" and row["value"] == "u"
                   for row in action.assessment["candidates"])
    evidence = action.assessment["unseen_displacements"]
    assert evidence[0]["square"] == (12, -8) and evidence[0]["age"] == 0
    assert "did not displace" in evidence[0]["source"]
    assert any("Displacement into (12, -8) refused" in reason for reason in action.assessment["rejected"])
    assert policy.unseen_contact is None
    assert len(action.assessment["assessment"]["threats"]) == len(after.hostiles)
    assert all(threat["name"] != "something" for threat in action.assessment["assessment"]["threats"])


def test_actual_last_public_contact_is_not_a_generic_retreat_destination_even_before_first_bump():
    (visible, before, _), actions = captured()
    policy = TacticalController()
    policy.routine(visible, actions[0])
    action = choose(policy, before)
    assert policy.unseen_contact[0].x == 12 and policy.unseen_contact[0].y == -8
    assert action is not None and (action.kind, action.value) != ("move", "u")
    assert any(entry["square"] == (12, -8) and "last public contact" in entry["source"]
               for entry in action.assessment["unseen_displacements"])


def test_captured_failed_escape_is_consumed_by_the_public_agent_without_repeating_u():
    (_, before, after), actions = captured()
    agent = RuleBasedAgent()
    agent.exploration.observe(before)
    agent.tactics.routine(before, actions[1])
    monsters, cells = after.monsters, after.cells
    action = asyncio.run(agent.decide(after))
    assert action is not None and (action.kind, action.value) != ("move", "u")
    assert agent.tactics.unseen_displacements.attempts == {(12, -8): 2472}
    assert after.monsters == monsters and after.cells == cells


@pytest.mark.parametrize("key", DIRECTIONS)
def test_every_normal_walking_direction_records_only_its_failed_attempt(key):
    before = room(key=key)
    policy = TacticalController()
    policy.routine(before, Action("move", key, objective="retreat"))
    after = response(before)
    policy.observe(after)
    assert policy.unseen_displacements.attempts == {DIRECTIONS[key]: after.turn}
    assert policy.unseen_contact is None


@pytest.mark.parametrize("change", [
    "ctrl", "visible_primary_cleave", "moved", "floor", "free_menu", "old_menu", "old_prompt", "target_mode",
    "reused_text", "incoming_only", "empty_cleave", "wall", "hidden_cell", "cloud", "skipped",
    "conf_before", "conf_after", "held_before", "held_after", "net", "web", "constricted",
    "paralysed", "petrified", "asleep", "status_detail", "restraint_struggle",
])
def test_ambiguous_or_nonwalking_results_cannot_record_a_failed_escape(change):
    before = room()
    after = response(before)
    action = Action("move", "u", objective="retreat")
    if change == "ctrl":
        action = Action("attack", "u", objective="defend unseen")
    elif change == "visible_primary_cleave":
        before = replace(before, monsters=(Monster(1, "orc", 1, -1),))
    elif change == "moved":
        after = replace(after, position=(1, -1))
    elif change == "floor":
        after = replace(after, depth=5)
    elif change == "free_menu":
        after = replace(after, turn=before.turn, menu={"title": "Abilities"})
    elif change == "old_menu":
        before = replace(before, menu={"title": "Abilities"})
    elif change == "old_prompt":
        before = replace(before, prompt="Really attack?")
    elif change == "reused_text":
        before = replace(before, messages=after.messages)
    elif change == "incoming_only":
        after = replace(after, messages=("Something hits you.",))
    elif change == "empty_cleave":
        after = replace(after, messages=after.messages + ("You attack empty space.",))
    elif change == "target_mode":
        before = replace(before, input_mode=4)
    elif change == "wall":
        before = replace(before, cells=tuple(replace(cell, feature=7) if (cell.x, cell.y) == (1, -1)
                                            else cell for cell in before.cells))
    elif change == "hidden_cell":
        before = replace(before, cells=tuple(replace(cell, visible=False) for cell in before.cells))
    elif change == "cloud":
        before = replace(before, cells=tuple(replace(cell, cloud=True) for cell in before.cells))
    elif change == "skipped":
        after = replace(after, turn=before.turn+2)
    elif change.endswith("_before"):
        before = replace(before, statuses=("Conf" if change.startswith("conf") else "Held",))
    elif change.endswith("_after"):
        after = replace(after, statuses=("Conf" if change.startswith("conf") else "Held",))
    elif change == "status_detail":
        before = replace(before, status_details=(Status("", "You are held in a net."),))
    elif change == "restraint_struggle":
        action = replace(action, objective="escape restraint")
    else:
        before = replace(before, statuses=(change,))
    policy = TacticalController()
    policy.routine(before, action)
    policy.observe(after)
    assert not policy.unseen_displacements.attempts


@pytest.mark.parametrize("death_message", ["You kill something!", "You kill the orc!"])
def test_secondary_death_does_not_turn_an_undisplaced_walk_into_an_escape(death_message):
    before = room()
    after = replace(response(before), messages=response(before).messages + (death_message,))
    policy = TacticalController()
    policy.routine(before, Action("move", "u", objective="retreat"))
    policy.observe(after)
    assert policy.unseen_displacements.attempts == {(1, -1): 101}
    assert policy.unseen_contact is None


def test_free_inspection_and_reused_rolling_text_do_not_renew_and_evidence_expires():
    before = room()
    after = response(before)
    policy = TacticalController()
    policy.routine(before, Action("move", "u", objective="retreat"))
    policy.observe(after)
    for number in range(3):
        menu = replace(after, menu={"title": f"Abilities {number}"})
        policy.routine(menu, Action("inspect", "abilities"))
        assert policy.unseen_displacements.attempts == {(1, -1): 101}
    # Reusing the original outgoing text on a new turn cannot reset its age.
    policy.routine(after, Action("move", "u", objective="retreat"))
    later = replace(after, turn=102)
    policy.observe(later)
    assert policy.unseen_displacements.attempts == {(1, -1): 101}
    assert policy.unseen_displacements.evidence(later)[0]["age"] == 1
    policy.routine(later, Action("wait"))
    expired = replace(later, turn=104)
    policy.observe(expired)
    assert not policy.unseen_displacements.attempts
    assert not policy.unseen_displacement_evidence(expired)


@pytest.mark.parametrize("change", ["move", "floor", "unknown_rebase", "known_rebase"])
def test_failed_attempts_follow_public_frame_reset_and_translation(change):
    before = room()
    after = response(before)
    policy = TacticalController()
    policy.routine(before, Action("move", "u", objective="retreat"))
    policy.observe(after)
    policy.routine(after, Action("wait"))
    if change == "known_rebase":
        policy.rebase(after.location, (8, -3))
        shifted = replace(after, position=(8, -3),
                          cells=tuple(replace(cell, x=cell.x+8, y=cell.y-3) for cell in after.cells))
        assert policy.unseen_displacements.evidence(shifted)[0]["square"] == (9, -4)
        assert policy.unseen_displacements.attempts == {(9, -4): 101}
        assert policy.unseen_displacements.evidence(after) == []
    elif change == "unknown_rebase":
        policy.rebase(after.location, None)
        assert not policy.unseen_displacements.attempts
    else:
        next_obs = replace(after, turn=102, position=(0, 1)) if change == "move" else replace(after, depth=5)
        policy.observe(next_obs)
        assert not policy.unseen_displacements.attempts


def test_failed_displacement_does_not_block_visible_melee_or_anonymous_nonmoving_search():
    before = room(only_target=True)
    for visible in (False, True):
        policy = TacticalController()
        policy.routine(before, Action("move", "u", objective="retreat"))
        after = response(before)
        if visible:
            after = replace(after, monsters=(Monster(1, "goblin", 1, -1, description="A small goblin."),))
        action = choose(policy, after)
        assert (action.kind, action.value) == (("move", "u") if visible else ("attack", "u"))
        assert action.objective == ("combat" if visible else "defend unseen")
        assert policy.unseen_displacements.attempts == {(1, -1): 101}


def test_failed_displacement_does_not_remove_emergency_healing_or_restraint_struggle():
    before = room(only_target=True)
    after = replace(response(before), hp=5,
                    inventory=(Item(0, "a", "potion of heal wounds", category="potion"),))
    policy = TacticalController()
    policy.routine(before, Action("move", "u", objective="retreat"))
    action = choose(policy, after)
    assert (action.kind, action.value) == ("quaff", "a")
    # The same factual attempted square cannot prohibit native net-cutting.
    held = replace(after, turn=102, statuses=("Held",), inventory=())
    action = choose(policy, held)
    assert action.kind == "move" and action.objective == "escape restraint"


def test_generic_corner_search_avoids_failed_square_and_finds_another_real_step():
    before = replace(room(key="h"), monsters=(Monster(1, "centaur", 0, 5, description="A centaur."),))
    policy = TacticalController()
    policy.routine(before, Action("move", "h", objective="retreat"))
    after = response(before)
    policy.observe(after)
    cells = {(cell.x, cell.y): cell for cell in after.cells}
    cover = lambda terrain, enemy, position: position in {(-1, 0), (0, -1)}
    assessment = policy.assess(after, cells, cover)
    assert policy._corner_step(after, cells, Counter(), cover, assessment) == "k"
    action = choose(policy, after, cover)
    assert (action.kind, action.value) != ("move", "h")
    assert all(row["kind"] != "move" or row["value"] != "h" for row in action.assessment["candidates"])


def test_outgoing_only_failed_walk_cannot_fall_through_to_exploration_and_expires_quietly():
    before = room(only_target=True)
    after = replace(response(before), messages=("You miss something.",))
    policy = TacticalController()
    policy.routine(before, Action("move", "u", objective="retreat"))
    action = choose(policy, after)
    assert action.kind == "pause" and action.objective == "blocked"
    assert policy.unseen_harm is None and policy.unseen_contact is None
    assert policy.unseen_displacements.active(after)
    for turn in (102, 103):
        after = replace(after, turn=turn, time=after.time+10)
        action = choose(policy, after)
        assert action.kind == "wait" and action.objective == "verify unseen"
    expired = replace(after, turn=104, time=after.time+10)
    assert choose(policy, expired) is None
    assert not policy.unseen_displacements.active(expired)
