"""Pinned 0.34 UI schemas, never hidden inventory/monster internals."""
import pytest

from dcss_bot.adapter import ProtocolError, WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.models import Action, Observation
from dcss_bot.state import GameState


def adapter_with_items(items=()):
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    adapter.state.apply({"msg": "player", "species": PROFILE.species, "god": PROFILE.god,
                         "hp": 30, "hp_max": 30, "xl": 5, "turn": 12,
                         "inv": {str(n): {"name": name, "quantity": 1, "letter": ord(letter)}
                                 for n, (letter, name) in enumerate(items)}})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return adapter


def test_native_defences_status_details_and_poison_estimate_preserve_unknowns():
    state = GameState()
    assert state.observation().ac is None
    assert state.observation().poison_survival is None
    state.apply({"msg": "player", "ac": 12, "ev": 8, "sh": 4, "piety_rank": 3,
                 "poison_survival": 17, "status": [
                     {"light": "Pois", "text": "seriously poisoned", "desc": "Poison is hurting you.", "col": 12},
                     {"light": "Tele", "text": "about to teleport", "col": 13}]})
    state.apply({"msg": "player", "hp": 22})
    obs = state.observation()
    assert (obs.ac, obs.ev, obs.sh, obs.piety_rank, obs.poison_survival) == (12, 8, 4, 3, 17)
    assert obs.status_details[0].text == "seriously poisoned"
    assert obs.status_details[0].colour == 12
    assert obs.statuses == ("Pois", "Tele")


def test_native_overview_counts_spaced_resistance_pips():
    state = GameState()
    state.apply({"msg": "ui-push", "type": "formatted-scroller", "tag": "resists",
                 "text": "<white>rFire  + + .\nrCold  x . .\nrPois  +\nWill   +++..</white>"})
    state.apply({"msg": "ui-pop"})
    assert {r.name: r.level for r in state.observation().resistances} == {
        "rFire": 2, "rCold": -1, "rPois": 1, "Will": 3}
    assert state.observation().will == 3


def test_native_crt_skill_rows_merge_training_and_target_views():
    state = GameState()
    state.apply({"msg": "menu", "type": "crt", "tag": "skills"})
    state.apply({"msg": "txt", "id": "menu_txt", "clear": True, "lines": {
        "0": "Skill           Level Train      Skill           Level Train",
        "1": " a + Fighting       4.2  50%       c - Dodging         2.0",
        "2": " b * Axes           7.1  50%"}})
    initial = {s.name: s for s in state.observation().skills}
    assert initial["Axes"].training == 50
    assert initial["Dodging"].training == 0
    state.apply({"msg": "txt", "id": "menu_txt", "lines": {
        "0": "Skill           Level Target     Skill           Level Target",
        "1": " a + Fighting       4.2  --        c - Dodging         2.0 --",
        "2": " b * Axes           7.1  14.0"}})
    skills = {s.name: s for s in state.observation().skills}
    assert skills["Axes"].level == 7.1 and skills["Axes"].training == 50
    assert skills["Axes"].target == 14 and skills["Axes"].hotkey == "b"
    assert skills["Dodging"].enabled is False


def test_blank_first_column_skill_training_preserves_second_column():
    state = GameState()
    state.apply({"msg": "menu", "type": "crt", "tag": "skills"})
    state.apply({"msg": "txt", "id": "menu_txt", "clear": True, "lines": {
        "0": "Skill           Level Train      Skill           Level Train",
        "1": " a - Fighting       4.2           c + Dodging         2.0 100%",
        "2": " b - Axes           7.1"}})
    skills = {s.name: s for s in state.observation().skills}
    assert skills["Fighting"].training == 0
    assert skills["Dodging"].training == 100
    assert skills["Axes"].training == 0


