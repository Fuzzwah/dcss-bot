"""Minotaur Berserker's survival-first rule-v4 policy."""
from collections import deque
from dataclasses import replace
import re

from ..models import Action, DIRECTIONS, Monster, Observation
from ..terrain import CLOSED_DOORS, DOWNSTAIRS, SOLID_CORNER_SIDES
from ..state import plain
from .base import CharacterProfile
from .mibe_tactics import TacticalConfig, TacticalController
from .mibe_equipment import equipment_kind, equipment_score, equipment_upgrade
from .mibe_development import DevelopmentPlanner, development_menu_action, pickup_menu_action, supply_name
from .mibe_progression import ProgressionPlanner
from .mibe_exploration import ExplorationMemory
from .mibe_encounter import EnclosureMemory, enclosed_melee_bypass




def visible_equipment_name(text: str) -> str:
    text = plain(text).strip()
    text = re.sub(r"^[a-zA-Z]\s+[-+#]\s+", "", text)
    text = re.sub(r"^You see here\s+", "", text, flags=re.I)
    return text.removesuffix(".").strip()


class RuleBasedAgent:
    identifier = "rule-v4"

    def __init__(self, *, tactical_config: TacticalConfig | None = None) -> None:
        self.tactics = TacticalController(tactical_config)
        self.development = DevelopmentPlanner()
        self.progression = ProgressionPlanner()
        self.exploration = ExplorationMemory()
        self.enclosures = EnclosureMemory()
        self.tactical_avoided: set[tuple[int, int]] = set()
        self.last_equipment = -100
        self.equipment_attempts: set[tuple[str, int, str]] = set()
        self.previous_messages: tuple[str, ...] = ()
        self.cooldown_recovery_started: int | None = None
        self.native_explore_attempt: tuple[str, frozenset[tuple[int, int]], int] | None = None
        self.native_explore_before: Observation | None = None
        self.native_explore_complete: tuple[str, frozenset[tuple[int, int]], int] | None = None
        self.ability_inspection_key: tuple[str, int | None, tuple[str, ...]] | None = None
        self.ability_refresh_pending = False
        self.abilities_observed = False

    def _neighbours(self, obs: Observation, cells: dict, position: tuple[int, int],
                    avoid_monsters=True, *, allow_clouds=False, allowed_avoidance=frozenset()):
        occupied = {(m.x, m.y) for m in obs.monsters if not m.passive} if avoid_monsters else set()
        for key, (dx, dy) in DIRECTIONS.items():
            target = position[0] + dx, position[1] + dy
            cell = cells.get(target)
            if (cell and cell.walkable and (allow_clouds or not cell.cloud)
                    and target not in occupied and target not in self.exploration.current.blocked):
                if target in self.tactical_avoided and target not in allowed_avoidance:
                    continue
                yield key, target

    def _route(self, obs: Observation, cells: dict, targets: set, *, allow_clouds=False,
               allowed_avoidance=frozenset(), visible_first=False) -> tuple[str, tuple[int, int]] | None:
        queue = deque([(obs.position, None)])
        seen = {obs.position}
        while queue:
            position, first = queue.popleft()
            if position in targets and first:
                return first, position
            for key, target in self._neighbours(obs, cells, position, allow_clouds=allow_clouds,
                                                allowed_avoidance=allowed_avoidance):
                if visible_first and first is None and not cells[target].visible:
                    continue
                if target not in seen:
                    seen.add(target)
                    queue.append((target, first or key))
        return None
    def _path(self, obs: Observation, cells: dict, targets: set, *, allow_clouds=False,
              allowed_avoidance=frozenset(), visible_first=False) -> str | None:
        route = self._route(obs, cells, targets, allow_clouds=allow_clouds,
                            allowed_avoidance=allowed_avoidance, visible_first=visible_first)
        return route[0] if route is not None else None

    def _native_route_safe(self, obs: Observation, cells: dict) -> bool:
        """Native routing cannot enforce our remembered per-square exclusions."""
        return (
            not obs.hostiles and not obs.statuses and obs.hp == obs.hp_max
            and not self.tactical_avoided and not self.tactics.recent_hostiles
            and self.tactics.withdrawal is None
            and self.tactics.unseen_harm is None and self.tactics.unseen_contact is None
            and not any(cell.cloud for cell in cells.values())
            and not any(pos in cells and cells[pos].walkable
                        for pos in self.exploration.current.blocked))


    def _leave_avoided_area(self, obs: Observation, cells: dict) -> Action | None:
        """Find an exit without reducing nearest remembered-threat clearance."""
        if obs.hostiles or obs.position not in self.tactical_avoided:
            return None
        centres = tuple(entry.position for entry in self.tactics.avoided.get(obs.location, ())
                        if obs.xl < entry.xl + 3)
        if not centres:
            return None
        def clearance(position):
            return min(max(abs(position[0]-x),abs(position[1]-y)) for x,y in centres)
        minimum = clearance(obs.position)
        visible = {position: cell for position,cell in cells.items()
                   if cell.visible and cell.walkable and not cell.cloud and cell.feature not in CLOSED_DOORS}
        exits = visible.keys() - self.tactical_avoided
        allowed = {position for position in visible if clearance(position) >= minimum}
        direction = self._path(obs,visible,exits,allowed_avoidance=allowed)
        if direction:
            return self._move(obs,direction,"Leave the remembered threat zone on visible ground without reducing nearest-threat clearance",
                              "retreat")
        return None

    @staticmethod
    def _unknown_edges(cells: dict, position: tuple[int, int]) -> frozenset[tuple[int, int]]:
        """Unknown neighbours likely revealable from a known viewpoint."""
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
            if cell.walkable and position not in self.exploration.current.blocked:
                edges = self._unknown_edges(cells, position)
                if edges:
                    frontiers[position] = edges
        if obs.position in frontiers:
            self.exploration.current.exhausted_frontiers[obs.position] = frontiers[obs.position]
        return {position for position, edges in frontiers.items()
                if self.exploration.current.exhausted_frontiers.get(position) != edges}

    def _corner_probes(self, cells: dict, position: tuple[int, int]):
        """Bounded unknown diagonal attempts, not optimistic traversability."""
        x, y = position
        for key, (dx, dy) in DIRECTIONS.items():
            if not dx or not dy:
                continue
            target = x + dx, y + dy
            cell = cells.get(target)
            if (cell is None or cell.feature != 0 or target in self.exploration.current.blocked
                    or target in self.tactical_avoided):
                continue
            sides = cells.get((x+dx,y)), cells.get((x,y+dy))
            if all(side is not None and side.feature in SOLID_CORNER_SIDES for side in sides):
                yield key

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



    @staticmethod
    def _equipment_options(obs: Observation) -> dict:
        skills = {skill.name: skill.level for skill in obs.skills}
        return {"axes_skill": skills.get("Axes"), "shields_skill": skills.get("Shields")}

    def _equipment(self, obs: Observation) -> Action | None:
        if obs.hostiles or obs.turn - self.last_equipment < 12:
            return None
        options = self._equipment_options(obs)
        candidates = [item for item in obs.inventory if not item.equipped
                      and equipment_upgrade(item.name, obs.inventory, **options,
                                            attack_delay=obs.attack_delay, hostiles=obs.hostiles)
                      and (equipment_kind(item.name), item.slot, item.name) not in self.equipment_attempts]
        if not candidates:
            return None
        item = max(candidates, key=lambda i: (equipment_score(i.name, **options), -i.slot))
        kind = equipment_kind(item.name)
        self.equipment_attempts.add((kind, item.slot, item.name))
        self.last_equipment = obs.turn
        return Action(kind, item.letter,
                      f"Try verified visible upgrade {item.name} on clear ground; confirm equipped state",
                      "equipment")

    async def decide(self, observation: Observation) -> Action:
        action = await self._decide(observation)
        return self.tactics.routine(observation, action)

    async def _decide(self, observation: Observation) -> Action:
        obs = observation
        cooldown = any(status.casefold()=="-berserk" for status in obs.statuses)
        if not cooldown:
            self.cooldown_recovery_started = None
        self.development.observe(obs)
        if obs.dead or obs.game_over:
            return Action("pause", reason="Game over", objective="finished")
        if not obs.hp_max:
            return Action("pause", reason="No playable character observation")
        development_menu = development_menu_action(obs)
        if development_menu is not None:
            return development_menu
        pickup_menu = pickup_menu_action(obs, **self._equipment_options(obs))
        if pickup_menu is not None:
            if pickup_menu.kind == "cancel" and (obs.hostiles or obs.hp_ratio < .98):
                # A danger interruption is not an empty/full/failed pile.
                # Keep the supplies eligible after the encounter is resolved.
                floor = self.exploration.current
                floor.pickup_attempts.discard(obs.position)
                floor.deferred_equipment[obs.position] = floor.known_positions, obs.xl
            return pickup_menu
        frame_change = self.exploration.observe(obs)
        native_signature = obs.location, self.exploration.current.known_positions, obs.xl
        if self.native_explore_before is not None and obs.native_automation == "explore":
            before = self.native_explore_before
            progressed = (obs.turn > before.turn or obs.position != before.position
                          or obs.inventory != before.inventory
                          or bool(obs.hostiles) != bool(before.hostiles)
                          or self.native_explore_attempt != native_signature)
            if obs.native_result == "complete":
                self.native_explore_complete = native_signature
                self.native_explore_attempt = native_signature
            elif obs.native_result == "control" or (
                    obs.native_result in {"interrupted", "yielded"} and progressed):
                # Native item/feature stops and voluntary control interruption
                # do not mean exploration failed, even on already mapped ground.
                self.native_explore_attempt = None
            elif obs.native_result in {"partial", "unsupported_delay"}:
                self.native_explore_attempt = native_signature
            self.native_explore_before = None
        if frame_change is not None:
            self.tactics.rebase(obs.location, frame_change.offset)
            self.tactical_avoided.clear()
            self.native_explore_attempt = None
            self.native_explore_before = None
            self.native_explore_complete = None
            if frame_change.offset != (0, 0):
                self.enclosures.clear()
        new_messages = obs.messages
        for overlap in range(min(len(self.previous_messages), len(obs.messages)), 0, -1):
            if self.previous_messages[-overlap:] == obs.messages[:overlap]:
                new_messages = obs.messages[overlap:]
                break
        self.previous_messages = obs.messages
        seen_here = [message for message in new_messages if message.casefold().startswith("you see here ")]
        supplies_here = any(supply_name(visible_equipment_name(message)) for message in seen_here)
        if supplies_here:
            self.exploration.current.supply_ground.add(obs.position)
        cells = {(c.x, c.y): c for c in obs.cells}
        self.enclosures.observe(obs,cells)
        status = self.tactics.status(obs)
        berserk = any(s.lower() == "berserk" for s in obs.statuses)
        missing_abilities = self.abilities_observed and not obs.abilities
        self.abilities_observed = bool(obs.abilities)
        if obs.god.casefold() == "trog" and obs.input_mode == 1 and not obs.menu and not obs.prompt:
            # Native menus grey out powers during restrictions, including an
            # initial public inspection of a resumed confused/berserking game.
            if berserk or any(mark in status for mark in ("conf","silenc","paral","petr","sleep")):
                self.ability_refresh_pending = True
            else:
                ability_cooldowns = tuple(sorted(s.casefold() for s in obs.statuses
                                                if "berserk" in s.casefold() or "exh" in s.casefold()))
                key = obs.god, obs.piety_rank, ability_cooldowns
                previous_key = self.ability_inspection_key
                if self.ability_refresh_pending or previous_key != key or missing_abilities:
                    inspect = self.ability_refresh_pending or missing_abilities or previous_key is not None or not obs.abilities
                    self.ability_inspection_key = key
                    self.ability_refresh_pending = False
                    if inspect:
                        return Action("inspect","abilities","Read current native powers after piety, restriction or public-data loss; do not retain stale or missing availability",
                                      "assess abilities")

        self.tactics.observe(obs)
        self.tactical_avoided = self.tactics.avoided_positions(obs,cells)
        if (obs.hostiles and self.tactics.unseen_harm is None and self.tactics.unseen_contact is None
                and not self.tactics.unseen_search.active(obs)
                and not self.tactics.unseen_displacement_evidence(obs)):
            bypass_cells = {position:cell for position,cell in cells.items()
                            if position == obs.position or position not in self.tactical_avoided}
            def bypass_targets():
                # Goal discovery retires frontiers. Run it only after actual
                # enclosure or finite separation evidence permits a bypass.
                yield from self._frontiers(obs,cells)
                yield from self.tactics.safe_stairs(
                    obs,{position for position,cell in cells.items() if cell.feature in DOWNSTAIRS})
            bypass = enclosed_melee_bypass(obs,bypass_cells,bypass_targets(),memory=self.enclosures,
                                           walking=self.tactics.walking_geometry(obs))
            if bypass is not None:
                return replace(self._move(obs,bypass.value,bypass.reason,bypass.objective),assessment=bypass.assessment)
        tactical = self.tactics.decide(obs, cells, self.exploration.current.visits, self._covered)
        self.tactical_avoided = self.tactics.avoided_positions(obs, cells)
        if tactical is not None:
            if tactical.kind == "move" and tactical.objective != "escape restraint":
                moved = self._move(obs, tactical.value, tactical.reason, tactical.objective)
                return replace(moved, assessment=tactical.assessment)
            return tactical
        escape_memory = self._leave_avoided_area(obs,cells)
        if escape_memory is not None:
            return escape_memory
        if "pois" in status:
            return Action("wait", reason="Reassess poison each turn after immediate treatment options", objective="recover")
        if obs.hp_ratio < .98 or berserk or any(s in status for s in ("slow", "exhaust")):
            return Action("rest", reason="Use native 5-key recovery on clear ground; new danger interrupts rest",
                          objective="recover")
        if cooldown:
            if self.cooldown_recovery_started is None:
                self.cooldown_recovery_started = obs.turn
            if obs.turn-self.cooldown_recovery_started >= self.tactics.config.berserk_recovery_limit:
                return Action("pause", reason="Berserk cooldown failed to clear within the bounded recovery interval",
                              objective="blocked")
            return Action("rest", reason="Use native 5-key rest until Berserk recovery clears; do not pace every rested turn",
                          objective="recover")
        development = self.development.decide(obs)
        if development is not None:
            return development
        action = self._equipment(obs)
        if action:
            return action
        here = cells.get(obs.position)
        # Supply descriptions were remembered before any tactical return.
        equipment_here = any(equipment_upgrade(visible_equipment_name(message), obs.inventory, **self._equipment_options(obs),
                                              attack_delay=obs.attack_delay, hostiles=obs.hostiles)
                             for message in seen_here)
        if seen_here and not equipment_here and not supplies_here:
            self.exploration.current.pickup_attempts.add(obs.position)
        self.exploration.current.equipment_ground.update(pos for pos, cell in cells.items()
                                     if cell.visible and cell.glyph in {"[", ")", "="})
        floor = self.exploration.current
        if (here and not here.cloud and (obs.position in floor.equipment_ground or obs.position in floor.supply_ground
                      or equipment_here or supplies_here)
                and obs.position not in floor.pickup_attempts):
            floor.pickup_attempts.add(obs.position)
            floor.equipment_target = None
            return Action("pickup", reason="Collect spotted supplies and inspect useful equipment on safe ground",
                          objective="supplies" if obs.position in floor.supply_ground else "equipment")
        # Keep unseen remembered loot reachable, but not when the same pursuit
        # just forced a deliberate withdrawal. Only newly mapped terrain or XL
        # progress permits another optional attempt; LOS and actor IDs do not.
        floor = self.exploration.current
        progress = floor.known_positions, obs.xl
        supply_targets = {pos for pos in floor.supply_ground - floor.pickup_attempts
                          if pos in cells and floor.deferred_equipment.get(pos) != progress}
        route = self._route(obs, cells, supply_targets, visible_first=True)
        if route is not None:
            direction, floor.equipment_target = route
            return self._move(obs, direction,
                              "Collect spotted supplies before exploring farther; use known safe ground",
                              "supplies")
        equipment_targets = {pos for pos in floor.equipment_ground - floor.pickup_attempts
                             if pos in cells and floor.deferred_equipment.get(pos) != progress}
        route = self._route(obs, cells, equipment_targets, visible_first=True)
        if route is not None:
            direction, floor.equipment_target = route
            return self._move(obs, direction, "Reach previously seen equipment via a visible clear step before leaving it behind",
                              "equipment")
        native_done = self.native_explore_complete == native_signature
        frontiers = set() if native_done else self._frontiers(obs, cells)
        progression = self.progression.choose(
            obs, cells, lambda targets: self._path(obs, cells, targets),
            lambda targets: self.tactics.safe_stairs(obs, targets), explored=False)
        if progression is not None:
            if progression.kind == "move":
                return self._move(obs, progression.value, progression.reason, progression.objective)
            return progression
        direction = self._path(obs, cells, frontiers)
        # Native o handles ordinary exploration and native item greed. Do not
        # let its route bypass our remembered dangers or optional-loot retreat.
        # Refused/no-progress exploration uses the manual fallback. Native
        # completion is authoritative; do not follow it with speculative probes.
        native_safe = (
            self._native_route_safe(obs, cells)
            and not any(signature == progress for signature in floor.deferred_equipment.values()))
        if native_safe and not native_done and self.native_explore_attempt != native_signature:
            self.native_explore_attempt = native_signature
            self.native_explore_before = obs
            self.exploration.last_move = None
            return Action("explore", reason="Use native o as the primary safe explorer; resume after benign stops and obey native completion",
                          objective="explore")
        if direction:
            return self._move(obs, direction, "Explore a reachable known boundary of unseen terrain", "explore")
        if frontiers and self._path(obs, cells, frontiers, allow_clouds=True):
            # Cloud blockage is not exploration completion. This route is only
            # a diagnostic; never send its movement key through the hazard.
            # The supervisor bounds waiting if the cloud never dissipates.
            return Action("wait", reason="Wait for clouds blocking unexplored terrain; do not rush downstairs",
                          objective="wait for terrain")
        # Native LOS can leave a diagonal floor unknown between two walls even
        # after every known viewpoint is exhausted. Try one movement key and
        # observe; failed no-turn moves are retired by normal blocked tracking.
        probes = () if native_done else tuple(self._corner_probes(cells, obs.position))
        if probes:
            return self._move(obs, probes[0], "Probe one unseen wall corner on safe ground; observe before continuing", "explore")
        corner_views = set() if native_done else {
            position for position, cell in cells.items()
            if cell.walkable and any(self._corner_probes(cells, position))}
        direction = self._path(obs, cells, corner_views)
        if direction:
            return self._move(obs, direction, "Reach a known viewpoint for a bounded unseen-corner probe", "explore")
        progression = self.progression.choose(
            obs, cells, lambda targets: self._path(obs, cells, targets),
            lambda targets: self.tactics.safe_stairs(obs, targets), explored=True)
        if progression is not None:
            if progression.kind == "move":
                return self._move(obs, progression.value, progression.reason, progression.objective)
            return progression
        stairs = self.tactics.safe_stairs(obs, {pos for pos, cell in cells.items() if cell.feature in DOWNSTAIRS})
        if obs.position in stairs:
            return Action("stairs_down", reason="No useful reachable frontier; descend healthy with no visible enemies", objective="descend")
        route = self._route(obs, cells, stairs)
        if route is not None:
            direction, target = route
            if self._native_route_safe(obs, cells):
                self.exploration.last_move = None
                floor.equipment_target = None
                return Action("travel_downstairs", f"{target[0]},{target[1]}",
                              "Use native map travel to the selected safe downstairs; observe arrival before descending",
                              "descend")
            return self._move(obs, direction, "Reach known downstairs by a guarded step; native routing cannot enforce remembered hazards",
                              "descend")
        if stairs and self._path(obs, cells, stairs, allow_clouds=True):
            # As with a frontier, a known useful stair behind a transient
            # cloud is not a dead end. Keep stair bans and every other route
            # restriction intact; only wait and reobserve, never cross it.
            return Action("wait", reason="Wait for clouds blocking safe known downstairs; do not return through an unsafe stair",
                          objective="wait for terrain")
        reevaluate = self.tactics.stair_reevaluation.choose(
            obs,cells,lambda targets:self._path(obs,cells,targets),
            self.tactics.unsafe_stairs,exhausted=True)
        if reevaluate is not None:
            if reevaluate.kind == "move":
                return self._move(obs,reevaluate.value,reevaluate.reason,reevaluate.objective)
            return reevaluate
        return Action("pause", reason="No safe reachable exploration or stairs; inspect recorded state", objective="blocked")

    def _move(self, obs: Observation, direction: str, reason: str, objective: str) -> Action:
        floor = self.exploration.current
        if objective in {"bypass barrier", "bypass enclosure", "planned retreat", "retreat", "lure"}:
            if floor.equipment_target is not None:
                floor.deferred_equipment[floor.equipment_target] = floor.known_positions, obs.xl
        elif objective not in {"equipment", "supplies"}:
            floor.equipment_target = None
        dx, dy = DIRECTIONS[direction]
        self.exploration.last_move = obs.position[0] + dx, obs.position[1] + dy
        return Action("move", direction, reason, objective)


PROFILE = CharacterProfile(
    identifier="mibe", abbreviation="MiBe", species="Minotaur", background="Berserker",
    god="Trog", weapon="hand axe", preferred_stat="strength", agent_factory=RuleBasedAgent,
    rc_options=(("autopickup", '$?!+"/='), ("default_autopickup", "true"),
                ("travel_delay", "20"), ("explore_delay", "20"),
                ("rest_delay", "5"), ("tile_runrest_rate", "17")),
)
