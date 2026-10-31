"""Clear-ground skill training and identification for the Minotaur Berserker.

Weapon thresholds come from pinned 0.34.1 fight.cc weapon_min_delay_skill.
Other targets are explicit policy milestones, not claims of mechanical caps.
Trog has no Invocations scaling, and this policy never trains magic.
Unknown-scroll probes are policy, not risk-free: pinned item-use.cc permits
noise (25), delayed teleport, torment, poison clouds, inner flame and reduced
willpower (30–49 turns). A stair is only a known clear-ground anchor; this
policy never promises safe traversal.
"""
import re

from ..models import Action, Item, Observation
from ..item_names import category_from_name, public_item_name, scroll_named
from ..state import plain
from ..terrain import DOWNSTAIRS, UPSTAIRS
from .mibe_equipment import AXES, axe_type, equipment_kind, equipment_score, equipment_slot, equipment_upgrade
from .mibe_mechanics import MAX_WEAPON_ENCHANT


def _clear(obs: Observation) -> bool:
    return (not obs.hostiles and not obs.prompt and not obs.menu
            and not any(cell.cloud and (cell.x, cell.y) == obs.position for cell in obs.cells))


def training_plan(obs: Observation) -> tuple[tuple[str, float], ...]:
    """Relevant milestones; only observed skills may be selected by the planner."""
    equipped = [item for item in obs.inventory if item.equipped]
    weapon = next((item for item in equipped if equipment_slot(item.name) == "weapon"), None)
    axe = axe_type(weapon.name) if weapon else None
    axe_target = AXES[axe].target if axe else None
    body = next((item.name.lower() for item in equipped if equipment_slot(item.name) == "body"), "")
    armour = bool(body and not any(light in body for light in ("robe", "animal skin")))
    shield = any(equipment_slot(item.name) == "shield" for item in equipped)
    evocations = any("wand of " in item.name.lower() and not re.search(r"\(0\s*(?:charges?)?\)", item.name.lower())
                     for item in obs.inventory)
    throwing = any(any(projectile in item.name.lower() for projectile in
                       ("boomerang", "javelin", "curare", "poisoned dart")) for item in obs.inventory)
    plan = []
    if axe_target is not None:
        plan.append(("Axes", min(10, axe_target)))
    plan.append(("Fighting", 6))
    if armour:
        plan.append(("Armour", 6))
    if shield:
        plan.append(("Shields", 4))
    plan.append(("Dodging", 4))
    if axe_target is not None:
        plan.append(("Axes", axe_target))
    plan.append(("Fighting", 12))
    if armour:
        plan.append(("Armour", 12))
    plan.append(("Dodging", 10))
    if shield:
        plan.append(("Shields", 10))
    if evocations:
        plan.append(("Evocations", 6))
    if throwing:
        plan.append(("Throwing", 6))
    for target in (18, 24, 27):
        plan.append(("Fighting", target))
        if armour:
            plan.append(("Armour", target))
        plan.append(("Dodging", target))
        if shield:
            plan.append(("Shields", target))
    return tuple(plan)


def unknown_consumable(name: str) -> str | None:
    """Classify only the displayed appearance, never hidden item fields."""
    name = name.casefold()
    if re.search(r"\bscrolls? labelled\b", name) and not re.search(r"\bscrolls? of\b", name):
        return "scroll"
    if re.search(r"\bpotions?\b", name) and not re.search(r"\bpotions? of\b", name):
        return "potion"
    return None


def _visible_item_choices(menu: dict):
    """Current enabled menu rows with one unambiguous ASCII selection key."""
    entries = menu.get("items", [])
    keys = []
    for entry in entries:
        row_keys = []
        for key in entry.get("hotkeys", []):
            if isinstance(key, int) and 0 <= key <= 127:
                key = chr(key)
            if isinstance(key, str) and len(key) == 1 and key.isascii() and key.isalpha():
                row_keys.append(key)
        keys.append(set(row_keys))
    for entry, row_keys in zip(entries, keys):
        if entry.get("q") == 0 or entry.get("level") == 1 or entry.get("colour") == 8:
            continue
        unique = [key for key in row_keys if sum(key in others for others in keys) == 1]
        if len(unique) != 1:
            continue
        label = re.sub(r"^[a-zA-Z]\s+[-+#]\s+", "", plain(entry.get("text", "")).strip())
        quantity = re.match(r"(\d+)\s+", label)
        yield unique[0],label,int(quantity[1]) if quantity else 1


