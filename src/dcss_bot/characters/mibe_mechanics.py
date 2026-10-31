"""DCSS 0.34.1 encounter knowledge, never instance/save-file information.

Facts: pinned source dat/mons/*.yaml; abilities in dat/descript/ability.txt;
opportunity attacks in mon-act.cc:3641; hydra heads in melee-attack.cc:3152.
Numbers below are base attacks or conservative policy bounds, not hidden rolls,
actual weapon enchantments, monster HP, or a claim of exact damage prediction.
"""
from dataclasses import dataclass, replace
import re

from ..models import Action, DIRECTIONS, Monster, Observation


VERSION = "0.34.1"
OPPORTUNITY_CHANCE = 1 / 3
MAX_WEAPON_ENCHANT = 9  # defines.h:162 in the pinned build
# main.cc:1728-1734; files.cc:2314-2329. Departure and arrival are
# separate world reactions; only start-adjacent hostiles are tagged to follow.
STAIR_DEPARTURE_ACTIONS = 1.
STAIR_ARRIVAL_MULTIPLIER = 1.5
# player-act.cc:699-700 applies Berserk's temporary +50% current/max HP.
BERSERK_HP_MULTIPLIER = 1.5
# spl-summoning.cc:768-778; only public names, not inferred summon provenance.
BROTHERS_ALLIES = frozenset({"black bear", "polar bear", "ogre", "two-headed ogre", "troll",
                            "deep troll", "iron troll", "cyclops", "stone giant"})


# melee-attack.cc:2744,2818-3066 in the pinned build. These are player
# melee verbs, not incoming "Something hits you" or a wish to attack.
_UNSEEN_MELEE = re.compile(
    r"\bYou (?:(?:barely|closely|completely) )?"
    r"(?:miss|hit|clumsily bash|puncture|impale|spit|skewer|stick|perforate|"
    r"slash|slice|dice|fracture|carve|shave|open|cut|chop|thump|sock|bludgeon|"
    r"shatter|splatter|crush|beat|hammer|pound|flatten|flog|flagellate|thrash|"
    r"punch|pummel|pulverise|squash) something\b")
# mon-death.cc:310-311 hides an unseen victim's identity on death.
_MELEE_END = re.compile(
    r"\bYou (?:kill|slay|destroy)\b|You feel a bit more experienced\.|"
    r"You swing at nothing\.|You attack empty space\.|"
    r"You feel something solid in that direction\.")


def _unseen_bump_messages(before: Observation, after: Observation, action: Action,
                          target: tuple[int,int]) -> tuple[str,...] | None:
    if (action.kind not in {"move", "attack"} or action.objective != "defend unseen"
            or action.value not in DIRECTIONS
            or before.location != after.location or after.turn != before.turn + 1
            or before.position != after.position):
        return None
    dx, dy = DIRECTIONS[action.value]
    if target != (before.position[0] + dx, before.position[1] + dy):
        return None
    cell = next((cell for cell in before.cells if (cell.x, cell.y) == target), None)
    if cell is None or not cell.visible or not cell.walkable or cell.cloud:
        return None
    # Do not use a rolling-window message retained from an older world action.
    messages = after.messages
    for overlap in range(min(len(before.messages), len(messages)), 0, -1):
        if before.messages[-overlap:] == messages[:overlap]:
            messages = messages[overlap:]
            break
    return messages


def confirmed_unseen_bump(before: Observation, after: Observation, action: Action,
                          target: tuple[int,int]) -> bool:
    """Fresh player contact permits ONE more attempt, never invisible visibility."""
    messages = _unseen_bump_messages(before,after,action,target)
    return (messages is not None and not any(_MELEE_END.search(message) for message in messages)
            and any(_UNSEEN_MELEE.search(message) for message in messages))


