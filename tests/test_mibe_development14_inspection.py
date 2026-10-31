"""Early source3 Sigmund inspection; composed native responses are synthetic."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import visible_dangerous_weapon
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development14_sigmund_inspection.json"


def captured():
    data = json.loads(FIXTURE.read_text())
    values = dict(data["observation"])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    for field in ("position", "messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values), Monster(**data["sigmund_examination"])


async def finish_failed_inspections(agent, obs):
    """Synthetic empty native responses: unchanged turn, pose and public fields."""
    attempted = set()
    for _ in range(len(obs.hostiles) + 1):
        action = await agent.decide(obs)
        if action.kind != "inspect_monster":
            return action, attempted
        assert action.value not in attempted
        attempted.add(action.value)
    pytest.fail("Empty native descriptions stalled the bounded free inspection sequence")


def assert_legal_defence(obs, action):
    assert action.kind == "move" and action.objective in {"retreat", "planned retreat"}
    dx, dy = DIRECTIONS[action.value]
    target = obs.position[0] + dx, obs.position[1] + dy
    cell = next(cell for cell in obs.cells if (cell.x, cell.y) == target)
    assert cell.visible and cell.walkable and not cell.cloud
    assert target not in {(enemy.x, enemy.y) for enemy in obs.monsters}
    sigmund = next(enemy for enemy in obs.hostiles if enemy.name == "Sigmund")
    assert sigmund.distance(target) >= sigmund.distance(obs.position)


async def test_actual_early_sigmund_is_read_before_an_adjacent_lizard_can_hide_him():
    obs, _ = captured()
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert obs.turn == 2103 and obs.hp == obs.hp_max == 31 and obs.position == (-3, 9)
    assert any(enemy.name == "frilled lizard" and enemy.distance(obs.position) == 1
               and enemy.description is not None for enemy in obs.hostiles)
    assert (action.kind, action.value) == ("inspect_monster", "-6,16")
    # Ordinary melee and emergency-labelled forecasts are not themselves reasons
    # to skip a turn-free inspection; only an actionable urgent response is.
    assert any(row["kind"] == "move" and row["value"] == "h"
               for row in action.assessment["candidates"])


async def test_captured_distortion_description_preserves_hard_avoidance_and_legal_defence():
    obs, known = captured()
    agent = RuleBasedAgent()
    first = await agent.decide(obs)
    assert (first.kind, first.value) == ("inspect_monster", "-6,16")
    target = next(enemy for enemy in obs.hostiles if enemy.name == "Sigmund")
    assert not visible_dangerous_weapon(target)
    # Synthetic free examination response: copy the actual later public native
    # description/book/equipment, not that later pose, HP, turn or instance id.
    target = replace(target, description=known.description, equipment=known.equipment,
                     speed_description=known.speed_description, spells=known.spells,
                     spell_ranges=known.spell_ranges)
    response = replace(obs, monsters=tuple(target if enemy.name == "Sigmund" else enemy
                                          for enemy in obs.monsters))
    action, attempted = await finish_failed_inspections(agent, response)
    assert visible_dangerous_weapon(target)
    assert attempted == {"-4,11", "-5,13"}
    threat = next(threat for threat in agent.tactics.latest_assessment.threats
                  if threat.name == "Sigmund")
    assert threat.avoid_melee
    assert not any(row["kind"] == "ability" and row["value"] == "berserk"
                   for row in action.assessment["candidates"])
    assert_legal_defence(response, action)


async def test_failed_native_inspections_cannot_loop_or_reinspect_the_same_visible_instance():
    obs, _ = captured()
    agent = RuleBasedAgent()
    action, attempted = await finish_failed_inspections(agent, obs)
    assert attempted == {"-6,16", "-5,13", "-4,11"}
    assert_legal_defence(obs, action)
    assert (await agent.decide(obs)).kind != "inspect_monster"
    # Spending a real turn also cannot re-arm the same failed native instances.
    next_turn = replace(obs, turn=obs.turn + 1, time=obs.time + 10)
    assert (await agent.decide(next_turn)).kind != "inspect_monster"


@pytest.mark.parametrize("response", ["curing", "healing", "teleport", "hand"])
async def test_synthetic_actionable_urgent_resources_preempt_missing_threat_fields(response):
    obs, _ = captured()
    if response == "hand":
        obs = replace(obs, abilities=(*obs.abilities, Ability("Trog's Hand", "b", True)))
        expected = "ability", "trogs_hand"
    else:
        name = {"curing": "potion of curing", "healing": "potion of heal wounds",
                "teleport": "scroll of teleportation"}[response]
        obs = replace(obs, hp=8, inventory=(*obs.inventory, Item(25, "z", name,
                      category="scroll" if response == "teleport" else "potion")),
                      statuses=("Pois",) if response == "curing" else (),
                      poison_survival=5 if response == "curing" else 8,
                      status_details=())
        expected = ("read" if response == "teleport" else "quaff"), "z"
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert (action.kind, action.value) == expected
    assert not agent.tactics.inspected_monsters


async def test_synthetic_fire_response_is_not_delayed_by_free_inspection():
    obs, _ = captured()
    obs = replace(obs, statuses=("Fire",), status_details=())
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert action.kind == "move" and action.objective == "emergency recovery"
    assert not agent.tactics.inspected_monsters


async def test_synthetic_adjacent_ordinary_berserk_commitment_reads_missing_fields_first():
    obs, _ = captured()
    lizard = next(enemy for enemy in obs.hostiles if enemy.name == "frilled lizard")
    kobold = next(enemy for enemy in obs.hostiles if enemy.name == "kobold")
    # Synthetic ordinary two-body contact using the captured public bodies.
    obs = replace(obs, hp=10, hp_max=10, poison_survival=10,
                  monsters=(lizard, replace(kobold, x=-3, y=10)))
    action = await RuleBasedAgent().decide(obs)
    assert (action.kind, action.value) == ("inspect_monster", "-3,10")
    assert any(row["kind"] == "ability" and row["value"] == "berserk"
               for row in action.assessment["candidates"])
