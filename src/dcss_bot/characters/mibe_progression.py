"""MiBe's initial Dungeon/Lair route, without pretending to plan three runes.

Pinned branch-data.h: Lair is five levels, enters from D:8–11. Other branch
entrances are not candidates. Readiness thresholds below are policy, not rules.
Current-floor entrance targets follow the latest map frame, not stale coordinates.
"""
from ..item_names import public_item_name
from ..models import Action, Observation
from ..terrain import LAIR_ENTRANCE, LAIR_EXIT, UPSTAIRS




class ProgressionPlanner:
    def __init__(self):
        self.lair_entrances: dict[str, set[tuple[int, int]]] = {}
        self.explored_lair_floors: set[int] = set()
        self.returning_from_lair = False

    @staticmethod
    def ready_for_lair(obs: Observation) -> bool:
        poison_defense = any(resistance.name.casefold() in {"rpois", "poison", "poison resistance"}
                             and resistance.level > 0 for resistance in obs.resistances)
        curing = any(item.category in {None, "potion"}
                     and public_item_name(item.name) == "potion of curing"
                     for item in obs.inventory)
        return obs.xl >= 10 and obs.ac is not None and obs.ac >= 12 and (poison_defense or curing)

    def observe(self, obs, cells):
        entrances = {position for position, cell in cells.items() if cell.feature == LAIR_ENTRANCE}
        self.lair_entrances[obs.location] = entrances

    def choose(self, obs, cells, path, safe_stairs, *, explored):
        if obs.hostiles or obs.hp_ratio < .98:
            return None
        self.observe(obs, cells)
        if obs.place == "Dungeon" and self.ready_for_lair(obs) and not self.returning_from_lair:
            targets = safe_stairs(self.lair_entrances.get(obs.location, set()))
            if obs.position in targets:
                return Action("stairs_down", reason="Enter known Lair with adequate development, armour and a poison response", objective="enter Lair")
            direction = path(targets)
            if direction:
                return Action("move", direction, "Reach the known Lair entrance rather than enter a harder branch", "enter Lair")
        if not explored:
            return None
        if obs.place == "Lair":
            self.explored_lair_floors.add(obs.depth)
        if obs.place not in {"Dungeon", "Lair"}:
            exits = safe_stairs({position for position, cell in cells.items() if cell.feature in UPSTAIRS})
            if obs.position in exits:
                return Action("stairs_up", reason="Leave an unplanned dangerous branch on clear ground", objective="return to route")
            return None
        if obs.place == "Lair" and obs.depth >= 5:
            self.returning_from_lair = True
        if obs.place == "Lair" and self.returning_from_lair:
            exits = safe_stairs({position for position, cell in cells.items() if cell.feature in UPSTAIRS | {LAIR_EXIT}})
            if obs.position in exits:
                return Action("stairs_up", reason="Useful reachable Lair frontier exhausted; return instead of entering a rune or Slime branch", objective="return from Lair")
            direction = path(exits)
            if direction:
                return Action("move", direction, "Reach known upstairs after the initial Lair progression objective", "return from Lair")
        return None
