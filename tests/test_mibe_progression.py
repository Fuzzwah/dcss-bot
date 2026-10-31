from dataclasses import replace

import pytest

from dcss_bot.characters.mibe_progression import ProgressionPlanner
from dcss_bot.models import Cell, Item, Monster, Observation, Resistance


def ready(**changes):
    return replace(Observation(place="Dungeon", depth=9, xl=11, hp=80, hp_max=80, ac=16,
                               position=(0, 0), resistances=(Resistance("rPois", 1),)), **changes)


def choose(planner, obs, feature, *, explored=False):
    cells = {obs.position: Cell(*obs.position, feature, ">", True)}
    return planner.choose(obs, cells, lambda targets: None, lambda targets: targets, explored=explored)


def test_known_lair_entry_requires_development_defense_and_poison_response():
    planner = ProgressionPlanner()
    obs = ready()
    assert choose(planner, obs, 71).kind == "stairs_down"
    assert choose(ProgressionPlanner(), replace(obs, xl=7), 71) is None
    assert choose(ProgressionPlanner(), replace(obs, ac=None), 71) is None
    assert choose(ProgressionPlanner(), replace(obs, resistances=()), 71) is None
    with_curing = replace(obs, resistances=(), inventory=(Item(2, "c", "2 potions of curing", 2),))
    assert choose(ProgressionPlanner(), with_curing, 71).kind == "stairs_down"


@pytest.mark.parametrize("category", [None, "potion"])
def test_curing_readiness_accepts_only_the_identified_potion(category):
    obs = ready(resistances=(), inventory=(
        Item(2, "c", "2 potions of curing", 2, category=category),
    ))
    assert ProgressionPlanner.ready_for_lair(obs)


@pytest.mark.parametrize("name,category", [
    ('+9 war axe "Curing"', "weapon"),
    ("amulet of curing", "jewellery"),
    ("unknown potion of curing", "potion"),
    ("potion of curing resistance", "potion"),
    ("potion of curing", "weapon"),
    ('+9 war axe "Curing"', None),
])
def test_equipment_names_and_unknown_potions_do_not_supply_a_poison_response(name, category):
    obs = ready(resistances=(), inventory=(Item(2, "c", name, category=category),))
    assert not ProgressionPlanner.ready_for_lair(obs)


@pytest.mark.parametrize("new_entrance", [(2, 0), None])
def test_shifted_or_removed_lair_entrance_replaces_current_floor_targets(new_entrance):
    planner = ProgressionPlanner()
    obs = ready()
    old_entrance = (1, 0)
    planner.observe(obs, {old_entrance: Cell(*old_entrance, 71, ">", True)})
    other_floor = replace(obs, depth=8)
    other_entrance = (5, 5)
    planner.observe(other_floor, {other_entrance: Cell(*other_entrance, 71, ">", True)})
    cells = {old_entrance: Cell(*old_entrance, 33, ".", True)}
    if new_entrance is not None:
        cells[new_entrance] = Cell(*new_entrance, 71, ">", True)
    requested_targets = []

    def path(targets):
        requested_targets.append(set(targets))
        return "l" if targets else None

    action = planner.choose(obs, cells, path, lambda targets: targets, explored=False)
    expected = {new_entrance} if new_entrance is not None else set()
    assert planner.lair_entrances[obs.location] == expected
    assert planner.lair_entrances[other_floor.location] == {other_entrance}
    assert requested_targets == [expected]
    assert old_entrance not in requested_targets[0]
    if new_entrance is None:
        assert action is None
    else:
        assert (action.kind, action.value, action.objective) == ("move", "l", "enter Lair")


def test_lair_entry_never_overrides_current_danger_or_recovery():
    obs = ready()
    assert choose(ProgressionPlanner(), replace(obs, hp=60), 71) is None
    assert choose(ProgressionPlanner(), replace(obs, monsters=(Monster(1, "hydra", 1, 0),)), 71) is None
    assert choose(ProgressionPlanner(), obs, 70) is None  # Orc isn't Lair.
    assert choose(ProgressionPlanner(), obs, 72) is None  # Never enter Slime by generic > routing.


