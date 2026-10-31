"""Development16 public caster pauses; changed-decision responses are synthetic."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development17_casters.json"
NEW_OBJECTIVES = {"bounded caster close", "prepare minor summoner", "intercept minor summon"}


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def recorded(seed):
    data = json.loads(FIXTURE.read_text())["encounters"][str(seed)]
    policy = TacticalController()
    cells = {}
    for row in data["history"]:
        for position in row["removed_cells"]:
            cells.pop(tuple(position), None)
        cells.update({(cell["x"], cell["y"]): cell for cell in row["observation"]["cells"]})
        values = dict(row["observation"], cells=list(cells.values()))
        obs = observation(values)
        action = Action(**row["action"])
        policy.routine(obs, action)
        # Restore actual spent commitments from the recorded chosen actions,
        # not assumed native-ID continuity or a rewritten retreat count.
        if action.objective == "bounded summoner approach":
            target = next(enemy for enemy in obs.hostiles if enemy.spells)
            policy.minor_summoner_approaches.add((obs.location, target.name.casefold()))
    agent = RuleBasedAgent()
    agent.tactics = policy
    return agent, observation(data["observation"])


def legal_step(obs, action):
    assert action.kind == "move"
    dx, dy = DIRECTIONS[action.value]
    position = obs.position[0]+dx, obs.position[1]+dy
    cell = next(cell for cell in obs.cells if (cell.x, cell.y) == position)
    assert cell.visible and cell.walkable and not cell.cloud
    assert position not in {(enemy.x, enemy.y) for enemy in obs.monsters}
    return position


def trapped_minor_summoner(obs):
    """Synthetic clouds close every adjacent exit, not a native response."""
    return replace(obs, cells=tuple(
        replace(cell, cloud=True)
        if max(abs(cell.x-obs.position[0]), abs(cell.y-obs.position[1])) == 1
        and cell.walkable else cell
        for cell in obs.cells))


async def test_actual_plain_slow_caster_pause_has_one_legal_time_budgeted_close():
    agent, obs = recorded(4)
    assert obs.turn == 990 and obs.hp == obs.hp_max == 26
    assert obs.statuses == ("Slow",)
    assert obs.status_details[0].description == "All your actions take 50% longer.\n"
    assert any(power.name == "Berserk" and power.available and power.failure == 9
               for power in obs.abilities)
    assert not any(power.name == "Trog's Hand" for power in obs.abilities)
    action = await agent.decide(obs)
    assert action.objective == "bounded caster close"
    assert legal_step(obs, action) == (-2, 5)
    assert action.assessment["small_book_action"]["action_world_time"] == 1.5
    selected = next(row for row in action.assessment["candidates"]
                    if (row["kind"], row["value"]) == (action.kind, action.value))
    assert selected["burst_loss"] == 24  # Two possible 3d4 darts during Slow.
    assert selected["expected_loss"] >= 11.25  # Raw 3d4 mean, no EV discount.
    reserve = action.assessment["small_book_action"]["aftermath_reserve"]
    assert reserve >= 10 and selected["expected_loss"]+reserve < obs.hp
    assert selected["burst_loss"] < obs.hp*.95
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])


async def test_synthetic_failed_close_or_refreshed_id_does_not_refill_or_repeat_it():
    agent, obs = recorded(4)
    first = await agent.decide(obs)
    assert first.objective == "bounded caster close"
    legal_step(obs, first)
    # Synthetic failed movement. No recorded response after this changed
    # decision is used as evidence of success, survival, or enemy movement.
    response = replace(obs, turn=obs.turn+1, time=obs.time+15,
                       monsters=tuple(replace(enemy, id=enemy.id+100) for enemy in obs.monsters))
    second = await agent.decide(response)
    assert second.objective != "bounded caster close"
    refreshed = replace(response, turn=response.turn+1, time=response.time+15,
                        monsters=tuple(replace(enemy, id=enemy.id+100) for enemy in response.monsters))
    third = await agent.decide(refreshed)
    assert third.objective != "bounded caster close"


@pytest.mark.parametrize("change", ["cooldown", "exhaustion", "low_hp", "hard_disable",
                                   "greater_summon", "unknown_book", "unknown_body",
                                   "dangerous_brand", "pending_teleport"])
async def test_synthetic_caster_safety_controls_do_not_inherit_new_close(change):
    agent, obs = recorded(4)
    target = obs.monsters[0]
    if change in {"hard_disable", "greater_summon", "unknown_book"}:
        spell = {"hard_disable": "Paralyse", "greater_summon": "Summon Greater Demon",
                 "unknown_book": "Unrecognized spell"}[change]
        target = replace(target, spells=(*target.spells, spell))
    elif change == "unknown_body":
        target = replace(target, description=None)
    elif change == "dangerous_brand":
        target = replace(target, equipment=("+0 dagger of distortion",))
    obs = replace(obs, monsters=(target,))
    if change in {"cooldown", "exhaustion", "pending_teleport"}:
        extra = {"cooldown": "-Berserk", "exhaustion": "Exh", "pending_teleport": "Tele"}[change]
        obs = replace(obs, statuses=(*obs.statuses, extra))
    elif change == "low_hp":
        obs = replace(obs, hp=12)
    action = await agent.decide(obs)
    assert action.objective not in NEW_OBJECTIVES
    assert "small_book_action" not in action.assessment


async def test_actual_minor_summoner_and_bat_pause_uses_credible_retreat_before_hand():
    agent, obs = recorded(6)
    assert obs.turn == 3321 and (obs.hp, obs.hp_max) == (45, 51) and not obs.statuses
    assert any(power.name == "Trog's Hand" and power.available and power.failure == 3
               for power in obs.abilities)
    action = await agent.decide(obs)
    assert action.objective == "planned retreat"
    position = legal_step(obs, action)
    route = action.assessment["moving_withdrawal"]
    assert position == tuple(route["path"][0])
    assert route["goal_kind"] in {"upstairs", "cover"}
    assert 0 < route["horizon"] <= 3
    assert route["expected_loss"] < obs.hp*.35
    # The fast bat is charged, not mistaken for a free equal-speed gap.
    assert 0 < route["burst_loss"] < obs.hp*.8
    assert obs.monsters[0].distance(position) >= obs.monsters[0].distance(obs.position)
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])


async def test_synthetic_trapped_minor_summoner_prepares_hand_once_without_renewing_approach():
    agent, captured = recorded(6)
    obs = trapped_minor_summoner(captured)
    action = await agent.decide(obs)
    assert (action.kind, action.value, action.objective) == (
        "ability", "trogs_hand", "prepare minor summoner")
    assert action.assessment["small_book_action"]["aftermath_reserve"] >= 24
    selected = next(row for row in action.assessment["candidates"]
                    if (row["kind"], row["value"]) == (action.kind, action.value))
    assert selected["expected_loss"]+24 < obs.hp
    assert selected["burst_loss"] < obs.hp*.95
    # Synthetic failed Hand, not a captured native outcome. Absence of Regen
    # must not refill either the preparation or the already-spent approach.
    failed = replace(obs, turn=obs.turn+1, time=obs.time+10)
    again = await agent.decide(failed)
    assert again.objective not in {"prepare minor summoner", "bounded summoner approach"}


async def test_synthetic_active_hand_does_not_repeat_preparation():
    agent, captured = recorded(6)
    obs = replace(trapped_minor_summoner(captured), statuses=("Regen",))
    action = await agent.decide(obs)
    assert not (action.kind == "ability" and action.value == "trogs_hand")


async def test_synthetic_prepared_minor_group_intercepts_one_weak_summon_once():
    agent, captured = recorded(6)
    target, bat = captured.monsters
    bat = replace(bat, x=-2, y=20)
    # Synthetic open chamber: no upstairs/opaque cover anchor, and clouds
    # leave only the south step clear. This is not a changed native rollout.
    cells = tuple(Cell(x=x, y=y, feature=33, glyph=".", visible=True,
                       cloud=max(abs(x+1), abs(y-18)) == 1 and (x, y) != (-1, 19))
                  for x in range(-6, 2) for y in range(15, 23))
    obs = replace(captured, cells=cells, monsters=(target, bat), statuses=("Regen",))
    action = await agent.decide(obs)
    assert action.objective == "intercept minor summon"
    position = legal_step(obs, action)
    assert bat.distance(position) == 1 and target.distance(position) >= 2
    assert action.assessment["small_book_action"]["aftermath_reserve"] >= 24
    # A failed step with refreshed native IDs cannot buy another interception.
    failed = replace(obs, turn=obs.turn+1, time=obs.time+10,
                     monsters=tuple(replace(enemy, id=enemy.id+100) for enemy in obs.monsters))
    again = await agent.decide(failed)
    assert again.objective not in {"intercept minor summon", "bounded summoner approach",
                                   "prepare minor summoner"}


@pytest.mark.parametrize("change", ["low_hp", "greater_summon", "hard_disable", "unknown_book",
                                   "unknown_body", "strong_companion", "unavailable_hand",
                                   "exhaustion", "poison", "pending_teleport", "held"])
async def test_synthetic_minor_summoner_controls_cannot_authorize_preparation(change):
    agent, captured = recorded(6)
    obs = trapped_minor_summoner(captured)
    target, bat = obs.monsters
    if change in {"greater_summon", "hard_disable", "unknown_book"}:
        spell = {"greater_summon": "Summon Greater Demon", "hard_disable": "Paralyse",
                 "unknown_book": "Unrecognized spell"}[change]
        target = replace(target, spells=(*target.spells, spell))
    elif change == "unknown_body":
        target = replace(target, description=None)
    elif change == "strong_companion":
        bat = replace(bat, description=bat.description.replace("Hit     1", "Hit     20"))
    obs = replace(obs, monsters=(target, bat))
    if change == "low_hp":
        obs = replace(obs, hp=15)
    elif change == "unavailable_hand":
        obs = replace(obs, abilities=tuple(power for power in obs.abilities if power.name != "Trog's Hand"))
    elif change in {"exhaustion", "poison", "pending_teleport", "held"}:
        status = {"exhaustion": "Exh", "poison": "Pois", "pending_teleport": "Tele",
                  "held": "Held"}[change]
        obs = replace(obs, statuses=(status,))
    action = await agent.decide(obs)
    assert action.objective not in NEW_OBJECTIVES
    assert not any(row.get("kind") == "ability" and row.get("value") == "berserk"
                   for row in action.assessment.get("candidates", ()))
