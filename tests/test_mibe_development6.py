"""Captured valid encounters must progress; return stairs are a scored risk."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Ability, Action, Cell, DIRECTIONS, Item, Monster, Observation, Resistance, Skill, Status


def observation(values):
    values = dict(values)
    for field,factory in (("cells",Cell),("monsters",Monster),("inventory",Item),("abilities",Ability),
                          ("resistances",Resistance),("skills",Skill),("status_details",Status)):
        values[field] = tuple(factory(**entry) for entry in values.get(field,()))
    values["position"] = tuple(values["position"])
    for field in ("messages","statuses"):
        values[field] = tuple(values[field])
    return Observation(**values)


def captured():
    return json.loads((Path(__file__).parent/"fixtures"/"recorded_development6_pauses.json").read_text())


def choose(controller,obs):
    cells = {(cell.x,cell.y):cell for cell in obs.cells}
    return controller.decide(obs,cells,Counter({position:2 for position in cells}),RuleBasedAgent._covered)


def stair_encounter():
    data = captured()
    departure = observation(data["stair_entry"]["departure"])
    arrival = observation(data["stair_entry"]["arrival"])
    emergency = observation(data["stair_emergency"])
    controller = TacticalController()
    controller.routine(departure,Action("stairs_down"))
    controller.observe(arrival)
    controller.routine(arrival,Action("wait"))
    return controller,departure,arrival,emergency


def adapter_for(obs):
    adapter = WebTilesAdapter("ws://unused","ScenarioBot","unused",character=PROFILE)
    adapter.state.apply({"msg":"player","species":obs.species,"god":obs.god,"hp":obs.hp,
                         "hp_max":obs.hp_max,"turn":obs.turn,"place":obs.place,"depth":obs.depth,
                         "pos":{"x":obs.position[0],"y":obs.position[1]},
                         "status":[{"light":status} for status in obs.statuses]})
    monsters = {(monster.x,monster.y):monster for monster in obs.monsters}
    cells = []
    for cell in obs.cells:
        raw = {"x":cell.x,"y":cell.y,"f":cell.feature,"t":{"bg":1 if cell.visible else 0x40001}}
        if monster := monsters.get((cell.x,cell.y)):
            raw["mon"] = {"id":monster.id,"name":monster.name,"att":monster.attitude,"threat":monster.threat}
        cells.append(raw)
    adapter.state.apply({"msg":"map","cells":cells})
    adapter.state.apply({"msg":"input_mode","mode":1})
    return adapter


@pytest.mark.parametrize("seed",[1,2,3,4,5,6,8])
def test_ordinary_recorded_encounters_do_not_freeze_for_lack_of_a_candidate(seed):
    obs = observation(captured()["observations"][str(seed)])
    controller = TacticalController()
    action = choose(controller,obs)
    assert action is not None and action.kind != "pause"
    assert action.objective not in {"explore","equipment","development"}
    if action.kind == "move":
        dx,dy = DIRECTIONS[action.value]
        target = obs.position[0]+dx,obs.position[1]+dy
        assert any((cell.x,cell.y)==target and cell.visible and cell.walkable and not cell.cloud for cell in obs.cells)
        assert sum(enemy.distance(target)<=1 for enemy in obs.hostiles)<=1


def test_public_travel_modifier_is_not_confused_with_attack_speed():
    obs = observation(captured()["observations"]["4"])
    natasha, = obs.hostiles
    info = mechanics(natasha)
    assert info.speed == 1 and info.movement_speed == 1.25


def test_captured_fatal_departure_envelope_does_not_authorize_stair_travel():
    controller,_,_,obs = stair_encounter()
    assert choose(controller,obs).kind == "pause"


async def test_recorded_reaching_dead_end_can_return_through_observed_clear_stair_and_ban_reentry():
    controller,departure,_,obs = stair_encounter()
    obs = replace(obs,hp=obs.hp_max)
    action = choose(controller,obs)
    assert action.kind == "escape_upstairs"
    evidence = action.assessment["stair_escape"]
    assert evidence["destination"] == departure.location
    assert evidence["departure_actions"] == 1.5
    assert evidence["arrival_actions"] >= 2.25
    assert evidence["arrival_uncertainty_allowance"] > 0
    assert evidence["departure_burst"] < obs.hp
    adapter = adapter_for(obs)

    async def command(message):
        if message.get("keycode") != ord("<"):
            pytest.fail("Escape must traverse upward, not attack or descend")
        adapter.state.apply({"msg":"player","depth":departure.depth,"turn":obs.turn+2,
                             "pos":{"x":0,"y":0}})
        adapter.state.apply({"msg":"map","clear":True,"cells":[
            {"x":0,"y":0,"f":43,"t":{"bg":1}}]})
        return adapter.state.observation()

    adapter._command = command
    returned = await adapter.dispatch(action)
    assert returned.depth == 1 and not returned.hostiles
    controller.observe(returned)
    assert controller.safe_stairs(returned,{returned.position}) == set()
    assert obs.location in controller.avoided


@pytest.mark.parametrize("change",["adjacent","expired","unknown_destination","pending_teleport","unseen_harm"])
def test_return_stair_guards_do_not_assume_followers_or_destination_safe(change):
    controller,_,_,obs = stair_encounter()
    feature = next(cell.feature for cell in obs.cells if (cell.x,cell.y)==obs.position)
    key = obs.location,feature
    if change == "adjacent":
        obs = replace(obs,monsters=(replace(obs.hostiles[0],x=obs.position[0]+1,y=obs.position[1]),))
    elif change == "expired":
        controller.return_stairs[key] = replace(controller.return_stairs[key],departure_turn=obs.turn-1001)
    elif change == "unknown_destination":
        controller.return_stairs[key] = replace(controller.return_stairs[key],observed_clear=False)
    elif change == "pending_teleport":
        obs = replace(obs,statuses=obs.statuses+("Tele",))
    else:
        controller.unseen_harm = obs.location,obs.turn,3
    assert choose(controller,obs).kind != "escape_upstairs"


@pytest.mark.parametrize("feature,adjacent",[(33,False),(60,False),(71,False),(88,False),(47,True)])
async def test_explicit_return_stair_action_is_rejected_before_any_command_when_boundary_is_wrong(feature,adjacent):
    _,_,_,obs = stair_encounter()
    obs = replace(obs,cells=tuple(replace(cell,feature=feature) if (cell.x,cell.y)==obs.position else cell for cell in obs.cells))
    if adjacent:
        obs = replace(obs,monsters=(replace(obs.hostiles[0],x=obs.position[0]+1,y=obs.position[1]),))
    adapter = adapter_for(obs)
    async def unexpected(message):
        pytest.fail("Invalid return-stair action reached the native command boundary")
    adapter._command = unexpected
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("escape_upstairs"))


def test_return_stair_coordinates_follow_proven_rebase_and_are_dropped_on_ambiguous_reset():
    controller,_,_,obs = stair_encounter()
    feature = next(cell.feature for cell in obs.cells if (cell.x,cell.y)==obs.position)
    controller.rebase(obs.location,(7,3))
    assert controller.return_stairs[(obs.location,feature)].position == (7,3)
    controller.rebase(obs.location,None)
    assert (obs.location,feature) not in controller.return_stairs


def test_two_nearby_reaching_enemies_can_be_prepared_for_before_the_first_hit():
    obs = Observation(species="Minotaur",god="Trog",place="Dungeon",depth=3,xl=5,turn=100,
                      hp=50,hp_max=50,ac=8,ev=10,input_mode=1,
                      cells=tuple(Cell(x,y,33,".",True) for x in range(-3,4) for y in range(-3,4)),
                      monsters=(Monster(1,"gnoll",2,0,1,description="Hit: +0 spear  14",equipment=("+0 spear",)),
                                Monster(2,"gnoll",2,1,1,description="Hit: +0 spear  14",equipment=("+0 spear",))),
                      abilities=(Ability("Berserk","a",True,0),))
    action = choose(TacticalController(),obs)
    assert (action.kind,action.value) == ("ability","berserk")


def test_berserk_fire_movement_keeps_a_safe_approach_to_the_recorded_beetle():
    obs = observation(captured()["observations"]["1"])
    obs = replace(obs,statuses=("Berserk","Fire"),hp=67,hp_max=67)
    action = choose(TacticalController(),obs)
    assert action.kind == "move" and action.objective == "emergency recovery"
    dx,dy = DIRECTIONS[action.value]
    target = obs.position[0]+dx,obs.position[1]+dy
    assert obs.hostiles[0].distance(target) < obs.hostiles[0].distance(obs.position)


def test_known_healing_precedes_fire_movement_when_the_next_tick_can_be_fatal():
    obs = observation(captured()["observations"]["1"])
    obs = replace(obs,statuses=("Fire",),hp=6,
                  inventory=(Item(100,"z","potion of heal wounds",category="potion"),))
    action = choose(TacticalController(),obs)
    assert (action.kind,action.value) == ("quaff","z")