def test_inspected_weapon_uses_public_body_not_hidden_inventory_plus():
    state = GameState()
    state.apply({"msg": "player", "weapon_index": 0, "xl": 3,
                 "inv": {"0": {"name": "a glowing axe", "quantity": 1, "plus": 99}}})
    state.observe_weapon(0, {"body": "It falls into the 'Axes' category.\nCurrent attack delay: 1.2."})
    assert state.observation().attack_delay == 1.2
    assert state.observation().weapon_skill == "Axes"
    assert "plus" not in state.observation().inventory[0].__dict__
    state.apply({"msg": "player", "weapon_index": 1})
    assert state.observation().attack_delay is None


@pytest.mark.parametrize("value", ["a", "c", "berserk!", "trogs-hand"])
def test_ability_action_rejects_positional_hotkeys(value):
    with pytest.raises(ValueError):
        Action("ability", value)


@pytest.mark.parametrize("kind,value", [("zap", "a:1,2,3"), ("blink", "aa:1,2"),
                                         ("throw", "a:1;2"), ("inspect_monster", "99999,0"),
                                         ("train_skill", "Axes:28"), ("identify", "ab:c")])
def test_new_actions_reject_unbounded_values(kind, value):
    with pytest.raises(ValueError):
        Action(kind, value)


@pytest.mark.parametrize("semantic,name", [("berserk", "Berserk"), ("trogs_hand", "Trog's Hand"),
                                            ("brothers_in_arms", "Brothers in Arms")])
async def test_ability_resolves_fresh_native_name_not_assumed_hotkey(semantic, name):
    adapter = adapter_with_items()
    sent = []

    async def key(value):
        sent.append(value)
        if value == "a":
            adapter.state.apply({"msg": "menu", "tag": "ability", "items": [
                {"text": name + "    Piety    0%", "hotkeys": [ord("X")]}]})
        elif value == "X":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "player", "turn": 13})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("ability", semantic))
    assert sent == ["a", "X"] and result.turn == 13


@pytest.mark.parametrize("wrong", ["pickup", "inventory"])
async def test_wrong_ability_menu_fails_without_selecting(wrong):
    adapter = adapter_with_items()
    sent = []

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "menu", "tag": wrong, "items": [
            {"text": "Berserk    None    0%", "hotkeys": [97]}]})
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="native ability"):
        await adapter.dispatch(Action("ability", "berserk"))
    assert sent == ["a"]


@pytest.mark.parametrize("entries", [
    [{"text": "Berserk    None    0%", "colour": 8, "hotkeys": [122]}],
    [{"text": "Trog's Hand    Piety    0%", "hotkeys": [121]}],
])
async def test_fresh_unavailable_or_missing_ability_returns_updated_state_without_cast(entries):
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "menu", "tag": "ability", "items": [
        {"text": "Berserk    None    0%", "hotkeys": [98]}]})
    adapter.state.apply({"msg": "close_menu"})
    assert adapter.state.observation().abilities[0].available
    sent = []

    async def key(value):
        sent.append(value)
        if value == "a":
            adapter.state.apply({"msg": "menu", "tag": "ability", "items": entries})
        else:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("ability", "berserk"))
    assert not result.menu
    assert not any(ability.name == "Berserk" and ability.available for ability in result.abilities)
    assert sent == ["a", 27]


async def test_monster_examine_is_public_visible_right_click_and_status_expires():
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "map", "cells": [{"x": 2, "y": 1, "t": {"bg": 1},
                        "mon": {"id": 7, "name": "Sigmund"}}]})
    sent = []

    async def command(message):
        sent.append(message)
        adapter.state.apply({"msg": "ui-push", "type": "describe-monster", "title": "Sigmund",
                             "body": "He is wielding a scythe of distortion.\nSpeed: 100%",
                             "status": "He is confused.",
                             "spellset": [{"label": "", "spells": [
                                 {"title": "Confuse", "effect": "chance to confuse: 50%"}]}]})
        return adapter.state.observation()

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "ui-pop"})
        return adapter.state.observation()

    adapter._command, adapter.key = command, key
    result = await adapter.dispatch(Action("inspect_monster", "2,1"))
    assert sent == [{"msg": "click_cell", "x": 2, "y": 1, "button": 3}, 27]
    assert "distortion" in result.monsters[0].description
    assert result.monsters[0].equipment == ("He is wielding a scythe of distortion.",)
    assert result.monsters[0].statuses == ("He is confused.",)
    assert result.monsters[0].spells == ("Confuse",)
    assert "chance to confuse: 50%" in result.monsters[0].description
    adapter.state.apply({"msg": "player", "turn": 13})
    assert adapter.state.observation().monsters[0].statuses == ()
    adapter.state.apply({"msg": "map", "cells": [{"x": 2, "y": 1, "t": {"bg": 0x20000}}]})
    with pytest.raises(ProtocolError, match="not visible"):
        await adapter.dispatch(Action("inspect_monster", "2,1"))


