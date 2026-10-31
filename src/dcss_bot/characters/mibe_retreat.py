"""Short moving forecasts towards public withdrawal anchors, not escape promises.

Only the next step is executable: reobserve and replan after every movement.
Remembered visited terrain may extend a route, but is never claimed currently
clear. Ordinary upstairs are destinations here; traversal/arrival safety belongs
to the caller. Cover uses the caller's conservative public-terrain heuristic,
not a reconstructed Crawl LOS or a guarantee against a moving shooter.
"""
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import ceil

from ..models import Cell, DIRECTIONS, Monster, Observation
from ..terrain import CLOSED_DOORS, UPSTAIRS
from .mibe_mechanics import EnemyMechanics, OPPORTUNITY_CHANCE, mechanics, plain_melee_attacks
from .mibe_geometry import WalkingDistances


Position = tuple[int, int]
_MAX_ROUTE = 256
_MAX_NODES = 4096
_COVER_STEPS = 3
_OPAQUE = frozenset({1, 2, 3, 5, 6, 7, 8, 9, 10})


@dataclass(frozen=True)
class WithdrawalPlan:
    path: tuple[Position, ...]
    goal: Position
    goal_kind: str
    horizon: float
    expected_loss: float
    burst_loss: float
    uncertainty: tuple[str, ...]
    reason: str


def _distance(left: Position, right: Position) -> int:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def _move_time(obs: Observation) -> float:
    # Native Slow and Haste/Berserk affect movement; -Berserk is cooldown only.
    statuses = {status.casefold() for status in obs.statuses}
    duration = 1.5 if "slow" in statuses else 1.
    if statuses & {"haste", "berserk"}:
        duration *= 2 / 3
    return duration


def _budgetable_interceptor(obs, enemy, info):
    return (0 < info.melee <= min(3, obs.hp * .1) and info.reach == 1
            and not any((info.ranged, info.disables, info.summons, info.poison,
                         info.escalates, info.avoid_melee, info.entangles))
            and not enemy.statuses and plain_melee_attacks(enemy))


def _contact_possible(enemy, info, point, duration, walking):
    limit = info.reach + ceil(duration * info.movement_speed - 1e-9)
    return walking.distance(enemy, info, point, limit) <= limit


def _forecast(obs, cells, path, enemies, covered, move_time, walking):
    """Bound energy left for attacks after travel, along successive moves.

    Unknown movement retains Chebyshev travel. Examined mundane land bodies may
    use a walking lower bound through known barriers, with unknown cells open.
    Earlier exposure is retained when a later tile increases distance.
    Ranged burst assumes every potential shot lands, even at apparent cover.
    """
    avoidance = min(.70, max(.25, .72 - (obs.ev if obs.ev is not None else 8) * .018
                            - (obs.sh or 0) * .008))
    expected = burst = 0.
    for enemy, info in enemies:
        envelope = weighted = opportunities = 0.
        budgetable = _budgetable_interceptor(obs, enemy, info)
        melee_possible = False
        for step, position in enumerate(path, 1):
            if budgetable and _contact_possible(enemy, info, position, step * move_time, walking):
                opportunities += 1
            distance = enemy.distance(position)
            limit = info.reach+ceil(step*move_time*info.movement_speed)
            walking_distance = walking.distance(enemy,info,position,limit)
            melee_travel = max(0,walking_distance-info.reach)/max(.1,info.movement_speed)
            ranged_travel = (max(0,distance-info.ranged_range)/max(.1,info.movement_speed)
                             if info.ranged else float("inf"))
            available = max(0.,step*move_time-min(melee_travel,ranged_travel))*info.speed
            melee_possible |= step*move_time-melee_travel > 1e-9
            increment = max(0., available - envelope)
            envelope = max(envelope, available)
            # The heuristic reduces the mean only, never the survival bound.
            apparent_cover = info.ranged and covered(cells, enemy, position)
            weighted += increment * (.5 if apparent_cover else 1.)
        # Ranged and melee attack rows are alternatives, never a combined hit.
        # If contact becomes possible, use the larger alternative for the full
        # exposure envelope rather than pretend that earlier shots remove it.
        damage = (max(info.ranged_max, info.melee if melee_possible else 0.)
                  if info.ranged else info.melee)
        mean = (max(info.ranged_mean,info.melee_mean if melee_possible else 0.)
                if info.ranged else info.melee_mean)
        per_hit = max(1.,mean-(obs.ac or 0)/3) if mean > 0 else 0.
        enemy_expected = weighted * per_hit * avoidance
        if info.poison and not any(r.name.casefold() in {"rpois", "poison"} and r.level > 0
                                   for r in obs.resistances):
            enemy_expected += weighted * 2
        if info.disables:
            enemy_expected *= 1.35
        if info.summons:
            enemy_expected *= 1.3
        # A pursuing nuisance can spend its movement energy and still make an
        # opportunity attack. Charge those separately; never call this free.
        expected += enemy_expected + opportunities * info.melee * OPPORTUNITY_CHANCE
        burst += max(enemy_expected, ceil(max(0., envelope - 1e-9)) * damage) + opportunities * info.melee
    return round(expected, 2), round(burst, 2)


