"""Incremental WebTiles state, following the pinned native browser protocol."""
from collections import deque
from copy import deepcopy
import html
import re

from .models import Cell, Item, Monster, Observation


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(text)))


def merge(target: dict, delta: dict) -> None:
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = deepcopy(value)


class GameState:
    def __init__(self) -> None:
        self.player: dict = {}
        self.cells: dict[tuple[int, int], dict] = {}
        self.monster_table: dict[int, dict] = {}
        self.equipment_slots: set[int] = set()
        self.messages: deque[str] = deque(maxlen=80)
        self.ui: list[dict] = []
        self.menus: list[dict] = []
        self.input_mode = 0
        self.more = False
        self.dead = False
        self.game_over = False
        self.end_reason = ""
        self.revision = 0
        self.last_x = self.last_y = 0

    def apply(self, message: dict) -> None:
        kind = message.get("msg")
        self.revision += 1
        if kind == "player":
            for slot, delta in message.get("inv", {}).items():
                if delta.get("quantity") == 0:
                    self.equipment_slots.discard(int(slot))
            merge(self.player, {k: v for k, v in message.items() if k != "msg"})
            if self.player.get("hp", 1) <= 0 and self.player.get("hp_max", 0):
                self.dead = True
        elif kind == "map":
            if message.get("clear"):
                self.cells.clear()
                self.monster_table.clear()
                self.last_x = self.last_y = 0
            for delta in message.get("cells", []):
                x = delta.get("x", self.last_x + 1)
                y = delta.get("y", self.last_y)
                self.last_x, self.last_y = x, y
                cell = self.cells.setdefault((x, y), {"x": x, "y": y})
                if isinstance(delta.get("mon"), dict):
                    mon = delta["mon"]
                    ident = mon.get("id", 0)
                    # A re-observed monster can get a fresh client ID while the
                    # server sends only that ID; unchanged fields belong to the
                    # prior occupant of this cell (as in the native client).
                    old = self.monster_table.get(ident) or cell.get("mon")
                    old = deepcopy(old or {})
                    merge(old, mon)
                    if ident:
                        self.monster_table[ident] = old
                    cell["mon"] = old
                merge(cell, {k: v for k, v in delta.items() if k != "mon"})
                if "mon" in delta and delta["mon"] is None:
                    cell["mon"] = None
        elif kind == "msgs":
            rollback = message.get("rollback", 0) + message.get("old_msgs", 0)
            for _ in range(min(rollback, len(self.messages))):
                self.messages.pop()
            self.more = message.get("more", self.more)
            for entry in message.get("messages", []):
                text = plain(entry.get("text", ""))
                self.messages.append(text)
                if "You die..." in text or "You have died" in text:
                    self.dead = True
        elif kind == "input_mode":
            self.input_mode = message["mode"]
            if self.input_mode == 1:
                self.more = False
        elif kind == "ui-push":
            self.ui.append(deepcopy(message))
            if message.get("type") == "game-over":
                self.game_over = True
                self.end_reason = plain(message.get("text", message.get("title", "")))
        elif kind == "ui-pop":
            if self.ui:
                self.ui.pop()
        elif kind == "ui-state" and self.ui:
            merge(self.ui[-1], message)
        elif kind == "ui-stack":
            self.ui.clear()
            self.menus.clear()
            for entry in message.get("items", []):
                self.apply(entry)
        elif kind == "menu":
            self.menus.append(deepcopy(message))
            self._observe_equipment_menu(message)
        elif kind in {"update_menu", "update_menu_items"} and self.menus:
            if "items" in message:
                menu = self.menus[-1]
                start = message.get("chunk_start", 0)
                items = menu.setdefault("items", [])
                while len(items) < start + len(message["items"]):
                    items.append({})
                items[start:start + len(message["items"])] = deepcopy(message["items"])
            merge(self.menus[-1], {k: v for k, v in message.items() if k != "items"})
            self._observe_equipment_menu(self.menus[-1])
        elif kind == "close_menu":
            if self.menus:
                self.menus.pop()
        elif kind == "close_all_menus":
            self.menus.clear()
        elif kind == "game_ended":
            self.game_over = True
            self.end_reason = message.get("reason", "game ended")

    def _observe_equipment_menu(self, menu: dict) -> None:
        # The player.inv names use DESC_A, which omits equipment suffixes.
        # Inventory menus expose DESC_INVENTORY_EQUIP to the human player.
        # Update only entries actually delivered (menus can arrive in chunks).
        if menu.get("tag") != "inventory":
            return
        for entry in menu.get("items", []):
            text = plain(entry.get("text", ""))
            match = re.match(r"\s*([a-zA-Z])\s+[-+#]\s+", text)
            if not match:
                continue
            # Equipment and supply inventories may reuse a hotkey. Match both
            # the visible label and hotkey, then retain the actual wire slot.
            for slot, item in self.player.get("inv", {}).items():
                letter = item.get("letter", int(slot) + ord("a"))
                letter = chr(letter) if isinstance(letter, int) else letter
                name = plain(item.get("name", ""))
                if letter != match.group(1) or not name or name not in text:
                    continue
                if any(marker in text for marker in ("(worn)", "(melded)")):
                    self.equipment_slots.add(int(slot))
                else:
                    self.equipment_slots.discard(int(slot))

    def observation(self) -> Observation:
        p = self.player
        pos = p.get("pos", {})
        cells = []
        monsters = []
        for (x, y), raw in self.cells.items():
            tile = raw.get("t", {})
            bg = tile.get("bg", 0)
            if isinstance(bg, list):
                bg = bg[0]
            visible = bool(tile) and not (bg & 0x60000)
            cells.append(Cell(x, y, raw.get("f", 0), raw.get("g", " "), visible,
                              bool(tile.get("cloud", 0)) if visible else False))
            mon = raw.get("mon")
            if visible and mon:
                monsters.append(Monster(mon.get("id", 0), plain(mon.get("name", "unknown")),
                                        x, y, mon.get("threat", 1), mon.get("att", 0)))
        inventory = []
        for slot, item in p.get("inv", {}).items():
            if item.get("quantity", 0) <= 0 or not item.get("name"):
                continue
            letter = item.get("letter", int(slot) + ord("a"))
            letter = chr(letter) if isinstance(letter, int) else letter
            name = plain(item["name"])
            equipped = (int(slot) in {p.get("weapon_index"), p.get("offhand_index")}
                        or int(slot) in self.equipment_slots or "(worn)" in name)
            inventory.append(Item(int(slot), letter, name, item["quantity"], equipped))
        menu = deepcopy(self.menus[-1] if self.menus else self.ui[-1] if self.ui else {})
        prompt = ""
        if self.input_mode in {7, 8}:
            title = menu.get("title", "")
            title = title.get("text", "") if isinstance(title, dict) else title
            prompt = plain(menu.get("prompt") or title or (self.messages[-1] if self.messages else ""))
        return Observation(
            name=p.get("name", ""), species=p.get("species", ""), god=p.get("god", ""),
            turn=p.get("turn", 0), time=p.get("time", 0), place=p.get("place", ""),
            depth=p.get("depth", 0), hp=p.get("hp", 0), hp_max=p.get("hp_max", 0),
            xl=p.get("xl", 1), position=(pos.get("x", 0), pos.get("y", 0)),
            statuses=tuple(plain(s.get("light", s.get("text", ""))) for s in p.get("status", [])),
            inventory=tuple(inventory), monsters=tuple(monsters), cells=tuple(cells),
            messages=tuple(self.messages), prompt=prompt, menu=menu,
            input_mode=5 if self.more else self.input_mode, dead=self.dead, game_over=self.game_over,
        )
