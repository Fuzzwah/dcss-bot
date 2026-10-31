from dataclasses import FrozenInstanceError, replace
import sys
from types import ModuleType

import pytest

from dcss_bot.agents import make_agent
from dcss_bot.characters import get_character
from dcss_bot.characters.base import CharacterProfile
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.models import Action, Observation


class AlternativeAgent:
    async def decide(self, observation):
        return Action("wait", reason="Test-only decision engine")


@pytest.fixture
def alternative():
    return CharacterProfile(
        identifier="test-only", abbreviation="Test", species="Human", background="Fighter",
        god="Okawaru", weapon="long sword", preferred_stat="intelligence",
        agent_factory=AlternativeAgent, rc_options=(("autopickup", "$"),),
    )




def test_default_factory_creates_independent_rule_v3_state():
    first = make_agent("rule", PROFILE)
    second = make_agent("rule", PROFILE)
    assert isinstance(first, RuleBasedAgent)
    assert isinstance(second, RuleBasedAgent)
    assert first is not second
    first.visits[(1, 2)] = 3
    first.equipment_attempts.add(("wear", 1, "+0 chain mail"))
    first.previous_hp = 10
    assert not second.visits
    assert not second.equipment_attempts
    assert second.previous_hp is None


def test_character_with_invalid_policy_is_rejected(alternative):
    with pytest.raises(ValueError, match="async decide"):
        make_agent("rule", replace(alternative, agent_factory=object))


@pytest.mark.parametrize("labels,expected", [
    (("Minotaur", "Human"), ("a", "b")),
    (("Berserker", "Fighter"), ("a", "b")),
    (("hand axe", "long sword"), ("a", "b")),
])
@pytest.mark.parametrize("section", ["main-items", "sub-items"])
def test_creation_is_selected_by_profile_not_transport(alternative, labels, expected, section):
    observation = Observation(menu={section: {"buttons": [
        {"label": labels[0], "hotkey": ord("a")},
        {"labels": [labels[1]], "hotkey": "b"},
    ]}})
    for profile, key in zip((PROFILE, alternative), expected):
        action = profile.creation_action(observation)
        assert (action.kind, action.value) == ("menu", key)


@pytest.mark.parametrize("menu", [
    {},
    {"main-items": {"buttons": [{"label": "Random", "hotkey": "a"}]}},
    {"sub-items": {"buttons": [{"label": "NotMinotaur", "hotkey": "b"}]}},
])
def test_unknown_creation_choices_pause_without_guessing(menu):
    action = PROFILE.creation_action(Observation(menu=menu))
    assert (action.kind, action.value) == ("pause", "")


@pytest.mark.parametrize("as_menu", [False, True])
def test_advancement_uses_character_preference(alternative, as_menu):
    prompt = "Increase (S)trength, (I)ntelligence or (D)exterity?"
    observation = (Observation(menu={"title": prompt}) if as_menu
                   else Observation(input_mode=7, prompt=prompt))
    assert PROFILE.advancement_action(observation).value == "S"
    assert alternative.advancement_action(observation).value == "I"
    dexterity = replace(alternative, preferred_stat="dexterity")
    assert dexterity.advancement_action(observation).value == "D"


@pytest.mark.parametrize("observation", [
    Observation(),
    Observation(input_mode=7, prompt="Which item?"),
    Observation(menu={"title": "Unknown menu"}),
    Observation(input_mode=7, prompt="Increase strength?"),
])
def test_unknown_advancement_does_not_select_a_stat(observation):
    assert PROFILE.advancement_action(observation) is None


@pytest.mark.parametrize("menu", [{}, {"title": "Which item?"}])
def test_stale_attribute_prompt_does_not_answer_other_input(menu):
    observation = Observation(
        input_mode=1, prompt="Increase (S)trength, (I)ntelligence or (D)exterity?", menu=menu,
    )
    assert PROFILE.advancement_action(observation) is None


def test_profile_settings_and_metadata_are_owned_and_immutable(alternative):
    settings = alternative.rc_settings()
    assert "weapon" not in replace(alternative, weapon=None).rc_settings()
    metadata = alternative.metadata()
    metadata["rc_options"]["autopickup"] = "changed"
    assert alternative.rc_settings() == settings
    with pytest.raises(FrozenInstanceError):
        alternative.species = "Minotaur"


def test_fresh_start_requires_native_background_evidence():
    observation = Observation(species="Minotaur", god="Trog", hp=20, hp_max=20,
                              messages=("Welcome, Tester the Minotaur Berserker.",))
    assert PROFILE.matches_observation(observation)
    assert PROFILE.matches_start(observation)
    wrong_background = replace(observation, messages=("Welcome, Tester the Minotaur Fighter.",))
    # Species and current religion cannot establish the original background.
    assert PROFILE.matches_observation(wrong_background)
    assert not PROFILE.matches_start(wrong_background)
    assert not PROFILE.matches_start(replace(observation, messages=()))
    assert not PROFILE.matches_start(replace(observation, messages=("Welcome back, Tester the Minotaur Berserker.",)))
    assert not PROFILE.matches_start(replace(observation, species="Human"))
    assert not PROFILE.matches_start(replace(observation, god="Okawaru"))




@pytest.mark.parametrize("selector", [
    "unknown", "_dcss_bot_missing_character_test_:PROFILE", "dcss_bot.characters.base:MISSING",
    "dcss_bot.characters.base:CharacterProfile",
])
def test_unknown_or_invalid_character_modules_are_rejected(selector):
    with pytest.raises(ValueError):
        get_character(selector)




def test_invalid_decision_engine_is_rejected(monkeypatch):
    module = ModuleType("test_invalid_engine")
    module.Engine = object
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ValueError, match="async decide"):
        make_agent("test_invalid_engine:Engine", PROFILE)
    with pytest.raises(ValueError, match="Agent must be"):
        make_agent("unknown", PROFILE)
