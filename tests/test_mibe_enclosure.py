"""Public development11 seed6: sealed undead must not cause endless luring."""
import asyncio
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_encounter import EnclosureMemory, EncounterProgress, enclosed_melee_bypass, enclosed_melee_component
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import DOWNSTAIRS


DATA = json.loads((Path(__file__).parent / "fixtures" / "recorded_development11_enclosure.json").read_text())
NATIVE = json.loads((Path(__file__).parent / "fixtures" / "recorded_development11_enclosure_visibility.json").read_text())


def captured(changes=None):
    return observed(DATA["snapshot"] | (changes or {}))


def observed(raw):
    values = dict(raw)
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def mapping(obs):
    return {(cell.x, cell.y): cell for cell in obs.cells}


def goals(obs):
    return {(cell.x, cell.y) for cell in obs.cells if cell.feature in DOWNSTAIRS}


def test_actual_period_one_wait_has_no_observed_approach_despite_advancing_time():
    budget = EncounterProgress()
    for row in DATA["transitions"]:
        before, after = captured(row["before_changes"]), captured(row["after_changes"])
        action = Action(**row["action"])
        assert action.kind == "wait" and action.objective == "defend choke"
        assert after.turn > before.turn and after.time > before.time
        assert before.position == after.position and before.hp == after.hp == 45
        assert [(enemy.id, enemy.x, enemy.y) for enemy in before.hostiles] == [(93, -11, 22)]
        assert [(enemy.id, enemy.x, enemy.y) for enemy in after.hostiles] == [(93, -11, 22)]
        budget.observe(after, before, action)
    assert not budget.may_wait
    # A free inspection at the same turn is not progress or another spent turn.
    stalled = budget.stalled_waits
    budget.observe(after, before, Action("inspect", "abilities"))
    assert budget.stalled_waits == stalled


def test_actual_cage_is_current_public_clear_rock_walls_not_sleep_inference():
    obs = captured()
    enemy = obs.hostiles[0]
    assert not enemy.statuses
    assert "100% chance to notice you each turn" in enemy.description
    assert enclosed_melee_component(enemy, mapping(obs)) == frozenset({(-11, 22)})
    assert all(mapping(obs)[(-11+dx, 22+dy)].feature == 11
               and mapping(obs)[(-11+dx, 22+dy)].visible for dx, dy in DIRECTIONS.values())


def test_actual_enclosed_zombie_permits_one_legal_move_toward_real_known_downstairs():
    obs = captured()
    action = enclosed_melee_bypass(obs, mapping(obs), goals(obs))
    assert action is not None and action.kind == "move" and action.objective == "bypass enclosure"
    dx, dy = DIRECTIONS[action.value]
    cell = mapping(obs)[(obs.position[0]+dx, obs.position[1]+dy)]
    assert cell.visible and cell.walkable and not cell.cloud
    assert tuple(action.assessment["goal"]) in goals(obs)
    # The actual dangerous creature remains in the original public snapshot.
    assert obs.hostiles[0].id == 93 and obs.hostiles[0].threat == 2


def test_agent_uses_playable_bypass_instead_of_repeating_the_recorded_wait():
    obs = captured()
    agent = RuleBasedAgent()
    action = asyncio.run(agent.decide(obs))
    assert action.kind == "move" and action.objective == "bypass enclosure"
    dx, dy = DIRECTIONS[action.value]
    cell = mapping(obs)[(obs.position[0]+dx, obs.position[1]+dy)]
    assert cell.visible and cell.walkable and not cell.cloud


def test_fresh_unseen_attack_is_processed_before_considering_enclosure_bypass():
    before = captured()
    after = replace(before, turn=before.turn+1, time=before.time+10,
                    messages=before.messages+("Something misses you.",))
    agent = RuleBasedAgent()
    agent.tactics.previous = before
    agent.tactics.last_action = Action("wait", objective="defend choke")
    action = asyncio.run(agent.decide(after))
    assert agent.tactics.unseen_harm is not None
    assert action.objective not in {"bypass enclosure", "explore", "equipment", "development"}


@pytest.mark.parametrize("feature,visible", [(33, True), (1, True), (0, True), (11, False), (30, True)])
def test_open_unknown_unseen_door_and_water_boundary_never_certify_unreachable(feature, visible):
    obs = captured()
    cells = mapping(obs)
    cells[(-12, 22)] = replace(cells[(-12, 22)], feature=feature, visible=visible)
    assert not enclosed_melee_component(obs.hostiles[0], cells)
    assert enclosed_melee_bypass(obs, cells, goals(obs)) is None