def confirmed_unseen_kill(before: Observation, after: Observation, action: Action,
                          target: tuple[int,int]) -> bool:
    """An unseen melee victim died; do not invent its identity or another hit."""
    messages = _unseen_bump_messages(before,after,action,target)
    return (messages is not None and not before.hostiles and not after.hostiles
            and any(_UNSEEN_MELEE.search(message) for message in messages)
            and any("You feel a bit more experienced." in message
                    or re.search(r"\bYou (?:kill|slay|destroy) something\b",message)
                    for message in messages))

# spl-data.h supplies native fixed ranges; spl-util.cc:1074-1094 caps them
# (including monster spells) at player vision, normally LOS_DEFAULT_RANGE=7.
# Keep this small: unclassified offensive spells retain the LOS-range bound.
_SELF_SPELLS = frozenset({
    "berserk", "berserker rage", "haste", "blink", "blink range", "blink away",
    "invisibility", "minor healing", "major healing",
})
# Battlecry has no direct damage; Throw Ally does (mon-cast.cc:9318–9324)
# as well as relocating a companion. Neither is an ordinary projectile.
_SUPPORT_SPELLS = frozenset({"battlecry","heal other","cantrip"})
_ENTANGLING_SPELLS = frozenset({"ensnare", "greater ensnare"})
_CONTROL_SPELLS = frozenset({"confuse","slow","paralyse","paralyze","petrify","banishment","sleep"}) | _ENTANGLING_SPELLS
_SPELL_RANGES = {
    "freeze": 1, "vampiric draining": 1, "sticky flame": 1,
    "throw frost": 6, "sting": 4, "spit poison": 6, "pyre arrow": 5,
}


def _non_attack_spell(spell: str) -> bool:
    name = spell.casefold()
    return (name in _SELF_SPELLS or name in _SUPPORT_SPELLS
            or name.startswith("summon ") or name == "call imp")

_HEAD_COUNTS = {word:number for number,word in enumerate((
    "one","two","three","four","five","six","seven","eight","nine","ten",
    "eleven","twelve","thirteen","fourteen","fifteen","sixteen","seventeen","eighteen","nineteen"),1)}
