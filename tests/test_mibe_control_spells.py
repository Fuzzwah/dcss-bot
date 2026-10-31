"""Public spell labels: real control risk is not invented projectile HP damage."""
from collections import Counter

from dcss_bot.characters.mibe import RuleBasedAgent
from dcss_bot.characters.mibe_mechanics import mechanics
from dcss_bot.characters.mibe_tactics import TacticalController
from dcss_bot.models import Cell, DIRECTIONS, Item, Monster, Observation


def choose(enemy):
    obs = Observation(species="Minotaur",god="Trog",place="Dungeon",depth=3,
                      turn=100,hp=31,hp_max=31,xl=4,will=3,input_mode=1,
                      inventory=(Item(52,"r","scroll of teleportation",category="scroll"),),
                      monsters=(enemy,),
                      cells=tuple(Cell(x,y,33,".",True) for x in range(-2,7) for y in range(-2,3)))
    action = TacticalController().decide(obs,{(c.x,c.y):c for c in obs.cells},Counter(),RuleBasedAgent._covered)
    assert action.kind == "move"
    dx,dy = DIRECTIONS[action.value]
    assert enemy.distance((dx,dy)) < enemy.distance(obs.position)
    return action


def test_high_will_does_not_erase_ensnare_or_turn_it_into_bite_damage_at_range():
    # Synthetic mixed book: Ensnare is physical web restraint even if another
    # listed spell uses Will. The native web effect has zero direct HP damage.
    enemy = Monster(1,"synthetic web caster",4,0,1,
                    description="Attack  Max Damage\nBite    9\n\nSpells:\nEnsnare: \nSlow: (0%)",
                    spells=("Ensnare","Slow"),spell_ranges=(("Ensnare",5),("Slow",7)))
    info = mechanics(enemy)
    assert info.ranged_max == 0 and info.entangles
    action = choose(enemy)
    assert action.assessment["assessment"]["threats"][0]["entangling"]


def test_contact_damage_plus_distant_control_does_not_waste_an_escape_scroll():
    # Synthetic composition of supported native spell labels; Freeze's damage
    # remains melee-only while Slow preserves the distant control capability.
    enemy = Monster(1,"synthetic icy caster",4,0,1,
                    description="Attack  Max Damage\nHit    2\n\nSpells:\nFreeze: (3d5)\nSlow: (0%)",
                    spells=("Freeze","Slow"),spell_ranges=(("Freeze",1),("Slow",7)))
    info = mechanics(enemy)
    assert info.melee >= 15 and info.ranged and info.ranged_max == 0
    choose(enemy)
