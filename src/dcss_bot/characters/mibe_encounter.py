"""Observed progress, narrow enclosures, and finite terrain separation proofs.

Only examined mundane bodies qualify. Full undead enclosures are distinct from
finite walking/ordinary-net separation: partial unknown exits never become a
permanent cage. One dry nonapproaching step is followed by a fresh observation.
Remembered walls are public historical evidence, not guaranteed unchanged.
Observations and real ranged/control capabilities are never filtered or rewritten.
"""
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import ceil

from ..models import Action, Cell, DIRECTIONS, Monster, Observation
from ..terrain import CLOSED_DOORS
from .mibe_mechanics import (
    ORDINARY_LAND_BASES, PHYSICAL_NET_RANGE_UPPER_BOUND, mechanics, mundane_land_mover,
    ordinary_net_thrower, visible_dangerous_weapon,
)
from .mibe_geometry import WalkingDistances


Position = tuple[int, int]
# dungeon-feature-type.h, TAG_MAJOR_VERSION=34: actual walls, not trees/doors.
_WALLS = frozenset({5, 6, 7, 8, 9, 10, 11, 12, 13})
_MAX_COMPONENT = 64
# Undead inherit base habitat (mon-util.cc:3232-3236), so do not generalize this
# to arbitrary zombies: unknown/wall-travelling bases need separate evidence.
_WALKING_UNDEAD = frozenset(f"{base} {kind}"
                          for base in ORDINARY_LAND_BASES
                          for kind in ("zombie", "skeleton"))


def _ordinary_undead(enemy: Monster) -> bool:
    info = mechanics(enemy)
    return (enemy.description is not None and enemy.name.casefold() in _WALKING_UNDEAD
            and not enemy.spells and info.reach == 1
            and not any("tele" in state.casefold() for state in enemy.statuses)
            and not (info.ranged or info.summons or info.disables or info.poison
                     or info.escalates or info.avoid_melee))


def enclosed_melee_component(enemy: Monster, cells: Mapping[Position, Cell]) -> frozenset[Position]:
    """Return a visible sealed component, or no certificate (never unreachable).

    These mundane humanoid undead have ordinary land movement. Restrict further
    to examined, spell-free, plain melee: this is not a general-purpose assertion
    about monsters behind glass.
    """
    if not _ordinary_undead(enemy):
        return frozenset()
    origin = enemy.x, enemy.y
    queue = deque([origin])
    component = {origin}
    while queue:
        position = queue.popleft()
        cell = cells.get(position)
        if cell is None or not cell.visible or cell.feature != 33 or cell.cloud:
            return frozenset()
        for dx, dy in DIRECTIONS.values():
            neighbor = position[0] + dx, position[1] + dy
            side = cells.get(neighbor)
            if side is None or not side.visible:
                return frozenset()
            if side.feature in _WALLS:
                continue
            if (side.feature != 33 or side.cloud
                    or len(component) >= _MAX_COMPONENT and neighbor not in component):
                return frozenset()
            if neighbor not in component:
                component.add(neighbor)
                queue.append(neighbor)
    return frozenset(component)


@dataclass(frozen=True)
class _EnclosureCertificate:
    enemy: Monster
    component: frozenset[Position]
    features: dict[Position, int]
    turn: int
    time: int