@pytest.mark.parametrize("kind,item,prefix,prompt", [
    ("zap", "a wand of flame (12)", "V", "Evoke which item?"),
    ("throw", "3 poisoned darts", "F", "Fire/throw/use which item?"),
    ("blink", "a scroll of blinking", "r", "Read which item?")])
async def test_targeted_actions_select_one_item_then_native_visible_target(kind, item, prefix, prompt):
    adapter = adapter_with_items((("b", item),))
    adapter.state.apply({"msg": "map", "cells": [{"x": 2, "y": 1, "f": 33, "t": {"bg": 1},
                        **({} if kind == "blink" else {"mon": {"id": 1, "name": "orc"}})}]})
    sent = []

    async def key(value):
        sent.append(value)
        if value == prefix:
            adapter.state.apply({"msg": "input_mode", "mode": 7})
            adapter.state.apply({"msg": "msgs", "messages": [{"text": prompt}]})
        elif value == "b":
            adapter.state.apply({"msg": "input_mode", "mode": 2})
        return adapter.state.observation()

    async def command(message):
        sent.append(message)
        adapter.state.apply({"msg": "player", "turn": 13})
        adapter.state.apply({"msg": "input_mode", "mode": 1})
        return adapter.state.observation()

    adapter.key, adapter._command = key, command
    result = await adapter.dispatch(Action(kind, "b:2,1"))
    assert sent == [prefix, "b", {"msg": "click_cell", "x": 2, "y": 1, "button": 1}]
    assert result.turn == 13 and result.input_mode == 1


async def test_targeted_action_never_sends_for_unseen_target():
    adapter = adapter_with_items((("b", "a wand of flame (12)"),))
    sent = []

    async def key(value):
        sent.append(value)
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="not currently visible"):
        await adapter.dispatch(Action("zap", "b:2,1"))
    assert sent == []


async def test_stale_item_prompt_cannot_select_item():
    adapter = adapter_with_items((("b", "a potion of curing"),))
    sent = []

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "input_mode", "mode": 7})
        adapter.state.apply({"msg": "msgs", "messages": [{"text": "Read which item?"}]})
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="native selection"):
        await adapter.dispatch(Action("quaff", "b"))
    assert sent == ["q"]


