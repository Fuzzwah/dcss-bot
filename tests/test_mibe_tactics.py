"""Observable encounter transitions and legal survival actions, not score snapshots."""
from collections import Counter
from dataclasses import replace

import pytest

from legacy_replay import decide_legacy_observation, decide_legacy_tactics

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation


def ground():
    return tuple(Cell(x, y, 33, ".", True) for x in range(-4, 5) for y in range(-2, 3))


def encounter(*monsters, hp=60, hp_max=60, inventory=(), statuses=(), **fields):
    return replace(Observation(species="Minotaur", god="Trog", place="Dungeon", depth=8,
                       xl=10, turn=100, hp=hp, hp_max=hp_max, ac=12, ev=10,
                       input_mode=1, cells=ground(), monsters=monsters,
                       inventory=inventory, statuses=statuses), **fields)


def choose(controller, obs, *, legacy=False):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    visits = Counter({position: 2 for position in cells})
    if legacy:
        return decide_legacy_tactics(controller,obs,cells,visits,RuleBasedAgent._covered)
    return controller.decide(obs, cells, visits, RuleBasedAgent._covered)


def attacking(obs, action):
    if action.kind != "move":
        return False
    dx, dy = DIRECTIONS[action.value]
    position = (obs.position[0] + dx, obs.position[1] + dy)
    return any((monster.x, monster.y) == position for monster in obs.hostiles)


def adapter_for(obs):
    """Populate real sparse state with native-shaped public packets."""
    adapter = WebTilesAdapter("ws://unused", "ScenarioBot", "unused", character=PROFILE)
    packet = {"msg": "player", "species": "Minotaur", "god": "Trog",
              "hp": obs.hp, "hp_max": obs.hp_max, "xl": obs.xl, "turn": obs.turn,
              "pos": {"x": obs.position[0], "y": obs.position[1]},
              "status": [{"light": status, "text": status} for status in obs.statuses],
              "inv": {str(item.slot): {"name": item.name, "quantity": item.quantity,
                                       "letter": item.letter} for item in obs.inventory}}
    adapter.state.apply(packet)
    cells = []
    for cell in obs.cells:
        raw = {"x": cell.x, "y": cell.y, "f": cell.feature, "g": cell.glyph,
               "t": {"bg": 1, "cloud": int(cell.cloud)}}
        monster = next((m for m in obs.monsters if (m.x, m.y) == (cell.x, cell.y)), None)
        if monster:
            raw["mon"] = {"id": monster.id, "name": monster.name,
                          "att": monster.attitude, "threat": monster.threat}
        cells.append(raw)
    adapter.state.apply({"msg": "map", "cells": cells})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return adapter


def test_merging_slimes_interrupt_an_attack_instead_of_committing_to_larger_melee():
    controller = TacticalController()
    single = encounter(Monster(1, "slime creature", 1, 0), hp=150, hp_max=150,
                       inventory=(Item(1, "b", "scroll of teleportation"),))
    assert attacking(single, choose(controller, single, legacy=True))
    merged = replace(single, turn=101, hp=120,
                     monsters=(Monster(1, "large slime creature", 1, 0),
                               Monster(2, "slime creature", 1, 1)))
    action = choose(controller, merged)
    assert not attacking(merged, action)
    assert (action.kind, action.value) == ("read", "b")
    assert any(threat["avoid_melee"] for threat in action.assessment["assessment"]["threats"])


def test_titanic_slime_at_low_hp_uses_known_escape_before_another_attack():
    obs = encounter(Monster(1, "titanic slime creature", 1, 0, 3), hp=12, hp_max=90,
                    inventory=(Item(1, "b", "scroll of teleportation"),))
    action = choose(TacticalController(), obs)
    assert (action.kind, action.value) == ("read", "b")
    assert not attacking(obs, action)
    assert action.assessment["assessment"]["expected_loss"] > obs.hp


