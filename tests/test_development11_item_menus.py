"""Captured identify transitions must refresh all native inventory categories."""
import json
from pathlib import Path

import pytest

from dcss_bot.adapter import WebTilesAdapter
from dcss_bot.characters.mibe import PROFILE
from dcss_bot.characters.mibe_development import identification_target
from dcss_bot.models import Action
from dcss_bot.state import GameState


CAPTURE = Path(__file__).parent / "fixtures" / "recorded_development11_item_menus.json"
RIGHT = -251  # Pinned native CK_RIGHT, not a selectable inventory letter.


def captured_adapter(seed):
    case = json.loads(CAPTURE.read_text())["cases"][str(seed)]
    observed = case["after"]
    adapter = WebTilesAdapter("ws://unused", "bot", "secret", character=PROFILE)
    base_types = {"weapon": 0, "armour": 2, "scroll": 5, "jewellery": 6, "potion": 7}
    adapter.state.apply({"msg": "player", **{k: observed[k] for k in (
        "species", "god", "hp", "hp_max", "xl", "turn")}, "inv": {
            str(item["slot"]): {"name": item["name"], "letter": ord(item["letter"]),
                                "quantity": item["quantity"], "base_type": base_types[item["category"]]}
            for item in observed["inventory"]}})
    adapter.state.apply({"msg": "input_mode", "mode": 1})
    return case, adapter


def inventory_pages(case):
    # These are modeled native category frames, not additional captured packets.
    # Their current labels come from the captured successful public ID message.
    labels = {}
    for change in case["after"]["messages"][-1].split("; "):
        _, visible = change.split(" -> ", 1)
        letter, name = visible.split(" - ", 1)
        labels[letter] = name
    pages = {"Gear": [], "Potions": [], "Scrolls": []}
    categories = {"potion": "Potions", "scroll": "Scrolls"}
    for item in case["after"]["inventory"]:
        name = labels.get(item["letter"], item["name"]) if item["slot"] >= 52 else item["name"]
        page = categories.get(item["category"], "Gear")
        pages[page].append({"text": f" {item['letter']} - {name}",
                            "hotkeys": [ord(item["letter"])], "q": item["quantity"],
                            "colour": 7, "level": 2})
    return [(name, rows) for name, rows in pages.items() if rows]


def apply_page(state, name, rows, *, opened=False):
    title = {"text": name + ":     (Left/Right to switch category)"}
    if opened:
        state.apply({"msg": "input_mode", "mode": 0})
        state.apply({"msg": "menu", "tag": "inventory", "title": title,
                     "total_items": len(rows), "items": rows})
    else:
        # InvMenu::set_page emits a size update followed by replacement rows.
        state.apply({"msg": "update_menu", "title": title, "total_items": len(rows)})
        state.apply({"msg": "update_menu_items", "chunk_start": 0, "items": rows})


@pytest.mark.parametrize("seed", [5, 7])
async def test_captured_identify_reletter_refreshes_supplies_before_spending_another_scroll(seed):
    case, adapter = captured_adapter(seed)
    pages = inventory_pages(case)
    sent = []
    page_index = 0

    async def key(value):
        nonlocal page_index
        sent.append(value)
        if value == "i":
            apply_page(adapter.state, *pages[0], opened=True)
        elif value == RIGHT:
            page_index = (page_index + 1) % len(pages)
            apply_page(adapter.state, *pages[page_index])
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        else:
            pytest.fail(f"A stale identify request must not spend or select anything: {value!r}")
        return adapter.state.observation()

    adapter.key = key
    action = Action(**case["failed_action"])
    before = adapter.state.observation()
    result = await adapter.dispatch(action)
    assert sent == ["i", *([RIGHT] * len(pages)), 27]
    assert result.turn == before.turn and not result.menu and result.input_mode == 1
    assert [(i.slot, i.letter, i.quantity) for i in result.inventory] == [
        (i.slot, i.letter, i.quantity) for i in before.inventory]
    by_slot = {item.slot: item for item in result.inventory}
    if seed == 5:
        assert by_slot[54].name == "a potion of curing"
        scroll, target = identification_target(result.inventory)
        assert (scroll.letter, target.slot, target.letter) == ("i", 53, "d")
        assert by_slot[2].name == "+0 sling"  # Same c hotkey, different class.
    else:
        assert by_slot[52].name == "a scroll of enchant weapon"
        assert identification_target(result.inventory) is None
        assert by_slot[2].name == "ring of protection from fire"
    # Merely refreshing must not consume the remaining identify scroll.
    assert next(i for i in result.inventory if i.letter == "i").quantity == 1


