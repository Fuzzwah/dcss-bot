"""Finite, public-only shooter approaches; every proposed move needs reobservation."""
from collections import deque
from dataclasses import dataclass
import re
from typing import Callable, Protocol

from ..models import Cell, DIRECTIONS, Monster, Observation
from .mibe_mechanics import mechanics, public_hp_estimate, visible_dangerous_weapon
from ..terrain import CLOSED_DOORS


# Native Sandblast is a simple BEAM_FRAG bolt; its extra effect is caster
# energy loss, not player control (mon-cast.cc:8647-8652, zap-data.h:1268-1281).
_BOUNDED_SPELLS = frozenset({"slug dart", "magic dart", "throw flame", "throw frost", "sandblast"})


class Exposure(Protocol):
    expected_loss: float
    burst_loss: float
    horizon: float


@dataclass
class ApproachAttempt:
    location: str
    target_id: int
    name: str
    started: int
    actions: int
    turn: int
    position: tuple[int, int]
    distance: int
    result_pending: bool = True


@dataclass(frozen=True)
class ApproachStep:
    key: str
    risk: Exposure
    evidence: dict


class BoundedShooterApproach:
    """One allowance per public encounter; interruptions never refill its budgets."""

    def __init__(self) -> None:
        self.active: ApproachAttempt | None = None
        self.cancelled: dict[tuple[str, str], str] = {}

    def cancel(self, reason: str) -> None:
        if self.active is not None:
            self.cancelled[self.active.location, self.active.name] = reason
            self.active = None

    def observe(self, obs: Observation) -> None:
        attempt = self.active
        if attempt is None:
            return
        if obs.location != attempt.location:
            self.cancel("changed floor")
            return
        if not obs.hostiles:
            self.cancel("target lost LOS")
            return
        targets = [enemy for enemy in obs.hostiles if enemy.name.casefold() == attempt.name]
        if len(targets) != 1:
            self.cancel("different or ambiguous visible target")
            return
        target = targets[0]
        # This is a shared encounter allowance, not an assertion that native
        # IDs identify a persistent body. A new ID needs its free examination.
        if (self.unsafe_target(target)
              or target.description is not None and not self.bounded_target(target)
              or self.restricted_status(
                  " ".join((*obs.statuses, *(entry.text for entry in obs.status_details))).casefold())):
            self.cancel("target book/mechanics or player status changed")
        elif obs.turn - attempt.started > 8:
            self.cancel("finite encounter time budget expired")
        elif attempt.result_pending and obs.turn > attempt.turn:
            # Consume this issued move's receipt exactly once. A later resource,
            # attack or deliberate withdrawal is not another result of it.
            attempt.result_pending = False
            if obs.position == attempt.position:
                self.cancel("approach failed to displace the player")
            elif target.distance(obs.position) >= attempt.distance:
                self.cancel("kiting or no observed distance progress")
        # An additional foe stops admission in plan(), not the allowance's
        # lifecycle. Its public resolution may permit the remaining actions
        # within the original eight turns. Check the issued move above even
        # when interrupted: neither a failed move nor kiting becomes resumable.

    def rebase(self, location: str, offset: tuple[int, int] | None) -> None:
        attempt = self.active
        if attempt is None or attempt.location != location:
            return
        if offset is None:
            self.cancel("public map coordinates lost")
            return
        dx, dy = offset
        attempt.position = attempt.position[0] + dx, attempt.position[1] + dy

    @staticmethod
    def bounded_book(target: Monster) -> bool:
        """Only displayed plain projectiles or annotated pure-damage spells."""
        description = (target.description or "").casefold()
        if not target.spells:
            return ("spells:\n" not in description and bool(re.search(
                r"^\s*(?:shoot|shot|throw)\b.*?\s{2,}\d+\s*$", description, re.MULTILINE)))
        # Deliberately small: annotations alone cannot bound poison, Blink,
        # buffs, invisible actors, summons, control or unknown secondary effects.
        if not {spell.casefold() for spell in target.spells} <= _BOUNDED_SPELLS:
            return False
        section = description.partition("spells:\n")[2]
        rows = dict((line.partition(":")[0].strip(), line.partition(":")[2].strip())
                    for line in section.splitlines() if ":" in line)
        return (set(rows) == {spell.casefold() for spell in target.spells}
                and all(re.fullmatch(r"\(?\d+d\d+\)?|\d+-\d+", rows.get(spell.casefold(), ""))
                        for spell in target.spells))

    @staticmethod
    def restricted_status(status: str) -> bool:
        return any(mark in status for mark in
                   ("pois", "slow", "exh", "berserk", "conf", "tele", "held", "net", "web",
                    "constr", "petr", "paral", "fire", "corr", "asleep", "vertigo",
                    "blind", "fear", "mesmer", "-scroll", "-potion"))

    @staticmethod
    def unsafe_target(target: Monster) -> bool:
        """Reject explicit unsafe data even while native examination is pending."""
        info = mechanics(target)
        # Missing attack tables restore generic threat bounds, not new observed
        # damage. Wait for inspection instead of treating those as unsafe data.
        return (target.description is not None and (info.melee > 12 or info.ranged_max > 24)
                or info.speed > 1.5 or info.movement_speed > 1.5
                or info.poison or info.escalates or info.avoid_melee or info.disables
                or info.summons or info.entangles or visible_dangerous_weapon(target)
                or not {spell.casefold() for spell in target.spells} <= _BOUNDED_SPELLS
                or any(mark in " ".join(target.equipment).casefold() for mark in
                       (" of ", "flaming", "freezing", "venom", "electrocution", "draining",
                        "pain", "penetration", "antimagic", "{", "}"))
                or any(mark in " ".join(target.statuses).casefold()
                       for mark in ("invis", "haste", "might", "berserk", "armed", "regen")))

    @classmethod
    def bounded_target(cls, target: Monster) -> bool:
        info = mechanics(target)
        hp_bound = public_hp_estimate(target)
        return (target.description is not None and hp_bound is not None and 0 < hp_bound <= 30
                and cls.bounded_book(target) and info.ranged and info.ranged_max > 0
                and not cls.unsafe_target(target))

    def plan(self, obs: Observation, cells: dict[tuple[int, int], Cell], *,
             status: str, uncertain_contact: bool, unresolved: bool,
             assess: Callable[[tuple[int, int]], Exposure]) -> tuple[ApproachStep | None, dict]:
        evidence = {"available": False, "rejected": [],
                    "uncertainty": "Public damage approximations, not a kill guarantee; no future healing or Berserk duration assumed"}
        failures = evidence["rejected"]
        if len(obs.hostiles) != 1:
            return None, evidence
        target = obs.hostiles[0]
        evidence["target_id"] = target.id
        key = obs.location, target.name.casefold()
        if key in self.cancelled:
            failures.append(self.cancelled[key])
        if uncertain_contact or self.restricted_status(status):
            failures.append("uncertain contact or restricted status")
        if unresolved:
            failures.append("unresolved additional foe; remaining allowance suspended")
        awaiting_inspection = target.description is None and not self.unsafe_target(target)
        if awaiting_inspection:
            failures.append("awaiting free public description inspection")
        elif not self.bounded_target(target):
            failures.append("unbounded public body, offensive book, weapon or mechanic")
        if self.active is None and obs.hp_ratio < .8:
            failures.append("new approach must start healthy (at least 80% HP)")
        if self.active is not None and (self.active.actions >= 6 or obs.turn-self.active.started > 8):
            failures.append("finite approach budget exhausted")
        if failures:
            cancellation = next((reason for reason in failures
                                 if reason not in {
                                     "awaiting free public description inspection",
                                     "unresolved additional foe; remaining allowance suspended"}), None)
            if cancellation is not None:
                self.cancel(cancellation)
            return None, evidence
        if target.distance(obs.position) <= 1:
            return None, evidence
        info = mechanics(target)

        occupied = {(monster.x, monster.y) for monster in obs.monsters}
        # Every route cell must already be visible clear ground. Surrounding
        # unknown cells do not authorize movement, but need not block this route.
        def safe(position: tuple[int, int]) -> bool:
            cell = cells.get(position)
            return bool(cell and cell.visible and cell.walkable and not cell.cloud
                        and cell.feature not in CLOSED_DOORS and position not in occupied)

        limit = 6 - (self.active.actions if self.active else 0)
        queue = deque([(obs.position, ())])
        seen = {obs.position}
        routes = []
        while queue:
            position, path = queue.popleft()
            if path and target.distance(position) == 1:
                risks = [assess(step) for step in path]
                expected = sum(risk.expected_loss for risk in risks)
                absolute = sum(risk.burst_loss for risk in risks)
                # An explicit stress margin, NOT a percentile or an absolute
                # damage guarantee. Absolute maxima remain separately logged.
                stress = expected * 1.1
                reserve = obs.hp_max * .15
                next_attack = max(info.melee, info.ranged_max
                                  if target.distance(path[0]) >= info.ranged_min_range else 0.)
                if (expected + reserve < obs.hp
                        and next_attack + reserve < obs.hp):
                    routes.append((stress, absolute, path, risks, reserve, next_attack))
                continue
            if len(path) >= limit:
                continue
            for dx, dy in DIRECTIONS.values():
                step = position[0]+dx, position[1]+dy
                if (step in seen or target.distance(step) >= target.distance(position)
                        or not safe(step)):
                    continue
                seen.add(step)
                queue.append((step, (*path, step)))
        if not routes:
            failures.append("no visible clear contact path within remaining expected-damage reserve")
            self.cancel(failures[0])
            return None, evidence
        stress, absolute, path, risks, reserve, next_attack = min(routes, key=lambda row: (row[0], row[1], len(row[2]), row[2]))
        first = path[0]
        direction = next(key for key, (dx, dy) in DIRECTIONS.items()
                         if (obs.position[0]+dx, obs.position[1]+dy) == first)
        evidence.update(available=True, remaining_steps=len(path),
                        remaining_expected_loss=round(sum(r.expected_loss for r in risks), 2),
                        remaining_absolute_burst=round(absolute, 2), hp_reserve=round(reserve, 2),
                        stress_heuristic=round(stress, 2),
                        stress_definition="1.10 times remaining expected approach loss; reported sensitivity, not an admission threshold, percentile or upper bound",
                        admission_budget="remaining expected approach loss and next-position single-attack maximum each fit with 15% max-HP reserve; multi-attack absolute bursts remain uncertainty, not survival guarantees",
                        one_action_expected_loss=risks[0].expected_loss,
                        one_action_absolute_burst=risks[0].burst_loss,
                        next_attack_upper_bound=next_attack,
                        actions_used=self.active.actions if self.active else 0,
                        action_limit=6, time_limit=8)
        return ApproachStep(direction, risks[0], evidence), evidence

    def commit(self, obs: Observation) -> None:
        target = obs.hostiles[0]
        old = self.active
        self.active = ApproachAttempt(obs.location, target.id, target.name.casefold(),
                                      old.started if old else obs.turn,
                                      old.actions+1 if old else 1, obs.turn,
                                      obs.position, target.distance(obs.position))
