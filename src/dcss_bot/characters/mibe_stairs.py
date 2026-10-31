"""One exhausted-frontier probe of an observed-clear withdrawal stair.

A distant unresolved encounter is not evidence that every stair on its floor is
an unsafe arrival. This does not erase tactical bans or retry an escape stair:
only a long, quiet, full-health planned departure can supply a probe candidate.
Ordinary paths, native ability readiness and all earlier alternatives still win.

A withdrawal that is already quiet may finish its last health recovery before
leaving; recent threats never gain permission to wait for a clearance receipt.
After alternatives are genuinely exhausted, an observed-clear pair may consume
the remaining quiet return turns without spending its single descent attempt.
"""
from dataclasses import dataclass

from ..models import Action
from ..terrain import DOWNSTAIRS, UPSTAIRS


@dataclass(frozen=True)
class ClearDeparture:
    destination: str
    destination_feature: int
    departure_turn: int
    return_turn: int
    quiet_turns: int


class StairReevaluation:
    # Policy bounds, not native mechanics. Twelve quiet turns also exclude a
    # follower just lost around the corner; waiting alone never supplies proof
    # for an immediate emergency escape, which is categorically ineligible.
    quiet_turns = 12
    attempt_limit = 1
    preparation_limit = 6

    def __init__(self):
        self.departures: dict[tuple[str, int], ClearDeparture] = {}
        self.attempted: set[tuple[str, int]] = set()
        self._quiet_since: dict[str, int] = {}
        self._preparations: dict[tuple[str, int], int] = {}
        self._observed = None

    @staticmethod
    def _quiet(obs):
        return (not obs.hostiles and not obs.statuses
                and not obs.dead and not obs.game_over
                and not any(cell.cloud for cell in obs.cells
                            if (cell.x, cell.y) == obs.position))

    @classmethod
    def _clear(cls, obs):
        return obs.hp_max > 0 and obs.hp == obs.hp_max and cls._quiet(obs)

    @staticmethod
    def _feature(obs):
        return next((cell.feature for cell in obs.cells
                     if (cell.x, cell.y) == obs.position), None)

    def observe(self, obs, previous, action, *, unseen_harm=False):
        """Consume public snapshots before the controller resets floor memory.

        Features identify the actually traversed ordinary stair pair. No player
        origin or monster-coordinate translation is needed or inferred.
        """
        if obs is self._observed:
            return
        old = previous
        transition = old is not None and old.location != obs.location
        if transition and action is not None:
            source_feature, arrival_feature = self._feature(old), self._feature(obs)
            source_key = old.location, source_feature
            self._preparations.pop(source_key, None)
            if action.kind in {"stairs_up", "escape_upstairs"} and arrival_feature in DOWNSTAIRS:
                # A newer withdrawal replaces old clearance evidence, including
                # an emergency escape or a departure that is no longer quiet.
                self.departures.pop((obs.location, arrival_feature), None)
            if action.kind == "stairs_down" and source_feature in DOWNSTAIRS:
                entry = self.departures.get(source_key)
                if entry is not None and (not self._clear(obs)
                        or obs.location != entry.destination
                        or arrival_feature != entry.destination_feature):
                    self.departures.pop(source_key, None)
            if (action.kind == "stairs_up" and action.objective == "planned retreat"
                    and old.place == obs.place and old.depth == obs.depth + 1
                    and source_feature in UPSTAIRS and arrival_feature in DOWNSTAIRS
                    and self._clear(old) and self._clear(obs) and not unseen_harm):
                quiet = old.turn - self._quiet_since.get(old.location, old.turn)
                if quiet >= self.quiet_turns:
                    self.departures[(obs.location, arrival_feature)] = ClearDeparture(
                        old.location, source_feature, old.turn, obs.turn, quiet)
            # Time spent on another floor is not observed quiet on this floor.
            self._quiet_since.pop(old.location, None)
        if not self._quiet(obs) or unseen_harm:
            self._quiet_since.pop(obs.location, None)
        elif (old is not None and old.location == obs.location
              and obs.turn > old.turn and obs.hp < old.hp):
            self._quiet_since[obs.location] = obs.turn
        else:
            self._quiet_since.setdefault(obs.location, obs.turn)
        self._observed = obs

    def prepare_departure(self, obs, *, unseen_danger=False):
        """Finish near-complete healing only after an already quiet withdrawal.

        Call only for an otherwise selected voluntary planned-upstairs action.
        This cannot delay an emergency or wait out recent contact to manufacture
        clearance. Full health is still checked on the actual departure later.
        """
        quiet_since = self._quiet_since.get(obs.location)
        feature = self._feature(obs)
        if (obs is not self._observed or unseen_danger or not self._quiet(obs)
                or not .98 <= obs.hp_ratio < 1 or feature not in UPSTAIRS
                or quiet_since is None or obs.turn - quiet_since < self.quiet_turns):
            return None
        key = obs.location, feature
        started = self._preparations.setdefault(key, obs.turn)
        if obs.turn - started >= self.preparation_limit:
            return None
        return Action("wait", reason=(
            "Finish the last health recovery on already quiet upstairs before "
            "the voluntary withdrawal; observe actual full health before leaving"),
            objective="prepare clear departure",
            assessment={"stair_reevaluation": {
                "quiet_departure_turns": obs.turn - quiet_since,
                "preparation_turns": obs.turn - started,
                "preparation_limit": self.preparation_limit}})

    def choose(self, obs, cells, path, unsafe_stairs, *, exhausted):
        """Return a bounded probe only after every ordinary alternative failed.

        ``path`` must be the ordinary known-ground pathfinder, retaining danger,
        occupancy and cloud restrictions. Calling this does not unban any stair.
        The attempt is spent only when the actual descent action is selected.
        An eligible pair may need a few quiet return turns first: reaching an
        exhausted floor is not itself a world action that advances that guard.
        """
        if (not exhausted or not self._clear(obs)
                or not any(ability.name.casefold() == "berserk" and ability.available
                           for ability in obs.abilities)):
            return None
        quiet_since = self._quiet_since.get(obs.location)
        if quiet_since is None:
            return None
        candidates = {}
        for position, cell in cells.items():
            key = obs.location, cell.feature
            entry = self.departures.get(key)
            if (cell.feature in DOWNSTAIRS and entry is not None
                    and key not in self.attempted
                    and obs.xl < unsafe_stairs.get(key, 0)):
                candidates[position] = entry
        if obs.position in candidates:
            entry = candidates[obs.position]
            quiet = obs.turn - max(entry.return_turn, quiet_since)
            if quiet < self.quiet_turns:
                return Action("wait", reason=(
                    "Observe the remaining quiet return turns before the one "
                    "exhausted-alternatives stair reevaluation"),
                    objective="prepare stair reevaluation",
                    assessment={"stair_reevaluation": {
                        "destination": entry.destination,
                        "quiet_return_turns": quiet,
                        "required_quiet_turns": self.quiet_turns}})
            self.attempted.add((obs.location, cells[obs.position].feature))
            return Action("stairs_down", reason=(
                "Reevaluate the observed-clear stair once after exhausting alternatives; "
                "the distant encounter is not assumed resolved"),
                objective="reevaluate clear stair", assessment={"stair_reevaluation": {
                    "destination": entry.destination,
                    "clear_departure_turn": entry.departure_turn,
                    "quiet_departure_turns": entry.quiet_turns,
                    "attempt": 1, "attempt_limit": self.attempt_limit}})
        direction = path(set(candidates)) if candidates else None
        if direction:
            return Action("move", direction,
                          "Reach the one observed-clear stair eligible for bounded reevaluation",
                          "reevaluate clear stair")
        return None