async def test_refreshed_seed7_enchant_scroll_can_be_read_with_its_actual_source_identity():
    case, adapter = captured_adapter(7)
    pages = inventory_pages(case)
    sent = []
    page_index = 0

    async def key(value):
        nonlocal page_index
        sent.append(value)
        if value == "i":
            apply_page(adapter.state, *pages[0], opened=True)
        elif value == RIGHT:
            page_index = (page_index + 1) % len(pages)
            apply_page(adapter.state, *pages[page_index])
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        elif value == "r":
            adapter.state.apply({"msg": "input_mode", "mode": 0})
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "Read which item?"},
                                 "items": pages[-1][1]})
        elif value == "w":
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "menu", "tag": "use_item",
                                 "title": {"text": "Enchant which weapon?"}, "items": [
                                     {"text": " a - a +0 hand axe (weapon)", "hotkeys": [97], "q": 1}]})
        else:
            pytest.fail(f"Unexpected source selection {value!r}")
        return adapter.state.observation()

    adapter.key = key
    await adapter.dispatch(Action(**case["failed_action"]))
    result = await adapter.dispatch(Action("read", "w"))
    assert sent[-2:] == ["r", "w"]
    assert adapter._is_development_menu(result)
    assert result.inventory[-1].letter == "i" and result.inventory[-1].quantity == 1


async def test_inventory_refresh_visits_evocables_and_skips_empty_native_categories():
    _, adapter = captured_adapter(7)
    adapter.state.apply({"msg": "player", "inv": {
        "52": {"quantity": 0}, "53": {"quantity": 0},
        "54": {"name": "wand of flame (3)", "quantity": 1, "letter": 102, "base_type": 3}}})
    pages = [("Gear", [{"text": " a - a +0 hand axe", "hotkeys": [97], "q": 1}]),
             ("Evocable Items", [{"text": " f - a wand of flame (2)", "hotkeys": [102], "q": 1}])]
    sent = []
    page_index = 0

    async def key(value):
        nonlocal page_index
        sent.append(value)
        if value == "i":
            apply_page(adapter.state, *pages[0], opened=True)
        elif value == RIGHT:
            page_index = (page_index + 1) % 2
            apply_page(adapter.state, *pages[page_index])
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        else:
            pytest.fail(f"Inventory inspection must not select an item: {value!r}")
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.inspect_inventory()
    assert sent == ["i", RIGHT, RIGHT, 27]
    assert next(i for i in result.inventory if i.slot == 54).name == "a wand of flame (2)"


def test_shorter_native_category_update_removes_prior_page_item_hotkeys():
    state = GameState()
    apply_page(state, "Gear", [
        {"text": " a - a +0 hand axe", "hotkeys": [97], "q": 1},
        {"text": " c - a +0 sling", "hotkeys": [99], "q": 1},
        {"text": " i - a +0 robe", "hotkeys": [105], "q": 1}], opened=True)
    apply_page(state, "Scrolls", [
        {"text": " i - a scroll of identify", "hotkeys": [105], "q": 1}])
    rows = state.observation().menu["items"]
    assert len(rows) == 1 and rows[0]["text"] == " i - a scroll of identify"
    assert [row for row in rows if 99 in row["hotkeys"]] == []
    state.apply({"msg": "update_menu", "total_items": 0})
    assert state.observation().menu["items"] == []


@pytest.mark.parametrize("mismatch", ["quantity", "duplicate"])
async def test_paged_refresh_does_not_reidentify_an_ambiguous_or_different_quantity_stack(mismatch):
    case, adapter = captured_adapter(5)
    pages = inventory_pages(case)
    potion_rows = pages[1][1]
    if mismatch == "quantity":
        potion_rows[0]["q"] += 1
    else:
        potion_rows.append(dict(potion_rows[0], text=" c - a potion of heal wounds"))
    page_index = 0
    sent = []

    async def key(value):
        nonlocal page_index
        sent.append(value)
        if value == "i":
            apply_page(adapter.state, *pages[0], opened=True)
        elif value == RIGHT:
            page_index = (page_index + 1) % len(pages)
            apply_page(adapter.state, *pages[page_index])
        elif value == 27:
            adapter.state.apply({"msg": "close_menu"})
            adapter.state.apply({"msg": "input_mode", "mode": 1})
        else:
            pytest.fail(f"Refresh must never guess a supply selection: {value!r}")
        return adapter.state.observation()

    adapter.key = key
    result = await adapter.inspect_inventory()
    assert sent == ["i", RIGHT, RIGHT, RIGHT, 27]
    potion = next(item for item in result.inventory if item.slot == 54)
    assert (potion.name, potion.quantity, potion.letter) == ("sapphire potion", 1, "c")
    assert next(item for item in result.inventory if item.slot == 2).name == "+0 sling"