async def test_visible_followers_on_upstairs_do_not_authorize_stair_traversal():
    obs = encounter(Monster(1, "orc", 1, 0), hp=40, hp_max=60)
    obs = replace(obs, cells=tuple(replace(cell, feature=45) if (cell.x, cell.y) == (0, 0)
                                   else cell for cell in obs.cells))
    action = await decide_legacy_observation(RuleBasedAgent(), obs)
    assert action.kind not in {"stairs_up", "stairs_down"}
    adapter = adapter_for(obs)
    keys = []

    async def key(value):
        keys.append(value)
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="hostiles"):
        await adapter.dispatch(Action("stairs_up"))
    assert keys == []


def test_dangerous_stair_arrival_is_remembered_when_enemies_leave_visibility():
    controller = TacticalController()
    source = encounter(hp=60, hp_max=60)
    source = replace(source, depth=7, position=(0, 0),
                     cells=tuple(replace(cell,feature=42) if (cell.x,cell.y)==(0,0) else cell for cell in source.cells))
    controller.routine(source, Action("stairs_down"))
    arrival = encounter(Monster(1, "two-headed ogre", 1, 0, 3), hp=50, hp_max=60)
    controller.observe(arrival)
    returned = replace(source, turn=102, position=(5,5), monsters=(),
                       cells=tuple(replace(cell,x=cell.x+5,y=cell.y+5) for cell in source.cells))
    controller.rebase(returned.location,(5,5))
    assert returned.position not in controller.safe_stairs(returned, {returned.position})
    assert returned.position in controller.safe_stairs(replace(returned,xl=returned.xl+2), {returned.position})


async def test_active_teleport_is_not_restarted_by_policy_or_generic_read_dispatch():
    obs = encounter(Monster(1, "titanic slime creature", 1, 0, 3), hp=12, hp_max=90,
                    statuses=("Tele",), inventory=(Item(1, "b", "scroll of teleportation"),))
    action = choose(TacticalController(), obs)
    assert (action.kind, action.value) != ("read", "b")
    assert not attacking(obs, action)
    adapter = adapter_for(obs)
    keys = []

    async def key(value):
        keys.append(value)
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("read", "b"))
    assert keys == []


def test_berserk_expiry_with_enemies_restores_legal_recovery_not_recommitment():
    controller = TacticalController()
    inventory = (Item(2, "c", "potion of heal wounds"),)
    abilities = (Ability("Berserk", "a", True),)
    raging = encounter(Monster(1, "orc", 1, 0), hp=28, hp_max=90,
                       statuses=("Berserk",), inventory=inventory, abilities=abilities)
    action = choose(controller, raging)
    assert action.kind not in {"quaff", "read", "ability", "blink"}
    expired = replace(raging, turn=101, hp=13, hp_max=60, statuses=("Slow", "-Berserk"))
    action = choose(controller, expired)
    assert (action.kind, action.value) == ("quaff", "c")


async def test_poisoned_berserk_cannot_quaff_curing_until_berserk_ends():
    inventory = (Item(2, "c", "potion of curing"),)
    raging = encounter(Monster(1, "orc", 1, 0), hp=15, hp_max=90,
                       statuses=("Berserk", "Pois"), poison_survival=3, inventory=inventory)
    controller = TacticalController()
    assert choose(controller, raging).kind not in {"quaff", "read", "ability", "blink"}
    adapter = adapter_for(raging)
    keys = []

    async def key(value):
        keys.append(value)
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="status"):
        await adapter.dispatch(Action("quaff", "c"))
    assert keys == []
    recovered = replace(raging, turn=101, hp=10, hp_max=60, statuses=("Pois", "-Berserk"))
    action = choose(controller, recovered)
    assert (action.kind, action.value) == ("quaff", "c")


def test_ordinary_axe_against_hydra_requires_nonmelee_response_not_weapon_swap():
    obs = encounter(Monster(1, "five-headed hydra", 1, 0, 3), hp=60, hp_max=60,
                    inventory=(Item(0, "a", "+4 war axe", equipped=True),
                               Item(1, "b", "scroll of teleportation"),
                               Item(2, "c", "+0 war axe of flaming")))
    action = choose(TacticalController(), obs)
    assert not attacking(obs, action)
    assert action.kind not in {"wield", "wear", "put_on"}
    assert (action.kind, action.value) == ("read", "b")


