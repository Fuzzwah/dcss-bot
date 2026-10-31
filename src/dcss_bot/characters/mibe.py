"""Minotaur Berserker's deterministic rule-v3 survival policy."""
from collections import Counter, deque
import re

from ..danger import assess_danger, is_ranged
from ..models import Action, DIRECTIONS, Item, Monster, Observation
from ..state import plain
from .base import CharacterProfile


WEAPON_RANKS = {"executioner's axe": 18, "battleaxe": 15, "broad axe": 13,
                "war axe": 11, "hand axe": 7}
ARMOUR_RANKS = {"plate armour": 10, "chain mail": 8, "scale mail": 6,
                "ring mail": 5, "leather armour": 3, "animal skin": 2, "robe": 1}


HAZARDOUS_EQUIPMENT = ("distortion", "chaos", "draining", "pain", "harm",
                       "fragile", "angry", "*rage", "*corrode", "*slow",
                       "ponderous", "contam")


def equipment_kind(name: str) -> str | None:
    name = name.lower()
    if any(base in name for base in WEAPON_RANKS):
        return "wield"
    if any(base in name for base in ARMOUR_RANKS) or "dragon scales" in name:
        return "wear"
    return None


def equipment_score(name: str) -> int | None:
    """Small visible-name ranking; unknown enchantments/artefacts stay unknown."""
    name = name.lower()
    kind = equipment_kind(name)
    if not kind or any(mark in name for mark in ('"', "{", "}")):
        return None
    ranks = WEAPON_RANKS if kind == "wield" else ARMOUR_RANKS
    base = next((base for base in ranks if base in name), None)
    if base is None:
        return None
    # Require the enchantment displayed before the base type, never wire plus.
    plus = re.search(r"([+-]\d+)\s+" + re.escape(base), name)
    if not plus:
        return None
    score = ranks[base] + int(plus.group(1))
    if kind == "wield" and any(brand in name for brand in
                             ("flaming", "freezing", "venom", "electrocution", "vampiric")):
        score += 2
    return score


def equipment_upgrade(name: str, inventory: tuple[Item, ...]) -> bool:
    """Shared safe pickup/equip filter using only player-visible descriptions."""
    kind = equipment_kind(name)
    score = equipment_score(name)
    if score is None or any(mark in name.lower() for mark in HAZARDOUS_EQUIPMENT):
        return False
    equipped = [item for item in inventory if item.equipped]
    if kind == "wield":
        if any("distortion" in item.name.lower() for item in equipped):
            return False  # Unwielding distortion is itself dangerous.
        if any(base in name.lower() for base in ("battleaxe", "executioner's axe")) and any(
                shield in item.name.lower() for item in equipped
                for shield in ("buckler", "kite shield", "tower shield")):
            return False
    current = [equipment_score(item.name) for item in equipped if equipment_kind(item.name) == kind]
    if any(value is None for value in current):
        return False  # Do not replace an unrankable known equipped item.
    return score > max(current, default=0)