def supply_name(name: str) -> bool:
    """Useful carried supplies, including unidentified public appearances."""
    name = public_item_name(plain(name))
    return (category_from_name(name) in {"scroll", "potion", "wand"}
            or bool(re.search(r"\bgold pieces?\b", name)))


def pickup_menu_action(obs: Observation, *, axes_skill: float | None = None,
                       shields_skill: float | None = None, hydra: bool = False) -> Action | None:
    """Select supplies plus the existing best verified equipment choice.

    Native pickup rows are multi-select toggles, not item-use letters. Multiple
    choices therefore need one bounded pickup_select transaction and one Enter.
    """
    if obs.menu.get("tag") != "pickup":
        return None
    title = obs.menu.get("title", "")
    title = plain(title.get("text", "") if isinstance(title, dict) else title).strip()
    if title and not (title.startswith("Pick up what?")
                      or title == "Select pick up quantity by entering a number, then select the item"):
        # Native square examination reuses the pickup tag, but is not pickup.
        return Action("cancel", reason="Leave a description menu rather than select items as pickup",
                      objective="supplies")
    unsafe_status = any(status.casefold() == "berserk"
                        or any(mark in status.casefold() for mark in
                               ("pois", "slow", "exhaust", "conf", "paral", "petr", "sleep"))
                        for status in obs.statuses)
    if (obs.hostiles or obs.hp_ratio < .98 or unsafe_status
            or any(cell.cloud and (cell.x, cell.y) == obs.position for cell in obs.cells)):
        return Action("cancel", reason="Leave supply pile while unsafe", objective="supplies")
    if any(re.match(r"^[a-zA-Z]\s+[+#]\s+", plain(entry.get("text", "")).strip())
           for entry in obs.menu.get("items", [])):
        return Action("cancel", reason="Leave an already-selected pickup menu rather than toggle unknown choices",
                      objective="supplies")
    supplies = []
    equipment = []
    options = dict(axes_skill=axes_skill, shields_skill=shields_skill, hydra=hydra)
    for key, name, _ in _visible_item_choices(obs.menu):
        if supply_name(name):
            supplies.append((key, name))
        elif equipment_upgrade(name, obs.inventory, **options,
                               attack_delay=obs.attack_delay, hostiles=obs.hostiles):
            equipment.append((equipment_score(name, **options), key, name))
    if equipment:
        _, key, name = max(equipment)
        supplies.append((key, name))
    if not supplies:
        return Action("cancel", reason="No uniquely selectable supplies or verified equipment upgrade in this pile",
                      objective="supplies")
    keys = "".join(key for key, _ in supplies)
    return Action("menu" if len(keys) == 1 else "pickup_select", keys,
                  "Collect visible useful pile items: " + "; ".join(name for _, name in supplies),
                  "supplies")


def development_menu_action(obs: Observation) -> Action | None:
    """Complete a verified Identify/enchantment scroll using visible items."""
    title = obs.menu.get("title", "")
    title = plain(title.get("text", "") if isinstance(title, dict) else title)
    if obs.menu.get("tag") != "use_item":
        return None
    if title == "Identify which item? (\\ to view known items)":
        choices = []
        for key,label,count in _visible_item_choices(obs.menu):
            kind = unknown_consumable(label)
            if not kind and re.search(r"\b(?:potions?|scrolls?)\b",label,re.I):
                continue
            choices.append((bool(kind),count,kind == "potion",key,label))
        if choices:
            _,_,_,key,name = max(choices)
            return Action("menu",key,f"Identify visible current-menu item: {name}","identification")
        return Action("cancel",reason="No unique useful item in the native Identify menu",objective="identification")
    if title not in {"Enchant which weapon?","Enchant which item?"}:
        return None
    kind = "wield" if title == "Enchant which weapon?" else "wear"
    choices = []
    for key,label,_ in _visible_item_choices(obs.menu):
        if equipment_kind(label) != kind:
            continue
        if any(item.equipped and item.letter == key and public_item_name(item.name) == public_item_name(label)
               for item in obs.inventory):
            choices.append((equipment_score(label),key,label))
    if choices:
        _,key,name = max(choices)
        return Action("menu",key,f"Enchant currently equipped useful item: {name}","equipment")
    return Action("cancel",reason="No unique eligible equipped item in the native enchantment menu",objective="equipment")


