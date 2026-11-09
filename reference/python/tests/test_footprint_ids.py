# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Footprint names: one refusal per character KiCad 9 would rewrite or refuse
in a footprint name, plus the empty name, whitespace, and each pad size that is
not a positive whole um. Every other printable ASCII character is kept."""
from __future__ import annotations

import pytest

from chiplet_format_io import ChipletFormatError
from chiplet_format_io.footprint_ids import (
    FootprintIdError,
    die_footprint,
    ext_pad_footprint,
)

# The same set footprint_ids.py takes from KiCad 9.0.6: every code point below
# U+0020 and ':', '\\', '<', '>', '"' (lib_id.cpp isLegalChar), plus '%', '$'
# and '/' (footprint.cpp StringLibNameInvalidChars).
_ILLEGAL = [chr(i) for i in range(0x20)] + list(":\\<>\"%$/")
_KEPT = [chr(i) for i in range(0x21, 0x7F) if chr(i) not in _ILLEGAL]


def test_a_cell_name_keeps_its_library():
    assert die_footprint("DRIVER") == "chiplet_dies:DRIVER"


@pytest.mark.parametrize("ch", _ILLEGAL, ids=lambda ch: f"U+{ord(ch):04X}")
def test_an_illegal_character_is_refused(ch):
    with pytest.raises(FootprintIdError):
        die_footprint(f"CELL{ch}")


@pytest.mark.parametrize("ch", _KEPT)
def test_every_other_printable_character_is_kept(ch):
    assert die_footprint(f"A{ch}B") == f"chiplet_dies:A{ch}B"


@pytest.mark.parametrize("name", ["A B", "A\u00a0B", "A\u2003B"])
def test_whitespace_is_refused(name):
    with pytest.raises(FootprintIdError):
        die_footprint(name)


@pytest.mark.parametrize("cell", [None, 7, b"DRIVER"])
def test_a_cell_that_is_not_a_string_is_refused(cell):
    with pytest.raises(FootprintIdError):
        die_footprint(cell)


def test_an_empty_name_is_refused():
    with pytest.raises(FootprintIdError):
        die_footprint("")


def test_the_refusal_is_a_format_error():
    assert issubclass(FootprintIdError, ChipletFormatError)


@pytest.mark.parametrize("um", [True, False, 80.0, "80", 0, -1])
def test_a_pad_size_must_be_a_positive_whole_number(um):
    with pytest.raises(FootprintIdError):
        ext_pad_footprint(um)


def test_eighty_micrometres_is_the_profile_pad():
    assert ext_pad_footprint(80) == "chiplet_carrier:EXT_PAD_80um"