@pytest.mark.parametrize("enemy", [
    Monster(93, "orc wizard", -11, 22, 2, description="Hit  5\n", spells=("Magic Dart",)),
    Monster(93, "unfamiliar creature zombie", -11, 22, 2, description="Hit  8\n"),
    Monster(93, "human zombie", -11, 22, 2, description="Hit  8\n", spells=("Summon Small Mammal",)),
    Monster(93, "human zombie", -11, 22, 2, description="Hit  8\n", spells=("Magic Dart",)),
    Monster(93, "deep troll earth mage", -11, 22, 2, description="Hit  20\n", spells=("Dig",)),
    Monster(93, "human zombie", -11, 22, 2),
])
def test_ranged_summon_wall_breaking_or_unexamined_monster_keeps_threat_protections(enemy):
    obs = replace(captured(), monsters=(enemy,))
    assert enclosed_melee_bypass(obs, mapping(obs), goals(obs)) is None


def test_an_additional_reachable_hostile_prevents_enclosure_bypass():
    obs = captured()
    obs = replace(obs, monsters=obs.monsters+(Monster(94, "adder", -16, 21, 2),))
    assert enclosed_melee_bypass(obs, mapping(obs), goals(obs)) is None


@pytest.mark.parametrize("changes", [{"hp": 25}, {"statuses": ("Slow",)}, {"statuses": ("Berserk",)},
                                     {"statuses": ("Pois",)}, {"statuses": ("Fire",)},
                                     {"prompt": "Really move?"}])
def test_bypass_never_overrides_recovery_or_disabled_input(changes):
    obs = replace(captured(), **changes)
    assert enclosed_melee_bypass(obs, mapping(obs), goals(obs)) is None


def test_no_visible_clear_first_step_does_not_authorize_autoexplore_or_unseen_probe():
    obs = captured()
    cells = mapping(obs)
    for dx, dy in DIRECTIONS.values():
        point = obs.position[0]+dx, obs.position[1]+dy
        if point in cells:
            cells[point] = replace(cells[point], visible=False)
    assert enclosed_melee_bypass(obs, cells, goals(obs)) is None


def test_only_observed_approach_reopens_the_wait_budget():
    obs = captured()
    budget = EncounterProgress(stalled_waits=3)
    side = replace(obs, turn=obs.turn+1, monsters=(replace(obs.monsters[0], y=23),))
    budget.observe(side, obs, Action("wait", objective="defend choke"))
    assert not budget.may_wait
    closer = replace(side, turn=side.turn+1, monsters=(replace(side.monsters[0], x=-12),))
    budget.observe(closer, side, Action("wait", objective="defend choke"))
    assert budget.may_wait


def test_reachable_ordinary_foe_is_approached_after_observed_waits_not_ignored_for_exploration():
    cells = tuple(Cell(x, y, 33, ".", True) for x in range(8) for y in range(3))
    obs = Observation(hp=45, hp_max=45, position=(0, 1), place="Dungeon", depth=4,
                      turn=10, time=100, input_mode=1, cells=cells,
                      monsters=(Monster(93, "human zombie", 6, 1, 2,
                                        description=captured().hostiles[0].description),))
    policy = TacticalController()
    policy.encounter_start = 0
    policy.encounter_last_seen = 10
    policy.ineffective_retreats = 2
    old = replace(obs, turn=9, time=90)
    for _ in range(4):
        policy.previous = old
        policy.last_action = Action("wait", objective="defend choke")
        policy.observe(obs)
        old, obs = obs, replace(obs, turn=obs.turn+1, time=obs.time+10)
    action = policy.decide(obs, mapping(obs), Counter(), RuleBasedAgent._covered)
    assert action.kind == "move" and action.objective == "combat"
    dx, dy = DIRECTIONS[action.value]
    assert obs.hostiles[0].distance((obs.position[0]+dx, obs.position[1]+dy)) < obs.hostiles[0].distance(obs.position)


