from dataclasses import replace

import pytest

from dcss_bot.adapter import WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE, RuleBasedAgent
from dcss_bot.characters.mibe_development import (
    DevelopmentPlanner, development_menu_action, identification_target, training_plan,
)
from dcss_bot.characters.mibe_equipment import (
    equipment_action, equipment_kind, equipment_score, equipment_upgrade,
)
from dcss_bot.models import Cell, Item, Monster, Observation, Skill, Status


def equipped(name="+0 war axe", *extras):
    return (Item(0, "a", name, equipped=True), *extras)


def clear_observation(*, inventory=None, skills=()):
    return Observation(hp=30, hp_max=30, turn=20, xl=4, input_mode=1,
                       cells=(Cell(0, 0, 33, "@", True),),
                       inventory=equipped() if inventory is None else inventory,
                       skills=skills)


def configured_planner(obs):
    planner = DevelopmentPlanner()
    assert planner.decide(obs).kind == "inspect"
    return planner


def test_enchanted_fast_axe_beats_untrained_huge_axe_and_skill_changes_choice():
    current = equipped("+2 war axe")
    assert not equipment_upgrade("+0 executioner's axe", current)
    assert not equipment_upgrade("+0 executioner's axe", current, axes_skill=0)
    assert equipment_upgrade("+0 executioner's axe", current, axes_skill=24)
    assert equipment_score("+0 hand axe of speed", axes_skill=14) > equipment_score("+0 hand axe", axes_skill=14)


def test_observed_delay_changes_practical_weapon_comparison():
    current = equipped("+0 war axe")
    # Ordinary untrained swings favour the war axe, but a genuinely observed
    # faster current swing must not be replaced using a made-up skill level.
    assert equipment_upgrade("+0 war axe", equipped("+0 hand axe"))
    assert not equipment_upgrade("+0 executioner's axe", current, axes_skill=5, attack_delay=0.7)


def test_brand_and_hydra_context_preserve_known_flaming_weapon():
    current = equipped("+0 war axe of flaming")
    assert not equipment_upgrade("+9 broad axe of freezing", current, axes_skill=18, hydra=True)
    assert equipment_upgrade("+0 war axe of flaming", equipped("+4 war axe"), axes_skill=16, hydra=True)
    assert equipment_score("+0 hand axe of electrocution", axes_skill=14) > equipment_score("+0 hand axe", axes_skill=14)


def test_shield_and_two_handed_axe_are_mutually_incompatible():
    shield = Item(1, "b", "+0 buckler", equipped=True)
    assert not equipment_upgrade("+9 battleaxe", equipped("+0 hand axe", shield), axes_skill=20)
    assert not equipment_upgrade("+0 kite shield", equipped("+0 executioner's axe"))
    assert equipment_upgrade("+0 buckler", equipped("+0 broad axe"))


@pytest.mark.parametrize("name", ["+0 pair of gloves", "+0 pair of boots", "+0 cloak",
                                  "+1 hat", "scarf of resistance", "+0 kite shield",
                                  "ring of poison resistance", "+4 ring of protection",
                                  "amulet of regeneration"])
def test_empty_defensive_slots_accept_only_visible_useful_properties(name):
    assert equipment_upgrade(name, equipped())
    assert equipment_kind(name) in {"wear", "put_on"}


def test_auxiliary_slots_are_not_compared_with_body_armour():
    inventory = equipped("+0 war axe", Item(1, "b", "+0 plate armour", equipped=True))
    assert equipment_upgrade("+0 gloves", inventory)
    assert not equipment_upgrade("+0 hat", inventory)  # Zero base AC, no ego.
    assert not equipment_upgrade("a glowing cloak", inventory)
    assert not equipment_upgrade("ring of protection", inventory)
    assert not equipment_upgrade("-3 ring of evasion", inventory)


@pytest.mark.parametrize("name", ['+9 broad axe "Unknown" {rF+}', '+9 cloak "Unknown"',
                                  "+9 war axe of distortion", "+9 war axe of chaos",
                                  "+9 war axe of draining", "+9 tower shield of ponderousness",
                                  "scarf of harm", "+9 war axe of mystery"])
def test_harmful_or_unknown_artefact_equipment_is_never_a_verified_upgrade(name):
    assert not equipment_upgrade(name, equipped())


def test_distortion_is_not_unwielded_even_for_large_known_upgrade():
    assert not equipment_upgrade("+9 broad axe of flaming", equipped("+0 hand axe of distortion"), axes_skill=18)


