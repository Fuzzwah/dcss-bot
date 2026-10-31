"""Free native UI observations preserve, but never renew, world-step evidence."""
from collections import Counter
from dataclasses import replace

import pytest

from dcss_bot.characters.mibe_tactics import TacticalController, WithdrawalGoal
from dcss_bot.models import Action, Cell, Item, Monster, Observation


def observation(**fields):
    base = Observation(
        species="Minotaur", god="Trog", place="Dungeon", depth=3,
        turn=100, hp=60, hp_max=60, xl=10, input_mode=1,
        ac=12, ev=10,
        cells=tuple(Cell(x, y, 33, ".", True)
                    for x in range(-2, 7) for y in range(-1, 2)),
    )
    return replace(base, **fields)


def uncovered(*_):
    return False


def choose(controller, obs):
    cells = {(cell.x, cell.y): cell for cell in obs.cells}
    return controller.decide(obs, cells, Counter({point: 1 for point in cells}), uncovered)


def test_damage_survives_free_information_and_still_changes_the_tactical_response():
    controller = TacticalController()
    rat = Monster(1, "rat", 1, 0, 0, description="A small rat.")
    before = observation(monsters=(rat,), inventory=(Item(0, "a", "potion of heal wounds"),))
    controller.routine(before, Action("move", "l", objective="combat"))
    harmed = replace(before, turn=101, hp=40, messages=("The rat bites you!",))
    controller.routine(harmed, Action("inspect", "abilities"))
    menu = replace(harmed, menu={"title": "Abilities"})
    controller.routine(menu, Action("cancel"))
    ready = replace(menu, menu={})
    action = choose(controller, ready)
    assert action.assessment["assessment"]["recent_damage"] == 20
    assert action.assessment["category"] == "emergency"
    assert (action.kind, action.value) == ("quaff", "a")

    # A genuinely later, unharmed game turn replaces this evidence, even if
    # another free inspection precedes the next tactical decision.
    unharmed = replace(ready, turn=102)
    controller.routine(unharmed, Action("inspect", "abilities"))
    action = choose(controller, replace(unharmed))
    assert action.assessment["assessment"]["recent_damage"] == 0
    assert action.assessment["category"] != "emergency"
    assert action.kind != "quaff"


@pytest.mark.parametrize("old_hp,old_max,old_status,new_hp,new_max,new_status,damage", [
    (60, 90, ("Berserk",), 30, 60, ("Slow", "-Berserk"), 10),
    (40, 60, (), 50, 90, ("Berserk",), 10),
    (60, 60, (), 40, 50, (), 20),
    (60, 90, ("Berserk",), 40, 90, ("Berserk",), 20),
])
def test_free_ui_retains_normalized_berserk_loss_without_discarding_draining_damage(
    old_hp, old_max, old_status, new_hp, new_max, new_status, damage,
):
    controller = TacticalController()
    before = observation(hp=old_hp, hp_max=old_max, statuses=old_status)
    controller.routine(before, Action("wait"))
    after = replace(before, turn=101, hp=new_hp, hp_max=new_max, statuses=new_status)
    controller.routine(after, Action("inspect", "abilities"))
    free = replace(after, menu={"title": "Abilities"})
    controller.routine(free, Action("cancel"))
    risk = controller.assess(replace(free, menu={}), {}, uncovered)
    assert risk.recent_damage == damage


def test_routine_records_disappearance_and_free_menus_cannot_extend_unseen_attack_memory():
    controller = TacticalController()
    wizard = Monster(1, "orc wizard", 1, 0, description="An orc wizard.")
    before = observation(monsters=(wizard,))
    controller.routine(before, Action("move", "l", objective="combat"))
    vanished = replace(before, turn=101, hp=53, monsters=(),
                       messages=("The orc wizard vanishes!", "Something hits you!"))
    controller.routine(vanished, Action("inspect", "abilities"))
    assert controller.unseen_contact == (wizard, 101)
    assert controller.unseen_harm == (vanished.location, 101, 7)

    # The attack messages remain in the public message window through menus
    # and later quiet turns; none of those snapshots is another attack.
    quiet = vanished
    for turn in (101, 102, 103):
        quiet = replace(quiet, turn=turn)
        controller.routine(quiet, Action("inspect", "abilities"))
        menu = replace(quiet, menu={"title": "Abilities"},
                       messages=quiet.messages + ("Something hits you!",))
        controller.routine(menu, Action("cancel"))
        quiet = menu
        assert controller.unseen_harm == (vanished.location, 101, 7)
        assert controller.unseen_contact == (wizard, 101)
    expired = replace(menu, turn=104)
    controller.routine(expired, Action("inspect", "abilities"))
    assert controller.unseen_harm is None
    assert controller.unseen_contact is None
    assert controller.recent_damage(expired) == 0


def test_routine_floor_arrival_completes_withdrawal_without_banning_the_clear_stair_chain():
    controller = TacticalController()
    before = observation(cells=(Cell(0, 0, 46, "<", True),))
    controller.withdrawal = WithdrawalGoal(before.location, before.position, "upstairs", 90, 0)
    controller.routine(before, Action("stairs_up", objective="planned retreat"))
    arrival = replace(before, depth=2, turn=101, position=(10, 0),
                      cells=(Cell(10, 0, 42, ">", True), Cell(11, 0, 46, "<", True)))
    controller.routine(arrival, Action("inspect", "abilities"))
    assert controller.withdrawal is None
    # The abandoned reverse stair stays guarded; a different clear upstairs
    # must not inherit the completed withdrawal's objective or stair ban.
    assert controller.safe_stairs(arrival, {(10, 0), (11, 0)}) == {(11, 0)}
    assert choose(controller, replace(arrival)) is None
    next_stair = replace(arrival, turn=102, position=(11, 0))
    controller.routine(next_stair, Action("stairs_up", objective="explore"))
    clear_floor = replace(next_stair, depth=1, turn=103, position=(20, 0),
                          cells=(Cell(20, 0, 42, ">", True),))
    controller.routine(clear_floor, Action("inspect", "abilities"))
    assert controller.withdrawal is None
    assert controller.safe_stairs(clear_floor, {clear_floor.position}) == {clear_floor.position}


