# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The one KiCad pin type table and the rule a pin list's pin is read with.

A pin's KiCad type comes from its role and direction. Every row of the table is
checked in both keyings, the pin list's words and the die contract's, and each
case of the reading rule is checked with the text it gives: a pin with a role
takes the table's word; a pin with no role takes its stored type, or passive
when it has none; a stored type that disagrees with the role is named and
loses. Words are exact capitals: anything else is refused, never guessed.
"""
import pytest

from chiplet_format_io import ChipletFormatError, die_contract, pins

TOKENS = ("input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
          "power_in", "power_out", "open_collector", "open_emitter", "no_connect")

# (role, direction or None for "any or absent", KiCad word), as the format states it.
ROWS = [
    ("POWER", None, "power_in"),
    ("GROUND", None, "power_in"),
    ("NO_CONNECT", None, "no_connect"),
    ("SIGNAL", "INPUT", "input"),
    ("SIGNAL", "OUTPUT", "output"),
    ("SIGNAL", "INOUT", "bidirectional"),
    ("SIGNAL", "ANALOG", "passive"),
    ("SIGNAL", "NONE", "passive"),
]
CONTRACT = {"SIGNAL": "signal", "POWER": "power", "GROUND": "ground", "NO_CONNECT": "no_connect",
            "INPUT": "input", "OUTPUT": "output", "INOUT": "bidir", "ANALOG": "analog",
            "NONE": "none"}


def _expanded():
    """Every (role, direction) a row covers: a supply row covers every direction and none."""
    for role, direction, word in ROWS:
        for each in ((None,) + pins.PIN_DIRECTIONS if direction is None else (direction,)):
            yield role, each, word


def test_the_vocabulary_is_the_nine_words_and_the_contract_has_each_one():
    assert pins.PIN_ROLES == ("SIGNAL", "POWER", "GROUND", "NO_CONNECT")
    assert pins.PIN_DIRECTIONS == ("INPUT", "OUTPUT", "INOUT", "ANALOG", "NONE")
    assert {CONTRACT[r] for r in pins.PIN_ROLES} == set(die_contract.TERMINAL_ROLES)
    assert {CONTRACT[d] for d in pins.PIN_DIRECTIONS} == set(die_contract.TERMINAL_DIRECTIONS)
    assert pins.KICAD_PIN_TYPES == TOKENS


@pytest.mark.parametrize("role,direction,word", list(_expanded()))
def test_every_row_in_the_pin_list_keying(role, direction, word):
    assert pins.KICAD_TYPE[(role, direction)] == word


@pytest.mark.parametrize("role,direction,word", list(_expanded()))
def test_every_row_in_the_contract_keying(role, direction, word):
    key = (CONTRACT[role], None if direction is None else CONTRACT[direction])
    assert pins.KICAD_TYPE_OF_TERMINAL[key] == word


def test_the_two_keyings_hold_the_same_rows_and_nothing_else():
    assert set(pins.KICAD_TYPE) == {(r, d) for r, d, _ in _expanded()}
    assert len(pins.KICAD_TYPE_OF_TERMINAL) == len(pins.KICAD_TYPE)
    assert set(pins.KICAD_TYPE.values()) <= set(TOKENS)
    # A signal pin with no direction is not a row: it is read with a warning (case 1).
    assert ("SIGNAL", None) not in pins.KICAD_TYPE


@pytest.mark.parametrize("role,direction,word", list(_expanded()))
def test_a_pin_with_a_role_takes_the_table_word(role, direction, word):
    pin = {"name": "P", "role": role}
    if direction is not None:
        pin["direction"] = direction
    assert pins.kicad_type(pin) == (word, ())


def test_a_signal_pin_with_no_direction_is_passive_and_says_so():
    assert pins.kicad_type({"name": "A", "pad_index": 0, "role": "SIGNAL"}) == ("passive", (
        "pin A (pad_index 0): role SIGNAL has no direction, so its KiCad type is passive; "
        "write a direction (INPUT, OUTPUT, INOUT, ANALOG or NONE).",))


def test_none_is_a_direction_not_an_absence():
    assert pins.kicad_type({"name": "A", "role": "SIGNAL", "direction": "NONE"}) == ("passive", ())


def test_a_pin_with_no_role_takes_its_stored_type_and_is_told_the_field_is_deprecated():
    assert pins.kicad_type({"name": "VDD", "type": "power_in"}) == ("power_in", (
        'pin VDD: it has no role, so its KiCad type is the stored type "power_in". The type '
        "field is deprecated from pins.json 1.1; write a role and a direction instead.",))


def test_a_direction_without_a_role_is_named_as_unused():
    word, said = pins.kicad_type({"name": "D", "type": "input", "direction": "OUTPUT"})
    assert word == "input"
    assert said[0].endswith(" Its direction is not used without a role.")
    word, said = pins.kicad_type({"name": "D", "direction": "OUTPUT"})
    assert word == "passive"
    assert said == ("pin D: it has neither a role nor a type, so its KiCad type is passive; "
                    "write a role and a direction. Its direction is not used without a role.",)


def test_a_pin_with_neither_is_passive_and_says_so():
    assert pins.kicad_type({"name": "X"}) == ("passive", (
        "pin X: it has neither a role nor a type, so its KiCad type is passive; "
        "write a role and a direction.",))


def test_a_stored_type_that_disagrees_is_named_and_the_derived_word_is_used():
    pin = {"name": "IO", "role": "SIGNAL", "direction": "INOUT", "type": "inout"}
    assert pins.kicad_type(pin) == ("bidirectional", (
        'pin IO: the stored type "inout" is not "bidirectional", the KiCad type its role and '
        'direction give; "bidirectional" is used.',))


def test_a_stored_type_that_agrees_says_nothing():
    pin = {"name": "VSS", "role": "GROUND", "direction": "INOUT", "type": "power_in"}
    assert pins.kicad_type(pin) == ("power_in", ())


@pytest.mark.parametrize("role", ["signal", "Signal", "PWR", "", None, 1])
def test_a_role_outside_the_four_capital_words_is_refused(role):
    with pytest.raises(ChipletFormatError) as caught:
        pins.kicad_type({"name": "P", "pad_index": 2, "role": role, "direction": "INPUT"})
    assert str(caught.value) == (
        f'pin P (pad_index 2): role "{role}" is not a pin-list role; write one of SIGNAL, '
        "POWER, GROUND, NO_CONNECT, in capitals.")


@pytest.mark.parametrize("direction", ["Input", "input", "BIDIR", "", None])
def test_a_direction_outside_the_five_capital_words_is_refused(direction):
    with pytest.raises(ChipletFormatError) as caught:
        pins.kicad_type({"name": "P", "role": "SIGNAL", "direction": direction})
    assert str(caught.value) == (
        f'pin P: direction "{direction}" is not a pin-list direction; write one of INPUT, '
        "OUTPUT, INOUT, ANALOG, NONE, in capitals.")


@pytest.mark.parametrize("stored", ["inout", "Input", "power", ""])
def test_a_roleless_pin_whose_type_is_not_a_kicad_token_is_refused(stored):
    with pytest.raises(ChipletFormatError) as caught:
        pins.kicad_type({"name": "P", "type": stored})
    assert str(caught.value) == (
        f'pin P: it has no role, and its type "{stored}" is not a KiCad pin type. Write a role '
        f"and a direction, or use one of these types: {', '.join(TOKENS)}.")


def test_the_reader_supports_1_1_while_writers_still_stamp_1_0():
    assert pins.SUPPORTED_PINS_VERSION == "1.1"
    assert pins.PINS_VERSION == "1.0"