@dataclass
class EnclosureMemory:
    """Historical public evidence, never a reconstruction of hidden terrain.

    A certificate requires a full current enclosure initially and a continuously
    visible, examined occupant thereafter. Any other unsealed hostile clears the
    entire set. Missing/contradictory map knowledge and floor/frame ambiguity also
    clear it; no identity is carried through disappearance or reconnect.
    """
    certificates: dict[int, _EnclosureCertificate] = field(default_factory=dict)
    location: str = ""
    _observation: Observation | None = None

    def clear(self) -> None:
        self.certificates.clear()
        self.location = ""
        self._observation = None

    def observe(self, obs: Observation, cells: Mapping[Position, Cell]) -> None:
        if (self.location != obs.location or self._observation is not None
                and (obs.turn < self._observation.turn or obs.time < self._observation.time)):
            self.clear()
        self.location = obs.location
        self._observation = obs
        current = {}
        for enemy in obs.hostiles:
            certificate = self.certificates.get(enemy.id)
            if certificate is not None:
                original = certificate.enemy
                valid = (
                    _ordinary_undead(enemy) and enemy.name == original.name
                    and mechanics(enemy) == mechanics(original)
                    and tuple(enemy.equipment) == tuple(original.equipment)
                    and (enemy.x, enemy.y) in certificate.component
                    and obs.position not in certificate.component
                    and all(point in cells and cells[point].feature == feature
                            for point, feature in certificate.features.items()))
                if not valid:
                    self.certificates.clear()
                    return
            else:
                component = enclosed_melee_component(enemy, cells)
                if not component or obs.position in component:
                    self.certificates.clear()
                    return
                footprint = set(component)
                for x, y in component:
                    footprint.update((x+dx, y+dy) for dx, dy in DIRECTIONS.values())
                certificate = _EnclosureCertificate(
                    enemy, component, {point: cells[point].feature for point in footprint},
                    obs.turn, obs.time)
            current[enemy.id] = certificate
        self.certificates = current

    def components(self, obs: Observation) -> list[frozenset[Position]]:
        if self._observation is not obs:
            return []
        return [self.certificates[enemy.id].component
                if enemy.id in self.certificates else frozenset() for enemy in obs.hostiles]

    def evidence(self, obs: Observation) -> list[dict]:
        return [{"hostile_id": enemy.id,
                 "observed_turn": self.certificates[enemy.id].turn,
                 "age_turns": obs.turn-self.certificates[enemy.id].turn,
                 "age_aut": obs.time-self.certificates[enemy.id].time,
                 "uncertainty": "Previously observed enclosure; remembered boundary may be outside LOS and is not guaranteed unchanged"}
                for enemy in obs.hostiles]


