"""Native terse inventory and verbose operation-menu identities must agree."""
import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.characters.mibe_development import development_menu_action, identification_target
from dcss_bot.models import Action, Item, Observation
from dcss_bot.state import GameState


def native_adapter(items):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 30, "hp_max": 30, "xl": 5, "turn": 12,
                         "inv": {str(n): dict(item, quantity=item.get("quantity", 1), letter=99)
                                 for n, item in enumerate(items)}})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return adapter


@pytest.mark.parametrize("kind,base,terse,verbose,prefix,title", [
    ("wield", 0, "+2 war axe (flame)", "+2 war axe of flaming", "w", "Wield or unwield which item?"),
    ("wield", 0, "+1 heavy battleaxe", "+1 battleaxe of heavy", "w", "Wield or unwield which item?"),
    ("wear", 2, "+1 ring mail {rF+}", "+1 ring mail of fire resistance", "W", "Wear or take off which item?"),
    ("wear", 2, "+0 stone dragon scales", "+0 stone dragon scales", "W", "Wear or take off which item?"),
    ("throw", 1, "3 darts (poison)", "3 poisoned darts", "F", "Fire/throw/use which item?"),
])
async def test_native_operation_selects_correct_class_and_equivalent_public_label(kind, base, terse, verbose, prefix, title):
    adapter = native_adapter([{"base_type": base, "name": terse},
                              {"base_type": 7, "name": "a potion of curing"}])
    sent = []

    async def key(value):
        sent.append(value)
        if value == prefix:
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": title},
                                 "items": [{"text": " c - " + verbose, "hotkeys": [99], "q": 1}]})
        else:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            adapter.state.apply({"msg": "player", "turn": 13})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter._select_item(kind, "c")
    assert result.turn == 13 and sent == [prefix, "c"]


async def test_curing_potion_cannot_be_selected_as_jewellery():
    adapter = native_adapter([{"base_type": 7, "name": "a potion of curing"}])
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("put_on", "c"))
    assert adapter.state.observation().turn == 12


@pytest.mark.parametrize("name,status", [("2 scrolls of blinking", []),
                                         ("2 scrolls of identify", []),
                                         ("2 scrolls of teleportation", [{"light": "Tele"}])])
async def test_plural_scroll_stacks_cannot_bypass_target_or_pending_teleport_guards(name, status):
    adapter = native_adapter([{"base_type": 5, "name": name, "quantity": 2}])
    adapter.state.apply({"msg": "player", "status": status})
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("read", "c"))
    assert adapter.state.observation().turn == 12


async def test_plural_blink_stack_with_reused_equipment_letter_uses_scroll_then_target():
    adapter = native_adapter([{"base_type": 0, "name": "+0 war axe"},
                              {"base_type": 5, "name": "2 scrolls of blinking", "quantity": 2}])
    adapter.state.apply({"msg": "map", "cells": [{"x": 2, "y": 1, "f": 33, "t": {"bg": 1}}]})
    sent = []

    async def key(value):
        sent.append(value)
        if value == "r":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": "Read which item?"},
                                 "items": [{"text": " c - 2 scrolls of blinking", "hotkeys": [99], "q": 2}]})
        else:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 2})
        return adapter.state.observation()

    async def command(message):
        sent.append(message)
        adapter.state.apply({"msg": "player", "pos": {"x": 2, "y": 1}, "turn": 13})
        adapter.state.apply({"msg": "input_mode", "mode": 1})
        return adapter.state.observation()

    adapter.key, adapter._command = key, command
    result = await adapter.dispatch(Action("blink", "c:2,1"))
    assert result.position == (2, 1) and result.turn == 13
    assert sent == ["r", "c", {"msg": "click_cell", "x": 2, "y": 1, "button": 1}]


def test_native_visible_class_does_not_expose_unidentified_subtype_or_enchantment():
    state = GameState()
    state.apply({"msg": "player", "inv": {"0": {"name": "a smoky potion", "quantity": 1,
                 "base_type": 7, "sub_type": 99, "plus": 55, "flags": 12345}}})
    item = state.observation().inventory[0]
    assert item.category == "potion" and item.name == "a smoky potion"
    assert not {"sub_type", "plus", "flags"}.intersection(vars(item))


def test_verbose_inventory_worn_marker_updates_terse_ego_armour():
    state = GameState()
    state.apply({"msg": "player", "inv": {"0": {"name": "+1 ring mail {rF+}", "quantity": 1,
                                                "base_type": 2, "letter": 99}}})
    state.apply({"msg": "menu", "tag": "inventory", "items": [
        {"text": " c - a +1 ring mail of fire resistance (worn)", "hotkeys": [99]}]})
    assert state.observation().inventory[0].equipped


def test_enchant_menu_matches_equipped_terse_branded_axe():
    obs = Observation(inventory=(Item(0, "c", "+2 war axe (flame)", equipped=True),),
                      menu={"tag": "use_item", "title": {"text": "Enchant which weapon?"},
                            "items": [{"text": " c - a +2 war axe of flaming (weapon)",
                                       "hotkeys": [99], "q": 1}]})
    action = development_menu_action(obs)
    assert (action.kind, action.value) == ("menu", "c")