def test_routine_downstairs_arrival_keeps_the_public_clear_return_link():
    controller = TacticalController()
    before = observation(cells=(Cell(0, 0, 42, ">", True),))
    controller.routine(before, Action("stairs_down"))
    arrival = replace(before, depth=4, turn=101, position=(10, 0),
                      cells=(Cell(10, 0, 46, "<", True),))
    controller.routine(arrival, Action("inspect", "abilities"))
    link = controller.return_stairs[(arrival.location, 46)]
    assert link.position == arrival.position
    assert link.destination == before.location
    assert link.observed_clear


@pytest.mark.parametrize("first_handler", ["observe", "decide"])
def test_same_immutable_observation_is_not_counted_twice(first_handler):
    controller = TacticalController()
    rats = (Monster(1, "rat", 1, 0), Monster(2, "rat", 2, 0))
    before = observation(monsters=rats)
    controller.routine(before, Action("move", "l", objective="combat"))
    killed = replace(before, turn=101, monsters=(), messages=("You kill the rat!",))
    if first_handler == "observe":
        controller.observe(killed)
    else:
        choose(controller, killed)
    controller.routine(killed, Action("inspect", "abilities"))
    assert controller.recent_hostiles == {"rat": (1, before.turn)}
    choose(controller, killed)
    controller.routine(killed, Action("cancel"))
    assert controller.recent_hostiles == {"rat": (1, before.turn)}


def test_routine_does_not_attach_a_stale_assessment_from_an_older_snapshot():
    controller = TacticalController()
    before = observation()
    assert choose(controller, before) is None
    same = controller.routine(before, Action("inspect", "abilities"))
    assert same.assessment["category"] == "safe"
    newer = replace(before, turn=101, hp=12,
                    monsters=(Monster(1, "two-headed ogre", 1, 0, 3),))
    action = controller.routine(newer, Action("inspect", "abilities"))
    assert not action.assessment
    # A same-turn but different public snapshot is not the earlier assessment's
    # source either (it can contain new monsters, status or equipment details).
    controller = TacticalController()
    assert choose(controller, before) is None
    refreshed = replace(before, monsters=newer.monsters)
    assert not controller.routine(refreshed, Action("inspect", "abilities")).assessment


@pytest.mark.parametrize("progressed", [False, True])
def test_free_inspection_does_not_erase_withdrawal_step_progress(progressed):
    controller = TacticalController()
    cells = tuple(Cell(x, 0, 46 if x == 6 else 33, visible=True) for x in range(7))
    before = observation(cells=cells)
    controller.withdrawal = WithdrawalGoal(before.location, (6, 0), "upstairs", 90, 6)
    controller.routine(before, Action("move", "l", objective="planned retreat"))
    after = replace(before, turn=101, position=(1 if progressed else 0, 0))
    controller.routine(after, Action("inspect", "abilities"))
    ready = replace(after)
    action = choose(controller, ready)
    if progressed:
        assert action is not None and action.objective == "planned retreat"
        assert controller.withdrawal.last_distance == 5
        assert (ready.location, (6, 0)) not in controller.failed_withdrawals
    else:
        assert action is None
        assert controller.withdrawal is None
        assert (ready.location, (6, 0)) in controller.failed_withdrawals


def test_routine_processes_retreat_pressure_before_a_free_action_replaces_the_move():
    controller = TacticalController()
    pursuer = Monster(1, "gnoll", -2, 0, description="A gnoll.")
    before = observation(monsters=(pursuer,))
    controller.routine(before, Action("move", "l", objective="retreat"))
    after = replace(before, turn=101, position=(1, 0),
                    monsters=(replace(pursuer, x=-1),))
    controller.routine(after, Action("inspect", "abilities"))
    assert controller.ineffective_retreats == 1
    menu = replace(after, menu={"title": "Abilities"})
    controller.routine(menu, Action("cancel"))
    choose(controller, menu)
    controller.routine(menu, Action("inspect", "abilities"))
    assert controller.ineffective_retreats == 1


def test_public_rebase_preserves_turn_damage_and_unknown_frame_discards_it():
    controller = TacticalController()
    before = observation()
    controller.routine(before, Action("wait"))
    harmed = replace(before, turn=101, hp=40)
    controller.routine(harmed, Action("inspect", "abilities"))
    controller.rebase(harmed.location, (10, -5))
    shifted = replace(harmed, position=(10, -5),
                      cells=tuple(replace(cell, x=cell.x+10, y=cell.y-5) for cell in harmed.cells))
    controller.routine(shifted, Action("inspect", "abilities"))
    assert controller.recent_damage(shifted) == 20
    controller.rebase(shifted.location, None)
    controller.routine(shifted, Action("inspect", "abilities"))
    assert controller.recent_damage(shifted) == 0
    later_floor = replace(shifted, depth=4)
    controller.routine(later_floor, Action("inspect", "abilities"))
    assert controller.recent_damage(later_floor) == 0