def plan_withdrawal(
    obs: Observation,
    cells: Mapping[Position, Cell],
    visits: Mapping[Position, int],
    covered: Callable[[Mapping[Position, Cell], Monster, Position], bool],
    *,
    target: Position | None = None,
    max_steps: int = 3,
    walking: WalkingDistances | None = None,
) -> WithdrawalPlan | None:
    """Choose ordinary upstairs or nearby apparent cover on known safe ground.

    Search is bounded to 4096 positions and 256 moves. Only the first max_steps
    *moving* actions are forecast, with time in ordinary ten-aut player actions.
    Expected loss must remain below 35% of current HP, burst below 80%; neither
    is a guarantee. Strong, reaching and disabling interceptors are refused.
    At most one examined weak plain-melee interceptor per forecast step may be
    damage-budgeted, including opportunity attacks. A pinned target must still
    be a credible anchor; it never authorizes arbitrary travel.
    """
    if max_steps < 1 or obs.hp <= 0 or obs.dead or obs.game_over:
        return None
    enemies: list[tuple[Monster, EnemyMechanics]] = []
    for enemy in obs.hostiles:
        info = mechanics(enemy)
        if info.reach < 2 and any(enemy.name.casefold() in message.casefold()
                                  and "you from afar" in message.casefold()
                                  for message in obs.messages[-5:]):
            info = replace(info,reach=2)
        enemies.append((enemy,info))
    move_time = _move_time(obs)
    walking = walking or WalkingDistances(cells)
    contacts = [(enemy, info) for enemy, info in enemies
                if enemy.distance(obs.position) <= info.reach]
    if len(contacts) > 1 or any(not _budgetable_interceptor(obs, enemy, info)
                                for enemy, info in contacts):
        return None
    occupied = {(enemy.x, enemy.y) for enemy in obs.monsters}
    shooters = tuple(enemy for enemy,info in enemies if info.ranged or info.summons)
    # Observed visibility refutes any cover hint at the current player square.
    # A goal must newly break at least one current firing lane.
    queue = deque([(obs.position, ())])
    seen = {obs.position}
    candidates = []
    while queue:
        position, path = queue.popleft()
        if path:
            cell = cells[position]
            kind = "upstairs" if cell.feature in UPSTAIRS else ""
            if (not kind and len(path) <= _COVER_STEPS and shooters
                    and any(covered(cells, enemy, position) for enemy in shooters)
                    and any((side := cells.get((position[0] + dx, position[1] + dy)))
                            is not None and side.feature in _OPAQUE
                            for dx, dy in DIRECTIONS.values())):
                kind = "cover"
            if kind and (target is None or position == target):
                forecast = path[:max_steps]
                expected, burst = _forecast(obs,cells,forecast,enemies,covered,move_time,walking)
                if expected < obs.hp * .35 and burst < obs.hp * .8:
                    remembered = any(not cells[step].visible for step in path)
                    unvisited = sum(not visits.get(step, 0) for step in path)
                    uncertainty = []
                    if remembered:
                        uncertainty.append("remembered visited terrain beyond LOS; current occupancy and clouds unknown")
                    if shooters:
                        uncertainty.append("casters/summoners may move or retain other attacks; cover is a terrain heuristic, not verified LOS")
                    if enemies:
                        uncertainty.append("enemy energy phase and damage rolls unknown; walking bounds keep unknown cells passable")
                    if any(_budgetable_interceptor(obs, enemy, info)
                           and any(_contact_possible(enemy, info, point, step * move_time, walking)
                                   for step, point in enumerate(forecast, 1))
                           for enemy, info in enemies):
                        uncertainty.append("weak plain-melee interception is damage-budgeted, including possible opportunity attacks")
                    if any(info.summons for _,info in enemies):
                        uncertainty.append("summoning can introduce faster or adjacent hostiles; no hypothetical summon positions are invented")
                    if kind == "upstairs":
                        uncertainty.append("upstairs traversal, followers and destination safety not forecast")
                    if len(path) > len(forecast):
                        uncertainty.append("route after forecast horizon not scored; reobserve after each step")
                    reason = ("Reach known ordinary upstairs" if kind == "upstairs"
                              else "Reach nearby apparent firing-lane cover")
                    if enemies and all(not info.ranged and abs(info.movement_speed * move_time - 1.) < 1e-9
                                       for _, info in enemies) and not burst:
                        reason += "; preserve nonadjacent equal-speed pursuit gap"
                    plan = WithdrawalPlan(path, position, kind, len(forecast) * move_time,
                                          expected, burst, tuple(uncertainty), reason)
                    candidates.append(((burst, expected, unvisited, kind != "upstairs", len(path), position), plan))
        if len(path) >= _MAX_ROUTE:
            continue
        neighbors = [(position[0] + dx, position[1] + dy) for dx, dy in DIRECTIONS.values()]
        neighbors.sort(key=lambda point: (not visits.get(point, 0),
                                         -min((enemy.distance(point) for enemy, _ in enemies), default=0), point))
        for point in neighbors:
            cell = cells.get(point)
            if (point in seen or len(seen) >= _MAX_NODES or point in occupied or cell is None
                    or not cell.walkable or cell.cloud or cell.feature in CLOSED_DOORS
                    or (not path and not cell.visible)
                    or (not cell.visible and not visits.get(point, 0))):
                continue
            if len(path) < max_steps:
                contacts = [(enemy, info) for enemy, info in enemies
                            if _contact_possible(enemy, info, point, (len(path)+1)*move_time, walking)]
                if len(contacts) > 1 or any(not _budgetable_interceptor(obs, enemy, info)
                                          for enemy, info in contacts):
                    continue
            seen.add(point)
            queue.append((point, (*path, point)))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None