def test_actual_visibility_loss_preserves_only_previously_observed_enclosure_evidence():
    before, after = observed(NATIVE["before"]), observed(NATIVE["after"])
    assert (before.turn, after.turn) == (3157, 3158)
    assert before.position == (0, 0) and after.position == (-1, 1)
    assert all(mapping(before)[(7, y)].visible for y in range(3))
    assert all(not mapping(after)[(7, y)].visible for y in range(3))
    assert all(mapping(after)[(7, y)].feature == 11 for y in range(3))
    assert not enclosed_melee_component(after.hostiles[0], mapping(after))
    assert enclosed_melee_bypass(after, mapping(after), goals(after)) is None
    memory = EnclosureMemory()
    memory.observe(before, mapping(before))
    memory.observe(after, mapping(after))
    action = enclosed_melee_bypass(after, mapping(after), goals(after), memory=memory)
    assert action is not None and action.kind == "move" and action.objective == "bypass enclosure"
    assert action.assessment["enclosure_evidence"][0]["age_turns"] == 1
    assert action.assessment["enclosure_evidence"][0]["observed_turn"] == 3157
    assert all(not mapping(after)[(7, y)].visible for y in range(3))
    assert after.hostiles[0].id == before.hostiles[0].id == 1


def test_agent_actual_follow_on_keeps_moving_instead_of_waiting_then_returning_to_origin():
    before, after = observed(NATIVE["before"]), observed(NATIVE["after"])
    agent = RuleBasedAgent()
    first = asyncio.run(agent.decide(before))
    assert (first.kind, first.value, first.objective) == ("move", "b", "bypass enclosure")
    follow_on = asyncio.run(agent.decide(after))
    assert follow_on.kind == "move" and follow_on.objective == "bypass enclosure"
    dx, dy = DIRECTIONS[follow_on.value]
    position = after.position[0]+dx, after.position[1]+dy
    cell = mapping(after)[position]
    assert cell.visible and cell.walkable and not cell.cloud
    assert position != before.position
    assert after.hostiles[0].distance(position) >= after.hostiles[0].distance(after.position)


@pytest.mark.parametrize("change", ["boundary", "interior", "escape", "capabilities", "type", "identity", "floor", "new_hostile"])
def test_historical_certificate_never_survives_contradiction_or_an_unsealed_new_threat(change):
    before, after = observed(NATIVE["before"]), observed(NATIVE["after"])
    memory = EnclosureMemory()
    memory.observe(before, mapping(before))
    if change in {"boundary", "interior"}:
        point = (7, 1) if change == "boundary" else (6, 1)
        after = replace(after, cells=tuple(replace(cell, feature=33 if change == "boundary" else 45)
                                          if (cell.x, cell.y) == point else cell for cell in after.cells))
    elif change == "escape":
        after = replace(after, monsters=(replace(after.hostiles[0], x=4),))
    elif change == "capabilities":
        after = replace(after, monsters=(replace(after.hostiles[0], spells=("Magic Dart",)),))
    elif change == "type":
        after = replace(after, monsters=(replace(after.hostiles[0], name="orc zombie"),))
    elif change == "identity":
        after = replace(after, monsters=(replace(after.hostiles[0], id=2),))
    elif change == "floor":
        after = replace(after, depth=5)
    else:
        after = replace(after, monsters=after.monsters+(Monster(2, "adder", -2, 1, 2),))
    memory.observe(after, mapping(after))
    assert enclosed_melee_bypass(after, mapping(after), goals(after), memory=memory) is None
    assert not memory.certificates


def test_a_disappeared_occupant_is_not_assumed_certified_on_a_later_sighting():
    before, after = observed(NATIVE["before"]), observed(NATIVE["after"])
    memory = EnclosureMemory()
    memory.observe(before, mapping(before))
    vanished = replace(after, monsters=())
    memory.observe(vanished, mapping(vanished))
    returned = replace(after, turn=after.turn+1, time=after.time+10)
    memory.observe(returned, mapping(returned))
    assert enclosed_melee_bypass(returned, mapping(returned), goals(returned), memory=memory) is None


def test_agent_ambiguous_native_frame_clears_historical_enclosure():
    before, after = observed(NATIVE["before"]), observed(NATIVE["after"])
    agent = RuleBasedAgent()
    assert asyncio.run(agent.decide(before)).objective == "bypass enclosure"
    # Removing public anchor knowledge is a real ExplorationMemory ambiguity,
    # not a guessed translation based on player movement.
    anchor_features = {42, 43, 44, 46, 47, 48, 60, 71, 88}
    ambiguous = replace(after, cells=tuple(cell for cell in after.cells if cell.feature not in anchor_features))
    action = asyncio.run(agent.decide(ambiguous))
    assert not agent.enclosures.certificates
    assert action.objective != "bypass enclosure"
