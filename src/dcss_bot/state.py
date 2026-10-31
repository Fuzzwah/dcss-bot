"""Incremental WebTiles state, following the pinned native browser protocol."""
from collections import deque
from copy import deepcopy
import html
import re

from .models import Ability, Cell, Item, Monster, Observation, Resistance, Skill, Status
from .item_names import category_from_native, public_item_name


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
        # Menus and modern UI popups share one native z-order. A description
        # popup can sit above a still-open examination menu.
        self.interfaces: list[dict] = []
        self.input_mode = 0
        self.more = False
        self.dead = False
        self.game_over = False
        self.end_reason = ""
        self.revision = 0
        self.last_x = self.last_y = 0
        self.text: dict[str, dict[int, str]] = {}
        self.skills: dict[str, Skill] = {}
        self.resistances: tuple[Resistance, ...] = ()
        self._resistance_context: tuple | None = None
        self.abilities: tuple[Ability, ...] = ()
        self.inspected_weapon: tuple[int, int, str] | None = None
        self.monster_descriptions: dict[int, tuple[int, dict]] = {}
        self.text_input: dict = {}

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
                self.monster_descriptions.clear()
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
            self.interfaces.append(self.ui[-1])
            if message.get("tag") == "resists":
                self._observe_resists(message)
            if message.get("type") == "game-over":
                self.game_over = True
                self.end_reason = plain(message.get("text", message.get("title", "")))
        elif kind == "ui-pop":
            if self.ui:
                popped = self.ui.pop()
                self.interfaces = [frame for frame in self.interfaces if frame is not popped]
        elif kind == "ui-state" and self.ui:
            merge(self.ui[-1], message)
            if self.ui[-1].get("tag") == "resists":
                self._observe_resists(self.ui[-1])
        elif kind == "ui-stack":
            self.ui.clear()
            self.menus.clear()
            self.interfaces.clear()
            self.text_input = {}
            for entry in message.get("items", []):
                self.apply(entry)
        elif kind == "menu":
            self.menus.append(deepcopy(message))
            self.interfaces.append(self.menus[-1])
            self._observe_equipment_menu(message)
            self._observe_abilities(message)
        elif kind in {"update_menu", "update_menu_items"} and self.menus:
            if "total_items" in message:
                # Native category switches resize first, then deliver rows.
                # Retaining the old tail invents entries on shorter pages.
                del self.menus[-1].setdefault("items", [])[message["total_items"]:]
            if "items" in message:
                menu = self.menus[-1]
                start = message.get("chunk_start", 0)
                items = menu.setdefault("items", [])
                while len(items) < start + len(message["items"]):
                    items.append({})
                items[start:start + len(message["items"])] = deepcopy(message["items"])
            merge(self.menus[-1], {k: v for k, v in message.items() if k != "items"})
            self._observe_equipment_menu(self.menus[-1])
            self._observe_abilities(self.menus[-1])
        elif kind == "close_menu":
            if self.menus:
                popped = self.menus.pop()
                self.interfaces = [frame for frame in self.interfaces if frame is not popped]
        elif kind == "close_all_menus":
            self.interfaces = [frame for frame in self.interfaces
                               if not any(frame is menu for menu in self.menus)]
            self.menus.clear()
        elif kind == "game_ended":
            self.game_over = True
            self.end_reason = message.get("reason", "game ended")
        elif kind == "init_input":
            self.text_input = deepcopy(message)
        elif kind == "close_input":
            self.text_input = {}
        elif kind == "txt":
            area = self.text.setdefault(message.get("id", ""), {})
            if message.get("clear"):
                area.clear()
            area.update({int(n): plain(line) for n, line in message.get("lines", {}).items()})
            if self.menus and self.menus[-1].get("tag") == "skills":
                self._observe_skills()

    def _observe_resists(self, frame: dict) -> None:
        text = plain(frame.get("text", ""))
        values = []
        pattern = r"\b(rFire|rCold|rNeg|rPois|rElec|rCorr|SInv|Will)\s+([+x.](?:[ \t]*[+x.]){0,4}|∞|inf)"
        for match in re.finditer(pattern, text):
            pips = match[2]
            # Immunity has no finite pip count: do not fabricate one.
            if pips not in {"∞", "inf"}:
                values.append(Resistance(match[1], pips.count("+") - pips.count("x")))
        self.resistances = tuple(values)
        self._resistance_context = self._defence_context()

    def _defence_context(self) -> tuple:
        """Public equipment, level and statuses that can change resistance pips."""
        slots = self.equipment_slots | {
            slot for slot in (self.player.get("weapon_index"),self.player.get("offhand_index"))
            if isinstance(slot,int)}
        equipment = tuple((slot,self.player.get("inv",{}).get(str(slot),{}).get("name",""))
                          for slot in sorted(slots))
        statuses = tuple(plain(status.get("light",status.get("text","")))
                         for status in self.player.get("status",[]))
        return self.player.get("xl",1),equipment,statuses

    def _observe_abilities(self, menu: dict) -> None:
        if menu.get("tag") != "ability":
            return
        abilities = []
        for entry in menu.get("items", []):
            text = plain(entry.get("text", "")).strip()
            keys = entry.get("hotkeys", [])
            if len(keys) != 1:
                continue
            key = chr(keys[0]) if isinstance(keys[0], int) else keys[0]
            text = re.sub(r"^[a-zA-Z]\s+[-+]\s+", "", text)
            name = re.split(r"\s{2,}", text, maxsplit=1)[0].strip()
            failure = re.search(r"(\d+)%\s*$", text)
            if not name:
                continue
            # ability.cc marks unusable entries COL_INAPPLICABLE (darkgrey).
            abilities.append(Ability(name, key, entry.get("colour") != 8,
                                     int(failure[1]) if failure else None))
        self.abilities = tuple(abilities)

    def skill_text(self) -> str:
        return "\n".join(self.text.get("menu", self.text.get("menu_txt", {})).get(n, "")
                         for n in sorted(self.text.get("menu", self.text.get("menu_txt", {}))))

    def _observe_skills(self) -> None:
        text = self.skill_text()
        training_view = bool(re.search(r"\bTrain\b", text))
        target_view = bool(re.search(r"\bTarget\b", text))
        # CRT rows have two columns. Never consume a newline after a blank
        # percentage: that would swallow the next row's leading delimiter.
        # A zero-width boundary also preserves the next column after blanks.
        pattern = r"(?<!\S)([a-zA-Z])[ \t]+([-+*])[ \t]+([A-Za-z][A-Za-z &]+?)[ \t]+(\d+(?:\.\d+)?)[ \t]*([0-9.]+%?|--)?"
        for match in re.finditer(pattern, text):
            key, sign, name, level, extra = match.groups()
            name = name.strip()
            old = self.skills.get(name)
            training = old.training if old else None
            target = old.target if old else None
            if training_view:
                training = int(extra[:-1]) if extra and extra.endswith("%") else 0
            if target_view:
                target = float(extra) if extra and extra != "--" else None
            self.skills[name] = Skill(name, float(level), training, target, key, sign != "-")

    def observe_weapon(self, slot: int, frame: dict) -> None:
        self.inspected_weapon = (slot, self.player.get("xl", 1), plain(frame.get("body", "")))

    def observe_monster(self, ident: int, frame: dict) -> None:
        self.monster_descriptions[ident] = (self.player.get("turn", 0), deepcopy(frame))

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
                if letter != match.group(1) or not name or public_item_name(name) != public_item_name(text):
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
                ident = mon.get("id", 0)
                inspected_turn, detail = self.monster_descriptions.get(ident, (-1, {}))
                spell_entries = [spell for book in detail.get("spellset", []) for spell in book.get("spells", [])]
                spells = tuple(plain(spell.get("title", "")) for spell in spell_entries)
                # describe-spells.cc exposes these coloured "(N)" labels to
                # the player. Do not inspect raw spell power or monster HD.
                spell_ranges = tuple(
                    (plain(spell.get("title", "")), int(match[1]))
                    for spell in spell_entries
                    if (match := re.fullmatch(r"\((\d+)\*?\)", plain(spell.get("range_string", "")).strip()))
                    and int(match[1]) > 0)
                description = plain(detail.get("body", "")).replace("SPELLSET_PLACEHOLDER", "").strip()
                if spell_entries:
                    description += "\nSpells:\n" + "\n".join(
                        plain(spell.get("title", "")) + ": " + plain(spell.get("effect", ""))
                        for spell in spell_entries)
                description = description or None
                status = plain(detail.get("status", ""))
                equipment = tuple(line.strip() for line in (description or "").splitlines()
                                  if any(word in line.lower() for word in ("wielding", "carrying", "wearing")))
                # describe.cc's public attack table names a wielded weapon
                # after "Hit:"/"Shoot:"; columns are separated by >=2 spaces.
                # Preserve that displayed column, never the raw monster item.
                equipment += tuple(match[1].strip() for match in re.finditer(
                    r"^[ \t]*(?:\d+x )?(?:Hit|Shoot):[ \t]+(.+?)[ \t]{2,}\d",
                    description or "", re.MULTILINE))
                speed = next((line.strip() for line in (description or "").splitlines()
                              if "speed" in line.lower() or "moves" in line.lower()), None)
                monsters.append(Monster(ident, plain(mon.get("name", "unknown")),
                                        x, y, mon.get("threat", 1), mon.get("att", 0),
                                        description, tuple(status.splitlines()) if inspected_turn == p.get("turn", 0) else (),
                                        equipment, speed, spells, spell_ranges))
        inventory = []
        for slot, item in p.get("inv", {}).items():
            if item.get("quantity", 0) <= 0 or not item.get("name"):
                continue
            letter = item.get("letter", int(slot) + ord("a"))
            letter = chr(letter) if isinstance(letter, int) else letter
            name = plain(item["name"])
            equipped = (int(slot) in {p.get("weapon_index"), p.get("offhand_index")}
                        or int(slot) in self.equipment_slots or "(worn)" in name)
            inventory.append(Item(int(slot), letter, name, item["quantity"], equipped,
                                  category_from_native(item.get("base_type"))))
        menu = deepcopy(self.text_input or (self.interfaces[-1] if self.interfaces else {}))
        prompt = ""
        if self.input_mode in {7, 8}:
            title = menu.get("title", "")
            title = title.get("text", "") if isinstance(title, dict) else title
            prompt = plain(menu.get("prompt") or title or (self.messages[-1] if self.messages else ""))
        weapon_text = ""
        if self.inspected_weapon and self.inspected_weapon[:2] == (p.get("weapon_index"), p.get("xl", 1)):
            weapon_text = self.inspected_weapon[2]
        delay = re.search(r"Current attack delay:\s*(\d+(?:\.\d+)?)", weapon_text)
        category = re.search(r"'([^']+)' category", weapon_text)
        statuses = tuple(Status(plain(s.get("light", "")), plain(s.get("text", "")),
                                plain(s.get("desc", "")), s.get("col")) for s in p.get("status", []))
        resistances = self.resistances if self._resistance_context == self._defence_context() else ()
        return Observation(
            name=p.get("name", ""), species=p.get("species", ""), god=p.get("god", ""),
            turn=p.get("turn", 0), time=p.get("time", 0), place=p.get("place", ""),
            depth=p.get("depth", 0), hp=p.get("hp", 0), hp_max=p.get("hp_max", 0),
            xl=p.get("xl", 1), position=(pos.get("x", 0), pos.get("y", 0)),
            statuses=tuple(plain(s.get("light", s.get("text", ""))) for s in p.get("status", [])),
            inventory=tuple(inventory), monsters=tuple(monsters), cells=tuple(cells),
            messages=tuple(self.messages), prompt=prompt, menu=menu,
            input_mode=5 if self.more else self.input_mode, dead=self.dead, game_over=self.game_over,
            ac=p.get("ac"), ev=p.get("ev"), sh=p.get("sh"), piety_rank=p.get("piety_rank"),
            poison_survival=p.get("poison_survival"), status_details=statuses,
            resistances=resistances,
            will=next((r.level for r in resistances if r.name == "Will"), None),
            abilities=self.abilities, skills=tuple(self.skills.values()),
            attack_delay=float(delay[1]) if delay else None,
            weapon_skill=category[1] if category else None,
        )
