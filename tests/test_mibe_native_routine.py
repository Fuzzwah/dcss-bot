"""Native automation must yield back to tactical and manual navigation policy."""
from dataclasses import replace

import pytest

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.models import Cell, Monster, Observation


def quiet_corridor():
    cells = tuple(Cell(x, 0, 33, "@" if x == 0 else ".", True) for x in range(3))
    walls = tuple(Cell(x, y, 7, "#", True) for x in range(-1, 4) for y in (-1, 1))
    return Observation(hp=26, hp_max=26, place="Dungeon", depth=1, turn=20,
                       input_mode=1, cells=cells + walls + (Cell(-1, 0, 7, "#", True),))


async def developed_agent(obs):
    agent = RuleBasedAgent()
    # Consume the ordinary initial public-development request, not its internals.
    action = await agent.decide(obs)
    assert action.kind == "inspect"
    return agent


@pytest.mark.parametrize("result", ["partial", "yielded", "unsupported_delay"])
async def test_native_explore_no_progress_falls_back_instead_of_repeating_o(result):
    obs = quiet_corridor()
    agent = await developed_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    refused = replace(obs, native_automation="explore", native_result=result,
                      messages=("Partly explored, can't reach some places.",) if result == "partial" else ())
    action = await agent.decide(refused)
    assert (action.kind, action.value) == ("move", "l")


@pytest.mark.parametrize("result", ["interrupted", "control", "yielded"])
async def test_native_explore_resumes_on_known_ground_but_visible_threat_preempts_it(result):
    obs = quiet_corridor()
    agent = await developed_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    resumed = replace(obs, turn=21, position=(1, 0),
                      cells=tuple(replace(cell, glyph="@" if (cell.x, cell.y) == (1, 0) else ".")
                                  if cell.walkable else cell for cell in obs.cells),
                      native_automation="explore", native_result=result)
    assert (await agent.decide(resumed)).kind == "explore"
    interrupted = replace(resumed, turn=22, native_result="interrupted",
                          monsters=(Monster(1, "rat", 2, 0, 0,
                                            description="Attacks  Max Damage\nBite:  3\n"),))
    action = await agent.decide(interrupted)
    assert (action.kind, action.value, action.objective) == ("move", "l", "combat")


async def test_clear_berserk_cooldown_uses_native_rest_but_new_enemy_interrupts_recovery():
    obs = replace(quiet_corridor(), statuses=("-Berserk",))
    agent = RuleBasedAgent()
    assert (await agent.decide(obs)).kind == "rest"
    danger = replace(obs, turn=21, monsters=(Monster(1, "rat", 1, 0, 0,
                                                   description="Attacks  Max Damage\nBite:  3\n"),))
    action = await agent.decide(danger)
    assert (action.kind, action.value, action.objective) == ("move", "l", "combat")


async def test_native_explore_runs_without_a_custom_frontier_and_done_skips_corner_detour():
    # A known corridor has one unseen diagonal behind two known walls. The old
    # frontier finder cannot route to it, but its speculative corner-viewpoint
    # finder would walk the entire corridor instead of trying native o.
    cells = {(x, y): Cell(x, y, 7, "#", True)
             for x in range(-1, 7) for y in range(-1, 2)}
    cells.update({(x, 0): Cell(x, 0, 42 if x == 0 else 33,
                              "@" if x == 0 else ".", True) for x in range(6)})
    cells[(6, -1)] = Cell(6, -1)
    obs = replace(quiet_corridor(), cells=tuple(cells.values()))
    agent = await developed_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    complete = replace(obs, native_automation="explore", native_result="complete",
                       messages=("Done exploring.",))
    assert (await agent.decide(complete)).kind == "stairs_down"


async def test_pause_before_native_progress_can_resume_o_without_manual_map_detour():
    obs = quiet_corridor()
    agent = await developed_agent(obs)
    assert (await agent.decide(obs)).kind == "explore"
    cancelled = replace(obs, native_automation="explore", native_result="control")
    assert (await agent.decide(cancelled)).kind == "explore"