def test_trogs_hand_expiry_invalidates_previously_observed_will_pips():
    state = GameState()
    state.apply({"msg": "player", "status": [{"light": "Regen"}, {"light": "Will++"}]})
    state.apply({"msg": "ui-push", "type": "formatted-scroller", "tag": "resists", "text": "Will  ++..."})
    state.apply({"msg": "ui-pop"})
    assert state.observation().will == 2
    state.apply({"msg": "player", "status": []})
    assert state.observation().will is None and state.observation().resistances == ()
    state.apply({"msg": "ui-push", "type": "formatted-scroller", "tag": "resists", "text": "Will  ....."})
    state.apply({"msg": "ui-pop"})
    assert state.observation().will == 0


async def test_recorded_seed7_identify_choice_reletters_types_without_name_delta_then_refreshes():
    # development9 seed7, decisions 1262-1263: the unknown identify stack is
    # already type-known inside the target menu, but is consumed only afterward.
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 31, "hp_max": 31, "xl": 3, "turn": 1242,
                         "inv": {
                             "2": {"name": "ring of protection from fire", "quantity": 1,
                                   "letter": 99, "base_type": 6},
                             "52": {"name": "scroll labelled UPRUMY ABAMM", "quantity": 1,
                                    "letter": 99, "base_type": 5},
                             "53": {"name": "2 scrolls labelled QOBUGATILY", "quantity": 2,
                                    "letter": 100, "base_type": 5}}})
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    adapter.state.apply({"msg": "menu", "tag": "use_item",
                         "title": {"text": "<white>Identify which item? (\\ to view known items)"},
                         "items": [
                             {"text": "Scrolls        (select first with <w>?</w><blue>)",
                              "colour": 1, "level": 1},
                             {"text": " c - a scroll labelled UPRUMY ABAMM", "q": 1,
                              "colour": 9, "hotkeys": [99, 63], "level": 2}]})
    sent = []

    async def key(value):
        sent.append(value)
        if value == "c":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            # _send_item's global type-name comparison omits slot 52's name.
            adapter.state.apply({"msg": "player", "turn": 1243, "inv": {
                "52": {"letter": 119},
                "53": {"letter": 105, "quantity": 1, "name": "scroll of identify"}}})
        elif value == "i":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " c - a ring of protection from fire", "hotkeys": [99], "q": 1},
                {"text": " w - a scroll of enchant weapon", "hotkeys": [119], "q": 1},
                {"text": " i - a scroll of identify", "hotkeys": [105], "q": 1}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        else:
            pytest.fail(f"Unexpected key {value!r}: no second identify should be read")
        return adapter.state.observation()

    adapter.key = key
    choice = development_menu_action(adapter.state.observation())
    assert (choice.kind, choice.value) == ("menu", "c")
    result = await adapter.dispatch(choice)
    assert result.turn == 1243 and sent == ["c"]
    scroll, target = identification_target(result.inventory)
    assert (scroll.letter, scroll.quantity, target.letter) == ("i", 1, "w")
    result = await adapter.dispatch(Action("identify", f"{scroll.letter}:{target.letter}"))
    assert sent == ["c", "i", 27] and result.turn == 1243 and not result.menu
    assert identification_target(result.inventory) is None
    by_slot = {item.slot: item for item in result.inventory}
    assert by_slot[52].name == "a scroll of enchant weapon"
    assert (by_slot[53].name, by_slot[53].quantity) == ("scroll of identify", 1)
    assert by_slot[2].name == "ring of protection from fire"


@pytest.mark.parametrize("mismatch", [None, "source", "target", "source_quantity", "target_quantity"])
async def test_chosen_identify_action_checks_actual_native_source_and_target(mismatch):
    adapter = native_adapter([
        {"base_type": 0, "name": "+0 hand axe"},
        {"base_type": 5, "name": "2 scrolls of identify", "quantity": 2}])
    adapter.state.apply({"msg": "player", "inv": {
        "52": {"base_type": 7, "name": "3 ruby potions", "quantity": 3, "letter": 122}}})
    sent = []

    async def key(value):
        sent.append(value)
        if value == "i":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " c - a +0 hand axe", "hotkeys": [99], "q": 1},
                {"text": " c - 2 scrolls of identify", "hotkeys": [99], "q": 2},
                {"text": " z - 3 ruby potions", "hotkeys": [122], "q": 3}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        elif value == "r":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            name = "2 scrolls of teleportation" if mismatch == "source" else "2 scrolls of identify"
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "Read which item?"},
                                 "items": [{"text": " c - " + name, "hotkeys": [99],
                                            "q": 1 if mismatch == "source_quantity" else 2}]})
        elif value == "c":
            adapter.state.apply({"msg": "close_menu"})
            name = "3 smoky potions" if mismatch == "target" else "3 ruby potions"
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "Identify which item? (\\ to view known items)"},
                                 "items": [{"text": " z - " + name, "hotkeys": [122],
                                            "q": 1 if mismatch == "target_quantity" else 3}]})
        elif value == "z":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            adapter.state.apply({"msg": "player", "turn": 13, "inv": {
                "1": {"name": "a scroll of identify", "quantity": 1},
                "52": {"name": "3 potions of curing"}}})
        return adapter.state.observation()

    adapter.key = key
    scroll, target = identification_target(adapter.state.observation().inventory)
    action = Action("identify", f"{scroll.letter}:{target.letter}")
    if mismatch:
        with pytest.raises(ProtocolError, match="Requested.*not present"):
            await adapter.dispatch(action)
        assert sent == (["i", 27, "r"] if mismatch.startswith("source") else ["i", 27, "r", "c"])
        assert adapter.state.observation().turn == 12
    else:
        result = await adapter.dispatch(action)
        assert sent == ["i", 27, "r", "c", "z"]
        assert result.turn == 13
        assert result.inventory[-1].name == "3 potions of curing"