@pytest.mark.parametrize("victim", ["five-headed hydra", "large slime creature"])
async def test_axe_cleave_refuses_forbidden_bystander_before_dispatch(victim):
    obs = encounter(Monster(1, "orc", 1, 0), Monster(2, victim, 1, 1),
                    inventory=(Item(0, "a", "+4 war axe", equipped=True),))
    action = choose(TacticalController(), obs, legacy=True)
    assert not attacking(obs, action)
    adapter = adapter_for(obs)

    async def command(message):
        key = chr(message["keycode"])
        dx,dy = DIRECTIONS[key]
        position = obs.position[0]+dx,obs.position[1]+dy
        assert any(cell.visible and cell.walkable and not cell.cloud
                   and (cell.x,cell.y) == position for cell in obs.cells)
        assert all((enemy.x,enemy.y) != position for enemy in obs.hostiles)
        adapter.state.apply({"msg":"player","turn":obs.turn+1,
                             "pos":{"x":position[0],"y":position[1]}})
        return adapter.state.observation()

    adapter._command = command
    assert action.kind in {"move","pause"}
    result = await adapter.dispatch(action)
    if action.kind == "move":
        assert result.position != obs.position and result.turn == obs.turn+1


@pytest.mark.parametrize("weapon", ["+4 war axe of flaming", "+4 war axe (flame)"])
def test_flaming_axe_can_attack_orc_beside_hydra(weapon):
    obs = encounter(Monster(1, "orc", 1, 0), Monster(2, "five-headed hydra", 1, 1),
                    hp=1000, hp_max=1000,
                    inventory=(Item(0, "a", weapon, equipped=True),))
    action = choose(TacticalController(), obs, legacy=True)
    assert attacking(obs, action)


def test_absent_weapon_inventory_does_not_invent_axe_cleave():
    obs = encounter(Monster(1, "orc", 1, 0), Monster(2, "five-headed hydra", 1, 1),
                    hp=300, hp_max=300)
    action = choose(TacticalController(), obs)
    assert any(candidate["kind"] == "move" and candidate["value"] == "l"
               for candidate in action.assessment["candidates"])


@pytest.mark.parametrize("status,potion", [("Might", "potion of might"),
                                          ("Resist", "potion of resistance")])
def test_active_preparation_status_does_not_offer_another_potion(status, potion):
    obs = encounter(Monster(1, "ice dragon", 1, 0, 3), hp=100, hp_max=100,
                    inventory=(Item(0, "a", potion),))
    initial = choose(TacticalController(), obs)
    assert any(candidate["kind"] == "quaff" and candidate["value"] == "a"
               for candidate in initial.assessment["candidates"])
    active = replace(obs, turn=101, statuses=(status,))
    action = choose(TacticalController(), active)
    assert (action.kind, action.value) != ("quaff", "a")
    assert not any(candidate["kind"] == "quaff" and candidate["value"] == "a"
                   for candidate in action.assessment["candidates"])


@pytest.mark.parametrize("kind,name", [("quaff", "potion of curing"),
                                      ("read", "scroll of teleportation"),
                                      ("zap", "wand of acid (3)"),
                                      ("throw", "javelin")])
def test_typed_irrelevant_item_cannot_supply_a_tactical_action(kind, name):
    obs = encounter(Monster(1, "orc", 1, 0), hp=10, statuses=("Pois",),
                    inventory=(Item(0, "a", name, category="armour"),))
    action = choose(TacticalController(), obs)
    assert (action.kind, action.value.split(":")[0]) != (kind, "a")
    assert not any(candidate["kind"] == kind and candidate["value"].split(":")[0] == "a"
                   for candidate in action.assessment["candidates"])
    assert TacticalController.item(obs, name, kind=kind) is None


@pytest.mark.parametrize("feature", [42, 43, 44, 46, 47, 48, 71, 88])
def test_remembered_stairs_require_a_current_stair_feature(feature):
    controller = TacticalController()
    obs = encounter(cells=(Cell(0, 0, feature, ">", True),
                           Cell(1, 0, 33, ".", True)))
    assert controller.safe_stairs(obs, {(0, 0), (1, 0), (2, 0)}) == {(0, 0)}
    controller.unsafe_stairs[(obs.location, feature)] = obs.xl + 2
    assert controller.safe_stairs(obs, {(0, 0)}) == set()


