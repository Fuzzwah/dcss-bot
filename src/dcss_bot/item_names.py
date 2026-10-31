"""Visible item classes and label normalization for DCSS 0.34.1 / save major 34.

Facts: object-class-type.h and item-name.cc:337–655,1417–1442,1563–1582,
1639–1652. Inventory packets use terse labels; menus use verbose labels.
Only player-visible class/name information is used. Wire sub_type, plus and
identification flags can reveal hidden information and must not drive policy.
"""
import re


_CATEGORIES = (
    "weapon", "missile", "armour", "wand", "food", "scroll", "jewellery",
    "potion", "book", "staff", "orb", "misc", "corpse", "gold", "rod",
    "rune", "talisman", "gem", "gizmo", "bauble",
)
_WEAPON_BRANDS = {
    "flame": "flaming", "freeze": "freezing", "holy": "holy wrath",
    "elec": "electrocution", "venom": "venom", "protect": "protection",
    "drain": "draining", "speed": "speed", "heavy": "heavy",
    "vamp": "vampirism", "pain": "pain", "antimagic": "antimagic",
    "distort": "distortion", "chaos": "chaos", "confuse": "confusion",
    "penet": "penetration", "reap": "reaping", "spect": "spectralising",
    "rebuke": "rebuke", "valour": "valour", "entangle": "entangling",
    "sunder": "sundering", "concuss": "concussion", "devious": "devious",
    "acid": "acid", "weak": "weakness", "vuln": "vulnerability",
    "foul flame": "foul flame",
}
_ARMOUR_EGOS = {
    "rf+": "fire resistance", "rc+": "cold resistance", "rpois": "poison resistance",
    "sinv": "see invisible", "+inv": "invisibility", "str+3": "strength",
    "dex+3": "dexterity", "int+3": "intelligence", "ponderous": "ponderousness",
    "fly": "flying", "will+": "willpower", "ac+3": "protection", "stlth+": "stealth",
    "rc+ rf+": "resistance", "rn+": "positive energy", "archmagi": "the archmagi",
    "rcorr": "corrosion resistance", "reflect": "reflection", "spirit": "spirit shield",
    "hurl": "hurling", "repulsion": "repulsion", "harm": "harm", "shadows": "shadows",
    "rampage": "rampaging", "infuse": "infusion", "light": "light", "*rage": "wrath",
    "mayhem": "mayhem", "guile": "guile", "energy": "energy", "snipe": "sniping",
    "ice": "ice", "fire": "fire", "air": "air", "earth": "earth", "archery": "archery",
    "command": "command", "death": "death", "resonance": "resonance", "parrying": "parrying",
    "glass": "glass", "pyromania": "pyromania", "stardust": "stardust", "mesmerism": "mesmerism",
}
_MISSILE_BRANDS = {"poison": "poisoned", "curare": "curare-tipped", "datura": "datura-tipped",
                   "atropa": "atropa-tipped", "silver": "silver"}
_ADJECTIVE_BRANDS = {"vampiric": "vampirism", "antimagic": "antimagic", "heavy": "heavy",
                     "spectral": "spectralising", "devious": "devious"}


def category_from_native(value: int | None) -> str | None:
    return _CATEGORIES[value] if type(value) is int and 0 <= value < len(_CATEGORIES) else None


def public_item_name(name: str) -> str:
    """Canonical plain-text identity, retaining known brands and enchantments.

    Callers remove HTML first. Stack quantities and worn/menu annotations are
    not identity; unknown artefact properties and wand charge text are retained.
    """
    name = re.sub(r"^[a-zA-Z]\s+[-+#]\s+", "", name.strip()).casefold()
    name = re.sub(r"^(?:\d+|an?|the)\s+", "", name)
    name = re.sub(r"\s+\((?:weapon(?: in hand)?|worn|melded|quivered|in hand|offhand|(?:on )?(?:left|right) hand|around neck)\)", "", name)
    suffix = re.fullmatch(r"(.+?)\s+\(([^()]+)\)", name)
    if suffix:
        base, brand = suffix.groups()
        missile = re.search(r"\b(?:darts?|javelins?|boomerangs?|stones?|throwing nets?|large rocks?)$", base)
        if missile and brand in _MISSILE_BRANDS:
            name = f"{_MISSILE_BRANDS[brand]} {base}"
        elif missile and brand in {"chaos", "disperse", "disjunct"}:
            name = f"{base} of { {'disperse':'dispersal','disjunct':'disjunction'}.get(brand,brand)}"
        elif brand in _WEAPON_BRANDS:
            name = f"{base} of {_WEAPON_BRANDS[brand]}"
    ego = re.fullmatch(r"(.+?)\s+\{([^{}]+)\}", name)
    if ego and ego[2] in _ARMOUR_EGOS:
        name = f"{ego[1]} of {_ARMOUR_EGOS[ego[2]]}"
    adjective = re.fullmatch(r"([+-]\d+\s+)?(vampiric|antimagic|heavy|spectral|devious)\s+(.+)", name)
    if adjective:
        name = f"{adjective[1] or ''}{adjective[3]} of {_ADJECTIVE_BRANDS[adjective[2]]}"
    name = re.sub(r"\b(potion|scroll|dart|stone|javelin|boomerang|net|rock)s\b", r"\1", name)
    return " ".join(name.split())


def category_from_name(name: str) -> str | None:
    """Infer the visible class from a rendered label when no class was observed."""
    name = public_item_name(name)
    for category, pattern in (
        ("potion", r"\bpotion\b"), ("scroll", r"\bscroll\b"), ("wand", r"\bwand\b"),
        ("jewellery", r"\b(?:ring(?!\s+mail)|amulet)\b"),
        ("armour", r"\b(?:armour|mail|scales|robe|skin|leather|cloak|gloves|boots|helmet|hat|buckler|shield|scarf)\b"),
        ("missile", r"\b(?:dart|stone|javelin|boomerang|throwing net|large rock)\b"),
        ("staff", r"\bstaff\b"),
        ("weapon", r"^[+-]\d+\b|\b(?:axe|battleaxe|dagger|sword|falchion|mace|club|flail|spear|trident|whip|bow|sling|arbalest)\b"),
    ):
        if re.search(pattern,name):
            return category
    return None


def scroll_named(name: str, subtype: str) -> bool:
    return bool(re.search(r"\bscrolls? of "+re.escape(subtype)+r"\b",name,re.I))