def test_jewellery_fills_two_ring_slots_without_unbounded_replacement():
    first = Item(1, "b", "ring of poison resistance", equipped=True)
    second = Item(2, "c", "ring of willpower", equipped=True)
    assert equipment_upgrade("ring of protection from fire", equipped("+0 war axe", first))
    assert not equipment_upgrade("ring of protection from fire", equipped("+0 war axe", first, second))


def test_equipment_and_development_never_interrupt_combat_or_cloud_danger():
    obs = clear_observation(inventory=equipped("+0 hand axe", Item(1, "b", "+9 war axe")))
    assert equipment_action(obs).value == "b"
    combat = replace(obs, monsters=(Monster(1, "rat", 1, 0),))
    assert equipment_action(combat) is None
    assert DevelopmentPlanner().decide(combat) is None
    assert not equipment_upgrade("+9 war axe", obs.inventory, hostiles=combat.hostiles)
    cloud = replace(obs, cells=(Cell(0, 0, 33, "@", True, True),))
    assert equipment_action(cloud) is None
    assert DevelopmentPlanner().decide(cloud) is None


def test_training_has_real_axe_target_then_stops_at_minimum_delay():
    obs = clear_observation(skills=(Skill("Axes", 5, 0, None), Skill("Fighting", 3, 1, None)))
    planner = configured_planner(obs)
    action = planner.decide(obs)
    assert (action.kind, action.value) == ("train_skill", "Axes:10")
    # Once foundational defenses are reached, finish war axe's minimum delay.
    later = replace(obs, skills=(Skill("Axes", 10, 0, None), Skill("Fighting", 6, 0, None),
                                 Skill("Dodging", 4, 0, None)))
    assert planner.decide(later).value == "Axes:16"
    reached = replace(later, skills=(Skill("Axes", 16, 0, None), Skill("Fighting", 6, 0, None),
                                    Skill("Dodging", 4, 0, None)))
    assert planner.decide(reached).value == "Fighting:12"


def test_equipment_controls_relevant_training_without_magic_or_invocations():
    plain = clear_observation()
    assert not {"Armour", "Shields", "Evocations", "Throwing"} & {name for name, _ in training_plan(plain)}
    inventory = equipped("+0 broad axe", Item(1, "b", "+0 chain mail", equipped=True),
                         Item(2, "c", "+0 buckler", equipped=True),
                         Item(3, "d", "wand of paralysis (4)"), Item(4, "e", "5 javelins", 5))
    plan = training_plan(replace(plain, inventory=inventory))
    names = {name for name, _ in plan}
    assert {"Armour", "Shields", "Evocations", "Throwing"} <= names
    assert "Invocations" not in names and not any("Magic" in name for name in names)
    assert max(target for name, target in plan if name == "Axes") == 18


def test_observed_target_prevents_retraining_and_unknown_skills_are_not_fabricated():
    obs = clear_observation(skills=(Skill("Axes", 5, 1, 10), Skill("Fighting", 3, 0, None)))
    planner = configured_planner(obs)
    assert planner.decide(obs) is None
    unknown = clear_observation()
    planner = configured_planner(unknown)
    assert planner.decide(unknown) is None
    assert planner.decide(replace(unknown, turn=21)) is None
    assert planner.decide(replace(unknown, turn=220)).kind == "inspect"


def test_failed_training_request_does_not_loop_on_unchanged_snapshot():
    obs = clear_observation(skills=(Skill("Axes", 4, 0, None),))
    planner = configured_planner(obs)
    assert planner.decide(obs).kind == "train_skill"
    assert planner.decide(obs) is None


def test_planner_allocates_shields_only_when_equipped_and_stops_at_target():
    skills = (Skill("Axes", 10, 0, None), Skill("Fighting", 6, 0, None),
              Skill("Dodging", 4, 0, None), Skill("Shields", 0, 0, None))
    shield = Item(1, "b", "+0 buckler", equipped=True)
    obs = clear_observation(inventory=equipped("+0 broad axe", shield), skills=skills)
    planner = configured_planner(obs)
    assert planner.decide(obs).value == "Shields:4"
    trained = replace(obs, skills=(*skills[:3], Skill("Shields", 4, 0, None)))
    assert planner.decide(trained).value == "Axes:18"
    without = replace(obs, inventory=equipped("+0 broad axe"))
    assert configured_planner(without).decide(without).value == "Axes:18"


