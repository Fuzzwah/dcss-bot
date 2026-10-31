"""Bounded reevaluation of source10's clear-but-banned second stair."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_stairs import StairReevaluation
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import DOWNSTAIRS


def captured(name, *, fixture="recorded_development10_stairs.json"):
    data = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text())
    values = dict(data["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def clear_withdrawal(*, departure=None, unseen_harm=False):
    policy = StairReevaluation()
    danger, quiet = captured("last_visible_before_clear_walk"), captured("clear_walk")
    departure = departure or captured("clear_departure")
    returned = captured("clear_return")
    policy.observe(danger, None, None)
    policy.observe(quiet, danger, Action("move", "y"))
    policy.observe(departure, quiet, Action("move", "u"))
    policy.observe(returned, departure, Action("stairs_up", objective="planned retreat"),
                   unseen_harm=unseen_harm)
    return policy


def exhausted_agent(*, no_remaining_supplies=False):
    obs = captured("exhausted")
    if no_remaining_supplies:
        # Synthetic isolation for stair arbitration: the two remembered scroll
        # piles were collected. Keep all recorded terrain, visibility, inventory
        # and withdrawal evidence; the original public fixture stays immutable.
        obs = replace(obs, cells=tuple(replace(cell, glyph=".")
                                       if cell.glyph in {"?", "!"} else cell
                                       for cell in obs.cells))
    agent = RuleBasedAgent()
    agent.tactics.stair_reevaluation = clear_withdrawal()
    agent.exploration.observe(obs)
    equipment = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
    agent.development._inspection_key = (obs.xl, obs.piety_rank, equipment)
    agent.development._last_skill_inspection = obs.turn
    agent.previous_messages = obs.messages
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    agent.exploration.current.exhausted_frontiers = {
        position: agent._unknown_edges(cells, position)
        for position, cell in cells.items() if cell.walkable}
    # These bounded probes were already exhausted in the native run. Mark only
    # unknown neighbours, never make a known route artificially unreachable.
    agent.exploration.current.blocked = {
        (x+dx, y+dy) for (x, y), cell in cells.items() if cell.walkable
        for dx, dy in DIRECTIONS.values()
        if (x+dx, y+dy) not in cells or cells[(x+dx, y+dy)].feature == 0}
    for cell in obs.cells:
        if cell.feature in DOWNSTAIRS:
            agent.tactics.unsafe_stairs[(obs.location, cell.feature)] = 7 if cell.feature == 43 else 8
    return agent, obs, cells


async def test_synthetic_loot_exhaustion_reaches_only_the_clear_planned_withdrawal_stair():
    agent, obs, cells = exhausted_agent(no_remaining_supplies=True)
    downstairs = {position for position, cell in cells.items() if cell.feature in DOWNSTAIRS}
    assert len(downstairs) == 3
    assert agent.tactics.safe_stairs(obs, downstairs) == set()
    assert (await agent.decide(obs)).kind == "explore"
    obs = replace(obs, native_automation="explore", native_result="complete")
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("move", "reevaluate clear stair")
    clear_stair = next(position for position in downstairs if cells[position].feature == 42)
    assert action.value == agent._path(obs, cells, {clear_stair})
    # The ordinary ban is retained; only the explicitly bounded fallback acts.
    assert agent.tactics.safe_stairs(obs, downstairs) == set()
    at_stair = replace(obs, turn=obs.turn+40, position=clear_stair)
    action = await agent.decide(at_stair)
    assert (action.kind, action.objective) == ("stairs_down", "reevaluate clear stair")
    assert action.assessment["stair_reevaluation"]["destination"] == "Dungeon:4"
    assert agent.tactics.stair_reevaluation.choose(
        at_stair, cells, lambda _: "h", agent.tactics.unsafe_stairs, exhausted=True) is None


async def test_recorded_remaining_supplies_preempt_exhausted_stair_reevaluation():
    agent, obs, cells = exhausted_agent()
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("move", "supplies")
    target = agent.exploration.current.equipment_target
    assert cells[target].glyph in {"?", "!"}
    dx, dy = DIRECTIONS[action.value]
    step = cells[(obs.position[0] + dx, obs.position[1] + dy)]
    assert step.visible and step.walkable and not step.cloud
    assert not agent.tactics.stair_reevaluation.attempted
    assert obs == captured("exhausted")


def test_recorded_clear_departure_waits_instead_of_immediately_reversing_or_starving():
    policy = clear_withdrawal()
    obs = captured("clear_return")
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    bans = {(obs.location, 42): 8}
    assert policy.departures[(obs.location, 42)].quiet_turns == 25
    while obs.turn < captured("clear_return").turn + policy.quiet_turns:
        action = policy.choose(obs, cells, lambda _: None, bans, exhausted=True)
        assert action.kind == "wait"
        assert not policy.attempted
        after = replace(obs, turn=obs.turn+1, time=obs.time+10)
        policy.observe(after, obs, action)
        obs = after
    action = policy.choose(obs, cells, lambda _: None, bans, exhausted=True)
    assert action.kind == "stairs_down"
    assert (obs.location, 42) in policy.attempted
    assert bans == {(obs.location, 42): 8}


@pytest.mark.parametrize("name", ["first_escape", "last_escape"])
def test_actual_emergency_escapes_never_become_probe_candidates(name):
    policy = StairReevaluation()
    old = captured(name + "_departure")
    returned = captured("first_escape_return" if name == "first_escape" else "exhausted")
    policy.observe(old, None, None)
    policy.observe(returned, old, Action("escape_upstairs", objective="retreat"))
    assert not policy.departures


async def test_an_ordinary_safe_stair_preempts_even_an_eligible_probe():
    agent, obs, cells = exhausted_agent(no_remaining_supplies=True)
    agent.tactics.unsafe_stairs.pop((obs.location, 43))
    assert (await agent.decide(obs)).kind == "explore"
    obs = replace(obs, native_automation="explore", native_result="complete")
    action = await agent.decide(obs)
    assert (action.kind, action.objective) == ("travel_downstairs", "descend")
    target = tuple(map(int, action.value.split(",")))
    assert cells[target].feature == 43
    assert not agent.tactics.stair_reevaluation.attempted


def test_new_quiet_withdrawal_cannot_replenish_a_spent_probe():
    policy = clear_withdrawal()
    source = captured("clear_return")
    cells = {(cell.x, cell.y): cell for cell in source.cells}
    source = replace(source, turn=source.turn+20)
    bans = {(source.location, 42): 8}
    assert policy.choose(source, cells, lambda _: None, bans, exhausted=True).kind == "stairs_down"
    quiet = replace(captured("clear_walk"), turn=source.turn+2)
    departure = replace(captured("clear_departure"), turn=quiet.turn+25)
    returned = replace(source, turn=departure.turn+2)
    policy.observe(quiet, source, Action("stairs_down"))
    policy.observe(departure, quiet, Action("move", "u"))
    policy.observe(returned, departure, Action("stairs_up", objective="planned retreat"))
    assert (source.location, 42) in policy.departures
    assert policy.choose(replace(returned, turn=returned.turn+20), cells, lambda _: "h",
                         bans, exhausted=True) is None


@pytest.mark.parametrize("change", ["adjacent_follower", "hurt", "unseen", "cooldown", "recent_contact"])
def test_quiet_departure_evidence_is_required_not_merely_the_retreat_objective(change):
    departure = captured("clear_departure")
    if change == "adjacent_follower":
        enemy = captured("last_escape_departure").hostiles[0]
        departure = replace(departure, monsters=(replace(enemy, x=1, y=0),))
    elif change == "hurt":
        departure = replace(departure, hp=departure.hp-1)
    elif change == "cooldown":
        departure = replace(departure, statuses=("-Berserk",))
    elif change == "recent_contact":
        departure = replace(departure, turn=captured("clear_walk").turn+2)
    policy = clear_withdrawal(departure=departure, unseen_harm=change == "unseen")
    assert not policy.departures


@pytest.mark.parametrize("change", ["alternatives", "hurt", "cooldown", "unknown_power", "blocked_path"])
def test_probe_preserves_readiness_and_ordinary_route_restrictions(change):
    agent, obs, cells = exhausted_agent()
    if change == "hurt":
        obs = replace(obs, hp=obs.hp-1)
    elif change == "cooldown":
        obs = replace(obs, statuses=("-Berserk",))
    elif change == "unknown_power":
        obs = replace(obs, abilities=())
    result = agent.tactics.stair_reevaluation.choose(
        obs, cells, lambda _: None if change == "blocked_path" else "h",
        agent.tactics.unsafe_stairs, exhausted=change != "alternatives")
    assert result is None
    assert not agent.tactics.stair_reevaluation.attempted


def test_new_hostile_arrival_revokes_old_clearance_and_does_not_erase_the_ban():
    policy = clear_withdrawal()
    source = captured("clear_return")
    arrival = replace(captured("last_escape_departure"), turn=source.turn+2,
                      cells=(Cell(0, 0, 46, "<", True),))
    policy.observe(arrival, source, Action("stairs_down", objective="reevaluate clear stair"))
    assert (source.location, 42) not in policy.departures


def test_feature_pairing_survives_public_coordinate_frames_without_player_translation():
    policy = clear_withdrawal()
    obs = captured("exhausted")
    assert captured("clear_return").position == obs.position == (0, 0)
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    targets = set()
    policy.choose(obs, cells, lambda positions: targets.update(positions),
                  {(obs.location, 42): 8}, exhausted=True)
    assert targets == {(-29, -6)}  # Actual current-frame feature 42, not player origin.


def test_no_public_clearance_cannot_be_replaced_by_exhaustion_or_waiting():
    policy = StairReevaluation()
    obs = captured("exhausted")
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    bans = {(obs.location, feature): 8 for feature in DOWNSTAIRS}
    assert policy.choose(replace(obs, turn=obs.turn+1000), cells, lambda _: "h", bans,
                         exhausted=True) is None


def test_exhaustion_can_route_to_a_clear_pair_before_the_return_guard_matures():
    policy = clear_withdrawal()
    obs = captured("clear_return")
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    stair = obs.position
    obs = replace(obs, position=(obs.position[0]+1, obs.position[1]))
    targets = set()
    def path(positions):
        targets.update(positions)
        return "h"
    action = policy.choose(obs, cells, path, {(obs.location, 42): 8}, exhausted=True)
    assert (action.kind, action.value) == ("move", "h")
    assert targets == {stair}
    assert not policy.attempted


@pytest.mark.parametrize("change", ["hurt", "hostile", "unseen"])
def test_return_time_does_not_replace_new_quiet_observation_after_danger(change):
    policy = clear_withdrawal()
    returned = captured("clear_return")
    danger = replace(returned, turn=returned.turn+30)
    if change == "hurt":
        danger = replace(danger, hp=danger.hp-1)
    elif change == "hostile":
        danger = replace(danger, monsters=captured("last_escape_departure").hostiles)
    policy.observe(danger, returned, Action("wait"), unseen_harm=change == "unseen")
    cells = {(cell.x, cell.y): cell for cell in returned.cells}
    bans = {(returned.location, 42): 8}
    assert policy.choose(danger, cells, lambda _: None, bans, exhausted=True) is None
    clear = replace(returned, turn=danger.turn+1)
    policy.observe(clear, danger, Action("wait"))
    action = policy.choose(clear, cells, lambda _: None, bans, exhausted=True)
    assert action.kind == "wait"
    assert not policy.attempted


def test_development12_endpoint_cannot_invent_full_health_departure_clearance():
    def recorded(name):
        return captured(name, fixture="recorded_development12_navigation.json")
    policy = StairReevaluation()
    escaped, first_return = recorded("first_escape_departure"), recorded("first_escape_return")
    policy.observe(escaped, None, None)
    policy.observe(first_return, escaped, Action("escape_upstairs", objective="retreat"))
    walk, second = recorded("quiet_walk"), recorded("second_departure")
    policy.observe(walk, first_return, Action("stairs_down"))
    policy.observe(second, walk, Action("move", "j", objective="planned retreat"))
    assert (second.hp, second.hp_max) == (50, 51)
    second_return = recorded("second_return")
    policy.observe(second_return, second, Action("stairs_up", objective="planned retreat"))
    third, third_return = recorded("third_departure"), recorded("third_return")
    policy.observe(third, second_return, Action("stairs_down"))
    policy.observe(third_return, third, Action("stairs_up", objective="planned retreat"))
    obs = recorded("endpoint")
    policy.observe(obs, third_return, Action("wait", objective="recover"))
    assert not policy.departures
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    bans = {(obs.location, feature): 8 for feature in DOWNSTAIRS}
    assert policy.choose(obs, cells, lambda _: "h", bans, exhausted=True) is None
    assert policy.choose(replace(obs, turn=obs.turn+100), cells, lambda _: "h", bans,
                         exhausted=True) is None
    assert not policy.attempted


def development12_quiet_departure():
    policy = StairReevaluation()
    walk = captured("quiet_walk", fixture="recorded_development12_navigation.json")
    departure = captured("second_departure", fixture="recorded_development12_navigation.json")
    policy.observe(walk, None, None)
    policy.observe(departure, walk, Action("move", "j", objective="planned retreat"))
    return policy, departure


def test_development12_voluntary_departure_can_finish_real_recovery_before_leaving():
    policy, departure = development12_quiet_departure()
    action = policy.prepare_departure(departure)
    assert action.kind == "wait"
    assert not policy.departures
    # This is a prospective legal wait, not a replacement of the captured
    # 50/51 departure: only the subsequently observed full HP supplies clearance.
    healed = replace(departure, turn=departure.turn+1, time=departure.time+10,
                     hp=departure.hp_max)
    policy.observe(healed, departure, action)
    assert policy.prepare_departure(healed) is None
    returned = captured("second_return", fixture="recorded_development12_navigation.json")
    returned = replace(returned, turn=returned.turn+1, time=returned.time+10,
                       hp=returned.hp_max)
    policy.observe(returned, healed, Action("stairs_up", objective="planned retreat"))
    entry = policy.departures[(returned.location, 44)]
    assert entry.departure_turn == healed.turn
    assert entry.quiet_turns >= policy.quiet_turns
    cells = {(cell.x, cell.y): cell for cell in returned.cells}
    bans = {(returned.location, feature): 8 for feature in DOWNSTAIRS}
    # The captured old-policy cache was populated during Conf and still says
    # every power is unavailable. A real full-health receipt alone must not
    # bypass native ability readiness; the strategy must refresh that cache.
    assert not any(ability.available for ability in returned.abilities)
    assert policy.choose(returned, cells, lambda _: None, bans, exhausted=True) is None
    assert not policy.attempted


@pytest.mark.parametrize("change", [
    "recent_contact", "hurt", "hostile", "unseen", "status", "wrong_feature", "unobserved", "cloud",
])
def test_preparation_never_delays_a_real_escape_or_manufactures_quiet(change):
    policy, obs = development12_quiet_departure()
    if change == "recent_contact":
        policy = StairReevaluation()
    elif change == "hurt":
        obs = replace(obs, hp=obs.hp-1)
    elif change == "hostile":
        obs = replace(obs, monsters=captured("last_escape_departure").hostiles)
    elif change == "status":
        obs = replace(obs, statuses=("-Berserk",))
    elif change == "wrong_feature":
        obs = replace(obs, cells=(Cell(*obs.position, 42, ">", True),))
    elif change == "unobserved":
        obs = replace(obs)
    elif change == "cloud":
        obs = replace(obs, cells=tuple(replace(cell,cloud="poison gas")
                      if (cell.x,cell.y) == obs.position else cell for cell in obs.cells))
    if change != "unobserved":
        policy.observe(obs, None, None)
    assert policy.prepare_departure(obs, unseen_danger=change == "unseen") is None
    assert not policy.departures


def test_preparation_is_bounded_if_public_full_health_never_arrives():
    policy, obs = development12_quiet_departure()
    for _ in range(policy.preparation_limit):
        action = policy.prepare_departure(obs)
        assert action.kind == "wait"
        after = replace(obs, turn=obs.turn+1, time=obs.time+10)
        policy.observe(after, obs, action)
        obs = after
    assert policy.prepare_departure(obs) is None
    assert not policy.departures