_HEAD_TENS = {"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}


def _visible_heads(name: str) -> int | None:
    match = re.search(r"\b(\d+|[a-z]+(?:-[a-z]+)?)[ -]headed\b",name)
    if not match:
        return None
    value = match[1]
    if value.isdigit():
        return int(value)
    if value in _HEAD_COUNTS or value in _HEAD_TENS:
        return _HEAD_COUNTS.get(value,_HEAD_TENS.get(value))
    tens,_,ones = value.partition("-")
    return _HEAD_TENS[tens]+_HEAD_COUNTS[ones] if tens in _HEAD_TENS and ones in _HEAD_COUNTS and _HEAD_COUNTS[ones]<10 else None


@dataclass(frozen=True)
class EnemyMechanics:
    melee: float
    speed: float = 1.0
    ranged: bool = False
    disables: bool = False
    summons: bool = False
    poison: bool = False
    escalates: bool = False
    avoid_melee: bool = False
    reach: int = 1
    will_check: bool = False
    ranged_range: int = 7
    uncertainty: str = "base attacks; weapon/spell damage may add to this"
    travel_speed: float | None = None
    ranged_damage: float | None = None
    ranged_average: float | None = None
    melee_average: float | None = None
    ranged_min_range: int = 1
    entangles: bool = False
    # Attacking worsens this encounter, unlike an ordinary high-risk opponent.
    melee_prohibited: bool = False
    # Public spellbook capabilities, not proof of a current target or buff.
    ally_reposition: bool = False
    ally_might: bool = False
    # Only HP damage requires an adjacent original source; control still works.
    damage_requires_adjacency: bool = False

    @property
    def melee_mean(self) -> float:
        return self.melee if self.melee_average is None else self.melee_average

    @property
    def ranged_max(self) -> float:
        return self.melee if self.ranged_damage is None else self.ranged_damage

    @property
    def ranged_mean(self) -> float:
        return self.ranged_max if self.ranged_average is None else self.ranged_average

    @property
    def movement_speed(self) -> float:
        return self.speed if self.travel_speed is None else self.travel_speed


# Small observed-failure/early-Dungeon catalogue. Unknown creatures retain the
# native relative threat classification instead of acquiring imaginary stats.
_KNOWN = {
    "rat": EnemyMechanics(3), "bat": EnemyMechanics(1, 3.0),
    "goblin": EnemyMechanics(5), "hobgoblin": EnemyMechanics(5),
    "jackal": EnemyMechanics(3, 1.4), "quokka": EnemyMechanics(5, 1.5),
    "ball python": EnemyMechanics(4),
    "frilled lizard": EnemyMechanics(3), "endoplasm": EnemyMechanics(5),
    "adder": EnemyMechanics(4, 1.3, poison=True),
    "gnoll": EnemyMechanics(14),  # includes unknown carried-weapon damage allowance
    "orc": EnemyMechanics(10), "orc warrior": EnemyMechanics(24),
    "orc priest": EnemyMechanics(17, ranged=True),  # includes smiting risk allowance
    "orc wizard": EnemyMechanics(12, ranged=True, disables=True, will_check=True),
    "ogre": EnemyMechanics(30), "two-headed ogre": EnemyMechanics(65, avoid_melee=True),
    "hound": EnemyMechanics(6, 1.5), "water moccasin": EnemyMechanics(10, 1.4, poison=True),
    "black mamba": EnemyMechanics(20, 1.8, poison=True),
    "killer bee": EnemyMechanics(10, 2.0, poison=True),
    "black bear": EnemyMechanics(19, escalates=True),
    "jelly": EnemyMechanics(12, escalates=True),  # acid and retaliatory splash allowance
    "ice beast": EnemyMechanics(20),  # cold flavour allowance, not base hit alone
    "centaur": EnemyMechanics(20, 1.5, ranged=True),
    "dart slug": EnemyMechanics(3, .4, ranged=True),
    "bombardier beetle": EnemyMechanics(8, .6, ranged=True),
    # mon-gear.cc:677,1354: generated halberd is named scythe. Keep its
    # reach guard even before the first free public examination completes.
    "sigmund": EnemyMechanics(24, ranged=True, disables=True, avoid_melee=True, will_check=True, reach=2),
    "grinder": EnemyMechanics(20, ranged=True, disables=True, avoid_melee=True, will_check=True),
    "eustachio": EnemyMechanics(18, summons=True),
    "menkaure": EnemyMechanics(30, .8, ranged=True, avoid_melee=True),
    "ijyb": EnemyMechanics(15, ranged=True, disables=True),
    "natasha": EnemyMechanics(12, ranged=True, summons=True),
    "orc sorcerer": EnemyMechanics(35, ranged=True, disables=True, summons=True, avoid_melee=True, will_check=True),
    "basilisk": EnemyMechanics(20, ranged=True, disables=True, will_check=True),
    "hornet": EnemyMechanics(18, 1.5, disables=True, poison=True, avoid_melee=True),
    "komodo dragon": EnemyMechanics(34), "cane toad": EnemyMechanics(26, poison=True),
    "yak": EnemyMechanics(18), "death yak": EnemyMechanics(30),
}


def _base_mechanics(monster: Monster) -> EnemyMechanics:
    name = monster.name.casefold()
    if "slime creature" in name:
        count = next((number for size, number in (("titanic", 5), ("enormous", 4),
                                                  ("very large", 3), ("large", 2)) if size in name), 1)
        return EnemyMechanics(22 * count, escalates=True, avoid_melee=count > 1,
                              melee_prohibited=count > 1,
                              uncertainty="merged count inferred from the visible size name")
    if "hydra" in name:
        heads = _visible_heads(name)
        return EnemyMechanics(18 * (heads if heads is not None else 8), avoid_melee=True,
                              melee_prohibited=True,
                              uncertainty="visible head count" if heads is not None
                              else "head count unavailable; uncertain high-risk melee allowance, not an observed count")
    known = _KNOWN.get(name)
    if known is not None:
        return known
    return EnemyMechanics((5, 12, 24, 45)[min(3, max(0, monster.threat))],
                          ranged=any(word in name for word in ("wizard", "priest", "mage", "dragon", "archer")),
                          uncertainty="unknown enemy: conservative native-threat bound")


def mechanics(monster: Monster) -> EnemyMechanics:
    info = _base_mechanics(monster)
    spells = " ".join(monster.spells).casefold()
    disable = any(mark in spells for mark in ("confus", "paralys", "petrif", "banish", "sleep", "slow"))
    summons = "summon" in spells
    description = (monster.description or "").casefold()
    public_melee = public_shot = 0
    melee_rows = 0
    attack_effects = []
    public_ranged = False
    # describe.cc separates public attack-table columns by >=2 spaces. Weapon
    # names/enchantments precede Max Damage; do not mistake "+0" for damage.
    verb = r"^\s*(?:hit|bite|claw|sting|touch|gore|trample|peck|kick|punch|headbutt|engulf|constrict|shoot|shot|throw|tentacle slap|tail slap)\b"
    inside_attacks = False
    for line in description.splitlines():
        if re.match(r"^\s*attacks?\s+max\s*damage\b",line):
            inside_attacks = True
            continue
        if not line.strip():
            inside_attacks = False
        row = (re.match(r"^\s*.*?\s{2,}(\d+)(.*)$",line) if inside_attacks else
               re.match(verb + r"(?::.*?)?\s{2,}(\d+)(.*)$", line)
               or re.match(verb + r"\s+(\d+)(.*)$", line))
        if row:
            shot = bool(re.match(r"^\s*(?:shoot|shot|throw)\b", line))
            damage = int(row[1]) + sum(int(value) for value in re.findall(r"\+\s*(\d+)", row[2]))
            public_ranged |= shot
            if shot:
                public_shot = max(public_shot, damage)
            else:
                public_melee += damage
                melee_rows += 1
            attack_effects.append(row[2])
    # Claw/bite/etc. can share one melee round. Shooting/throwing and melee
    # are alternatives: a whip6 plus stones2 is not an eight-damage projectile.
    public_max = max(public_melee, public_shot)
    effects = " ".join(attack_effects)
    contact_disable = any(mark in effects for mark in ("confus", "paraly", "petrif", "banish"))
    speed = info.speed
    speed_text = re.search(r"Speed:\s*(\d+)%", monster.speed_description or "")
    if speed_text:
        speed = int(speed_text[1]) / 100
    elif any("haste" in status.casefold() or "berserk" in status.casefold() for status in monster.statuses):
        speed *= 1.5
    travel = re.search(r"travel:\s*(\d+)%",monster.speed_description or "")
    travel_speed = int(travel[1])/100 if travel else None
    observed_ranges = {}
    for title, value in monster.spell_ranges:
        observed_ranges[title.casefold()] = max(value,observed_ranges.get(title.casefold(),0))
    spell_range = max((observed_ranges.get(spell.casefold(),_SPELL_RANGES.get(spell.casefold(),7))
                       for spell in monster.spells if not _non_attack_spell(spell)),default=0)
    # A contact spell does not erase an independently known ranged capability.
    # A public ranged spellbook can establish a shorter range, but never shorten
    # a separately described bow/throwing attack.
    ranged = info.ranged or public_ranged or spell_range > 1
    ranged_range = (info.ranged_range if public_ranged or spell_range <= 1 else spell_range)
    reach = 2 if any(word in " ".join(monster.equipment).casefold()
                     for word in ("spear", "trident", "halberd", "glaive", "bardiche", "scythe")) else info.reach
    # A public melee round is not a projectile or a distant spell. Preserve
    # composite/escalating bounds when a displayed row is per head.
    composite = info.escalates or _visible_heads(monster.name.casefold()) is not None
    damage = max(info.melee,public_melee) if composite or not public_melee else public_melee
    uncertainty = info.uncertainty
    spell_section = description.partition("spells:\n")[2]
    spell_max = 0
    spell_mean = 0.
    # Robin's class HD is fixed at two (dat/mons/robin.yaml:10).
    # Throw Ally rolls random2(2*HD), then AC: 0–3 before AC, not Robin's
    # wielded-weapon damage. Unknown throwers retain the uncertain fallback.
    ally_throw_max = 3 if monster.name.casefold() == "robin" else 0
    if ally_throw_max and any(spell.casefold() == "throw ally" for spell in monster.spells):
        spell_max = ally_throw_max
        spell_mean = ally_throw_max / 2
    contact_max = contact_mean = 0.
    unbounded_spell = False
    unbounded_ranged_spell = False
    bounded_spells = set()
    if ally_throw_max:
        bounded_spells.add("throw ally")
    for line in spell_section.splitlines():
        title = line.partition(":")[0].strip()
        if not title or _non_attack_spell(title) or title in _CONTROL_SPELLS:
            continue
        if title == "throw ally" and ally_throw_max:
            continue
        effect = line.partition(":")[2].strip()
        dice = re.findall(r"(\d+)d(\d+)",effect)
        maximum = sum(int(count)*int(sides) for count,sides in dice)
        average = sum(int(count)*(int(sides)+1)/2 for count,sides in dice)
        interval = re.fullmatch(r"(\d+)-(\d+)",effect)
        if interval:
            maximum = max(maximum,int(interval[2]))
            average = max(average,(int(interval[1])+int(interval[2]))/2)
        if observed_ranges.get(title,_SPELL_RANGES.get(title,7)) <= 1:
            contact_max = max(contact_max,maximum or info.melee)
            contact_mean = max(contact_mean,average or info.melee)
        else:
            spell_max = max(spell_max,maximum)
            spell_mean = max(spell_mean,average)
        if maximum:
            bounded_spells.add(title)
        elif title and not _non_attack_spell(title) and title not in _CONTROL_SPELLS:
            unbounded_spell = True
            if observed_ranges.get(title,_SPELL_RANGES.get(title,7)) > 1:
                unbounded_ranged_spell = True
    ranged_damage = ranged_average = None
    unbounded_spell |= any(not _non_attack_spell(spell) and spell.casefold() not in _CONTROL_SPELLS
                           and spell.casefold() not in bounded_spells for spell in monster.spells)
    damaging_ranged_spells = {
        spell.casefold() for spell in monster.spells
        if not _non_attack_spell(spell) and spell.casefold() not in _CONTROL_SPELLS
        and observed_ranges.get(spell.casefold(),_SPELL_RANGES.get(spell.casefold(),7)) > 1}
    unbounded_ranged_spell |= any(spell not in bounded_spells for spell in damaging_ranged_spells)
    if public_max and uncertainty.startswith("unknown enemy:") and not unbounded_spell:
        uncertainty = "displayed attacks and bounded public spell annotations; non-damage effects remain uncertain"
    if public_ranged or spell_max:
        ranged_damage = max(public_shot,spell_max)
        # Ranged attacks share attack.cc:918–944's rolled damage; archer
        # modifiers are rolled too (ranged-attack.cc:369–389). Keep a conservative
        # half-maximum approximation, not the maximum on every shot. This still
        # ignores misses, AC and blocks; zero-damage control stays exactly zero.
        shot_average = (public_shot+1)/2 if public_shot else 0.
        ranged_average = max(shot_average,spell_mean)
        if unbounded_ranged_spell:
            # A displayed projectile/spell does not bound a different effect
            # whose damage formula is not represented in its public label.
            ranged_damage = max(ranged_damage,info.melee)
            ranged_average = max(ranged_average,ranged_damage)
    elif ranged and not info.ranged and not unbounded_ranged_spell:
        # A complete control-only ranged book is not a physical projectile.
        # Ensnare creates a web (mon-cast.cc:2460–2466; beam.cc:4432–4433).
        ranged_damage = ranged_average = 0.
    # mon-act.cc:1507–1523: ordinary throwers stop shooting in melee. These
    # examined early humanoids have no prefer_ranged flag in dat/mons/*.yaml;
    # recognizing their behaviour does not require guessed catalogue damage.
    # Other unknown species and observed armed buffs retain the allowance.
    specialists = {"deep elf master archer","merfolk javelineer","naga sharpshooter",
                   "satyr","statue","vashnia","dancing weapon"}
    damaging_spells = bool(damaging_ranged_spells)
    ordinary_thrower = (monster.name.casefold() in _KNOWN or (
        monster.description is not None
        and monster.name.casefold() in {"goblin","hobgoblin","kobold"}))
    minimum_range = (2 if public_ranged and ordinary_thrower
                     and monster.name.casefold() not in specialists and not damaging_spells
                     and not any("armed" in status.casefold() for status in monster.statuses) else 1)
    melee_average = min(damage,(max(min(info.melee,damage),public_melee)+melee_rows)/2) if public_melee else None
    if contact_max:
        melee_average = max(damage if melee_average is None else melee_average,contact_mean)
        damage = max(damage,contact_max)
    return replace(info, melee=damage, speed=speed, travel_speed=travel_speed, reach=reach, uncertainty=uncertainty,
                   # attack.cc:918–944 rolls natural/weapon/enchantment damage.
                   # Keep the maximum unchanged; missing tables retain the bound.
                   melee_average=melee_average,
                   ranged=ranged, ranged_range=ranged_range, ranged_damage=ranged_damage,
                   ranged_average=ranged_average,
                   ranged_min_range=minimum_range,
                   disables=info.disables or disable or contact_disable,
                   will_check=info.will_check or disable,
                   poison=info.poison or "poison" in effects or "venom" in effects,
                   # describe.cc:5524–5525 displays net restraint separately
                   # from its zero HP damage; it is not a Will-check disable.
                   entangles=("ensnare in a net" in effects or any(
                       spell.casefold() in _ENTANGLING_SPELLS for spell in monster.spells)),
                   summons=info.summons or summons,
                   ally_reposition=any(spell.casefold() == "throw ally" for spell in monster.spells),
                   ally_might=any(spell.casefold() == "battlecry" for spell in monster.spells),
                   # clockroach.yaml:3, mon-place.cc:1141–1142: innate, infinite
                   # warding persists when a one-turn inspection status expires.
                   # Other bodies need the actual current public status.
                   damage_requires_adjacency=(monster.name.casefold() == "clockroach" or any(
                       re.match(r"^\s*damage-immune at range(?:\s*:|$)", line, re.IGNORECASE)
                       for status in monster.statuses for line in status.splitlines())))


def visible_dangerous_weapon(monster: Monster) -> bool:
    details = " ".join((monster.description or "", *monster.equipment)).casefold()
    return any(word in details for word in ("distortion", "chaos", "banish", "paralys"))


def plain_melee_attacks(monster: Monster) -> bool:
    """Native examined natural attacks with no weapon, spell or bonus column."""
    if (monster.equipment or monster.spells or public_hp_estimate(monster) is None
            or re.search(r"(?m)^Spells:", monster.description or "")):
        return False
    # describe.cc:5596–5606 adds a separate column for every attack flavour.
    table = re.search(r"(?m)^Attacks?[ \t]+Max Damage[ \t]*\n((?:[^\n]+\n?)+)",
                      monster.description or "")
    return bool(table) and all(re.fullmatch(r"[a-zA-Z][a-zA-Z '-]*[ \t]+\d+[ \t]*", row)
                               for row in table[1].splitlines())


def public_hp_estimate(monster: Monster) -> int | None:
    """Approximate maximum shown by native examination, never the hidden HP."""
    match = re.search(r"\bMax HP:\s*~?(\d+)",monster.description or "")
    return int(match[1]) if match else None


def short_caster_book(monster: Monster) -> bool:
    """Observed small Dart/Slow/Call Imp family, including a visible flame wand."""
    spells = {spell.casefold() for spell in monster.spells}
    return bool(spells) and spells <= {"magic dart","slow","call imp","throw flame"}


def minor_summoner(monster: Monster) -> bool:
    """Pinned mon-cast.cc:7830-7844,7915-7934: mammals/class-5 demons, not greater summons."""
    spells = {spell.casefold() for spell in monster.spells}
    minor = {"summon small mammal","summon minor demon","call imp"}
    return bool(spells & minor) and spells <= minor | {"blink"}


# dat/mons/{human,orc,goblin,hobgoblin,kobold,gnoll}.yaml: ordinary land bodies,
# no flight flags; util/mon-gen.py:385 defaults habitat to HT_LAND. Their mundane
# zombies/skeletons inherit the base habitat (mon-util.cc:3232–3236).
ORDINARY_LAND_BASES = frozenset({"human","orc","goblin","hobgoblin","kobold","gnoll"})


def mundane_land_mover(monster: Monster) -> bool:
    """Only an examined, currently mundane body has a useful walking bound."""
    base = re.sub(r" (?:zombie|skeleton)$","",monster.name.casefold())
    movement = " ".join((*monster.statuses,monster.description or "")).casefold()
    return (base in ORDINARY_LAND_BASES and monster.description is not None
            and not monster.spells
            and not any(mark in movement for mark in
                        ("flying","airborne","levitat","teleport","blinking","pass through walls","burrow")))


# throw.cc:478 uses current player vision, not a shorter net-specific constant.
# los.cc:119 caps that at LOS_RADIUS=8 (defines.h:113); Daystalker can extend
# default7 (player.cc:893–898). No public mutation field permits assuming seven.
PHYSICAL_NET_RANGE_UPPER_BOUND = 8


def ordinary_net_thrower(monster: Monster, info: EnemyMechanics) -> bool:
    """Examined ordinary land body with ONLY the displayed normal throwing net.

    mons_throw's nonteleport branch fires the ordinary missile beam
    (throw.cc:811–876); no spellbook, wand, other throwing row or unknown
    unusual item gets this physical-collision certificate. Net restraint is
    independent of Will and remains a real ranged/entangling capability.
    """
    if (not mundane_land_mover(monster) or monster.spell_ranges
            or not info.ranged or not info.entangles
            or info.ranged_max != 0 or info.disables or info.summons or info.poison
            or info.escalates or info.avoid_melee or info.will_check
            or visible_dangerous_weapon(monster)):
        return False
    description = (monster.description or "").casefold()
    equipment = " ".join(monster.equipment).casefold()
    if ("wand" in description or any(mark in equipment for mark in
                                    ("wand","bow","sling","arbalest","hand cannon"))
            or "spells:" in description):
        return False
    inside = False
    found_net = False
    found_melee = False
    for line in description.splitlines():
        if re.match(r"^\s*attacks?\s+max\s*damage\b",line):
            inside = True
            continue
        if inside:
            if not line.strip():
                inside = False
            elif re.fullmatch(r"\s*throw:\s+a throwing net\s+0\s+ensnare in a net\s*",line):
                if found_net:
                    return False
                found_net = True
            elif re.match(r"^\s*hit:\s+.+?\s{2,}\d+",line):
                found_melee = True
            else:
                return False
        if "unusual item:" in line and line.strip() != "it has an unusual item: a throwing net.":
            return False
    return found_net and found_melee