async def test_training_uses_visible_hotkeys_exclusive_manual_target_and_no_turn():
    adapter = adapter_with_items()
    sent = []
    mode, view = "auto", "training"
    enabled = {"Fighting": True, "Axes": False}
    target = None

    def screen():
        if not adapter.state.menus:
            adapter.state.apply({"msg": "menu", "type": "crt", "tag": "skills"})
        title = "Train" if view == "training" else "Target"
        lines = {"0": f"mode: {mode}     Skill Level {title}"}
        for n, (name, key) in enumerate((("Fighting", "h"), ("Axes", "z")), 1):
            sign = "+" if enabled[name] else "-"
            extra = ("100%" if enabled[name] else "") if view == "training" else (
                str(target) if name == "Axes" and target is not None else "--")
            lines[str(n)] = f" {key} {sign} {name:<15} 4.0 {extra}"
        adapter.state.apply({"msg": "txt", "id": "menu_txt", "clear": True, "lines": lines})

    async def key(value):
        nonlocal mode, view
        sent.append(value)
        if value == "m":
            screen()
        elif value == "/":
            mode = "manual"
            screen()
        elif value == "!":
            view = "targets" if view == "training" else "training"
            screen()
        elif value == "=":
            pass
        elif value in {"h", "z"}:
            if view == "targets":
                adapter.state.apply({"msg": "init_input", "type": "generic", "tag": "skill_target",
                                     "prompt": "Enter a skill target for Axes: ", "prefill": "0"})
            else:
                name = "Fighting" if value == "h" else "Axes"
                enabled[name] = not enabled[name]
                screen()
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    async def send(message):
        sent.append(message)

    async def command(message):
        nonlocal target
        sent.append(message)
        target = float(message["text"].strip())
        adapter.state.apply({"msg": "close_input"})
        screen()
        return adapter.state.observation()

    adapter.key, adapter.send, adapter._command = key, send, command
    result = await adapter.dispatch(Action("train_skill", "Axes:14"))
    skills = {s.name: s for s in result.skills}
    assert skills["Axes"].enabled and not skills["Fighting"].enabled
    assert skills["Axes"].target == 14 and result.turn == 12 and not result.menu
    assert {"msg": "text_input", "text": "14\r"} in sent
    assert sent.count("z") == 2 and sent.count("h") == 1


async def test_identify_selects_explicit_item_not_arbitrary_menu_default():
    adapter = adapter_with_items((("a", "a scroll of identify"), ("b", "a blue potion")))
    sent = []

    async def key(value):
        sent.append(value)
        if value == "i":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " a - a scroll of identify", "hotkeys": [97], "q": 1},
                {"text": " b - a blue potion", "hotkeys": [98], "q": 1}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        elif value == "r":
            adapter.state.apply({"msg": "input_mode", "mode": 7})
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Read which item?"}]})
        elif value == "a":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "Identify which item? (\\ to view known items)"},
                                 "items": [{"text": " b - a blue potion", "q": 1, "hotkeys": [98], "level": 2}]})
        elif value == "b":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            adapter.state.apply({"msg": "player", "turn": 13,
                                 "inv": {"1": {"name": "a potion of heal wounds"}}})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("identify", "a:b"))
    assert sent == ["i", 27, "r", "a", "b"]
    assert result.inventory[1].name == "a potion of heal wounds"


def test_action_assessment_is_recorded_without_changing_agent_contract():
    action = Action("wait", assessment={"candidate": "wait", "risk": 4})
    assert action.to_dict()["assessment"] == {"candidate": "wait", "risk": 4}
    assert Observation().abilities == ()


async def test_empty_ring_slot_equips_and_refreshes_native_worn_marker():
    adapter = adapter_with_items((("a", "+0 ring mail"), ("b", "a ring of protection")))
    adapter.state.equipment_slots.add(0)
    sent = []

    async def key(value):
        sent.append(value)
        if value == "P":
            adapter.state.apply({"msg": "input_mode", "mode": 7})
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Put on which piece of jewellery?"}]})
        elif value == "b":
            adapter.state.apply({"msg": "input_mode", "mode": 1})
            adapter.state.apply({"msg": "player", "turn": 13})
        elif value == "i":
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " a - +0 ring mail (worn)", "hotkeys": [97]},
                {"text": " b - a ring of protection (worn)", "hotkeys": [98]}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("put_on", "b"))
    assert sent == ["P", "b", "i", 27]
    assert result.inventory[1].equipped and result.turn == 13


async def test_training_wrong_native_screen_fails_before_any_selection():
    adapter = adapter_with_items()
    sent = []

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "menu", "tag": "inventory", "type": "crt"})
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="native skills"):
        await adapter.dispatch(Action("train_skill", "Axes:18"))
    assert sent == ["m"]


