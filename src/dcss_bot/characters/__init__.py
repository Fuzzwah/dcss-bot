"""Supported character modules; custom modules expose a CharacterProfile instance."""
import importlib

from .base import CharacterProfile


DEFAULT_CHARACTER = "mibe"
BUILTIN_CHARACTERS = {"mibe": "dcss_bot.characters.mibe:PROFILE"}


def get_character(identifier: str) -> CharacterProfile:
    target = BUILTIN_CHARACTERS.get(identifier.casefold(), identifier)
    if ":" not in target:
        raise ValueError(f"Unknown character {identifier!r}; supported: {', '.join(BUILTIN_CHARACTERS)} "
                         "(or an installed module:PROFILE)")
    module, name = target.rsplit(":", 1)
    try:
        profile = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"Cannot load character module {identifier!r}: {exc}") from exc
    if not isinstance(profile, CharacterProfile):
        raise ValueError(f"Character {identifier!r} must expose a CharacterProfile instance")
    return profile