def test_identification_uses_known_scroll_and_unknown_consumable_not_emergency_random_use():
    inventory = equipped("+0 war axe", Item(1, "b", "scroll of identify"),
                         Item(2, "c", "ruby potion", 3),
                         Item(3, "d", "scroll labelled FOO"),
                         Item(4, "e", "potion of curing"))
    obs = clear_observation(inventory=inventory)
    planner = configured_planner(obs)
    action = planner.decide(obs)
    assert (action.kind, action.value) == ("identify", "b:c")
    assert planner.decide(obs) is None
    assert identification_target((inventory[0], inventory[2])) is None
    known = (inventory[0], inventory[1], inventory[4])
    assert identification_target(known) is None
    assert DevelopmentPlanner().decide(replace(obs, monsters=(Monster(1, "orc", 3, 0),))) is None


def probe_observation(*, inventory=None):
    return replace(clear_observation(inventory=inventory or equipped(
        "+0 war axe", Item(1, "b", "2 scrolls labelled FOO", 2), Item(2, "c", "3 ruby potions", 3))),
        cells=(Cell(0, 0, 42, ">", True),), poison_survival=30)


def ready_probe(obs):
    planner = configured_planner(replace(obs, turn=obs.turn - DevelopmentPlanner.QUIET_TURNS))
    return planner


def test_unknown_scroll_bootstrap_reads_spare_stack_once_and_records_actual_reason():
    obs = probe_observation()
    planner = ready_probe(obs)
    action = planner.decide(obs)
    assert (action.kind, action.value, action.objective) == ("read", "b", "identification")
    assert "spare unknown-scroll stack" in action.reason
    assert "quantity 2" in action.reason and "12 quiet turns" in action.reason
    assert "teleport" in action.reason and "torment" in action.reason
    assert planner.decide(obs) is None
    # A changed slot, quantity, floor or plural prefix must not retry an
    # appearance whose previous read failed, was cancelled, or is still unknown.
    changed = replace(obs, turn=40, depth=2, inventory=equipped(
        "+0 war axe", Item(3, "d", "3 scrolls labelled FOO", 3)))
    assert planner.decide(changed) is None
    assert planner.decide(replace(changed, turn=52)) is None


@pytest.mark.parametrize("changes", [
    {"hp": 29}, {"statuses": ("Pois",)}, {"statuses": ("Tele",)},
    {"statuses": ("Slow",)}, {"statuses": ("Exh",)}, {"statuses": ("Berserk",)},
    {"statuses": ("-Scroll",)}, {"statuses": ("Sil",)}, {"poison_survival": 29},
    {"status_details": (Status("Tele", "about to teleport"),)},
    {"prompt": "Really read?"}, {"menu": {"tag": "inventory"}},
    {"monsters": (Monster(1, "rat", 1, 0),)},
    {"monsters": (Monster(1, "friendly butterfly", 1, 0, attitude=1),)},
    {"cells": (Cell(0, 0, 42, ">", True, True),)},
    {"cells": (Cell(0, 0, 42, ">", True), Cell(1, 0, 33, ".", True, True))},
    {"cells": (Cell(0, 0, 33, ".", True),)},
    {"cells": (Cell(0, 0, 45, ">", True),)},  # Hatch is not an ordinary stair.
    {"cells": (Cell(0, 0, 71, ">", True),)},  # Branch entry is not an anchor.
    {"cells": (Cell(0, 0, 42, ">", False),)},
    {"cells": ()},
])
def test_unknown_scroll_probe_rejects_each_unsafe_condition(changes):
    obs = probe_observation()
    planner = ready_probe(obs)
    assert planner.decide(replace(obs, **changes)) is None


def test_probe_requires_observed_quiet_interval_and_resets_after_visible_hostile():
    obs = probe_observation()
    planner = configured_planner(obs)
    assert planner.decide(replace(obs, turn=31)) is None
    planner.observe(replace(obs, turn=31, monsters=(Monster(1, "orc", 5, 0),)))
    assert planner.decide(replace(obs, turn=42)) is None
    assert planner.decide(replace(obs, turn=43)).kind == "read"


def test_unknown_scroll_policy_never_blindly_drinks_and_singleton_bootstrap_is_bounded():
    potion = Item(2, "c", "6 ruby potions", 6)
    obs = probe_observation(inventory=equipped("+0 war axe", potion))
    assert ready_probe(obs).decide(obs) is None
    one = replace(obs, inventory=equipped("+0 war axe", potion, Item(1, "b", "scroll labelled FIRST")))
    assert ready_probe(one).decide(one) is None
    three = replace(one, inventory=(*one.inventory, Item(3, "d", "scroll labelled SECOND"),
                                    Item(4, "e", "scroll labelled THIRD")))
    planner = ready_probe(three)
    action = planner.decide(three)
    assert (action.kind, action.value) == ("read", "e")
    assert "one singleton bootstrap among 3" in action.reason
    assert planner.decide(replace(three, turn=32)) is None


