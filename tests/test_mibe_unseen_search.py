"""Synthetic boundaries for native attack-without-moving encounter handling."""
from collections import Counter
from dataclasses import replace

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Action, Cell, DIRECTIONS, Observation


def room():
    return Observation(species="Minotaur", god="Trog", place="Dungeon", depth=4,
                       turn=100, hp=45, hp_max=45, xl=5, input_mode=1,
                       cells=tuple(Cell(x,y,33,".",True) for x in range(-1,2) for y in range(-1,2)))


def choose(policy, obs):
    return policy.decide(obs,{(c.x,c.y):c for c in obs.cells},Counter(),RuleBasedAgent._covered)


def test_unlocated_melee_search_exhausts_directions_without_moving_or_looping_forever():
    policy = TacticalController()
    obs = room()
    policy.routine(obs,Action("wait"))
    directions = set()
    for _ in DIRECTIONS:
        obs = replace(obs,turn=obs.turn+1,messages=obs.messages + (
            "You swing at nothing.","Something hits you but does no damage."))
        action = choose(policy,obs)
        assert action.kind == "attack" and action.objective == "defend unseen"
        assert action.value not in directions
        directions.add(action.value)
    obs = replace(obs,turn=obs.turn+1,messages=obs.messages + (
        "You swing at nothing.","Something hits you but does no damage."))
    assert choose(policy,obs).kind == "pause"


def test_native_contact_renews_only_the_successful_command_not_an_invisible_monster_location():
    policy = TacticalController()
    obs = room()
    policy.routine(obs,Action("wait"))
    obs = replace(obs,turn=101,messages=("Something hits you but does no damage.",))
    first = choose(policy,obs)
    assert first.kind == "attack"
    contacted = replace(obs,turn=102,messages=obs.messages + (
        "You miss something.","Something hits you but does no damage."))
    second = choose(policy,contacted)
    assert second.kind == "attack" and second.value == first.value
    # An axe may cleave an unseen secondary target. A hit/miss cannot identify
    # which square or former monster it reached; only retry the native command.
    assert policy.unseen_contact is None
    quiet = replace(contacted,turn=103)
    assert choose(policy,quiet).objective != "defend unseen"