def enclosed_melee_bypass(
    obs: Observation,
    cells: Mapping[Position, Cell],
    targets: Iterable[Position],
    *,
    memory: EnclosureMemory | None = None,
    walking: WalkingDistances | None = None,
) -> Action | None:
    """Take one clear step past observed enclosure or finite walking separation.

    A partial moat is not a cage. Unknown cells remain passable in its walking
    bound; only the next visible dry step is authorized. Ordinary net-only
    throwers also require projectile/control exclusion at every optimistic
    shooter origin: one departure action at the current endpoint, four actions
    at the next endpoint. The caller retains real goals and unseen/status guards.
    """
    if not obs.hostiles or obs.dead or obs.game_over:
        return None
    status = " ".join((*obs.statuses, *(entry.text for entry in obs.status_details))).casefold()
    if (obs.hp_ratio < .98 or obs.menu or obs.prompt or obs.input_mode != 1
            or any(mark in status for mark in ("berserk", "pois", "conf", "petr", "paral", "slow", "exh", "fire", "held", "net", "tele"))):
        return None
    certificates = (memory.components(obs) if memory is not None
                    else [enclosed_melee_component(enemy, cells) for enemy in obs.hostiles])
    sealed = (len(certificates) == len(obs.hostiles)
              and all(component and obs.position not in component for component in certificates))
    walking = walking or WalkingDistances(cells)
    enemies = [(enemy,mechanics(enemy)) for enemy in obs.hostiles]
    net_only = {enemy.id for enemy,info in enemies if ordinary_net_thrower(enemy,info)}
    separated_positions = {}
    def separated(position, projectile_actions):
        key = position,projectile_actions
        if key in separated_positions:
            return separated_positions[key]
        separated_positions[key] = False
        for enemy,info in enemies:
            net = enemy.id in net_only
            if (not mundane_land_mover(enemy) or info.reach not in (1,2)
                    or info.ranged and not net or info.entangles and not net
                    or info.summons or info.disables or info.poison
                    or info.escalates or info.avoid_melee or visible_dangerous_weapon(enemy)):
                return False
            moves = ceil(4*info.movement_speed)
            limit = info.reach+moves
            if walking.distance(enemy,info,position,limit) <= max(limit,enemy.distance(position)):
                return False
            # Movement occurs before world_reacts (movement.cc:915,1058–1070;
            # main.cc:1293,1329). On success enemies react to the NEXT square.
            # Still protect the CURRENT square for one whole departure action
            # in case displacement fails; never assume an unseen bump moved us.
            if net and not walking.excludes_projectile_exposure(
                    enemy,(position,),ceil(projectile_actions*info.movement_speed),
                    PHYSICAL_NET_RANGE_UPPER_BOUND):
                return False
        separated_positions[key] = True
        return True
    if not sealed and not separated(obs.position,1):
        return None
    goals = set(targets) - {obs.position}
    occupied = {(enemy.x, enemy.y) for enemy in obs.monsters}
    queue = deque([(obs.position, "")])
    seen = {obs.position}
    while queue:
        position, first = queue.popleft()
        if first and position in goals:
            evidence = {"goal": list(position),"chosen": {"kind":"move","value":first}}
            if sealed:
                evidence.update(enclosed_hostile_ids=[enemy.id for enemy in obs.hostiles],
                                enclosure_evidence=memory.evidence(obs) if memory is not None else [])
            else:
                evidence.update(
                    separated_hostile_ids=[enemy.id for enemy in obs.hostiles],
                    separation_window_actions=4,
                    uncertainty="Finite walking bound through known barriers; unknown cells remain passable, no permanent cage or hidden position is inferred")
                if net_only:
                    dx,dy = DIRECTIONS[first]
                    evidence.update(
                        projectile_separation_evidence=[
                            {"hostile_id": enemy.id,
                             "capability": "examined ordinary physical throwing net only; zero HP damage, real restraint independent of Will",
                             "departure_window_actions": 1,
                             "poststep_window_actions": 4,
                             "optimistic_movement_steps": {
                                 "departure": ceil(info.movement_speed),
                                 "poststep": ceil(4*info.movement_speed)},
                             "projectile_range_upper_bound": PHYSICAL_NET_RANGE_UPPER_BOUND,
                             "player_endpoints": [list(obs.position),[obs.position[0]+dx,obs.position[1]+dy]],
                             "proof": "Every optimistic land shooter origin is out of range or known-wall-cut: current endpoint for one departure action (including displacement failure), next endpoint for four actions"}
                            for enemy,info in enemies if enemy.id in net_only],
                        uncertainty="Finite physical-arrival four-action bound at both endpoints; ordinary-net departure one-action/poststep four-action bounds. Unknown cells and water are projectile-open, actors and door costs ignored. Observed walls may change; no permanent cage or later safety is inferred")
            return Action("move",first,
                          ("Bypass examined melee using its observed wall enclosure; reassess historical uncertainty"
                           if sealed else "Take one dry nonapproaching step: physical arrival excluded for four actions, ordinary net control excluded for departure and a four-action next-square runway; reobserve"),
                          "bypass enclosure" if sealed else "bypass barrier",evidence)
        for key, (dx, dy) in DIRECTIONS.items():
            neighbor = position[0] + dx, position[1] + dy
            cell = cells.get(neighbor)
            if (neighbor in seen or neighbor in occupied or cell is None
                    or not cell.walkable or cell.cloud or cell.feature in CLOSED_DOORS
                    or not first and not cell.visible):
                continue
            if (not first and not sealed
                    and (not separated(neighbor,4)
                         or any(enemy.distance(neighbor) < enemy.distance(obs.position) for enemy in obs.hostiles))):
                continue
            seen.add(neighbor)
            queue.append((neighbor, first or key))
    return None


@dataclass
class EncounterProgress:
    """Bound melee luring by actual approach, not elapsed encounter time.

    Free inspections cannot spend/reset the budget. A sideways-moving distant
    foe, incoming damage, or another elapsed wait is not approach progress.
    """
    stalled_waits: int = 0
    limit: int = 3
    _observed: tuple | None = None

    @property
    def may_wait(self) -> bool:
        return self.stalled_waits < self.limit

    def observe(self, obs: Observation, old: Observation | None, action: Action | None) -> None:
        stamp = obs.location, obs.turn, obs.time, obs.position
        if stamp == self._observed:
            return
        self._observed = stamp
        if old is None or old.location != obs.location or not obs.hostiles:
            self.stalled_waits = 0
            return
        if obs.turn <= old.turn:
            return
        before = {enemy.id: enemy for enemy in old.hostiles}
        after = {enemy.id: enemy for enemy in obs.hostiles}
        approaching = any(enemy.id in before and enemy.distance(obs.position)
                          < before[enemy.id].distance(old.position) for enemy in obs.hostiles)
        defeated = bool(before.keys() - after.keys()) and not after.keys() - before.keys()
        if approaching or defeated or obs.position != old.position:
            self.stalled_waits = 0
        elif action is not None and action.kind == "wait" and action.objective in {"lure", "defend choke"}:
            self.stalled_waits += 1
