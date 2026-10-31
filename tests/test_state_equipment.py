from dcss_bot.state import GameState


def initial_state():
    state = GameState()
    state.apply({"msg": "player", "weapon_index": 0, "inv": {
        "0": {"name": "+0 hand axe", "quantity": 1, "letter": 97},
        "1": {"name": "+0 animal skin", "quantity": 1, "letter": 98},
        "2": {"name": "+0 ring mail", "quantity": 1, "letter": 99},
    }})
    return state


def test_equipped_armour_comes_from_public_inventory_menu_not_hidden_flags():
    state = initial_state()
    # tileweb.cc sends DESC_A in inv.name: no '(worn)' and no equipment bit.
    state.apply({"msg": "player", "inv": {"1": {"flags": 0xFFFFFFFF, "sub_type": 2, "plus": 99}}})
    assert not state.observation().inventory[1].equipped
    state.apply({"msg": "menu", "tag": "inventory", "items": [
        {"text": " a - a +0 hand axe (weapon)", "hotkeys": [97]},
        {"text": " <white>b - a +0 animal skin (worn)</white>", "hotkeys": [98]},
        {"text": " c - a +0 ring mail", "hotkeys": [99]},
    ]})
    state.apply({"msg": "close_menu"})
    inventory = state.observation().inventory
    assert [item.equipped for item in inventory] == [True, True, False]
    assert inventory[1].name == "+0 animal skin"


def test_worn_state_changes_only_on_public_observation_and_survives_menu_close():
    state = initial_state()
    state.apply({"msg": "menu", "tag": "inventory", "items": [
        {"text": " b - a +0 animal skin (worn)"}, {"text": " c - a +0 ring mail"}]})
    state.apply({"msg": "update_menu_items", "chunk_start": 0, "items": [
        {"text": " b - a +0 animal skin"}, {"text": " c - a +0 ring mail (worn)"}]})
    state.apply({"msg": "close_menu"})
    assert [item.equipped for item in state.observation().inventory] == [True, False, True]


def test_noninventory_menu_does_not_forge_worn_state_and_reused_slot_is_not_equipped():
    state = initial_state()
    state.apply({"msg": "menu", "tag": "pickup", "items": [{"text": " b - +0 animal skin (worn)"}]})
    assert not state.observation().inventory[1].equipped
    state.apply({"msg": "close_menu"})
    state.apply({"msg": "menu", "tag": "inventory", "items": [{"text": " b - +0 animal skin (worn)"}]})
    state.apply({"msg": "close_menu"})
    state.apply({"msg": "player", "inv": {"1": {"quantity": 0}}})
    state.apply({"msg": "player", "inv": {"1": {"name": "a potion of curing", "quantity": 1}}})
    assert not state.observation().inventory[1].equipped


def test_weapon_index_changes_do_not_leave_old_weapon_marked_equipped():
    state = initial_state()
    state.apply({"msg": "menu", "tag": "inventory", "items": [{"text": " a - +0 hand axe (weapon)"}]})
    state.apply({"msg": "close_menu"})
    state.apply({"msg": "player", "weapon_index": -1})
    assert not state.observation().inventory[0].equipped


def test_supply_and_equipment_hotkey_collision_does_not_confuse_worn_slots():
    state = GameState()
    state.apply({"msg": "player", "inv": {
        "2": {"name": "a ring of flight", "quantity": 1, "letter": 99},
        "53": {"name": "a scroll of teleportation", "quantity": 1, "letter": 99},
    }})
    state.apply({"msg": "menu", "tag": "inventory", "items": [
        {"text": " c - a ring of flight (worn)", "hotkeys": [99]},
        {"text": " c - a scroll of teleportation", "hotkeys": [99]},
    ]})
    inventory = state.observation().inventory
    assert [(item.slot, item.equipped) for item in inventory] == [(2, True), (53, False)]
    state.apply({"msg": "player", "inv": {"53": {"quantity": 0}}})
    assert state.observation().inventory[0].equipped