def test_disabling_caster_uses_observed_trogs_hand_without_blind_berserk():
    obs = encounter(Monster(1, "orc wizard", 4, 0), hp=60, hp_max=60, will=0,
                    abilities=(Ability("Berserk", "a", True), Ability("Trog's Hand", "b", True)))
    action = choose(TacticalController(), obs)
    assert (action.kind, action.value) == ("inspect_monster", "4,0")
    observed = replace(obs, monsters=(replace(obs.monsters[0], description="A wizard with confusing magic."),))
    action = choose(TacticalController(), observed)
    assert (action.kind, action.value) == ("ability", "trogs_hand")
    assert any(threat["disabling"] for threat in action.assessment["assessment"]["threats"])


async def test_unexpected_threat_interrupts_exploration_on_the_next_observation():
    agent = RuleBasedAgent()
    initial = encounter()
    # Acknowledge the missing ability-menu capture, then satisfy the real,
    # turn-free development inspection without mocking the planner.
    inspection = await decide_legacy_observation(agent, initial)
    assert inspection.kind == "inspect" and inspection.objective == "development"
    exploring = await agent.decide(initial)
    assert exploring.objective == "explore"
    unexpected = replace(initial, turn=101, hp=35,
                         monsters=(Monster(1, "two-headed ogre", 1, 0, 3),),
                         inventory=(Item(1, "b", "scroll of teleportation"),))
    action = await agent.decide(unexpected)
    assert action.objective != "explore"
    assert (action.kind, action.value) == ("read", "b")


def test_known_blink_escape_selects_clear_visible_separation_not_unknown_ground():
    obs = encounter(Monster(1, "titanic slime creature", 1, 0, 3), hp=10, hp_max=90,
                    inventory=(Item(1, "b", "scroll of blinking"),))
    obs = replace(obs, cells=obs.cells + (Cell(-8, 0, 33, ".", False),
                                        Cell(-7, 0, 33, ".", True, True)))
    action = choose(TacticalController(), obs)
    assert action.kind == "blink"
    letter, target = action.value.split(":")
    x, y = map(int, target.split(","))
    assert letter == "b"
    assert any(cell.visible and cell.walkable and not cell.cloud and (cell.x, cell.y) == (x, y)
               for cell in obs.cells)
    assert all(monster.distance((x, y)) >= 3 for monster in obs.hostiles)


async def test_chosen_known_curing_dispatches_through_native_drink_prompt():
    obs = encounter(Monster(1, "adder", 1, 0), hp=12, hp_max=60,
                    statuses=("Pois",), poison_survival=2,
                    inventory=(Item(2, "c", "potion of curing"), Item(3, "d", "ruby potion")))
    action = choose(TacticalController(), obs)
    assert (action.kind, action.value) == ("quaff", "c")
    adapter = adapter_for(obs)
    keys = []

    async def key(value):
        keys.append(value)
        if value == "q":
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Drink which item?"}]})
            adapter.state.apply({"msg": "input_mode", "mode": 7})
        elif value == "c":
            adapter.state.apply({"msg": "player", "hp": 20,
                                 "inv": {"2": {"quantity": 0}}, "status": []})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(action)
    assert keys == ["q", "c"]
    assert result.hp == 20
    assert not any(item.letter == "c" for item in result.inventory)
    assert any(item.letter == "d" and item.name == "ruby potion" for item in result.inventory)


def test_blink_remains_available_when_every_adjacent_square_is_occupied():
    ring = tuple(Monster(n,"orc warrior",dx,dy,3)
                 for n,(dx,dy) in enumerate(DIRECTIONS.values(),1))
    obs = encounter(*ring,hp=10,inventory=(Item(1,"b","scroll of blinking"),))
    action = choose(TacticalController(),obs)
    assert action.kind == "blink"
    letter,target = action.value.split(":")
    x,y = map(int,target.split(","))
    assert letter == "b"
    assert any(cell.visible and cell.walkable and (cell.x,cell.y)==(x,y) for cell in obs.cells)
    assert all(monster.distance((x,y))>=3 for monster in obs.monsters)


