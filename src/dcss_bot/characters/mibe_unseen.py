"""Bounded unseen-command evidence; no synthetic monsters or invisible coordinates."""
from dataclasses import dataclass, field

from ..models import Action, Cell, DIRECTIONS, Observation
from .mibe_mechanics import _UNSEEN_MELEE, confirmed_unseen_bump, confirmed_unseen_kill


@dataclass
class FailedUnseenDisplacements:
    """Short-lived facts about failed walking commands, not hidden occupants."""
    origin: tuple[str, tuple[int, int]] | None = None
    attempts: dict[tuple[int, int], int] = field(default_factory=dict)

    def reset(self) -> None:
        self.origin = None
        self.attempts.clear()

    def rebase(self, location: str, offset: tuple[int, int] | None) -> None:
        if self.origin is None or self.origin[0] != location:
            return
        if offset is None:
            self.reset()
            return
        dx, dy = offset
        self.origin = location, (self.origin[1][0] + dx, self.origin[1][1] + dy)
        self.attempts = {(x + dx, y + dy): turn for (x, y), turn in self.attempts.items()}

    def observe(self, obs: Observation, old: Observation | None,
                action: Action | None, messages: tuple[str, ...]) -> None:
        origin = obs.location, obs.position
        if self.origin != origin:
            self.reset()
            self.origin = origin
        self.attempts = {position: turn for position, turn in self.attempts.items()
                         if turn <= obs.turn <= turn + 2}
        if (old is None or action is None or action.kind != "move"
                or action.objective in {"escape restraint", "defend unseen"} or action.value not in DIRECTIONS
                or old.location != obs.location or obs.turn != old.turn + 1
                or old.position != obs.position or old.input_mode != 1 or old.menu or old.prompt):
            return
        statuses = " ".join((*old.statuses, *obs.statuses,
                             *(entry.text for entry in (*old.status_details, *obs.status_details)))).casefold()
        if any(mark in statuses for mark in
               ("conf", "held", "net", "web", "constr", "petr", "paral", "asleep")):
            return
        dx, dy = DIRECTIONS[action.value]
        target = old.position[0] + dx, old.position[1] + dy
        cell = next((cell for cell in old.cells if (cell.x, cell.y) == target), None)
        if (cell is None or not cell.visible or not cell.walkable or cell.cloud
                or any((monster.x, monster.y) == target for monster in old.monsters)):
            return
        if (any(_UNSEEN_MELEE.search(message) for message in messages)
                and not any(phrase in message for message in messages for phrase in
                            ("You swing at nothing.", "You attack empty space.",
                             "You feel something solid in that direction."))):
            # An axe may also hit secondary targets. The unchanged player
            # position proves only that this walk did not displace the player;
            # anonymous melee does not locate or identify any of its victims.
            self.attempts[target] = obs.turn

    def active(self, obs: Observation) -> bool:
        return (self.origin == (obs.location, obs.position)
                and any(turn <= obs.turn <= turn + 2 for turn in self.attempts.values()))

    def evidence(self, obs: Observation) -> list[dict]:
        if self.origin != (obs.location, obs.position):
            return []
        return [{"square": position, "turn": turn, "age": obs.turn - turn,
                 "source": "normal walk did not displace player; fresh anonymous outgoing melee"}
                for position, turn in sorted(self.attempts.items()) if turn <= obs.turn <= turn + 2]


@dataclass
class UnseenMeleeSearch:
    origin: tuple[str, tuple[int, int]] | None = None
    attempted: set[str] = field(default_factory=set)
    confirmed: tuple[tuple[int, int], int] | None = None
    last_signal: int = -1

    def reset(self) -> None:
        self.origin = None
        self.attempted.clear()
        self.confirmed = None
        self.last_signal = -1

    def rebase(self, location: str, offset: tuple[int, int] | None) -> None:
        if self.origin is None or self.origin[0] != location:
            return
        if offset is None:
            self.reset()
            return
        dx, dy = offset
        self.origin = location, (self.origin[1][0] + dx, self.origin[1][1] + dy)
        if self.confirmed is not None:
            position, turn = self.confirmed
            self.confirmed = (position[0] + dx, position[1] + dy), turn

    def observe(self, obs: Observation, old: Observation | None,
                action: Action | None, *, fresh_melee: bool) -> None:
        origin = obs.location, obs.position
        if self.origin != origin or obs.turn > self.last_signal + 2:
            self.reset()
            self.origin = origin
        if fresh_melee:
            self.last_signal = obs.turn
        if old is None or action is None or action.objective != "defend unseen":
            return
        if action.kind not in {"move", "attack"} or action.value not in DIRECTIONS:
            return
        dx, dy = DIRECTIONS[action.value]
        target = old.position[0] + dx, old.position[1] + dy
        if confirmed_unseen_bump(old, obs, action, target):
            # Native contact may be an axe-cleave secondary target, not an
            # occupant of this command's square. Retry this successful command
            # once; never infer a monster's position or identity from it.
            self.confirmed = target, obs.turn
            self.attempted.discard(action.value)
            self.last_signal = obs.turn
        elif confirmed_unseen_kill(old, obs, action, target):
            self.confirmed = None
        elif obs.turn > old.turn:
            self.confirmed = None

    def active(self, obs: Observation) -> bool:
        return (self.origin == (obs.location, obs.position) and self.confirmed is not None
                and obs.turn <= self.confirmed[1] + 2)

    def choose(self, obs: Observation, cells: dict[tuple[int, int], Cell],
               preferred: tuple[int, int] | None = None, *, search_all: bool = False) -> str | None:
        if self.origin != (obs.location, obs.position):
            return None
        occupied = {(monster.x, monster.y) for monster in obs.monsters}
        confirmed = self.confirmed[0] if self.active(obs) else None
        choices = []
        for key, (dx, dy) in DIRECTIONS.items():
            position = obs.position[0] + dx, obs.position[1] + dy
            cell = cells.get(position)
            if (not search_all and position not in {confirmed, preferred}
                    or key in self.attempted or position in occupied or cell is None
                    or not cell.visible or not cell.walkable or cell.cloud):
                continue
            choices.append((position != confirmed, position != preferred, len(choices), key))
        return min(choices)[-1] if choices else None
