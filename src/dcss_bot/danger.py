"""Character-independent risk annotations from player-visible observations."""
from dataclasses import dataclass

from .models import Monster, Observation


RANGED_NAMES = ("dart slug", "orc priest", "wizard", "centaur", "kobold",
                "orc sorcerer", "orc high priest", "yaktaur", "cyclops",
                "stone giant", "deep elf", "electric eel", "oklob",
                "acid dragon", "steam dragon", "manticore", "bombardier beetle")


def is_ranged(monster: Monster) -> bool:
    """Recognise common shooters/casters by visible names, not monster internals."""
    return any(name in monster.name.lower() for name in RANGED_NAMES)


@dataclass(frozen=True)
class Danger:
    emergency: bool
    reasons: tuple[str, ...]
    adjacent: int
    incoming_damage: int


def assess_danger(obs: Observation, previous_hp: int | None = None) -> Danger:
    enemies = obs.hostiles
    adjacent = sum(m.distance(obs.position) <= 1 for m in enemies)
    damage = max(0, (previous_hp if previous_hp is not None else obs.hp) - obs.hp)
    status = " ".join(obs.statuses).lower()
    reasons = []
    if obs.hp_ratio < .50:
        reasons.append("low HP")
    if adjacent >= 3:
        reasons.append("surrounded")
    if any(m.threat >= 3 for m in enemies):
        reasons.append("extremely dangerous enemy")
    if any(m.threat >= 2 for m in enemies) and obs.hp_ratio < .75:
        reasons.append("dangerous enemy while injured")
    if damage >= max(4, obs.hp_max * .20):
        reasons.append("significant incoming damage")
    if any(s in status for s in ("conf", "petr", "slow", "barbs", "corrod")):
        reasons.append("dangerous status")
    if "pois" in status and obs.hp_ratio < .65:
        reasons.append("poison while injured")
    if any(s.lower() == "fire" for s in obs.statuses):
        reasons.append("liquid fire: movement required")
    slimes = [m for m in enemies if "slime creature" in m.name.lower()]
    if len(slimes) >= 2 or any(any(size in m.name.lower() for size in ("large", "enormous", "titanic")) for m in slimes):
        reasons.append("merging slime creatures")
    shooters = [m for m in enemies if is_ranged(m) and m.distance(obs.position) > 1]
    if len(shooters) >= 2:
        reasons.append("multiple ranged threats")
    if any(m.threat >= 2 for m in shooters):
        reasons.append("dangerous ranged threat")
    return Danger(bool(reasons), tuple(reasons), adjacent, damage)
