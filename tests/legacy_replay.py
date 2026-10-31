"""Bounded replay of historical captures lacking newer public menu fields."""

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Action, Observation


def _missing_public_request(action: Action, observation: Observation):
    if (action.kind, action.value) == ("inspect", "abilities") and not observation.abilities:
        return "abilities", ""
    if action.kind == "inspect_monster":
        missing = {f"{monster.x},{monster.y}" for monster in observation.hostiles
                   if monster.description is None}
        assert action.value in missing, "Only missing captured descriptions may use this boundary"
        return "monster", action.value
    return None


async def decide_legacy_observation(agent: RuleBasedAgent, observation: Observation) -> Action:
    """Consume only bounded missing-field requests, without inventing a response.

    Historical captures cannot supply these native menus. The identical public
    snapshot models an empty response, not a successful inspection or unavailable
    powers. Requests to refresh existing data and all other UI remain visible.
    Modern inspection tests must use the real decision entry point instead.
    """
    attempted = set()
    for _ in range(len(observation.hostiles)+2):
        action = await agent.decide(observation)
        request = _missing_public_request(action,observation)
        if request is None:
            return action
        assert request not in attempted, "A missing native response must not repeat a free inspection"
        attempted.add(request)
    raise AssertionError("Historical missing-field inspection exceeded its finite budget")


def decide_legacy_tactics(controller, observation, cells, visits, covered) -> Action | None:
    """The same empty-response boundary for historical tactical-only scenarios."""
    attempted = set()
    for _ in range(len(observation.hostiles)+1):
        action = controller.decide(observation,cells,visits,covered)
        if action is None:
            return action
        request = _missing_public_request(action,observation)
        if request is None:
            return action
        assert request not in attempted, "A missing native response must not repeat a free inspection"
        attempted.add(request)
    raise AssertionError("Historical missing-field inspection exceeded its finite budget")
