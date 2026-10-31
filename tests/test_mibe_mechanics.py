"""Pinned native spell ranges and public ranged-attack evidence."""
import pytest

from dcss_bot.characters.mibe_mechanics import EnemyMechanics, mechanics
from dcss_bot.models import Monster


@pytest.mark.parametrize("spell", ["Freeze", "Vampiric Draining", "Sticky Flame"])
def test_contact_spells_do_not_create_ranged_exposure(spell):
    info = mechanics(Monster(1, "endoplasm", 6, 0, spells=(spell,)))
    assert not info.ranged
    assert info.reach == 1
    assert info.melee >= 5


@pytest.mark.parametrize("spell", ["Berserk", "Haste", "Blink", "Blink Range", "Blink Away",
                                  "Invisibility", "Minor Healing", "Major Healing"])
def test_self_spells_do_not_create_ranged_exposure(spell):
    assert not mechanics(Monster(1, "goblin", 6, 0, spells=(spell,))).ranged


@pytest.mark.parametrize("spell,distance", [("Throw Frost", 6), ("Sting", 4),
                                            ("Spit Poison", 6), ("Pyre Arrow", 5),
                                            ("Magic Dart", 7), ("Slug Dart", 7)])
def test_offensive_spells_keep_their_verified_range_or_los_bound(spell, distance):
    info = mechanics(Monster(1, "caster", 6, 0, spells=(spell, "Haste", "Freeze")))
    assert info.ranged
    assert info.ranged_range == distance


def test_multiple_ranged_spells_use_the_longest_effective_range():
    info = mechanics(Monster(1, "caster", 6, 0, spells=("Sting", "Throw Frost")))
    assert info.ranged_range == 6
    unknown = mechanics(Monster(1, "caster", 6, 0, spells=("Sting", "Unclassified attack")))
    assert unknown.ranged and unknown.ranged_range == 7


@pytest.mark.parametrize("name", ["centaur", "orc priest", "orc wizard", "dart slug",
                                  "bombardier beetle", "ijyb"])
def test_catalog_ranged_capabilities_keep_los_fallback_without_shorter_evidence(name):
    info = mechanics(Monster(1, name, 6, 0))
    assert info.ranged and info.ranged_range == EnemyMechanics(1).ranged_range == 7


def test_public_short_range_spellbook_can_refine_catalog_range():
    info = mechanics(Monster(1, "bombardier beetle", 6, 0, spells=("Pyre Arrow",)))
    assert info.ranged and info.ranged_range == 5


def test_contact_spell_does_not_erase_a_separate_catalog_ranged_capability():
    info = mechanics(Monster(1, "centaur", 6, 0, spells=("Freeze",)))
    assert info.ranged and info.ranged_range == 7


@pytest.mark.parametrize("attack", ["Shoot: +0 shortbow", "Throw"])
def test_public_weapon_attack_preserves_los_capability_even_with_a_short_spell(attack):
    monster = Monster(1, "goblin", 6, 0, spells=("Sting",),
                      description=f"Attack                Max Damage\n{attack}  12\n")
    info = mechanics(monster)
    assert info.ranged and info.ranged_range == 7
    assert info.ranged_max == 12


def test_observed_native_contact_range_supersedes_unclassified_spell_fallback():
    info = mechanics(Monster(1,"unclassified creature",6,0,spells=("Unclassified touch",),
                             spell_ranges=(("Unclassified touch",1),)))
    assert not info.ranged


def test_multiple_public_books_do_not_shorten_an_observed_spell_range():
    info = mechanics(Monster(1,"caster",6,0,spells=("Sting",),
                             spell_ranges=(("Sting",5),("Sting",3))))
    assert info.ranged and info.ranged_range == 5


def test_displayed_iguana_attack_supersedes_unknown_native_threat_allowance():
    # development5 seed6: real public Bite15, relative threat High. The fallback
    # allowance24 must not overwrite the attack value the player actually read.
    info = mechanics(Monster(1,"iguana",2,0,2,description="Attack  Max Damage\nBite    15\n"))
    assert info.melee == 15


def test_displayed_slug_dart_dice_are_not_replaced_with_its_weaker_bite():
    info = mechanics(Monster(1,"dart slug",4,0,description="Attack  Max Damage\nBite    3\nSpells:\nSlug Dart: (2d3)",
                             spells=("Slug Dart",)))
    assert info.ranged_max == 6 and info.ranged


def test_displayed_single_hydra_bite_does_not_erase_visible_multiple_heads():
    info = mechanics(Monster(1,"five-headed hydra",1,0,description="Bite    18\n"))
    assert info.melee == 90 and info.avoid_melee


@pytest.mark.parametrize("name", ["goblin", "hobgoblin", "kobold"])
def test_examined_ordinary_early_throwers_use_melee_not_projectiles_at_contact(name):
    # Synthetic native attack rows matching the captured source3 kobold bounds.
    # dat/mons/{goblin,hobgoblin,kobold}.yaml have no prefer_ranged flag;
    # mon-act.cc:1507–1523 consequently forbids their adjacent projectiles.
    info = mechanics(Monster(1, name, 1, 0, description=(
        "Attacks  Max Damage\nHit: +0 whip  6\nThrow: javelin  14\n")))
    assert info.melee == 6 and info.ranged_max == 14
    assert info.ranged_min_range == 2


@pytest.mark.parametrize("name,statuses,spells", [
    ("unexamined species", (), ()),
    ("deep elf master archer", (), ()),
    ("kobold", ("Armed",), ()),
    ("kobold", (), ("Magic Dart",)),
])
def test_ordinary_thrower_refinement_keeps_unknown_specialist_buff_and_spell_exceptions(name, statuses, spells):
    info = mechanics(Monster(1, name, 1, 0, statuses=statuses, spells=spells,
        description="Attacks  Max Damage\nHit: +0 whip  6\nThrow: javelin  14\n"))
    assert info.ranged_min_range == 1
