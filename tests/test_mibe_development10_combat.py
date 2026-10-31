"""Public stair-follower bounds, withdrawal interception, and guarded combat."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_retreat import plan_withdrawal
from dcss_bot.characters.mibe_tactics import ReturnStair, TacticalController
from dcss_bot.models import Ability, Action, Cell, Item, Monster, Observation, Resistance, Skill, Status


def captured(name):
    data = json.loads((Path(__file__).parent / "fixtures" / "recorded_development10_combat.json").read_text())
    values = dict(data["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def choose(policy, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return policy.decide(obs, cells, Counter({position: 1 for position in cells}), RuleBasedAgent._covered)


def test_far_observed_ordinary_enemy_cannot_be_assumed_an_adjacent_stair_follower():
    before, obs = captured("last_seen_far_iguana"), captured("far_iguana_at_stair")
    policy = TacticalController()
    policy.observe(before)
    policy.previous = before
    policy.last_action = Action("move", "h", objective="planned retreat")
    feature = next(cell.feature for cell in obs.cells if (cell.x,cell.y) == obs.position)
    policy.return_stairs[(obs.location,feature)] = ReturnStair(obs.position,"Dungeon:3",42,obs.turn-100,True)
    action = choose(policy, obs)
    assert action.kind == "escape_upstairs"


def test_missing_last_seen_position_never_supplies_safe_follower_evidence():
    obs = captured("far_iguana_at_stair")
    policy = TacticalController()
    policy.recent_hostiles["iguana"] = (1,obs.turn-1)
    assert policy._unresolved_can_reach_stair(obs,1)


def test_distant_faster_pursuer_allows_only_nonintercepted_withdrawal_prefix():
    cells = tuple(Cell(x,0,46 if x == 0 else 33,visible=True) for x in range(11))
    obs = Observation(hp=40,hp_max=40,position=(3,0),cells=cells,
                      monsters=(Monster(1,"adder",10,0,1,description="Bite  4 (poison)\n"),))
    mapping = {(cell.x,cell.y):cell for cell in cells}
    visits = Counter({position:1 for position in mapping})
    plan = plan_withdrawal(obs,mapping,visits,RuleBasedAgent._covered)
    assert plan is not None and plan.path == ((2,0),(1,0),(0,0))
    near = replace(obs,monsters=(replace(obs.monsters[0],x=5),))
    assert plan_withdrawal(near,mapping,visits,RuleBasedAgent._covered) is None


def test_native_healthy_weak_poison_wave_uses_rage_before_taking_damage():
    obs = captured("poison_wave")
    policy = TacticalController()
    policy.encounter_start = obs.turn-8
    policy.encounter_last_seen = obs.turn
    policy.ineffective_retreats = 2
    action = choose(policy,obs)
    assert (action.kind,action.value) == ("ability","berserk")
    assert any(ability.name == "Berserk" and ability.available for ability in obs.abilities)


def test_native_hand_light_allows_one_guarded_short_caster_turn_without_inventing_will_snapshot():
    obs = captured("prepared_short_caster")
    assert obs.will is None
    policy = TacticalController()
    policy.encounter_start = obs.turn-3
    policy.encounter_last_seen = obs.turn
    policy.lure_steps = 2
    policy.ineffective_retreats = 2
    action = choose(policy,obs)
    assert action.kind == "wait" and action.objective == "defend choke"
    assert policy.lure_steps == 3