class RuleBasedAgent:
    identifier = "rule-v3"

    def __init__(self) -> None:
        self.visits: Counter = Counter()
        self.last_location = ""
        self.previous_hp: int | None = None
        self.previous_turn = -1
        self.previous_position: tuple[int, int] | None = None
        self.last_ability = -100
        self.last_equipment = -100
        self.equipment_attempts: set[tuple[str, int, str]] = set()
        self.lure_until = -1
        self.ranged_first_seen = -100
        self.ranged_last_seen = -100
        self.pickup_attempts: set = set()
        self.equipment_ground: set[tuple[int, int]] = set()
        self.blocked: set = set()
        self.exhausted_frontiers: dict[tuple[int, int], frozenset[tuple[int, int]]] = {}
        self.last_move: tuple[int, int] | None = None
        self.retreat_from_adjacent = False
        self.pursued = False
        self.previous_messages: tuple[str, ...] = ()

    def _neighbours(self, obs: Observation, cells: dict, position: tuple[int, int],
                    avoid_monsters=True, *, allow_clouds=False):
        occupied = {(m.x, m.y) for m in obs.monsters if not m.passive} if avoid_monsters else set()
        for key, (dx, dy) in DIRECTIONS.items():
            target = position[0] + dx, position[1] + dy
            cell = cells.get(target)
            if (cell and cell.walkable and (allow_clouds or not cell.cloud)
                    and target not in occupied and target not in self.blocked):
                yield key, target

    def _path(self, obs: Observation, cells: dict, targets: set, *, allow_clouds=False) -> str | None:
        queue = deque([(obs.position, None)])
        seen = {obs.position}
        while queue:
            position, first = queue.popleft()
            if position in targets and first:
                return first
            for key, target in self._neighbours(obs, cells, position, allow_clouds=allow_clouds):
                if target not in seen:
                    seen.add(target)
                    queue.append((target, first or key))
        return None

    @staticmethod
    def _unknown_edges(cells: dict, position: tuple[int, int]) -> frozenset[tuple[int, int]]:
        """Unknown neighbours that are not hidden behind a sealed wall corner."""
        x, y = position
        edges = set()
        for dx, dy in DIRECTIONS.values():
            target = x + dx, y + dy
            cell = cells.get(target)
            if cell is not None and cell.feature != 0:
                continue
            if dx and dy:
                sides = cells.get((x + dx, y)), cells.get((x, y + dy))
                if all(side is not None and side.feature != 0 and not side.walkable for side in sides):
                    continue
            edges.add(target)
        return frozenset(edges)

    def _frontiers(self, obs: Observation, cells: dict) -> set[tuple[int, int]]:
        """Try known boundary squares, never sweep already revealed interiors.

        Standing on a boundary has already exposed what this viewpoint can show.
        Retire only that exact viewpoint with that exact unknown boundary, not
        neighbouring viewpoints merely passed on the way. New local knowledge
        can make a previously exhausted viewpoint worth trying again.
        """
        frontiers = {}
        for position, cell in cells.items():
            if cell.walkable and position not in self.blocked:
                edges = self._unknown_edges(cells, position)
                if edges:
                    frontiers[position] = edges
        if obs.position in frontiers:
            self.exhausted_frontiers[obs.position] = frontiers[obs.position]
        return {position for position, edges in frontiers.items()
                if self.exhausted_frontiers.get(position) != edges}

    @staticmethod
    def _covered(cells: dict, shooter: Monster, position: tuple[int, int]) -> bool:
        """Conservative straight-lane heuristic, deliberately not Crawl LOS.

        Both rounding tracks must meet a known opaque wall/closed door. Unknown
        cells, clear walls, trees and creatures never count as reliable cover.
        """
        dx, dy = position[0] - shooter.x, position[1] - shooter.y
        length = max(abs(dx), abs(dy))
        if length <= 1:
            return False
        blocked = [False, False]
        for step in range(1, length):
            nx, ny = shooter.x * length + dx * step, shooter.y * length + dy * step
            for track, point in enumerate(((nx // length, ny // length),
                                           (-(-nx // length), -(-ny // length)))):
                cell = cells.get(point)
                if cell and cell.feature in {1, 2, 3, 5, 6, 7, 8, 9, 10}:
                    blocked[track] = True
        return all(blocked)

    def _exposure(self, obs: Observation, cells: dict, position: tuple[int, int]) -> int:
        return sum(m.threat + 1 for m in obs.hostiles
                   if is_ranged(m) and not self._covered(cells, m, position))

    def _cover(self, obs: Observation, cells: dict) -> str | None:
        """Find nearby visible cover without advancing through an enemy group."""
        current = self._exposure(obs, cells, obs.position)
        if not current:
            return None
        adjacent = sum(m.distance(obs.position) <= 1 for m in obs.hostiles)
        queue = deque([(obs.position, None, 0)])
        seen = {obs.position}
        options = []
        while queue:
            position, first, steps = queue.popleft()
            exposure = self._exposure(obs, cells, position)
            if first and exposure < current:
                options.append((exposure, steps, -self.visits[position], first))
                continue
            if steps == 3:
                continue
            for key, target in self._neighbours(obs, cells, position):
                if target in seen or not cells[target].visible or cells[target].feature == 1:
                    continue
                if sum(m.distance(target) <= 1 for m in obs.hostiles) > adjacent:
                    continue
                if self._exposure(obs, cells, target) > current:
                    continue
                # Do not first close on shooters just to reach speculative cover.
                if any(m.distance(target) < min(3, m.distance(obs.position)) for m in obs.hostiles):
                    continue
                seen.add(target)
                queue.append((target, first or key, steps + 1))
        return min(options)[3] if options else None

    def _retreat(self, obs: Observation, cells: dict) -> str | None:
        def risk(pos):
            distances = [m.distance(pos) for m in obs.hostiles]
            return (sum(d <= 1 for d in distances), self._exposure(obs, cells, pos),
                    -min(distances, default=9),
                    sum(max(0, 5 - d) * (m.threat + 1) for d, m in zip(distances, obs.hostiles)))
        current = risk(obs.position)
        options = [(risk(target), -self.visits[target], key)
                   for key, target in self._neighbours(obs, cells, obs.position)
                   if cells[target].visible and self.visits[target] > 0]
        options = [option for option in options if option[0] < current]
        if self.pursued:
            # The last adjacent foe followed our retreat. Distance alone is
            # not escape; it can grant free pursuit attacks. Require cover or
            # a formation improvement that still helps after one foe follows.
            options = [option for option in options
                       if option[0][1] < current[1] or option[0][0] + 1 < current[0]]
        return min(options)[2] if options else None

    def _consumable(self, obs: Observation, kind: str, names: tuple[str, ...], reason: str) -> Action | None:
        for name in names:
            for item in obs.inventory:
                if name in item.name.lower():
                    return Action(kind, item.letter, reason, "emergency recovery")
        return None


    def _equipment(self, obs: Observation) -> Action | None:
        if obs.hostiles or obs.turn - self.last_equipment < 12:
            return None
        candidates = [item for item in obs.inventory if not item.equipped
                      and equipment_upgrade(item.name, obs.inventory)
                      and (equipment_kind(item.name), item.slot, item.name) not in self.equipment_attempts]
        if not candidates:
            return None
        item = max(candidates, key=lambda i: (equipment_score(i.name), -i.slot))
        kind = equipment_kind(item.name)
        self.equipment_attempts.add((kind, item.slot, item.name))
        self.last_equipment = obs.turn
        return Action(kind, item.letter,
                      f"Try verified visible upgrade {item.name} on clear ground; confirm equipped state",
                      "equipment")

    async def decide(self, observation: Observation) -> Action:
        obs = observation
        if obs.dead or obs.game_over:
            return Action("pause", reason="Game over", objective="finished")
        if not obs.hp_max:
            return Action("pause", reason="No playable character observation")
        if obs.menu.get("tag") == "pickup":
            if obs.hostiles or obs.hp_ratio < .98:
                return Action("cancel", reason="Leave equipment pile while unsafe", objective="equipment")
            choices = []
            for entry in obs.menu.get("items", []):
                name = plain(entry.get("text", ""))
                hotkeys = entry.get("hotkeys", [])
                if not hotkeys or not equipment_upgrade(name, obs.inventory):
                    continue
                key = chr(hotkeys[0]) if isinstance(hotkeys[0], int) else hotkeys[0]
                if len(key) == 1 and key.isascii() and key.isalpha():
                    choices.append((equipment_score(name), key, name))
            if choices:
                _, key, name = max(choices)
                return Action("menu", key, f"Collect visible useful equipment: {name.strip()}", "equipment")
            return Action("cancel", reason="No verified useful equipment in this pile; leave it", objective="equipment")
        if obs.location != self.last_location:
            self.visits.clear()
            self.blocked.clear()
            self.exhausted_frontiers.clear()
            self.pickup_attempts.clear()
            self.equipment_ground.clear()
            self.lure_until = -1
            self.ranged_first_seen = self.ranged_last_seen = -100
            self.last_move = None
            self.retreat_from_adjacent = False
            self.pursued = False
            self.last_location = obs.location
        if self.last_move is not None and obs.position != self.last_move and obs.turn == self.previous_turn:
            self.blocked.add(self.last_move)
        adjacent_now = any(m.distance(obs.position) <= 1 for m in obs.hostiles)
        if not adjacent_now:
            self.pursued = False
        elif (self.retreat_from_adjacent and obs.turn > self.previous_turn
              and obs.position != self.previous_position):
            self.pursued = True
        self.retreat_from_adjacent = False
        if obs.turn != self.previous_turn or obs.position != self.previous_position:
            self.visits[obs.position] += 1
        danger = assess_danger(obs, self.previous_hp)
        self.previous_hp = obs.hp
        self.previous_turn = obs.turn
        self.previous_position = obs.position
        self.last_move = None
        new_messages = obs.messages
        for overlap in range(min(len(self.previous_messages), len(obs.messages)), 0, -1):
            if self.previous_messages[-overlap:] == obs.messages[:overlap]:
                new_messages = obs.messages[overlap:]
                break
        self.previous_messages = obs.messages
        cells = {(c.x, c.y): c for c in obs.cells}
        status = " ".join(obs.statuses).lower()
        berserk = any(s.lower() == "berserk" for s in obs.statuses)
        reason = "; ".join(danger.reasons)

        ranged = [m for m in obs.hostiles if is_ranged(m)]
        if ranged:
            if obs.turn - self.ranged_last_seen > 8:
                self.ranged_first_seen = obs.turn
            self.ranged_last_seen = obs.turn
        # Brief LOS loss and regenerated wire IDs must not restart the lure.
        close_after_lure = (len(obs.hostiles) == len(ranged) == 1
                            and ranged[0].threat < 2 and obs.hp_ratio >= .75
                            and not danger.emergency
                            and obs.turn - self.ranged_first_seen >= 12)

        if not berserk:
            if "pois" in status or "conf" in status:
                action = self._consumable(obs, "quaff", ("potion of curing", "potions of curing"), "Cure poison/confusion before it becomes fatal")
                if action:
                    return action
            if obs.hp_ratio < .55:
                action = self._consumable(obs, "quaff", ("heal wounds", "potion of curing", "potions of curing"), "Low HP: use known healing")
                if action:
                    return action
        if any(s.lower() == "fire" for s in obs.statuses):
            options = []
            for key, target in self._neighbours(obs, cells, obs.position):
                if not cells[target].visible or cells[target].feature == 1:
                    continue  # Opening a door is not a step that sheds fire.
                distances = [m.distance(target) for m in obs.hostiles]
                risk = (sum(d <= 1 for d in distances), self._exposure(obs, cells, target),
                        -min(distances, default=9), not self.visits[target], self.visits[target])
                options.append((risk, key))
            if options:
                return self._move(obs, min(options)[1], "Move on clear visible ground to shake off liquid fire",
                                  "emergency recovery")
            return Action("pause", reason="Liquid fire with no clear movement square; unsafe to wait or guess",
                          objective="blocked")
        if not berserk:
            if obs.hostiles and danger.emergency:
                if obs.hp_ratio < .35 and "tele" not in status:
                    action = self._consumable(obs, "read", ("teleportation",), "Emergency escape; teleport takes time, keep defending")
                    if action:
                        return action
                direction = self._cover(obs, cells) or self._retreat(obs, cells)
                if direction:
                    self.lure_until = obs.turn + 3
                    return self._move(obs, direction, reason + "; seek cover or safer known ground", "retreat")

        adjacent = sorted((m for m in obs.hostiles if m.distance(obs.position) <= 1),
                          key=lambda m: (-m.threat, m.id, m.name))
        if adjacent:
            can_berserk = not any(s in status for s in ("berserk", "exhaust", "-berserk", "slow", "conf"))
            if (can_berserk and obs.hp_ratio >= .55 and obs.turn - self.last_ability > 40
                    and (self.pursued or len(adjacent) >= 2 or any(m.threat >= 2 for m in adjacent))
                    and not any("slime creature" in m.name for m in obs.hostiles)):
                self.last_ability = obs.turn
                reason = ("Retreat did not gain separation; commit with Trog berserk before HP collapses"
                          if self.pursued else "Trog berserk for a committed close-range fight")
                return Action("ability", "a", reason, "combat")
            enemy = adjacent[0]
            delta = enemy.x - obs.position[0], enemy.y - obs.position[1]
            direction = next(k for k, d in DIRECTIONS.items() if d == delta)
            return Action("move", direction, f"Strike adjacent {enemy.name}; reassess after one attack", "combat")
        if obs.hostiles:
            if ranged and not close_after_lure and not self._exposure(obs, cells, obs.position) and obs.turn < self.lure_until:
                return Action("wait", reason="Briefly hold the covered lane to lure the shooter closer", objective="lure")
            direction = self._cover(obs, cells) if ranged and not close_after_lure else None
            if direction:
                self.lure_until = obs.turn + 3
                return self._move(obs, direction, "Break a ranged firing lane using nearby known cover; lure enemies closer",
                                  "retreat" if danger.emergency else "lure")
            direction = self._retreat(obs, cells) if danger.emergency else None
            if direction:
                return self._move(obs, direction, reason + "; retreat onto known ground", "retreat")
            # Visible reach-attack messages identify an immediate attacker
            # without consulting hidden weapon fields. Already-berserk combat
            # should also close safe distance rather than waste its duration.
            reaching = [m for m in obs.hostiles if m.distance(obs.position) == 2
                        and any(m.name.lower() in message.lower() and "you from afar" in message.lower()
                                for message in new_messages)]
            targets = [m for m in obs.hostiles if m in reaching or (berserk and m.threat < 2)
                       or (len(ranged) == 1 and m == ranged[0] and m.threat < 2 and obs.hp_ratio >= .5)]
            for target in sorted(targets, key=lambda m: (m.distance(obs.position), -m.threat, m.name, m.x, m.y)):
                options = [(target.distance(pos), self.visits[pos], key)
                           for key, pos in self._neighbours(obs, cells, obs.position)
                           if cells[pos].visible and cells[pos].feature != 1
                           and target.distance(pos) < target.distance(obs.position)
                           and all(m.id == target.id or m.distance(pos) > 1 for m in obs.hostiles)]
                if options:
                    return self._move(obs, min(options)[2],
                                      f"Close one visible step on {target.name}; avoid free ranged hits or wasted committed combat",
                                      "combat")
            if reaching:
                return Action("pause", reason="Active reach attacker with no safe attack or retreat route",
                              objective="blocked")
            return Action("wait", reason="Hold position; enemies visible, no safe advance", objective="combat")
        if obs.turn < self.lure_until:
            return Action("wait", reason="Briefly hold cover to lure the last visible shooter; do not immediately reopen its lane",
                          objective="lure")
        if obs.hp_ratio < .98 or berserk or any(s in status for s in ("pois", "slow", "exhaust")):
            return Action("wait", reason="Recover one turn with no visible hostiles", objective="recover")
        action = self._equipment(obs)
        if action:
            return action
        here = cells.get(obs.position)
        seen_here = [message for message in new_messages if "you see here" in message.lower()]
        equipment_here = any(equipment_upgrade(message, obs.inventory) for message in seen_here)
        if seen_here and not equipment_here:
            self.pickup_attempts.add(obs.position)  # A described non-upgrade needs no pickup inspection.
        self.equipment_ground.update(pos for pos, cell in cells.items()
                                     if cell.visible and cell.glyph in {"[", ")"})
        if here and (obs.position in self.equipment_ground or equipment_here) and obs.position not in self.pickup_attempts:
            self.pickup_attempts.add(obs.position)
            return Action("pickup", reason="Inspect visible equipment once on safe ground; select only useful pile items",
                          objective="equipment")
        equipment_targets = {pos for pos in self.equipment_ground - self.pickup_attempts
                             if pos in cells and cells[pos].visible}
        direction = self._path(obs, {pos: cell for pos, cell in cells.items() if cell.visible}, equipment_targets)
        if direction:
            return self._move(obs, direction, "Inspect visible equipment on clear nearby terrain before leaving it behind",
                              "equipment")
        frontiers = self._frontiers(obs, cells)
        direction = self._path(obs, cells, frontiers)
        if direction:
            return self._move(obs, direction, "Explore a reachable known boundary of unseen terrain", "explore")
        if frontiers and self._path(obs, cells, frontiers, allow_clouds=True):
            # Cloud blockage is not exploration completion. This route is only
            # a diagnostic; never send its movement key through the hazard.
            # The supervisor bounds waiting if the cloud never dissipates.
            return Action("wait", reason="Wait for clouds blocking unexplored terrain; do not rush downstairs",
                          objective="wait for terrain")
        stairs = {pos for pos, cell in cells.items() if cell.feature in {42, 43, 44}}
        if obs.position in stairs:
            return Action("stairs_down", reason="No useful reachable frontier; descend healthy with no visible enemies", objective="descend")
        direction = self._path(obs, cells, stairs)
        if direction:
            return self._move(obs, direction, "Reach known downstairs after exhausting useful frontiers", "descend")
        return Action("pause", reason="No safe reachable exploration or stairs; inspect recorded state", objective="blocked")

    def _move(self, obs: Observation, direction: str, reason: str, objective: str) -> Action:
        dx, dy = DIRECTIONS[direction]
        self.last_move = obs.position[0] + dx, obs.position[1] + dy
        self.retreat_from_adjacent = objective == "retreat" and any(
            m.distance(obs.position) <= 1 for m in obs.hostiles)
        return Action("move", direction, reason, objective)


PROFILE = CharacterProfile(
    identifier="mibe", abbreviation="MiBe", species="Minotaur", background="Berserker",
    god="Trog", weapon="hand axe", preferred_stat="strength", agent_factory=RuleBasedAgent,
    rc_options=(("autopickup", '$?!+"/='), ("default_autopickup", "true")),
)
