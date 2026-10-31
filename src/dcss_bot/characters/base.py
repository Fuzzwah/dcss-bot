"""Character setup and strategy selection, separate from WebTiles transport."""
from collections.abc import Callable
from dataclasses import dataclass
import re

from ..models import Action, Agent, Observation
from ..state import plain


_ATTRIBUTES = {"strength": "S", "intelligence": "I", "dexterity": "D"}


@dataclass(frozen=True)
class CharacterProfile:
    """One supported species/background combination and its default policy.

    Modules provide a PROFILE instance. Unusual creation/advancement interfaces
    can override these methods rather than adding character branches to the adapter.
    """

    identifier: str
    abbreviation: str
    species: str
    background: str
    god: str
    weapon: str | None
    preferred_stat: str
    agent_factory: Callable[[], Agent]
    rc_options: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.preferred_stat not in _ATTRIBUTES:
            raise ValueError("Character preferred_stat must be strength, intelligence or dexterity")
        if not callable(self.agent_factory):
            raise ValueError("Character agent_factory must create a decision engine")

    def metadata(self) -> dict:
        return {"id": self.identifier, "abbreviation": self.abbreviation,
                "species": self.species, "background": self.background,
                "god": self.god, "weapon": self.weapon, "preferred_stat": self.preferred_stat,
                "rc_options": dict(self.rc_options)}

    def rc_settings(self) -> dict[str, str]:
        settings = {"species": self.species, "background": self.background}
        if self.weapon:
            settings["weapon"] = self.weapon
        settings.update(self.rc_options)
        return settings

    def creation_action(self, observation: Observation) -> Action:
        choices = tuple(value.casefold() for value in (self.species, self.background, self.weapon) if value)
        for section in ("main-items", "sub-items"):
            for button in observation.menu.get(section, {}).get("buttons", []):
                label = plain(button.get("label", " ".join(button.get("labels", [])))).casefold()
                if any(re.search(r"(?<!\w)" + re.escape(choice) + r"(?!\w)", label) for choice in choices):
                    key = button["hotkey"]
                    key = chr(key) if isinstance(key, int) else key
                    return Action("menu", key, f"Create {self.species} {self.background}", "character creation")
        return Action("pause", reason=f"Unrecognised creation choice for {self.identifier}")

    def advancement_action(self, observation: Observation) -> Action | None:
        if observation.input_mode != 7 and not observation.menu:
            return None
        text = re.sub(r"\(([A-Za-z])\)", r"\1",
                      plain(observation.prompt if observation.input_mode == 7 else str(observation.menu))).lower()
        if "increase" in text and all(attribute in text for attribute in _ATTRIBUTES):
            return Action("menu", _ATTRIBUTES[self.preferred_stat],
                          f"Increase {self.preferred_stat} for {self.identifier}", "character advancement")
        return None

    def matches_observation(self, observation: Observation) -> bool:
        """Current public species/religion, not an inferred original background."""
        return observation.species == self.species and observation.god == self.god

    def matches_saved_description(self, description: str) -> bool:
        """Species/background from the public lobby; current god is checked in play."""
        combo = re.escape(f"{self.species} {self.background}")
        return re.fullmatch(combo + r"(?: of .+)?(?: \(WIZ\))?", description) is not None

    def matches_start(self, observation: Observation) -> bool:
        """The native fresh-game greeting independently identifies the background."""
        ending = f" the {self.species} {self.background}."
        return self.matches_observation(observation) and any(
            message.startswith("Welcome, ") and message.endswith(ending)
            for message in observation.messages)