async def test_only_explicit_fresh_save_transaction_may_accept_confirmation():
    adapter = adapter_with_items()
    sent = []

    async def key(value):
        sent.append(value)
        if value == "S":
            adapter.state.apply({"msg": "input_mode", "mode": 8})
            adapter.state.apply({"msg": "msgs", "messages": [{"text": "Save game and exit?"}]})
        elif value == "Y":
            adapter.state.apply({"msg": "game_ended", "reason": "saved"})
        return adapter.state.observation()

    adapter.key = key
    await adapter.dispatch(Action("save"))
    assert (await adapter.dispatch(Action("confirm", "y"))).game_over
    assert sent == ["S", "Y"]
    other = adapter_with_items()
    other.state.apply({"msg": "input_mode", "mode": 8})
    other.state.apply({"msg": "msgs", "messages": [{"text": "Really attack your ally?"}]})
    with pytest.raises(ProtocolError, match="Unsolicited"):
        await other.dispatch(Action("confirm", "y"))


def test_berserk_cooldown_does_not_prohibit_consumables_or_prompt_cancellation():
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "player", "status": [
        {"light": "-Berserk", "text": "on berserk cooldown", "col": 14}]})
    observation = adapter.state.observation()
    assert not adapter._status_illegal(observation, "quaff")
    assert not adapter._status_illegal(observation, "read")
    adapter.state.apply({"msg": "player", "status": [
        {"light": "Para", "text": "paralysed", "col": 4}]})
    assert not adapter._status_illegal(adapter.state.observation(), "cancel")


@pytest.mark.parametrize("assessment", [None, [], {"risk": float("nan")},
                                          {"risk": float("inf")}, {"provider": object()}])
def test_action_assessment_rejects_non_json_or_nonfinite_metadata(assessment):
    with pytest.raises(ValueError, match="assessment"):
        Action("wait", assessment=assessment)


def test_native_menu_and_popup_share_z_order_through_reconnection_stack():
    state = GameState()
    state.apply({"msg": "ui-stack", "items": [
        {"msg": "menu", "tag": "pickup", "title": {"text": "What do you want to examine?"}},
        {"msg": "ui-push", "type": "describe-monster", "title": "A hobgoblin."}]})
    assert state.observation().menu["type"] == "describe-monster"
    state.apply({"msg": "ui-pop"})
    assert state.observation().menu["tag"] == "pickup"
    state.apply({"msg": "ui-push", "type": "describe-monster"})
    state.apply({"msg": "close_all_menus"})
    assert state.observation().menu["type"] == "describe-monster"
    state.apply({"msg": "ui-pop"})
    assert not state.observation().menu


async def test_monster_on_item_square_uses_examination_menu_without_pickup_or_turn():
    # Native seed-6 turn646: hobgoblin stands on a remembered item. The
    # examination-only DescMenu deliberately reuses tag="pickup".
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "map", "cells": [
        {"x": 2, "y": 4, "t": {"bg": 1}, "mon": {"id": 1, "name": "hobgoblin"}}]})
    sent = []

    async def command(message):
        sent.append(message)
        adapter.state.apply({"msg": "menu", "tag": "pickup", "title": {
            "text": "<white>What do you want to examine?"}, "items": [
                {"text": "Monsters", "level": 1},
                {"text": " a - (<brown>g</brown>) a hobgoblin (wandering)",
                 "q": 1, "hotkeys": [97], "level": 2},
                {"text": "Items", "level": 1},
                {"text": " b - a jackal corpse", "q": 2, "hotkeys": [98], "level": 2}]})
        return adapter.state.observation()

    async def key(value):
        sent.append(value)
        if value == "a":
            adapter.state.apply({"msg": "ui-push", "type": "describe-monster",
                                 "title": "A hobgoblin.", "body": "Max HP: ~5"})
        elif value == 27 and adapter.state.ui:
            adapter.state.apply({"msg": "ui-pop"})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter._command, adapter.key = command, key
    result = await adapter.dispatch(Action("inspect_monster", "2,4"))
    assert sent == [{"msg": "click_cell", "x": 2, "y": 4, "button": 3}, "a", 27, 27]
    assert result.monsters[0].description == "Max HP: ~5"
    assert result.turn == 12 and not result.menu and not result.inventory


@pytest.mark.parametrize("title,entry_name", [
    ("Pick up what?", "hobgoblin"),
    ("What do you want to examine?", "jackal")])