def test_brothers_can_spawn_beyond_an_occupied_adjacent_ring_and_neutral_is_not_support():
    ring = tuple(Monster(n,"orc warrior",dx,dy,3)
                 for n,(dx,dy) in enumerate(DIRECTIONS.values(),1))
    obs = encounter(*ring,Monster(20,"yak",-3,0,attitude=1),hp=30,
                    abilities=(Ability("Brothers in Arms","c",True,0),))
    action = choose(TacticalController(),obs)
    assert (action.kind,action.value) == ("ability","brothers_in_arms")


def test_unseen_incoming_damage_interrupts_routine_exploration_without_invented_enemy_position():
    controller = TacticalController()
    controller.previous = encounter()
    obs = encounter(hp=25,turn=101,inventory=(Item(1,"b","scroll of teleportation"),),
                    messages=("Something hits you!",))
    action = choose(controller,obs)
    assert (action.kind,action.value) == ("read","b")
    assert not action.assessment["assessment"]["threats"]
    assert action.assessment["category"] == "emergency"


def test_disabling_status_without_visible_foes_does_not_allow_looting_or_exploration():
    action = choose(TacticalController(),encounter(statuses=("Confused",)))
    assert action.kind == "wait"


def test_start_teleport_before_spending_healing_then_heal_during_countdown():
    controller = TacticalController()
    obs = encounter(Monster(1,"orc",1,0,2),Monster(2,"orc",1,1,2),Monster(3,"orc",0,1,2),
                    hp=50,hp_max=80,inventory=(Item(1,"b","scroll of teleportation"),
                                             Item(2,"c","potion of heal wounds")))
    action = choose(controller,obs)
    assert (action.kind,action.value) == ("read","b")
    pending = replace(obs,turn=101,hp=30,statuses=("Tele",))
    action = choose(controller,pending)
    assert (action.kind,action.value) == ("quaff","c")


def test_actual_weapon_damage_column_changes_the_fight_instead_of_using_enchantment_as_damage():
    foe = Monster(1,"gnoll",1,0,0,
                  description="Attack                             Max Damage\nHit: +0 flail of electrocution      15 + 20 (electrocution)")
    obs = encounter(foe,abilities=(Ability("Berserk","a",True,0),))
    action = choose(TacticalController(),obs)
    assert (action.kind,action.value) == ("ability","berserk")


def test_native_trogs_hand_failure_is_reassessed_without_an_artificial_eight_turn_lockout():
    controller = TacticalController()
    obs = encounter(Monster(1,"Grinder",4,0,3,description="A demon with Paralysis."),hp=45,
                    abilities=(Ability("Trog's Hand","b",True,3),))
    action = choose(controller,obs)
    assert (action.kind,action.value) == ("ability","trogs_hand")
    failed = replace(obs,turn=101,messages=("You fail to use your ability.",))
    action = choose(controller,failed)
    assert (action.kind,action.value) == ("ability","trogs_hand")
    succeeded = replace(failed,turn=102,statuses=("Regen Will++",),will=2)
    action = choose(controller,succeeded)
    assert (action.kind,action.value) != ("ability","trogs_hand")


def test_an_engaged_visible_ally_can_fight_without_the_bot_pausing_the_game():
    controller = TacticalController()
    obs = encounter(Monster(1,"eight-headed hydra",3,0,3,description="An eight-headed hydra."),
                    Monster(2,"ogre",2,0,attitude=4))
    controller.encounter_start = 95
    controller.encounter_last_seen = 100
    controller.ineffective_retreats = 2
    action = choose(controller,obs)
    assert action.kind == "wait"


def test_recently_seen_unresolved_enemies_prevent_a_new_berserk_commitment():
    controller = TacticalController()
    previous = encounter(Monster(1,"gnoll",1,0,2,description="A gnoll with a flail."),
                         Monster(2,"orc priest",4,0,2))
    controller.observe(previous)
    controller.routine(previous,Action("wait"))
    obs = replace(previous,turn=101,monsters=(previous.monsters[0],),
                  abilities=(Ability("Berserk","a",True,0),))
    action = choose(controller,obs)
    assert (action.kind,action.value) != ("ability","berserk")
