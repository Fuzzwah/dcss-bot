"""MiBe's public-map position memory, scoped to each native coordinate frame.

WebTiles re-origins map coordinates on level entry and reconnect. Only matching
unique immutable public features establish a translation; player positions never
do, since an unacknowledged command may have moved the character.
"""
from collections import Counter
from dataclasses import dataclass, field

from ..models import Observation
from ..terrain import DOWNSTAIRS, LAIR_ENTRANCE, LAIR_EXIT, UPSTAIRS


Position = tuple[int, int]
PUBLIC_ANCHORS = DOWNSTAIRS | UPSTAIRS | {60, LAIR_ENTRANCE, LAIR_EXIT}


@dataclass
class FloorMemory:
    visits: Counter[Position] = field(default_factory=Counter)
    blocked: set[Position] = field(default_factory=set)
    exhausted_frontiers: dict[Position, frozenset[Position]] = field(default_factory=dict)
    pickup_attempts: set[Position] = field(default_factory=set)
    equipment_ground: set[Position] = field(default_factory=set)
    supply_ground: set[Position] = field(default_factory=set)
    equipment_target: Position | None = None
    # Optional loot must not undo a withdrawal with no public exploration or
    # character progress. LOS loss and changing monster IDs are not progress.
    deferred_equipment: dict[Position, tuple[frozenset[Position], int]] = field(default_factory=dict)
    anchors: dict[int, frozenset[Position]] = field(default_factory=dict)
    known_positions: frozenset[Position] = frozenset()

    def translate(self, offset: Position) -> None:
        """Move every cached position, including frontier signature values."""
        dx, dy = offset

        def shifted(position: Position) -> Position:
            return position[0] + dx, position[1] + dy

        self.visits = Counter({shifted(pos): count for pos, count in self.visits.items()})
        self.blocked = {shifted(pos) for pos in self.blocked}
        self.exhausted_frontiers = {
            shifted(pos): frozenset(shifted(edge) for edge in edges)
            for pos, edges in self.exhausted_frontiers.items()
        }
        self.pickup_attempts = {shifted(pos) for pos in self.pickup_attempts}
        self.equipment_ground = {shifted(pos) for pos in self.equipment_ground}
        self.supply_ground = {shifted(pos) for pos in self.supply_ground}
        if self.equipment_target is not None:
            self.equipment_target = shifted(self.equipment_target)
        self.deferred_equipment = {
            shifted(pos): (frozenset(shifted(known) for known in signature), xl)
            for pos, (signature, xl) in self.deferred_equipment.items()
        }
        self.anchors = {feature: frozenset(shifted(pos) for pos in positions)
                        for feature, positions in self.anchors.items()}
        self.known_positions = frozenset(shifted(pos) for pos in self.known_positions)


@dataclass(frozen=True)
class FrameChange:
    # None means the cached positions were invalidated, not a zero translation.
    offset: Position | None


def anchor_translation(old: dict[int, frozenset[Position]],
                       new: dict[int, frozenset[Position]]) -> Position | None:
    """Require one unique match and agreement of every common anchor variant."""
    offset = None
    for feature in old.keys() & new.keys():
        if len(old[feature]) != 1 or len(new[feature]) != 1:
            return None
        before, after = next(iter(old[feature])), next(iter(new[feature]))
        candidate = after[0] - before[0], after[1] - before[1]
        if offset is not None and candidate != offset:
            return None
        offset = candidate
    return offset


@dataclass
class ExplorationMemory:
    current: FloorMemory = field(default_factory=FloorMemory)
    floors: dict[str, FloorMemory] = field(default_factory=dict)
    location: str = ""
    previous_turn: int = -1
    previous_position: Position | None = None
    last_move: Position | None = None

    def observe(self, obs: Observation) -> FrameChange | None:
        anchors: dict[int, set[Position]] = {}
        known = set()
        for cell in obs.cells:
            position = cell.x, cell.y
            if cell.feature:
                known.add(position)
            if cell.feature in PUBLIC_ANCHORS:
                anchors.setdefault(cell.feature, set()).add(position)
        public_anchors = {feature: frozenset(positions) for feature, positions in anchors.items()}
        changed = None
        entered = obs.location != self.location
        if entered:
            if self.location:
                self.floors[self.location] = self.current
            cached = self.floors.get(obs.location)
            self.current = cached if cached is not None else FloorMemory()
            self.location = obs.location
            if cached is not None:
                changed = self._restore(public_anchors)
        else:
            # Normal native updates accumulate known cells and retain anchors.
            # A disappearing known region or relocated anchor exposes a reset
            # even when reconnect leaves the player at the same native position.
            discontinuity = (not self.current.known_positions.issubset(known)
                             or any(not positions.issubset(public_anchors.get(feature, frozenset()))
                                    for feature, positions in self.current.anchors.items()))
            if discontinuity:
                changed = self._restore(public_anchors)
        if entered or changed is not None:
            self.last_move = None
            self.previous_position = None
            self.previous_turn = -1
        if (self.last_move is not None and obs.position != self.last_move
                and obs.turn == self.previous_turn):
            self.current.blocked.add(self.last_move)
        if obs.turn != self.previous_turn or obs.position != self.previous_position:
            self.current.visits[obs.position] += 1
        self.previous_turn = obs.turn
        self.previous_position = obs.position
        self.last_move = None
        self.current.anchors = public_anchors
        self.current.known_positions = frozenset(known)
        # The native public map also carries remembered item glyphs. Accept
        # those after reconnect or a native macro, not just its final FOV.
        # LOS loss and the player's @ glyph covering loot must not erase goals.
        self.current.supply_ground.update(
            (cell.x, cell.y) for cell in obs.cells
            if cell.feature and cell.glyph in {"?", "!"})
        self.floors[self.location] = self.current
        return changed

    def _restore(self, anchors: dict[int, frozenset[Position]]) -> FrameChange:
        offset = anchor_translation(self.current.anchors, anchors)
        if offset is None:
            self.current = FloorMemory()
        elif offset != (0, 0):
            self.current.translate(offset)
        return FrameChange(offset)