async def test_square_inspection_refuses_pickup_or_wrong_monster_entry(title, entry_name):
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "map", "cells": [
        {"x": 2, "y": 4, "t": {"bg": 1}, "mon": {"id": 1, "name": "hobgoblin"}}]})
    sent = []

    async def command(message):
        sent.append(message)
        adapter.state.apply({"msg": "menu", "tag": "pickup", "title": {"text": title}, "items": [
            {"text": "Monsters", "level": 1},
            {"text": entry_name, "q": 1, "hotkeys": [97], "level": 2}]})
        return adapter.state.observation()

    adapter._command = command
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("inspect_monster", "2,4"))
    assert len(sent) == 1


async def test_native_use_item_armour_menu_selects_matching_equipment_not_supply_letter():
    # Actual pilot2 seed6 turn19 packets: item menu is ready while input_mode
    # remains 0; equipment c and supply c are distinct public inventory slots.
    adapter = adapter_with_items((("a", "+0 hand axe"), ("b", "+0 animal skin"),
                                  ("c", "+0 ring mail")))
    adapter.state.apply({"msg": "player", "weapon_index": 0, "inv": {
        "52": {"name": "scroll labelled IGETHA EFRIU", "quantity": 1, "letter": 99}}})
    adapter.state.equipment_slots.add(1)
    sent = []

    async def key(value):
        sent.append(value)
        if value == "W":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "<white>Wear or take off which item?"},
                                 "items": [
                                     {"text": "Armour", "level": 1},
                                     {"text": " b - a +0 animal skin (worn)",
                                      "q": 1, "hotkeys": [98, 91], "level": 2},
                                     {"text": " c - a +0 ring mail",
                                      "q": 1, "hotkeys": [99, 91], "level": 2}]})
        elif value == "c":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "player", "turn": 23, "ac": 5})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        elif value == "i":
            adapter.state.apply({"msg": "menu", "tag": "inventory", "items": [
                {"text": " b - a +0 animal skin", "hotkeys": [98]},
                {"text": " c - a +0 ring mail (worn)", "hotkeys": [99]},
                {"text": " c - a scroll labelled IGETHA EFRIU", "hotkeys": [99]}]})
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("wear", "c"))
    assert sent == ["W", "c", "i", 27]
    assert result.ac == 5 and result.inventory[2].equipped
    assert not result.inventory[1].equipped
    assert not result.inventory[3].equipped
    assert not result.menu and result.input_mode == 1


@pytest.mark.parametrize("kind,name,prefix,title", [
    ("quaff", "potion of curing", "q", "Drink which item?"),
    ("read", "scroll of fog", "r", "Read which item?"),
    ("wield", "+0 war axe", "w", "Wield or unwield which item (- for none)?"),
    ("put_on", "ring of protection", "P", "Put on or remove which piece of jewellery?"),
    ("zap", "wand of flame (12)", "V", "Evoke which item?"),
    ("throw", "3 poisoned darts", "F", "Fire/throw/use which item?")])
async def test_native_use_item_selection_is_supported_for_each_explicit_item_class(kind, name, prefix, title):
    adapter = adapter_with_items((("c", name),))
    sent = []

    async def key(value):
        sent.append(value)
        if value == prefix:
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": title},
                                 "items": [{"text": " c - " + name, "hotkeys": [99, 91]}]})
        elif value == "c":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        return adapter.state.observation()

    adapter.key = key
    await adapter._select_item(kind, "c")
    assert sent == [prefix, "c"]


@pytest.mark.parametrize("items", [[], [{"text": " c - a potion of degeneration", "hotkeys": [99]}]])
async def test_native_use_item_menu_still_rejects_absent_or_changed_selection(items):
    adapter = adapter_with_items((("c", "potion of curing"),))
    sent = []

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "input_mode", "mode": 0})
        adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": "Drink which item?"},
                             "items": items})
        return adapter.state.observation()

    adapter.key = key
    with pytest.raises(ProtocolError, match="not present"):
        await adapter.dispatch(Action("quaff", "c"))
    assert sent == ["q"]


