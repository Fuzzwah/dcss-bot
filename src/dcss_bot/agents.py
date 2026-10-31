"""Decision-engine loading, independent of character setup and tactics."""
import importlib

from .characters.base import CharacterProfile
from .models import Agent


def make_agent(identifier: str, character: CharacterProfile) -> Agent:
    if identifier == "rule":
        agent = character.agent_factory()
    else:
        if ":" not in identifier:
            raise ValueError("Agent must be 'rule' or an installed module:Class")
        module, name = identifier.rsplit(":", 1)
        agent = getattr(importlib.import_module(module), name)()
    if not callable(getattr(agent, "decide", None)):
        raise ValueError("Agent must implement async decide(observation)")
    return agent