def identification_target(inventory: tuple[Item, ...]) -> tuple[Item, Item] | None:
    """Use known identify first; stacks get priority from public appearance names.

    No subtype, hidden enchantment, or undocumented wire flag is used.
    """
    scroll = next((item for item in inventory
                   if (item.category or category_from_name(item.name)) == "scroll"
                   and scroll_named(item.name, "identify")), None)
    if scroll is None:
        return None
    unknown = []
    for item in inventory:
        kind = unknown_consumable(item.name)
        if kind and (item.category or category_from_name(item.name)) != kind:
            continue
        potion = kind == "potion"
        if item.letter != scroll.letter and kind:
            unknown.append((item.quantity, bool(potion), item))
    if not unknown:
        return None
    target = max(unknown, key=lambda option: option[:2])[2]
    return scroll, target


class DevelopmentPlanner:
    """Bounded public UI actions; no optimistic skill or identification state."""

    QUIET_TURNS = 12
    MAX_UNKNOWN_READS = 4

    def __init__(self) -> None:
        self._inspection_key: tuple | None = None
        self._training_attempt: tuple | None = None
        self._identification_attempt: tuple | None = None
        self._enhancement_attempt: tuple | None = None
        self._last_skill_inspection = -200
        self._last_resistance_statuses: tuple[str,...] | None = None
        self._quiet_location = ""
        self._quiet_since: int | None = None
        self._last_unknown_read: int | None = None
        self._read_appearances: set[str] = set()
        self._singleton_read = False

    def observe(self, obs: Observation) -> None:
        """Called even when tactics/recovery consume the current decision."""
        if obs.location != self._quiet_location or self._quiet_since is None:
            self._quiet_location = obs.location
            self._quiet_since = obs.turn
        if obs.hostiles:
            self._quiet_since = obs.turn

    def _unknown_scroll(self, obs: Observation) -> Action | None:
        if (obs.hp_max <= 0 or obs.hp != obs.hp_max or obs.monsters
                or obs.statuses or obs.status_details
                or (obs.poison_survival is not None and obs.poison_survival < obs.hp)
                or any(cell.cloud for cell in obs.cells)
                or self._quiet_since is None or obs.turn - self._quiet_since < self.QUIET_TURNS
                or len(self._read_appearances) >= self.MAX_UNKNOWN_READS
                or (self._last_unknown_read is not None
                    and obs.turn - self._last_unknown_read < self.QUIET_TURNS)):
            return None
        here = next((cell for cell in obs.cells if (cell.x, cell.y) == obs.position), None)
        if here is None or not here.visible or here.feature not in DOWNSTAIRS | UPSTAIRS:
            return None
        unknown = [(item, re.search(r"\bscrolls? labelled\s+(.+)", item.name.casefold())[1])
                   for item in obs.inventory if unknown_consumable(item.name) == "scroll"
                   and (item.category or category_from_name(item.name)) == "scroll"]
        appearances = {appearance for _, appearance in unknown}
        candidates = [(item, appearance) for item, appearance in unknown
                      if appearance not in self._read_appearances
                      and sum(other.letter == item.letter
                              and (other.category or category_from_name(other.name)) == "scroll"
                              for other in obs.inventory) == 1
                      and item.quantity >= 2]
        basis = "spare unknown-scroll stack"
        if not candidates:
            known_identify = any((item.category or category_from_name(item.name)) == "scroll"
                                 and scroll_named(item.name, "identify") for item in obs.inventory)
            if known_identify or self._singleton_read or len(appearances) < 3:
                return None
            candidates = [(item, appearance) for item, appearance in unknown
                          if appearance not in self._read_appearances
                          and sum(other.letter == item.letter
                                  and (other.category or category_from_name(other.name)) == "scroll"
                                  for other in obs.inventory) == 1]
            basis = f"one singleton bootstrap among {len(appearances)} unknown scroll appearances"
        if not candidates:
            return None
        item, appearance = max(candidates, key=lambda candidate: (candidate[0].quantity, candidate[0].letter))
        self._read_appearances.add(appearance)
        self._last_unknown_read = obs.turn
        self._quiet_since = obs.turn  # Noise/teleport/cloud effects need a fresh quiet interval.
        self._singleton_read |= item.quantity == 1
        return Action("read", item.letter,
                      f"Unknown-scroll identification probe {len(self._read_appearances)}/{self.MAX_UNKNOWN_READS}: "
                      f"{basis} ({item.name}, quantity {item.quantity}); full HP, no statuses or creatures, "
                      f"{self.QUIET_TURNS} quiet turns on a known ordinary stair tile; "
                      "read once and reassess noise, teleport, torment or cloud effects",
                      "identification")

    def decide(self, obs: Observation) -> Action | None:
        self.observe(obs)
        refresh_resists = (self._last_resistance_statuses is not None and not obs.resistances
                           and self._last_resistance_statuses != obs.statuses)
        if obs.resistances or refresh_resists:
            self._last_resistance_statuses = obs.statuses
        if not _clear(obs):
            return None
        equipment = tuple((item.letter, item.name) for item in obs.inventory if item.equipped)
        inspection_key = (obs.xl, obs.piety_rank, equipment)
        # Skills change through combat XP. Refresh periodically even when the
        # last snapshot exists, but never spin on unavailable public UI fields.
        inspect_due = (self._inspection_key != inspection_key
                       or obs.turn - self._last_skill_inspection >= 200 or refresh_resists)
        if inspect_due:
            self._inspection_key = inspection_key
            self._last_skill_inspection = obs.turn
            return Action("inspect", reason="Observe current skills and equipment before development", objective="development")
        skills = {skill.name: skill for skill in obs.skills}
        for name, target in training_plan(obs):
            skill = skills.get(name)
            if skill is None or skill.level >= target:
                continue
            # train_skill selects manual/exclusive training and a native target;
            # the next observation, not this request, proves it took effect.
            active_others = any(other.training not in (None, 0) for other in obs.skills if other.name != name)
            if skill.training not in (None, 0) and skill.target == target and not active_others:
                break
            snapshot = tuple((s.name, s.level, s.training, s.target) for s in obs.skills)
            attempt = (equipment, snapshot, name, target)
            if attempt != self._training_attempt:
                self._training_attempt = attempt
                return Action("train_skill", f"{name}:{target:g}",
                              f"Train {name} to the next relevant target ({target:g})", "development")
            break
        # Improve equipment before spending enchantments on a weapon we would
        # immediately replace. The caller's existing equipment policy applies it.
        axes = skills.get("Axes")
        if any(not item.equipped and equipment_upgrade(
                item.name,obs.inventory,axes_skill=axes.level if axes else None,attack_delay=obs.attack_delay)
               for item in obs.inventory):
            return None
        selected = identification_target(obs.inventory)
        if selected:
            scroll, target = selected
            inventory = tuple((item.letter, item.name, item.quantity) for item in obs.inventory)
            attempt = (inventory, scroll.letter, target.letter)
            if attempt != self._identification_attempt:
                self._identification_attempt = attempt
                return Action("identify", f"{scroll.letter}:{target.letter}",
                              "Identify an unknown consumable safely rather than use it blindly", "identification")
            return None
        scroll = next((item for item in obs.inventory
                       if re.search(r"\bscrolls? of enchant weapon\b",item.name,re.I)),None)
        weapon = next((item for item in obs.inventory if item.equipped and equipment_kind(item.name) == "wield"
                       and "{" not in item.name and (plus := re.match(r"^([+-]\d+)\b",item.name))
                       and int(plus[1]) < MAX_WEAPON_ENCHANT),None)
        if scroll and weapon:
            inventory = tuple((item.letter,item.name,item.quantity) for item in obs.inventory)
            attempt = inventory,scroll.letter,weapon.letter
            if attempt != self._enhancement_attempt:
                self._enhancement_attempt = attempt
                return Action("read",scroll.letter,"Improve the currently equipped axe on clear ground before the next fight","equipment")
        return self._unknown_scroll(obs)