def test_lair_bottom_returns_without_reentering_or_claiming_full_clear():
    planner = ProgressionPlanner()
    bottom = ready(place="Lair", depth=5)
    assert choose(planner, bottom, 46, explored=True).kind == "stairs_up"
    assert choose(planner, replace(bottom, depth=4), 46, explored=True).kind == "stairs_up"
    assert choose(planner, ready(), 71) is None
    # Frontier exhaustion is recorded only for floors actually inspected; one
    # bottom-floor visit does not prove every Lair floor or enemy was cleared.
    assert planner.explored_lair_floors == {4, 5}


def test_dangerous_destination_memory_can_exclude_the_only_branch_entry():
    planner = ProgressionPlanner()
    obs = ready()
    cells = {(0, 0): Cell(0, 0, 71, ">", True)}
    action = planner.choose(obs, cells, lambda targets: None, lambda targets: set(), explored=True)
    assert action is None


async def test_altar_bottleneck_is_crossed_without_worship_or_premature_floor_completion():
    from dcss_bot.characters.mibe import RuleBasedAgent

    # Live seed3 stopped on D:1 because its only remaining route crossed an
    # ecumenical altar (165). Stepping onto it is not a religion action.
    terrain = {(0,0): 33, (1,0): 165, (2,0): 33, (3,0): 42}
    world = tuple(Cell(x,y,terrain.get((x,y),7),".",True)
                  for x in range(-1,5) for y in range(-1,2))
    observation = Observation(hp=19,hp_max=19,place="Dungeon",depth=1,input_mode=1,cells=world)
    agent = RuleBasedAgent()
    assert (await agent.decide(observation)).kind == "inspect"
    assert (await agent.decide(observation)).kind == "explore"
    observation = replace(observation, native_automation="explore", native_result="complete")
    action = await agent.decide(observation)
    assert (action.kind, action.value, action.objective) == ("travel_downstairs", "3,0", "descend")
    # Native travel crosses the ordinary walkable altar, but does not activate
    # it or combine arrival with descent.
    observation = replace(observation, turn=observation.turn+3, position=(3,0),
                          native_automation="travel_downstairs", native_result="complete")
    assert observation.position == (3,0)
    assert (await agent.decide(observation)).kind == "stairs_down"


async def test_recorded_unseen_diagonal_corner_is_probed_once_then_retired_if_solid():
    from dcss_bot.characters.mibe import RuleBasedAgent

    # Recorded seed3 public map at turn584: native exploration later entered
    # this target despite the two orthogonal rock walls. Coordinates translated.
    world = (Cell(0,0,33,".",True), Cell(1,-1,0," ",False),
             Cell(1,0,7,"#",True), Cell(0,-1,7,"#",True))
    observation = Observation(hp=19,hp_max=19,place="Dungeon",depth=1,input_mode=1,cells=world)
    agent = RuleBasedAgent()
    await agent.decide(observation)
    assert (await agent.decide(observation)).kind == "explore"
    observation = replace(observation, native_automation="explore", native_result="partial")
    action = await agent.decide(observation)
    assert (action.kind,action.value) == ("move","u")
    assert not world[1].walkable  # The unknown target was attempted, not declared safe.
    # A native rejected move takes no turn; do not probe it forever.
    rejected = await agent.decide(observation)
    assert rejected.kind == "pause"


async def test_corner_probe_cannot_override_a_new_adjacent_enemy():
    from dcss_bot.characters.mibe import RuleBasedAgent

    world = (Cell(0,0,33,".",True), Cell(1,-1,0," ",False), Cell(1,0,7,"#",True),
             Cell(0,-1,7,"#",True), Cell(0,1,33,".",True))
    observation = Observation(hp=19,hp_max=19,place="Dungeon",depth=1,input_mode=1,cells=world,
                              monsters=(Monster(1,"orc priest",0,1,3),))
    action = await RuleBasedAgent().decide(observation)
    assert (action.kind,action.value) != ("move","u")
    assert action.kind != "stairs_down"
