"""MiBe equipment decisions from identified, player-visible item names.

Numbers are DCSS 0.34.1 item-prop.cc Weapon_prop/Armour_prop; delay and
training thresholds are fight.cc weapon_min_delay[_skill] and player-act.cc
attack_delay_with. Scores are conservative policy utilities, not predicted AC,
EV, SH or exact damage against an unknown monster.
Native terse labels and verbose menu labels share one public item identity.
"""
import re
from dataclasses import dataclass

from ..item_names import public_item_name
from ..models import Action, Item, Observation


@dataclass(frozen=True)
class Axe:
    damage: int
    accuracy: int
    delay: int
    minimum: int
    two_handed: bool = False

    @property
    def target(self) -> int:
        return 2 * (self.delay - self.minimum)


AXES = {
    "executioner's axe": Axe(18, -6, 19, 7, True),
    "battleaxe": Axe(15, -4, 17, 7, True),
    "broad axe": Axe(13, -2, 16, 7),
    "war axe": Axe(11, 0, 15, 7),
    "hand axe": Axe(7, 3, 13, 6),
}
# (equipment slot, base AC); shield numbers below are base shield rating, not AC.
ARMOUR = {
    "crystal plate armour": ("body", 14), "plate armour": ("body", 10),
    "chain mail": ("body", 8), "scale mail": ("body", 6),
    "ring mail": ("body", 5), "troll leather armour": ("body", 3),
    "leather armour": ("body", 3), "animal skin": ("body", 2),
    "robe": ("body", 2), "cloak": ("cloak", 1), "scarf": ("cloak", 0),
    "gloves": ("gloves", 1), "boots": ("boots", 1), "hat": ("head", 0),
    "buckler": ("shield", 3), "kite shield": ("shield", 8),
    "tower shield": ("shield", 13),
}
# Only egos whose visible descriptions we understand. Values are priorities,
# deliberately not invented changes to the character's displayed defenses.
ARMOUR_EGOS = {
    "": 0, "fire resistance": 2, "cold resistance": 2, "poison resistance": 2,
    "positive energy": 1, "willpower": 2, "resistance": 4,
    "corrosion resistance": 2, "see invisible": 1, "reflection": 2,
    "protection": 2, "repulsion": 3, "strength": 1, "dexterity": 1,
    "stealth": 0, "running": 3, "regeneration": 3,
}
JEWELLERY = {
    "ring of protection from fire": 3, "ring of protection from cold": 3,
    "ring of fire resistance": 3, "ring of cold resistance": 3,
    "ring of poison resistance": 3, "ring of resist corrosion": 3,
    "ring of willpower": 3, "ring of see invisible": 2,
    "ring of positive energy": 2, "ring of slaying": 0,
    "ring of protection": 0, "ring of evasion": 0, "ring of strength": 0,
    "ring of dexterity": 0, "amulet of regeneration": 4,
    "amulet of reflection": 3, "amulet of guardian spirit": 2,
}
HAZARDOUS_EQUIPMENT = (
    "distortion", "chaos", "draining", "pain", "harm", "fragile", "angry",
    "*rage", "*corrode", "*slow", "ponderous", "contam", "corrode",
    "teleportation", "inaccuracy", "rampage",
)
BRANDS = {"", "flaming", "freezing", "venom", "electrocution", "vampirism",
          "vampiric", "speed", "heavy", "protection", "antimagic", "holy wrath"}


def _name(name: str) -> str:
    return public_item_name(name)


def axe_type(name: str) -> str | None:
    name = _name(name)
    return next((base for base in AXES if re.search(r"\b" + re.escape(base) + r"\b", name)), None)


def _base(name: str) -> str | None:
    return next((base for base in ARMOUR if re.search(r"\b" + re.escape(base) + r"\b", name)), None)


def equipment_kind(name: str) -> str | None:
    if axe_type(name):
        return "wield"
    if _base(_name(name)):
        return "wear"
    if "ring of " in name.lower() or "amulet of " in name.lower():
        return "put_on"
    return None


def equipment_slot(name: str) -> str | None:
    kind = equipment_kind(name)
    if kind == "wield":
        return "weapon"
    if kind == "put_on":
        return "amulet" if "amulet" in name.lower() else "ring"
    base = _base(_name(name))
    return ARMOUR[base][0] if base else None


def _safe(name: str) -> bool:
    # Artefacts may conceal drawbacks even when their base/enchantment is known.
    return not any(mark in name for mark in ('"', "{", "}", "artefact", "artifact", "?",
                                             *HAZARDOUS_EQUIPMENT))


def _identified(name: str, base: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"([+-]\d+) (?:pair of )?" + re.escape(base) + r"(?: of ([a-z ]+))?", name)
    if match:
        return int(match[1]), match[2] or ""
    if base == "scarf" and name.startswith("scarf of "):
        return 0, name.removeprefix("scarf of ")
    return None


def is_flaming_axe(name: str) -> bool:
    """Only a publicly identified axe brand can cauterise hydra heads."""
    base = axe_type(name)
    details = _identified(_name(name),base) if base else None
    return details is not None and details[1] == "flaming"


def axe_delay(name: str, axes_skill: float | None) -> float | None:
    """Expected unmodified delay in aut; unknown skill uses an untrained bound."""
    base = axe_type(name)
    details = _identified(_name(name), base) if base else None
    if not base or not details or details[1] not in BRANDS:
        return None
    axe = AXES[base]
    delay = axe.delay - min(max(0, axes_skill or 0), axe.target) / 2
    if details[1] == "speed":
        delay *= 2 / 3
    elif details[1] == "heavy":
        delay *= 1.5
    return delay


