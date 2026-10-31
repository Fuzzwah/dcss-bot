"""Public, player-visible observations and bounded actions.

Wire-only inventory subtype/plus fields are deliberately absent: DCSS sends some
unidentified item internals to its renderer. Agents must not learn them.
"""
from dataclasses import asdict, dataclass, field
from typing import Protocol

DIRECTIONS = {"h": (-1, 0), "j": (0, 1), "k": (0, -1), "l": (1, 0),
              "y": (-1, -1), "u": (1, -1), "b": (-1, 1), "n": (1, 1)}


@dataclass(frozen=True)
class Item:
    slot: int
    letter: str
    name: str
    quantity: int = 1
    equipped: bool = False


@dataclass(frozen=True)
class Monster:
    id: int
    name: str
    x: int
    y: int
    threat: int = 1
    attitude: int = 0

    def distance(self, position: tuple[int, int]) -> int:
        return max(abs(self.x - position[0]), abs(self.y - position[1]))

    @property
    def passive(self) -> bool:
        # Flora has hostile attitude on the wire but cannot attack. Do not use
        # threat==0 alone: trivial rats/snakes still deal damage. Oklobs and
        # active ballistomycetes are deliberately not passive.
        return self.name.lower() in {"plant", "demonic plant", "withered plant", "fungus", "toadstool", "bush"}


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    feature: int = 0
    glyph: str = " "
    visible: bool = False
    cloud: bool = False

    @property
    def walkable(self) -> bool:
        # DCSS 0.34 / TAG_MAJOR_VERSION=34. Exclude water, traps, sealed/runed
        # doors and branch portals. Hatch/exit squares can be crossed safely;
        # walkability does not authorise taking them. Closed doors bump open.
        return self.feature in {1, 33, 34, 42, 43, 44, 45, 46, 47, 48, 49, 60}


@dataclass(frozen=True)
class Observation:
    name: str = ""
    species: str = ""
    god: str = ""
    turn: int = 0
    time: int = 0
    place: str = ""
    depth: int = 0
    hp: int = 0
    hp_max: int = 0
    xl: int = 1
    position: tuple[int, int] = (0, 0)
    statuses: tuple[str, ...] = ()
    inventory: tuple[Item, ...] = ()
    monsters: tuple[Monster, ...] = ()
    cells: tuple[Cell, ...] = ()
    messages: tuple[str, ...] = ()
    prompt: str = ""
    menu: dict = field(default_factory=dict)
    input_mode: int = 0
    dead: bool = False
    game_over: bool = False

    @property
    def hp_ratio(self) -> float:
        return self.hp / self.hp_max if self.hp_max else 0.0

    @property
    def location(self) -> str:
        return f"{self.place}:{self.depth}"

    @property
    def hostiles(self) -> tuple[Monster, ...]:
        return tuple(m for m in self.monsters if m.attitude == 0 and not m.passive)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Action:
    kind: str
    value: str = ""
    reason: str = ""
    objective: str = ""

    def __post_init__(self) -> None:
        simple = {"wait", "explore", "stairs_up", "stairs_down", "pickup",
                  "cancel", "more", "save", "pause"}
        slotted = {"quaff", "read", "wield", "wear", "ability", "menu"}
        if not all(isinstance(x, str) for x in (self.kind, self.value, self.reason, self.objective)):
            raise ValueError("Action fields must be strings")
        if self.kind == "move":
            valid = self.value in DIRECTIONS
        elif self.kind in simple:
            valid = not self.value
        elif self.kind in slotted:
            valid = len(self.value) == 1 and self.value.isascii() and self.value.isalpha()
        elif self.kind == "confirm":
            valid = self.value in {"y", "n", "Y", "N"}
        else:
            valid = False
        if not valid:
            raise ValueError(f"Invalid action: {self.kind!r} {self.value!r}")

    def to_dict(self) -> dict:
        return asdict(self)


class Agent(Protocol):
    async def decide(self, observation: Observation) -> Action: ...