def test_spare_stack_precedes_singleton_bootstrap_and_global_probe_count_is_bounded():
    inventory = equipped("+0 war axe", Item(1, "b", "2 scrolls labelled A", 2),
                         Item(2, "c", "scroll labelled B"), Item(3, "d", "scroll labelled C"))
    obs = probe_observation(inventory=inventory)
    planner = ready_probe(obs)
    assert planner.decide(obs).value == "b"
    different = replace(obs, turn=31, inventory=equipped(
        "+0 war axe", Item(1, "b", "2 scrolls labelled D", 2)))
    assert planner.decide(different) is None
    # Reassess the effects of the first read before any later probe.
    assert planner.decide(replace(different, turn=32, hp=15, poison_survival=15)) is None
    assert planner.decide(replace(different, turn=32, statuses=("Tele",))) is None
    assert planner.decide(replace(different, turn=32, cells=(
        Cell(0, 0, 42, ">", True), Cell(1, 0, 33, ".", True, True)))) is None
    for index, appearance in enumerate(("D", "E", "F"), 1):
        obs = replace(obs, turn=20 + 12 * index,
                      inventory=equipped("+0 war axe", Item(1, "b", f"2 scrolls labelled {appearance}", 2)))
        assert planner.decide(obs).kind == "read"
    assert planner.decide(replace(obs, turn=68, inventory=equipped(
        "+0 war axe", Item(1, "b", "2 scrolls labelled G", 2)))) is None


def test_known_identify_preempts_unknown_reads_even_after_unchanged_failed_attempt():
    obs = probe_observation(inventory=equipped(
        "+0 war axe", Item(1, "b", "2 scrolls labelled FOO", 2),
        Item(2, "c", "scroll of identify"), Item(3, "d", "ruby potion", 3)))
    planner = ready_probe(obs)
    assert (planner.decide(obs).kind, planner.decide(replace(obs, turn=32))) == ("identify", None)


def identify_menu(*items, **changes):
    return replace(probe_observation(), input_mode=0, menu={
        "tag": "use_item", "title": {"text": "Identify which item? (\\ to view known items)"},
        "items": list(items)}, **changes)


async def test_native_identify_menu_uses_unique_current_menu_stack_not_inventory_letter():
    obs = identify_menu(
        {"text": " z - 4 <red>ruby potions</red>", "hotkeys": [ord("z")], "q": 4, "level": 2},
        {"text": " b - a scroll labelled FOO", "hotkeys": [ord("b")], "q": 1, "level": 2},
        {"text": " a - 9 potions of curing", "hotkeys": [ord("a")], "q": 9, "level": 2})
    action = await RuleBasedAgent().decide(obs)
    assert (action.kind, action.value) == ("menu", "z")
    assert "ruby potions" in action.reason
    assert all(item.letter != "z" for item in obs.inventory)


@pytest.mark.parametrize("rows", [
    [{"text": " a - 3 ruby potions", "hotkeys": [97]}, {"text": " a - a scroll labelled FOO", "hotkeys": ["a"]}],
    [{"text": " a - 3 ruby potions", "hotkeys": [97]}, {"text": " a - a potion of curing", "hotkeys": [97]}],
    [{"text": " a - a ruby potion", "hotkeys": [97], "q": 0}],
    [{"text": " a - a ruby potion", "hotkeys": [97], "colour": 8}],
    [{"text": " a - a ruby potion", "hotkeys": [97], "level": 1}],
    [{"text": " a - a ruby potion", "hotkeys": [ord("!")]}],
    [{"text": " a - a ruby potion", "hotkeys": [97, 98]}],
    [{"text": " a - a potion of curing", "hotkeys": [97]}],
    [],
])
def test_identify_menu_cancels_absent_disabled_ambiguous_or_known_targets(rows):
    assert development_menu_action(identify_menu(*rows)).kind == "cancel"


@pytest.mark.parametrize("menu", [
    {"tag": "inventory", "title": "Identify which item?"},
    {"tag": "use_item", "title": "Read which item?"},
    {"tag": "use_item", "title": "Really Identify which item?"},
    {"tag": "use_item", "title": "Identify which item? (\\ to view known items) unexpected"},
])
def test_identify_menu_requires_verified_native_tag_and_title_prefix(menu):
    assert development_menu_action(replace(probe_observation(), menu=menu)) is None


