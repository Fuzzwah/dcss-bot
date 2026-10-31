"""Captured development10 invisible-wizard contact, without hidden coordinates."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import confirmed_unseen_bump
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def recorded():
    raw = json.loads((Path(__file__).parent / "fixtures" / "recorded_development10_unseen_bump.json").read_text())

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

    visible = dict(raw["base"], **raw["visible_changes"])
    changes = {(cell["x"], cell["y"]): cell for cell in raw["visible_cell_changes"]}
    visible["cells"] = [changes.get((cell["x"], cell["y"]), cell) for cell in raw["base"]["cells"]]
    return (raw, observation(visible), observation(raw["base"]),
            observation(dict(raw["base"], **raw["after_changes"])))


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return controller.decide(obs, cells, Counter({position: 2 for position in cells}), RuleBasedAgent._covered)


def test_captured_stationary_bump_has_fresh_player_contact_not_just_incoming_damage():
    raw, visible, before, after = recorded()
    target = visible.hostiles[0].x, visible.hostiles[0].y
    assert before.turn == 2897 and after.turn == 2898
    assert before.position == after.position == (3, -14)
    assert before.hp == 24 and after.hp == 17
    assert "You miss something." in after.messages and "You miss something." not in before.messages
    assert not before.monsters and not after.monsters
    assert confirmed_unseen_bump(before, after, Action(**raw["bump_action"]), target)


def test_captured_stall_gets_one_more_confirmed_attempt_then_stale_feedback_cannot_continue():
    raw, visible, before, after = recorded()
    controller = TacticalController()
    controller.ineffective_retreats = raw["policy_state"]["ineffective_retreats"]
    controller.routine(visible, Action(**raw["previous_action"]))
    first = choose(controller, before)
    assert (first.kind, first.value, first.objective) == ("attack", "j", "defend unseen")
    assert controller.unseen_attempted

    controller.observe(after)
    second = choose(controller, after)
    assert (second.kind, second.value, second.objective) == ("attack", "j", "defend unseen")
    assert controller.unseen_attempted
    assert second.assessment["assessment"]["threats"] == ()

    # The rolling native message window is unchanged on the following world
    # turn: an old miss is not evidence of another still-occupied square.
    quiet = replace(after, turn=after.turn + 1, time=after.time + 12)
    next_action = choose(controller, quiet)
    assert next_action.objective != "defend unseen"
    assert controller.unseen_attempted


@pytest.mark.parametrize("message", [
    "You hit something.", "You hit something but do no damage.",
    "You miss something.", "You barely miss something.",
    "You closely miss something.", "You completely miss something.",
    "You slash something!", "You slice something!!",
])
def test_native_player_melee_feedback_can_confirm_one_completed_bump(message):
    raw, visible, before, after = recorded()
    target = visible.hostiles[0].x, visible.hostiles[0].y
    after = replace(after, messages=before.messages + (message,))
    assert confirmed_unseen_bump(before, after, Action(**raw["bump_action"]), target)


@pytest.mark.parametrize("change", [
    "displaced", "wrong_direction", "nonadjacent", "different_floor", "skipped_turn",
    "free_ui", "inspection", "ordinary_cleave", "reused_message", "incoming_only",
    "empty", "empty_with_cleave", "wall", "kill_named", "kill_unseen", "hidden_kill",
])
def test_no_contact_credit_for_movement_ambiguity_kills_or_reused_feedback(change):
    raw, visible, before, after = recorded()
    target = visible.hostiles[0].x, visible.hostiles[0].y
    action = Action(**raw["bump_action"])
    if change == "displaced":
        after = replace(after, position=target)
    elif change == "wrong_direction":
        action = replace(action, value="k")
    elif change == "nonadjacent":
        target = target[0], target[1] + 1
    elif change == "different_floor":
        after = replace(after, depth=after.depth + 1)
    elif change == "skipped_turn":
        after = replace(after, turn=before.turn + 2)
    elif change == "free_ui":
        after = replace(after, turn=before.turn)
    elif change == "inspection":
        action = Action("inspect", "abilities")
    elif change == "ordinary_cleave":
        action = replace(action, objective="combat")
    elif change == "reused_message":
        before = replace(before, messages=after.messages)
    elif change == "incoming_only":
        after = replace(after, messages=before.messages + ("Something hits you.",))
    elif change == "empty":
        after = replace(after, messages=before.messages + ("You swing at nothing.",))
    elif change == "empty_with_cleave":
        after = replace(after, messages=after.messages + ("You attack empty space.",))
    elif change == "wall":
        after = replace(after, messages=after.messages + ("You feel something solid in that direction.",))
    elif change == "kill_named":
        after = replace(after, messages=after.messages + ("You kill the orc wizard!",))
    elif change == "kill_unseen":
        after = replace(after, messages=after.messages + ("You kill something!",))
    elif change == "hidden_kill":
        after = replace(after, messages=after.messages + ("You feel a bit more experienced.",))
    assert not confirmed_unseen_bump(before, after, action, target)


def test_free_inspections_cannot_rearm_a_consumed_attempt_or_renew_unseen_timestamps():
    raw, visible, before, after = recorded()
    controller = TacticalController()
    controller.ineffective_retreats = raw["policy_state"]["ineffective_retreats"]
    controller.routine(visible, Action(**raw["previous_action"]))
    assert choose(controller, before).objective == "defend unseen"
    assert choose(controller, after).objective == "defend unseen"
    contact, harm = controller.unseen_contact, controller.unseen_harm
    for number in range(3):
        menu = replace(after, menu={"title": f"Abilities {number}"})
        controller.routine(menu, Action("inspect", "abilities"))
        assert controller.unseen_contact == contact and controller.unseen_harm == harm
        assert controller.unseen_attempted
    assert choose(controller, replace(after, turn=after.turn + 1)).objective != "defend unseen"


@pytest.mark.parametrize("key", DIRECTIONS)
def test_confirmation_uses_requested_bump_geometry_in_every_native_direction(key):
    dx, dy = DIRECTIONS[key]
    target = dx, dy
    before = Observation(place="Dungeon", depth=4, turn=100, position=(0, 0),
                         cells=(Cell(dx, dy, 33, ".", True),))
    after = replace(before, turn=101, messages=("You miss something.",))
    assert confirmed_unseen_bump(before, after, Action("move", key, objective="defend unseen"), target)
    assert not confirmed_unseen_bump(before, after, Action("move", key, objective="defend unseen"), (-dx, -dy))
