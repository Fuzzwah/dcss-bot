"""Known barriers constrain walking; unknown cells never become invented walls."""
from dataclasses import replace

import pytest

from dcss_bot.characters.mibe_encounter import enclosed_melee_bypass
from dcss_bot.models import Cell, Monster, Observation


def moated_room():
    cells = {(x,y):Cell(x,y,33,".",True) for x in range(-3,4) for y in range(-4,2)}
    for x in range(-1,2):
        cells[(x,-1)] = Cell(x,-1,11,"#",True)
    for x in range(-2,3):
        cells[(x,-2)] = Cell(x,-2,31,"~",True)
    enemy = Monster(1,"gnoll",0,0,1,description="Attack  Max Damage\nHit: +0 whip  11",
                    equipment=("+0 whip",))
    return Observation(place="Dungeon",depth=5,turn=100,hp=51,hp_max=51,xl=6,
                       position=(0,-3),input_mode=1,monsters=(enemy,),cells=tuple(cells.values()))


def choose(obs):
    return enclosed_melee_bypass(obs,{(c.x,c.y):c for c in obs.cells},{(0,-4)})


def test_observed_moat_allows_one_dry_step_away_but_does_not_claim_a_permanent_cage():
    obs = moated_room()
    action = choose(obs)
    assert action.kind == "move" and action.value == "k"
    assert action.objective == "bypass barrier"
    assert "enclosed_hostile_ids" not in action.assessment
    assert action.assessment["separated_hostile_ids"] == [1]


def test_unknown_shortcut_prevents_a_finite_barrier_bypass():
    obs = moated_room()
    # Synthetic visibility loss: these cells have never supplied a known wall or
    # water feature. A potentially open shortcut defeats the travel guarantee.
    cells = tuple(replace(c,feature=0,glyph="",visible=False) if (c.x,c.y) in {(0,-1),(0,-2)}
                  else c for c in obs.cells)
    assert choose(replace(obs,cells=cells)) is None


@pytest.mark.parametrize("change",[
    {"name":"unfamiliar creature"},
    {"statuses":("flying",)},
    {"speed_description":"Speed: 200%"},
    {"spells":("Blink",)},
])
def test_unknown_flying_fast_or_blinking_foes_cannot_use_the_ordinary_walking_bypass(change):
    obs = moated_room()
    assert choose(replace(obs,monsters=(replace(obs.monsters[0],**change),))) is None


@pytest.mark.parametrize("feature",[30,31])
def test_lava_and_deep_water_can_bound_ordinary_land_travel(feature):
    obs = moated_room()
    cells = tuple(replace(cell,feature=feature) if cell.feature == 31 else cell
                  for cell in obs.cells)
    action = choose(replace(obs,cells=cells))
    assert action.kind == "move"
    assert next(cell for cell in cells if (cell.x,cell.y) == (0,-4)).feature == 33


def test_shallow_water_cannot_supply_a_false_walking_separation_certificate():
    obs = moated_room()
    # Synthetic open shallow-water shortcut through the north wall. Save34
    # features are lava30/deep31/shallow32, not deep31/lava32.
    cells = tuple(replace(cell,feature=32,glyph="~") if (cell.x,cell.y) in {(0,-1),(0,-2)}
                  else cell for cell in obs.cells)
    assert choose(replace(obs,cells=cells)) is None