@pytest.mark.parametrize("tag,title,deferred", [
    ("use_item", "<white>Identify which item? (\\ to view known items)</white>", True),
    ("inventory", "Identify which item? (\\ to view known items)", False),
    ("use_item", "Read which item?", False),
    ("use_item", "Identify which item?", False),
    ("use_item", "Identify which item? (\\ to view known items) extra", False),
])
def test_housekeeping_defers_only_exact_native_identification_menu(tag, title, deferred):
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    adapter.state.apply({"msg": "menu", "tag": tag, "title": {"text": title},
                         "items": [{"text": " c - a blue potion", "q": 1, "hotkeys": [99], "level": 2}]})
    action = adapter.housekeeping(adapter.state.observation())
    assert (action is None) is deferred
    if action is not None:
        assert action.kind == "cancel"


async def test_identification_menu_uses_visible_unique_hotkey_despite_inventory_letter_collision():
    adapter = adapter_with_items((("c", "ring of flight"), ("c", "a blue potion")))
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    adapter.state.apply({"msg": "menu", "tag": "use_item",
                         "title": {"text": "Identify which item? (\\ to view known items)"},
                         "items": [{"text": " c - a blue potion", "q": 1, "hotkeys": [99, 33], "level": 2}]})
    sent = []

    async def key(value):
        sent.append(value)
        adapter.state.apply({"msg": "close_menu"})
        adapter.state.apply({"msg": "input_mode", "mode": 1})
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.dispatch(Action("menu", "c"))
    assert sent == ["c"] and not result.menu


@pytest.mark.parametrize("items,key", [
    ([], "c"),
    ([{"text": " c - a blue potion", "q": 1, "hotkeys": [99]},
      {"text": " c - a scroll labelled FOO", "q": 1, "hotkeys": ["c"]}], "c"),
    ([{"text": " c - a blue potion", "q": 1, "hotkeys": [99]},
      {"text": "Unselectable row", "q": 0, "hotkeys": [99]}], "c"),
    ([{"text": " c - a blue potion", "q": 0, "hotkeys": [99]}], "c"),
    ([{"text": "Potions", "level": 1, "hotkeys": [99]}], "c"),
    ([{"text": " c - a blue potion", "colour": 8, "hotkeys": [99]}], "c"),
])
async def test_identification_menu_rejects_absent_ambiguous_or_disabled_current_choice(items, key):
    adapter = adapter_with_items((("c", "a blue potion"),))
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    adapter.state.apply({"msg": "menu", "tag": "use_item",
                         "title": {"text": "Identify which item? (\\ to view known items)"},
                         "items": items})
    sent = []

    async def keypress(value):
        sent.append(value)
        return adapter.state.observation()

    adapter.key = keypress
    with pytest.raises(ProtocolError, match="absent, ambiguous or unavailable"):
        await adapter.dispatch(Action("menu", key))
    assert sent == []


async def test_generic_use_item_choice_cannot_bypass_verified_identify_title():
    adapter = adapter_with_items()
    adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": "Drink which item?"},
                         "items": [{"text": " c - a blue potion", "hotkeys": [99]}]})
    sent = []
    async def key(value):
        sent.append(value)
        return adapter.state.observation()
    adapter.key = key
    with pytest.raises(ProtocolError):
        await adapter.dispatch(Action("menu", "c"))
    assert not sent


@pytest.mark.parametrize("attack,expected", [
    ("Hit: +0 flail  15", ("+0 flail",)),
    ("Hit: +0 spear  12  Reaches from afar", ("+0 spear",)),
    ("2x Hit: +0 halberd  18", ("+0 halberd",)),
    ("Shoot: +0 shortbow  11", ("+0 shortbow",)),
    ("Hit  15", ()),
])
def test_public_monster_attack_table_exposes_only_displayed_equipment(attack, expected):
    state = GameState()
    state.apply({"msg": "map", "cells": [
        {"x": 1, "y": 0, "f": 33, "t": {"bg": 1},
         "mon": {"id": 7, "name": "gnoll", "weapon": "+9 hidden scythe"}}]})
    state.observe_monster(7, {"body": "Attack         Max Damage\n" + attack + "\n"})
    monster = state.observation().monsters[0]
    assert monster.equipment == expected
    assert "hidden" not in " ".join(monster.equipment)


