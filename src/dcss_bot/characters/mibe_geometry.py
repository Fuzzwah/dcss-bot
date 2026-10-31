"""Optimistic walking lower bounds, never hidden paths or permanent cages.

Unknown cells are passable. Only observed solid walls/deep water/lava constrain
known mundane land bodies. Ignore doors' opening cost, other actors and hazards:
that can underestimate travel, never certify an unknown route blocked. A result
beyond the search window is a finite lower bound, not 'unreachable'.
"""
from collections import deque
from collections.abc import Mapping

from ..models import Cell, DIRECTIONS, Monster
from .mibe_mechanics import EnemyMechanics, mundane_land_mover


Position = tuple[int,int]
_PROJECTILE_WALLS = frozenset(range(5,14))  # including transparent solid walls11..13
_BLOCKED_LAND = _PROJECTILE_WALLS | {30,31}  # lava/deep water; shallow water32 is passable


def possible_projectile_path(source: Position, target: Position,
                             cells: Mapping[Position,Cell]) -> bool:
    """Overapproximate a straight, nonbouncing physical beam, not generic LOS.

    Pinned beam.cc:1327–1329 stops ordinary missiles at feat_is_solid;
    transparent walls are solid too. Unknown terrain and water stay open here.
    Native find_ray fixes each coordinate's sign (los.cc:631–646); ray.cc's
    diamond-grid advance visits adjacent cells on that fixed-direction ray.
    Thus every such ray gives a coordinate-monotone eight-neighbor path inside
    the endpoints' rectangle. Admit ALL those paths, even nonstraight ones and
    corner squeezes: their absence is a known-wall cut, their presence proves
    nothing about actual line of fire. No shortest-Chebyshev-ray claim is needed.
    Never apply this certificate to wall-affecting, exploding, bouncing,
    teleporting, smiting or unknown attacks.
    """
    sx = (target[0] > source[0]) - (target[0] < source[0])
    sy = (target[1] > source[1]) - (target[1] < source[1])
    queue = deque([source])
    seen = {source}
    while queue:
        x,y = queue.popleft()
        if (x,y) == target:
            return True
        for dx,dy in ((sx,0),(0,sy),(sx,sy)):
            neighbor = x+dx,y+dy
            if (neighbor in seen or dx and x == target[0]
                    or dy and y == target[1]):
                continue
            cell = cells.get(neighbor)
            if cell is not None and cell.feature in _PROJECTILE_WALLS:
                continue
            seen.add(neighbor)
            queue.append(neighbor)
    return False


class WalkingDistances:
    """Per-observation cache; callers replace it when public terrain updates."""
    def __init__(self, cells: Mapping[Position,Cell]):
        self.cells = cells
        self._fields: dict[tuple[Position,int],dict[Position,int]] = {}

    def reachable_origins(self, enemy: Monster, max_steps: int) -> Mapping[Position,int]:
        """Every optimistic land origin in the window; not legal move guidance.

        Callers must first establish an ordinary land body. Ignore actors,
        diagonal corner restrictions, door-opening cost and unknown terrain.
        """
        origin = enemy.x,enemy.y
        key = origin,max_steps
        distances = self._fields.get(key)
        if distances is None:
            distances = {origin:0}
            queue = deque([origin])
            while queue:
                position = queue.popleft()
                depth = distances[position]
                if depth >= max_steps:
                    continue
                for dx,dy in DIRECTIONS.values():
                    neighbor = position[0]+dx,position[1]+dy
                    cell = self.cells.get(neighbor)
                    if neighbor in distances or cell is not None and cell.feature in _BLOCKED_LAND:
                        continue
                    distances[neighbor] = depth+1
                    queue.append(neighbor)
            self._fields[key] = distances
        return distances

    def excludes_projectile_exposure(self, enemy: Monster, targets: tuple[Position,...],
                                     max_steps: int, projectile_range: int) -> bool:
        """Finite ordinary-physical-only certificate, capability checked outside.

        Include the initial shooter and every origin reachable after max_steps
        optimistic moves, even though moving consumes its own attack time.
        Check both player endpoints; authorize no later player action.
        """
        return all(max(abs(origin[0]-target[0]),abs(origin[1]-target[1])) > projectile_range
                   or not possible_projectile_path(origin,target,self.cells)
                   for origin in self.reachable_origins(enemy,max_steps) for target in targets)

    def distance(self, enemy: Monster, info: EnemyMechanics,
                 target: Position, max_steps: int) -> int:
        """Return a capped optimistic distance, not an inaccessible-target claim.

        Reach two uses min(walking-to-target, walking-to-reach-square + 2).
        Thus the result bounds walking distance, and result - reach also bounds
        attack arrival even across barriers. Reach squares ignore line of fire;
        contact already within reach keeps its exact Chebyshev distance.
        """
        direct = enemy.distance(target)
        if (info.reach not in (1,2) or not mundane_land_mover(enemy)
                or direct <= info.reach):
            return direct
        distances = self.reachable_origins(enemy,max_steps)
        distance = distances.get(target,max_steps+1)
        if info.reach == 2:
            # Walking to the target and subtracting reach is NOT a safe arrival
            # bound across a barrier. An attack square can be on its near side.
            # Ignore line of fire, and include every reachable square in range.
            # min(target distance, attack-square steps + reach) stays a walking
            # lower bound; subtracting reach cannot overestimate attack arrival.
            x,y = target
            attack_steps = min(distances.get((x+dx,y+dy),max_steps+1)
                               for dx in range(-2,3) for dy in range(-2,3))
            distance = min(distance,attack_steps+info.reach)
        return distance