def equipment_score(name: str, *, axes_skill: float | None = None,
                    attack_delay: float | None = None, hydra: bool = False,
                    shields_skill: float | None = None) -> float | None:
    """Rank verified equipment, within its slot only.

    attack_delay is the displayed attack delay in turns, ONLY for the weapon
    whose delay was actually observed. Weapon score approximates damage/time,
    with an accuracy discount and a slow-swing survival discount. Unknown skill
    is evaluated at the untrained bound, never represented as an observed level.
    """
    name = _name(name)
    if not _safe(name):
        return None
    kind = equipment_kind(name)
    if kind == "wield":
        base = axe_type(name)
        details = _identified(name, base)
        if not details or details[1] not in BRANDS:
            return None
        plus, brand = details
        if hydra and brand != "flaming":
            return None  # Chopping living hydras without cauterisation grows heads.
        axe = AXES[base]
        delay = attack_delay * 10 if attack_delay is not None and attack_delay > 0 else axe_delay(name, axes_skill)
        damage = max(0, (axe.damage * (1.8 if brand == "heavy" else 1) + plus) / 2)
        if brand in {"flaming", "freezing"}:
            damage *= 1.2  # Discount resistance-sensitive extra damage.
        elif brand == "electrocution":
            damage += 3.5  # (8 + mean(random2(13))) / 4, before resistance.
        elif brand == "venom":
            damage += 1  # Policy value, not an assumed poison-susceptible target.
        accuracy = max(0.7, 1 + axe.accuracy * 0.035)
        slow_penalty = 1 + max(0, delay - 10) * 0.025
        return damage * accuracy / delay / slow_penalty
    if kind == "wear":
        base = _base(name)
        details = _identified(name, base)
        if not details or details[1] not in ARMOUR_EGOS:
            return None
        plus, ego = details
        slot, rating = ARMOUR[base]
        if slot == "shield":
            # Large shields carry larger skill-dependent attack/EV penalties.
            # These are utility discounts, not claimed observed SH or delay.
            penalty = {"buckler": 0, "kite shield": 4, "tower shield": 10}[base]
            rating /= 1 + max(0, penalty - (shields_skill or 0)) / 3
        return rating + plus + ARMOUR_EGOS[ego]
    if kind == "put_on":
        match = re.fullmatch(r"(?:([+-]\d+) )?(.+)", name)
        plus, base = match.groups()
        if base not in JEWELLERY:
            return None
        if base in {"ring of protection", "ring of evasion", "ring of slaying",
                    "ring of strength", "ring of dexterity"}:
            return float(plus) if plus is not None else None
        return float(JEWELLERY[base]) if plus is None else None
    return None


def equipment_upgrade(name: str, inventory: tuple[Item, ...], *,
                      axes_skill: float | None = None, attack_delay: float | None = None,
                      hostiles: tuple = (), hydra: bool = False,
                      shields_skill: float | None = None) -> bool:
    """Safe pickup/equip comparison; refuse all changes with visible hostiles."""
    if hostiles:
        return False
    slot = equipment_slot(name)
    options = dict(axes_skill=axes_skill, hydra=hydra, shields_skill=shields_skill)
    score = equipment_score(name, **options)
    if score is None or score <= 0:
        return False
    equipped = [item for item in inventory if item.equipped]
    current = [item for item in equipped if equipment_slot(item.name) == slot]
    if slot == "weapon":
        if any("distortion" in _name(item.name) for item in current):
            return False
        if AXES[axe_type(name)].two_handed and any(equipment_slot(item.name) == "shield" for item in equipped):
            return False
    elif slot == "shield":
        weapons = [item for item in equipped if equipment_slot(item.name) == "weapon"]
        if not weapons or any(AXES[axe_type(item.name)].two_handed for item in weapons):
            return False
    elif slot == "ring":
        # Fill empty slots only: an automatic ring swap opens another UI choice
        # and resistance values are not universally interchangeable.
        return len(current) < 2
    elif slot == "amulet":
        # The adapter supports filling an empty jewellery slot, not replacement.
        return not current
    scores = []
    for item in current:
        value = equipment_score(item.name, **options,
                                attack_delay=attack_delay if slot == "weapon" else None)
        if value is None:
            # A known ordinary nonflaming axe is not a suitable hydra weapon.
            if hydra and slot == "weapon" and equipment_score(item.name, axes_skill=axes_skill) is not None:
                value = 0
            else:
                return False
        scores.append(value)
    if slot == "weapon" and current and attack_delay and axes_skill is not None:
        # Retain the observed common shield/status delay penalty for candidates.
        baseline = axe_delay(current[0].name, axes_skill)
        candidate = axe_delay(name, axes_skill)
        if baseline and candidate:
            delay = candidate + max(0, attack_delay * 10 - baseline)
            score = equipment_score(name, **options, attack_delay=delay / 10)
    return score > max(scores, default=0)


def equipment_action(obs: Observation) -> Action | None:
    """Select one clear-ground upgrade; success is always observed, never assumed."""
    if obs.hostiles or obs.menu or obs.prompt or any(cell.cloud and (cell.x, cell.y) == obs.position for cell in obs.cells):
        return None
    skills = {skill.name: skill.level for skill in obs.skills}
    candidates = []
    for item in obs.inventory:
        if item.equipped:
            continue
        options = dict(axes_skill=skills.get("Axes"), shields_skill=skills.get("Shields"))
        if equipment_upgrade(item.name, obs.inventory, **options, attack_delay=obs.attack_delay):
            candidates.append((equipment_score(item.name, **options), item))
    if not candidates:
        return None
    item = max(candidates, key=lambda pair: pair[0])[1]
    return Action(equipment_kind(item.name), item.letter, "Equip a verified safe upgrade on clear ground", "equipment")
