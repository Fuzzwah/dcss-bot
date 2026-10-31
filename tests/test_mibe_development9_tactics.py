"""Actual failed rage, intercepted retreat and a net incorrectly treated as paralysis."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from legacy_replay import decide_legacy_tactics

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_retreat import plan_withdrawal
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status
from dcss_bot.terrain import UPSTAIRS


def captured(name):
    values = dict(json.loads((Path(__file__).parent / "fixtures" / "recorded_development9_tactics.json").read_text())["observations"][name])
    for field, factory in (("cells", Cell), ("monsters", Monster), ("inventory", Item),
                           ("abilities", Ability), ("resistances", Resistance),
                           ("skills", Skill), ("status_details", Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field, ()))
    values["position"] = tuple(values["position"])
    for field in ("messages", "statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def decide(obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return decide_legacy_tactics(TacticalController(), obs, cells, Counter({position: 2 for position in cells}), RuleBasedAgent._covered)


def test_recorded_healthy_single_front_queue_can_retry_rage_after_native_misfire():
    obs = captured("ordinary_queue_after_misfire")
    action = decide(obs)
    assert (action.kind, action.value) == ("ability", "berserk")
    assert any(ability.name == "Berserk" and ability.available and ability.failure < 100
               for ability in obs.abilities)


def test_recorded_net_with_reaching_enemy_chooses_executable_struggle_not_pause():
    obs = captured("held_spear")
    action = decide(obs)
    assert action.kind == "move" and action.value in DIRECTIONS
    assert action.objective == "escape restraint"


def test_known_healing_preempts_net_struggle_at_critical_health():
    obs = captured("held_spear")
    obs = replace(obs, inventory=(*obs.inventory, Item(99, "z", "potion of heal wounds", category="potion")))
    action = decide(obs)
    assert (action.kind, action.value) == ("quaff", "z")


async def test_net_release_does_not_blacklist_the_only_real_exploration_step():
    cells = tuple(Cell(x,y,33 if y == 0 and x >= 0 else 2,visible=True)
                  for x in range(-1,3) for y in range(-1,2))
    obs = Observation(turn=10,hp=20,hp_max=20,xl=3,place="Dungeon",depth=2,position=(0,0),
                      statuses=("Held",),cells=cells)
    agent = RuleBasedAgent()
    agent.development._inspection_key = (obs.xl,obs.piety_rank,())
    agent.development._last_skill_inspection = obs.turn
    action = await agent.decide(obs)
    assert action.objective == "escape restraint"
    released = replace(obs,turn=11,statuses=(),messages=("You cut through the net.",))
    assert (await agent.decide(released)).kind == "explore"
    # A synthetic native no-progress reply must retain the legal manual step;
    # the earlier struggle was not a failed movement into this floor square.
    stopped = replace(released,native_automation="explore",native_result="partial")
    action = await agent.decide(stopped)
    assert action.kind == "move" and DIRECTIONS[action.value] == (1,0)
    assert action.objective == "explore"


def test_stair_route_cannot_walk_into_an_approaching_interceptor():
    obs = Observation(turn=10,hp=50,hp_max=50,position=(0,0),
                      cells=tuple(Cell(x,0,46 if x == 6 else 33,visible=True) for x in range(7)),
                      monsters=(Monster(1,"iguana",3,0,0,description="Attack  Max Damage\nBite  14\n"),))
    cells = {(cell.x,cell.y):cell for cell in obs.cells}
    assert plan_withdrawal(obs,cells,Counter({position:1 for position in cells}),RuleBasedAgent._covered,max_steps=1) is None


def test_captured_withdrawal_does_not_label_entering_iguana_contact_an_escape():
    obs = captured("intercepted_withdrawal")
    action = decide(obs)
    if action.kind == "move" and action.objective in {"retreat", "planned retreat"}:
        dx,dy = DIRECTIONS[action.value]
        target = obs.position[0]+dx,obs.position[1]+dy
        assert any(cell.visible and cell.walkable and not cell.cloud and (cell.x,cell.y) == target for cell in obs.cells)
        assert all(enemy.distance(target) > mechanics(enemy).reach+int(mechanics(enemy).movement_speed)
                   for enemy in obs.hostiles)
    else:
        assert action.kind in {"wait", "ability", "blink", "read", "quaff", "pause"}


async def test_released_net_duel_reads_missing_ability_availability_instead_of_assuming_no_power():
    obs = captured("released_reaching_duel")
    agent = RuleBasedAgent()
    action = await agent.decide(obs)
    assert (action.kind,action.value) == ("inspect","abilities")
    # A failed/empty response cannot create an infinite zero-turn inspection loop.
    action = await agent.decide(obs)
    assert (action.kind,action.value) != ("inspect","abilities")


def test_known_healing_beats_last_resort_rage_in_the_released_net_duel():
    obs = captured("released_reaching_duel")
    obs = replace(obs,inventory=(*obs.inventory,Item(99,"z","potion of heal wounds",category="potion")))
    action = decide(obs)
    assert (action.kind,action.value) == ("quaff","z")


def test_poisoned_post_net_character_keeps_potion_access_instead_of_last_resort_rage():
    obs = captured("released_reaching_duel")
    obs = replace(obs,statuses=(*obs.statuses,"Pois"),
                  inventory=(*obs.inventory,Item(99,"z","potion of curing",category="potion")))
    action = decide(obs)
    assert (action.kind,action.value) == ("quaff","z")


async def test_changed_piety_refreshes_ability_unlocks_before_another_combat_action():
    obs = captured("held_spear")
    agent = RuleBasedAgent()
    await agent.decide(obs)
    changed = replace(obs,turn=obs.turn+1,piety_rank=obs.piety_rank+1)
    action = await agent.decide(changed)
    assert (action.kind,action.value) == ("inspect","abilities")


def test_native_observed_available_rage_is_used_for_the_otherwise_stalled_ordinary_duel():
    obs = captured("observed_last_resort_duel")
    action = decide(obs)
    assert (action.kind,action.value) == ("ability","berserk")
    assert any(ability.name == "Berserk" and ability.available and ability.failure < 100
               for ability in obs.abilities)


def test_raging_low_hp_duellist_attacks_instead_of_wasting_rage_on_emergency_backsteps():
    obs = captured("raging_adjacent_duel")
    action = decide(obs)
    assert action.kind == "move" and action.objective == "combat"
    dx,dy = DIRECTIONS[action.value]
    target = obs.position[0]+dx,obs.position[1]+dy
    assert any((enemy.x,enemy.y) == target for enemy in obs.hostiles)


def test_known_nearby_small_caster_can_be_fought_proactively_instead_of_freezing():
    obs = captured("natasha_solo")
    action = decide(obs)
    assert (action.kind,action.value) == ("ability","berserk")
    assert any(ability.name == "Berserk" and ability.available for ability in obs.abilities)


def test_short_caster_commitment_does_not_expand_to_blink_invisibility_or_hard_control():
    original = captured("natasha_solo")
    for spell in ("Blink","Invisibility","Confuse","Paralyse","Banishment"):
        enemy = replace(original.hostiles[0],name="unfamiliar caster",
                        spells=(*original.hostiles[0].spells,spell),
                        description=original.hostiles[0].description+f"\n{spell}: ")
        action = decide(replace(original,monsters=(enemy,)))
        assert (action.kind,action.value) != ("ability","berserk")


def engaged_summoner(obs):
    controller = TacticalController()
    controller.encounter_start = obs.turn-6
    controller.encounter_last_seen = obs.turn
    controller.ineffective_retreats = 2
    controller.lure_steps = 2
    return controller


def summoner_action(controller,obs):
    cells = {(cell.x,cell.y):cell for cell in obs.cells}
    return controller.decide(obs,cells,Counter({position:2 for position in cells}),RuleBasedAgent._covered)



def test_minor_summoner_holding_is_bounded_and_not_used_for_unknown_greater_summons():
    obs = captured("minor_summoner_dead_end")
    # Synthetic no-anchor variant: preserve captured actors but remove the
    # upstairs goal so a newly permitted distant-fast retreat cannot preempt it.
    obs = replace(obs,cells=tuple(replace(cell,feature=33) if cell.feature in UPSTAIRS else cell
                                  for cell in obs.cells))
    action = summoner_action(engaged_summoner(obs),obs)
    assert action.kind in {"wait","move"}
    if action.kind == "move":
        dx,dy = DIRECTIONS[action.value]
        assert any((cell.x,cell.y) == (obs.position[0]+dx,obs.position[1]+dy)
                   and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)
    controller = engaged_summoner(obs)
    controller.lure_steps = controller.config.lure_step_limit
    assert summoner_action(controller,obs).kind != "wait"
    boss,companion = obs.hostiles
    greater = replace(boss,name="unfamiliar summoner",spells=("Summon Demon","Blink"),
                      description=boss.description.replace("Summon Minor Demon","Summon Demon"))
    unknown = replace(obs,monsters=(greater,companion))
    assert summoner_action(engaged_summoner(unknown),unknown).kind != "wait"


def test_prepared_healthy_character_can_melee_a_minor_summoner_without_a_false_hydra_ban():
    obs = captured("minor_summoner_dead_end")
    boss,companion = obs.hostiles
    cell = min((cell for cell in obs.cells if cell.visible and cell.walkable and not cell.cloud
                and max(abs(cell.x-obs.position[0]),abs(cell.y-obs.position[1])) == 1
                and (cell.x,cell.y) != (companion.x,companion.y)),
               key=lambda cell:boss.distance((cell.x,cell.y)))
    close = replace(obs,turn=obs.turn+2,monsters=(replace(boss,x=cell.x,y=cell.y),companion))
    action = summoner_action(engaged_summoner(close),close)
    assert action.kind == "move" and action.objective == "combat"
    dx,dy = DIRECTIONS[action.value]
    assert (close.position[0]+dx,close.position[1]+dy) == (cell.x,cell.y)
