"""Public, player-visible observations and bounded actions.

Wire-only inventory subtype/plus fields are deliberately absent: DCSS sends some
unidentified item internals to its renderer. Agents must not learn them.
"""
from dataclasses import asdict, dataclass, field
from typing import Protocol
import json

from .terrain import SAFE_WALKABLE

DIRECTIONS = {"h": (-1, 0), "j": (0, 1), "k": (0, -1), "l": (1, 0),
              "y": (-1, -1), "u": (1, -1), "b": (-1, 1), "n": (1, 1)}


@dataclass(frozen=True)
class Item:
    slot: int
    letter: str
    name: str
    quantity: int = 1
    equipped: bool = False
    category: str | None = None


@dataclass(frozen=True)
class Status:
    light: str
    text: str = ""
    description: str = ""
    colour: int | None = None


@dataclass(frozen=True)
class Resistance:
    name: str
    level: int


@dataclass(frozen=True)
class Ability:
    name: str
    hotkey: str
    available: bool
    failure: int | None = None


@dataclass(frozen=True)
class Skill:
    name: str
    level: float
    training: int | None = None
    target: float | None = None
    hotkey: str = ""
    enabled: bool | None = None

@dataclass(frozen=True)
class Monster:
    id: int
    name: str
    x: int
    y: int
    threat: int = 1
    attitude: int = 0
    description: str | None = None
    statuses: tuple[str, ...] = ()
    equipment: tuple[str, ...] = ()
    speed_description: str | None = None
    spells: tuple[str, ...] = ()
    spell_ranges: tuple[tuple[str, int], ...] = ()

    def distance(self, position: tuple[int, int]) -> int:
        return max(abs(self.x - position[0]), abs(self.y - position[1]))

    @property
    def passive(self) -> bool:
        # Harmless flora and butterflies can have hostile wire attitude. Native
        # 0.34.1 butterfly.yaml has no attacks; flight/speed are not damage.
        # Do not use threat==0: trivial rats/snakes and active oklobs can attack.
        return self.name.lower() in {"plant", "demonic plant", "withered plant", "fungus",
                                     "toadstool", "bush", "butterfly"}


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
        # Movement over a safe feature is not permission to activate it.
        return self.feature in SAFE_WALKABLE


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
    ac: int | None = None
    ev: int | None = None
    sh: int | None = None
    piety_rank: int | None = None
    poison_survival: int | None = None
    status_details: tuple[Status, ...] = ()
    resistances: tuple[Resistance, ...] = ()
    will: int | None = None
    abilities: tuple[Ability, ...] = ()
    skills: tuple[Skill, ...] = ()
    attack_delay: float | None = None
    weapon_skill: str | None = None
    # Per-command adapter result, never inferred from old message history.
    # Native stair travel completes only on its exact same-floor destination.
    native_automation: str = ""
    native_result: str = ""

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
    assessment: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        simple = {"wait", "rest", "explore", "stairs_up", "stairs_down", "escape_upstairs", "pickup",
                  "cancel", "more", "save", "pause"}
        slotted = {"quaff", "read", "wield", "wear", "put_on", "menu"}
        if not all(isinstance(x, str) for x in (self.kind, self.value, self.reason, self.objective)):
            raise ValueError("Action fields must be strings")
        if not isinstance(self.assessment, dict):
            raise ValueError("Action assessment must be a JSON-safe dictionary")
        try:
            json.dumps(self.assessment, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Action assessment must be finite JSON-safe data") from error
        if self.kind in {"move", "attack"}:
            valid = self.value in DIRECTIONS
        elif self.kind == "inspect":
            valid = self.value in {"", "abilities"}
        elif self.kind in simple:
            valid = not self.value
        elif self.kind in slotted:
            valid = len(self.value) == 1 and self.value.isascii() and self.value.isalpha()
        elif self.kind == "pickup_select":
            valid = (0 < len(self.value) <= 52 and self.value.isascii()
                     and self.value.isalpha() and len(set(self.value)) == len(self.value))
        elif self.kind == "ability":
            valid = self.value in {"berserk", "trogs_hand", "brothers_in_arms"}
        elif self.kind == "train_skill":
            import re
            match = re.fullmatch(r"([A-Za-z][A-Za-z &]+):(\d{1,2}(?:\.\d)?)", self.value)
            valid = bool(match and 0 < float(match[2]) <= 27)
        elif self.kind in {"zap", "throw", "blink", "inspect_monster", "travel_downstairs"}:
            import re
            prefix = "" if self.kind in {"inspect_monster", "travel_downstairs"} else "[a-zA-Z]:"
            valid = bool(re.fullmatch(prefix + r"-?(?:0|[1-9]\d{0,3}),-?(?:0|[1-9]\d{0,3})", self.value))
        elif self.kind == "identify":
            import re
            valid = bool(re.fullmatch(r"[a-zA-Z]:[a-zA-Z]", self.value))
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