@pytest.mark.parametrize("prompt,confirmed", [
    ("Really abort (and waste the scroll)? ",True),
    ("Really attack this friendly creature?",False),
])
async def test_explicit_cancellation_only_confirms_its_own_native_scroll_abort(prompt,confirmed):
    adapter = adapter_with_items((("a","scroll of brand weapon"),))
    adapter.state.apply({"msg":"menu","tag":"use_item","title":{"text":"Brand which weapon?"},"items":[]})
    keys = []
    async def native_key(key):
        keys.append(key)
        if key == 27:
            adapter.state.apply({"msg":"menu","tag":"prompt","title":{"text":prompt},
                                 "items":[{"text":" Y - Yes","hotkeys":[89,121]},
                                          {"text":" N - No","hotkeys":[78,110]}]})
            adapter.state.apply({"msg":"input_mode","mode":8})
        elif key == "Y" and confirmed:
            adapter.state.apply({"msg":"ui-stack","items":[]})
            adapter.state.apply({"msg":"player","turn":13,"inv":{"0":{"quantity":0}}})
            adapter.state.apply({"msg":"input_mode","mode":1})
        else:
            pytest.fail("An unrelated confirmation must never receive Yes")
        return adapter.state.observation()
    adapter.key = native_key
    result = await adapter.dispatch(Action("cancel"))
    if confirmed:
        assert result.input_mode == 1 and not result.menu and not result.inventory
        assert result.turn == 13
        assert keys == [27,"Y"]
    else:
        assert result.input_mode == 8 and keys == [27]


async def test_lowercase_semantic_no_uses_native_uppercase_confirmation_key():
    adapter = adapter_with_items()
    adapter.state.apply({"msg":"input_mode","mode":8,"prompt":"Really attack this friendly creature?"})
    async def native_key(key):
        if key == "N":
            adapter.state.apply({"msg":"input_mode","mode":1})
        return adapter.state.observation()
    adapter.key = native_key
    result = await adapter.dispatch(Action("confirm","n"))
    assert result.input_mode == 1


@pytest.mark.parametrize("title", ["Enchant which weapon?", "Enchant which item?",
                                  "Identify which item? (\\ to view known items)"])
async def test_saved_native_item_menu_is_ready_for_strategy_without_another_packet(title):
    adapter = adapter_with_items((("a", "+0 hand axe"),))
    adapter.state.apply({"msg": "input_mode", "mode": 0})
    adapter.state.apply({"msg": "menu", "tag": "use_item", "title": {"text": title},
                         "items": [{"text": " a - a +0 hand axe (weapon)", "hotkeys": [97], "q": 1}]})
    result = await adapter.create_character()
    assert result.hp == 30 and result.menu["title"]["text"] == title
    assert adapter.housekeeping(result) is None


def test_monster_spell_ranges_come_only_from_displayed_native_labels():
    adapter = adapter_with_items()
    adapter.state.apply({"msg":"map","cells":[{"x":2,"y":1,"f":33,"t":{"bg":1},
                         "mon":{"id":7,"name":"endoplasm","threat":0}}]})
    adapter.state.observe_monster(7,{"body":"A visible endoplasm.","spellset":[{"spells":[
        {"title":"Freeze","effect":"(1d5)","range_string":"(<lightred>1</lightred>)","power":999},
        {"title":"Unknown ability","effect":"","power":999},
        {"title":"Distant attack","effect":"(2d3)","range_string":"(<lightgray>5*</lightgray>)"}]}]})
    monster = adapter.state.observation().monsters[0]
    assert monster.spell_ranges == (("Freeze",1),("Distant attack",5))
    assert "power" not in vars(monster)
