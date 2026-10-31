"""Native Conf and exhaustion must not permanently disable Trog powers."""
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Ability, Cell, Item, Monster, Observation, Resistance, Skill, Status


FIXTURE = Path(__file__).parent / "fixtures" / "recorded_development12_abilities.json"


def captured(name):
    values = dict(json.loads(FIXTURE.read_text())["observations"][name])
    for field, factory in (("cells",Cell),("monsters",Monster),("inventory",Item),
                           ("abilities",Ability),("resistances",Resistance),
                           ("skills",Skill),("status_details",Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field,()))
    for field in ("position","messages","statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


async def test_native_conf_does_not_inspect_greyed_powers_and_refreshes_after_recovery():
    agent = RuleBasedAgent()
    await agent.decide(captured("cooldown"))
    confused = captured("confused")
    assert "Conf" in confused.statuses
    action = await agent.decide(confused)
    assert (action.kind,action.value) != ("inspect","abilities")
    recovered = captured("recovered")
    assert not recovered.statuses and not any(a.available for a in recovered.abilities)
    action = await agent.decide(recovered)
    assert (action.kind,action.value) == ("inspect","abilities")
    # A failed native response must not cause a zero-turn menu loop.
    action = await agent.decide(recovered)
    assert (action.kind,action.value) != ("inspect","abilities")


async def test_cold_resume_with_native_greyed_conf_menu_requires_a_fresh_menu_after_clear():
    agent = RuleBasedAgent()
    confused = captured("greyed")
    assert "Conf" in confused.statuses and not any(a.available for a in confused.abilities)
    action = await agent.decide(confused)
    assert (action.kind,action.value) != ("inspect","abilities")
    action = await agent.decide(captured("recovered"))
    assert (action.kind,action.value) == ("inspect","abilities")


async def test_exhaustion_expiry_refreshes_native_rage_availability_without_a_piety_change():
    agent = RuleBasedAgent()
    exhausted = captured("cooldown")
    assert "-Berserk" in exhausted.statuses
    assert any(a.name == "Berserk" and not a.available for a in exhausted.abilities)
    await agent.decide(exhausted)
    # Explicitly synthetic world response: the two native cooldown lights have
    # ended, but the old menu cache remains until the selected inspection runs.
    recovered = replace(exhausted,turn=exhausted.turn+40,time=exhausted.time+400,
                        statuses=(),status_details=())
    action = await agent.decide(recovered)
    assert (action.kind,action.value) == ("inspect","abilities")


async def test_reconnection_style_public_data_loss_refreshes_once_without_rank_or_turn_change():
    agent = RuleBasedAgent()
    ready = captured("ready")
    await agent.decide(ready)
    # Synthetic reconnection snapshot: native actor/menu metadata was reset,
    # while the character's actual turn, god and rank did not change.
    missing = replace(ready,abilities=())
    action = await agent.decide(missing)
    assert (action.kind,action.value) == ("inspect","abilities")
    action = await agent.decide(missing)
    assert (action.kind,action.value) != ("inspect","abilities")
