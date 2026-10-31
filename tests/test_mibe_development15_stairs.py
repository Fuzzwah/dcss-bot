"""Captured development15 stair exclusions, not cold-snapshot native replays.

The fix for these pauses belongs before the unnecessary tactical withdrawal.
Neither recovered HP nor beneficial Hand statuses can manufacture the missing
contemporary clearance. The fixture records the public chronology proving why.
"""
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_stairs import StairReevaluation
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, Item, Monster, Observation, Resistance, Skill, Status


def evidence():
    return json.loads((Path(__file__).parent / "fixtures" /
                       "recorded_development15_stairs.json").read_text())


def observation(values):
    values = dict(values)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values.get(field, ()))
    return Observation(**values)


def test_captured_seed1_spent_probes_and_later_unclear_withdrawal_stay_ineligible():
    recorded = evidence()["seed1"]
    policy = StairReevaluation()
    bans = {(location, feature): xl for location, feature, xl in recorded["unsafe_stairs"]}
    previous = action = None
    spent = []
    for row in recorded["history"]:
        obs = observation(row["before"])
        policy.observe(obs, previous, action)
        action = Action(**row["action"])
        if action.kind == "stairs_down" and action.objective == "reevaluate clear stair":
            cells = {(cell.x, cell.y): cell for cell in obs.cells}
            chosen = policy.choose(obs, cells, lambda _: None, bans, exhausted=True)
            assert (chosen.kind, chosen.objective) == (action.kind, action.objective)
            spent.append((row["sequence"], obs.turn, cells[obs.position].feature))
        if row["sequence"] == 2988:
            # Feature43's full-health withdrawal was only eight quiet turns
            # after the actually visible dart slug, not a clearance receipt.
            assert ("Dungeon:3", 43) not in policy.departures
        if row["sequence"] == 3351:
            assert policy.departures[("Dungeon:3", 44)].quiet_turns == 14
        previous = obs
    endpoint = observation(recorded["endpoint"])
    policy.observe(endpoint, previous, action)
    assert spent == [(3363, 3264, 44), (3401, 3302, 42)]
    assert set(policy.departures) == {("Dungeon:3", 42)}
    assert policy.attempted == {("Dungeon:3", 42), ("Dungeon:3", 44)}
    assert (endpoint.turn, endpoint.hp, endpoint.hp_max) == (3609, 50, 51)
    cells = {(cell.x, cell.y): cell for cell in endpoint.cells}
    assert policy.choose(endpoint, cells, lambda _: "h", bans, exhausted=True) is None

    # SYNTHETIC CONTROL, not an invented native healing result: full HP and
    # arbitrarily more quiet time still cannot replenish either spent probe or
    # replace feature44's revoked receipt (the last departure was 48/51 HP).
    healed = replace(endpoint, hp=endpoint.hp_max, turn=endpoint.turn + 100)
    policy.observe(healed, endpoint, Action("wait"))
    assert policy.choose(healed, cells, lambda _: "h", bans, exhausted=True) is None
    assert bans[("Dungeon:3", 43)] == 7


def replay_wizard_withdrawal(rows, *, remove_hand=False):
    controller = TacticalController()
    seen_unseen = []
    for row in rows:
        obs = observation(row["before"])
        if remove_hand:
            # SYNTHETIC CONTROL: alter only the visible beneficial statuses;
            # retain the actual wizard, messages, turns, HP and stair actions.
            obs = replace(obs, statuses=(), status_details=())
        controller.observe(obs)
        if controller.unseen_harm is not None:
            seen_unseen.append((row["sequence"], controller.unseen_harm))
        controller.previous = obs
        controller.last_action = Action(**row["action"])
    return controller, seen_unseen


def test_captured_seed6_no_damage_unseen_flame_is_not_clear_hand_withdrawal():
    recorded = evidence()["seed6"]
    for remove_hand in (False, True):
        controller, seen_unseen = replay_wizard_withdrawal(
            recorded["history"], remove_hand=remove_hand)
        # Native seq3827 cast from thin air hits without HP loss. The tactical
        # observer still records unseen danger at the next public turn3694.
        assert (3828, ("Dungeon:5", 3694, 1)) in seen_unseen
        assert not controller.stair_reevaluation.departures
        endpoint = observation(recorded["endpoint"])
        assert (endpoint.turn, endpoint.hp, endpoint.hp_max) == (3987, 56, 57)
        cells = {(cell.x, cell.y): cell for cell in endpoint.cells}
        assert controller.stair_reevaluation.choose(
            endpoint, cells, lambda _: "h", {("Dungeon:4", 42): 9}, exhausted=True) is None

    # The one unbanned ordinary alternative is real, but public terrain puts
    # feature44 on a disconnected island. A cold route check is sufficient
    # for this geometry only, not a reconstruction of prior tactical receipts.
    agent = RuleBasedAgent()
    agent.exploration.observe(endpoint)
    agent.tactics.unsafe_stairs = {("Dungeon:4", 42): 9, ("Dungeon:4", 43): 9}
    downstairs = {position for position, cell in cells.items()
                  if cell.feature in {42, 43, 44}}
    ordinary = agent.tactics.safe_stairs(endpoint, downstairs)
    assert ordinary == {(34, -22)}
    assert not agent.exploration.current.blocked
    assert not agent.tactical_avoided
    assert agent._path(endpoint, cells, ordinary, allow_clouds=True) is None