async def test_unknown_read_then_native_identify_choice_dispatches_only_explicit_legal_keys():
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 30, "hp_max": 30, "turn": 20, "pos": {"x": 0, "y": 0},
                         "inv": {"0": {"name": "2 scrolls labelled FOO", "quantity": 2, "letter": 98},
                                 "1": {"name": "3 ruby potions", "quantity": 3, "letter": 99}}})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    obs = replace(adapter.state.observation(), cells=(Cell(0, 0, 42, ">", True),))
    planner = ready_probe(obs)
    read = planner.decide(obs)
    sent = []

    async def key(value):
        sent.append(value)
        if value == "r":
            adapter.state.apply({"msg": "input_mode", "mode": 7})
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Read which item?"}]})
        elif value == "b":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", **identify_menu(
                {"text": " z - 3 ruby potions", "hotkeys": [122], "q": 3, "level": 2}).menu})
        elif value == "z":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            adapter.state.apply({"msg": "player", "turn": 21,
                                 "inv": {"0": {"name": "scroll of identify", "quantity": 1},
                                         "1": {"name": "3 potions of curing"}}})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(read)
    choice = await RuleBasedAgent().decide(result)
    assert (choice.kind, choice.value) == ("menu", "z")
    result = await adapter.dispatch(choice)
    assert sent == ["r", "b", "z"]
    assert result.inventory[1].name == "3 potions of curing"
    assert result.turn == 21


async def test_tactics_and_recovery_remain_before_development_but_still_update_quiet_memory():
    obs = probe_observation()
    agent = RuleBasedAgent()
    agent.development = ready_probe(obs)
    planner_decide = agent.development.decide
    agent.development.decide = lambda _: pytest.fail("Development preempted tactics or recovery")
    threat = replace(obs, monsters=(Monster(1, "rat", 1, 0),))
    assert (await agent.decide(threat)).objective != "identification"
    assert agent.development._quiet_since == obs.turn
    hurt = replace(obs, turn=21, hp=15, poison_survival=15)
    assert (await agent.decide(hurt)).kind == "wait"
    assert planner_decide(replace(obs, turn=31)) is None


def test_probe_accepts_separate_equipment_and_scroll_letters_but_not_two_scrolls_on_same_key():
    obs = probe_observation(inventory=equipped(
        "+0 war axe", Item(1, "b", "+0 dagger"), Item(52, "b", "2 scrolls labelled FOO", 2)))
    assert ready_probe(obs).decide(obs).value == "b"
    duplicate = replace(obs, inventory=(*obs.inventory, Item(53, "b", "scroll labelled BAR")))
    assert ready_probe(duplicate).decide(duplicate) is None


def test_known_enchant_weapon_is_used_on_current_axe_but_not_reissued_without_native_change():
    obs = clear_observation(inventory=equipped("+0 war axe",Item(52,"b","2 scrolls of enchant weapon",2)))
    planner = configured_planner(obs)
    action = planner.decide(obs)
    assert (action.kind,action.value) == ("read","b")
    assert planner.decide(obs) is None
    menu = replace(obs,input_mode=0,menu={"tag":"use_item","title":{"text":"<white>Enchant which weapon?"},
                  "items":[{"text":" a - a +0 war axe (weapon)","hotkeys":[97],"q":1,"level":2}]})
    choice = development_menu_action(menu)
    assert (choice.kind,choice.value) == ("menu","a")


def test_equip_a_better_axe_before_spending_the_known_weapon_enchantment():
    obs = clear_observation(inventory=equipped("+0 hand axe",Item(1,"b","+3 war axe"),
                                             Item(52,"b","scroll of enchant weapon")))
    assert configured_planner(obs).decide(obs) is None


def test_enchantment_menu_ignores_an_unworn_backup_and_disabled_equipped_target():
    obs = clear_observation(inventory=equipped("+0 war axe",Item(1,"b","+3 war axe")))
    menu = {"tag":"use_item","title":{"text":"Enchant which weapon?"},
            "items":[{"text":" a - a +0 war axe (weapon)","hotkeys":[97],"q":0,"level":2},
                     {"text":" b - a +3 war axe","hotkeys":[98],"q":1,"level":2}]}
    action = development_menu_action(replace(obs,input_mode=0,menu=menu))
    assert action.kind == "cancel"


def test_native_identify_can_reveal_an_eligible_equipment_item_when_no_consumables_remain():
    obs = identify_menu({"text":" a - a scintillating plate armour","hotkeys":[97],"q":1,"level":2})
    action = development_menu_action(obs)
    assert (action.kind,action.value) == ("menu","a")
