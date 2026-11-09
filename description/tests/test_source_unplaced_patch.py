# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Placing an unplaced source (format 1.3) through the patcher: each edit
byte-exact, every other byte kept, and the stamp lowered once nothing needs 1.3."""
from __future__ import annotations

import pytest

from chiplet_system_description.source import SourceError, check_text
from chiplet_system_description.source.patch import (
    Change, Delete, Place, Replace, SetEntries, SetSize, patch_system)

from tests.test_source_unplaced import PINS_ONLY, UNPLACED

#: Everything that places :data:`UNPLACED`, in the order placement gives it.
PLACE_ALL = [Place("U1", (1000, 2200), 0),
             SetSize((2000, 3200)),
             SetEntries("west", ("VDD", "VSS")), SetEntries("north", ("U1.CLK",)),
             SetEntries("south", ("U2.STATUS",)), SetEntries("unplaced", ())]


def _text(source, edits):
    return patch_system(source.encode(), edits).text.decode()


def test_placing_an_unplaced_source_writes_the_placed_file_and_lowers_the_stamp():
    placed = _text(UNPLACED, PLACE_ALL)
    assert placed == (
        UNPLACED.replace('format_version: "1.3"', 'format_version: "1.1"')
        .replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [2000, 3200]}")
        .replace("die: dies/DRIVER, method",
                 "die: dies/DRIVER, at: [1000, 2200], rotation: 0, method")
        .replace("    profile: intm4tm2_wire_bond_v0\n",
                 "    profile: intm4tm2_wire_bond_v0\n    edges:\n      north: [U1.CLK]\n"
                 "      south: [U2.STATUS]\n      west: [VDD, VSS]\n")
        .replace("unplaced:\n  pins: [U2.STATUS, U1.CLK, VDD, VSS]\n", ""))
    source, findings = check_text(placed)
    assert findings == [] and source.unplaced is None


def test_the_changes_name_each_placement_and_the_stamp():
    changes = patch_system(UNPLACED.encode(), PLACE_ALL).changes
    assert [(c.block, c.item, c.field, c.old, c.line) for c in changes] == [
        ("format_version", "format_version", None, '"1.3"', 2),
        ("carrier", "carrier", "size", None, 13),
        ("dies", "U1", "at", None, 15), ("dies", "U1", "rotation", None, 15),
        ("external", "north", None, None, 23), ("external", "south", None, None, 23),
        ("external", "west", None, None, 23),
        ("unplaced", "pins", None, "[U2.STATUS, U1.CLK, VDD, VSS]", 25)]


def test_a_die_placed_alone_keeps_the_stamp_while_anything_else_is_unplaced():
    text = _text(UNPLACED, [Place("U1", (1000, 2200.5))])
    assert text == UNPLACED.replace("die: dies/DRIVER, method",
                                    "die: dies/DRIVER, at: [1000, 2200.5], method")


def test_a_rotation_is_one_of_the_four():
    with pytest.raises(ValueError, match="is not 0, 90, 180 or 270"):
        patch_system(UNPLACED.encode(), [Place("U1", (1, 1), 45)])


def test_placing_and_deleting_one_die_is_refused():
    with pytest.raises(ValueError, match="is both replaced and deleted") as exc:
        patch_system(UNPLACED.encode(), [Place("U1", (1, 2), 0), Delete("dies", "U1")])
    assert not isinstance(exc.value, SourceError)


def test_a_die_is_placed_once():
    with pytest.raises(ValueError, match="is placed twice") as exc:
        patch_system(UNPLACED.encode(), [Place("U1", (1, 2)), Place("U1", (3, 4), 90)])
    assert not isinstance(exc.value, SourceError)


def test_a_placed_die_and_a_sized_carrier_are_replaced_in_place():
    text = _text(PINS_ONLY, [SetSize((2400, 3200)),
                             Replace("dies", "U2", "at", (1000, 1100))])
    assert text == PINS_ONLY.replace("size: [2000, 3200]", "size: [2400, 3200]").replace(
        "at: [1000, 1000]", "at: [1000, 1100]")
    assert _text(PINS_ONLY, [SetSize((2000, 3200))]) == PINS_ONLY
    with pytest.raises(ValueError, match="cannot be dropped"):
        patch_system(PINS_ONLY.encode(), [SetSize(None)])


def test_a_block_carrier_gets_its_size_on_a_line_of_its_own():
    block = UNPLACED.replace("  carrier: {technology: intm4tm2, top_cell: CF_CARRIER}\n",
                             "  carrier:\n    technology: intm4tm2\n"
                             "    top_cell: CF_CARRIER   # the frame\n")
    assert _text(block, [SetSize((2000, 3200.25))]) == block.replace(
        "# the frame\n", "# the frame\n    size: [2000, 3200.25]\n")


#: What puts :data:`PINS_ONLY`'s one unplaced pin at the end of its west row.
TO_WEST = [SetEntries("west", ("VDD", "VSS", "U1.CLK", "U2.STATUS")), SetEntries("unplaced", ())]


def test_a_pin_added_to_an_edge_row_that_exists_keeps_its_comment():
    text = _text(PINS_ONLY, TO_WEST)
    assert text == (PINS_ONLY.replace('format_version: "1.3"', 'format_version: "1.1"')
                    .replace("west: [VDD, VSS, U1.CLK]", "west: [VDD, VSS, U1.CLK, U2.STATUS]")
                    .replace("unplaced:\n  pins: [U2.STATUS]\n", ""))
    commented = PINS_ONLY.replace("U1.CLK]\n", "U1.CLK]  # pad row 1\n")
    assert "west: [VDD, VSS, U1.CLK, U2.STATUS]  # pad row 1\n" in _text(commented, TO_WEST)


def test_some_pins_move_and_the_rest_stay_unplaced():
    text = _text(UNPLACED, [SetEntries("north", ("U1.CLK", "VDD")),
                            SetEntries("unplaced", ("U2.STATUS", "VSS"))])
    assert text == UNPLACED.replace(
        "    profile: intm4tm2_wire_bond_v0\n",
        "    profile: intm4tm2_wire_bond_v0\n    edges:\n      north: [U1.CLK, VDD]\n").replace(
        "pins: [U2.STATUS, U1.CLK, VDD, VSS]", "pins: [U2.STATUS, VSS]")


def test_an_edge_row_that_begins_the_entries_keeps_its_range():
    """A row whose entries, by what they stand for, begin the new ones keeps
    its bytes; any other row is written whole, each entry as given."""
    ranged = PINS_ONLY.replace("west: [VDD, VSS, U1.CLK]", 'west: [VDD, VSS, "U1.d2d_f[0..2]"]')
    ranged = ranged.replace(
        '  buses:\n    - {ends: [U1, U2], ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}\n',
        "").replace("pins: [U2.STATUS]", "pins: [U2.STATUS, U1.CLK]")
    added = _text(ranged, [SetEntries("west", ("VDD", "VSS", "U1.d2d_f0", "U1.d2d_f1",
                                               "U1.d2d_f2", "U1.CLK")),
                           SetEntries("unplaced", ("U2.STATUS",))])
    assert 'west: [VDD, VSS, "U1.d2d_f[0..2]", U1.CLK]\n' in added
    whole = _text(ranged, [SetEntries("west", ("VDD", "U1.CLK", "U1.d2d_f[0..2]", "VSS")),
                           SetEntries("unplaced", ("U2.STATUS",))])
    assert 'west: [VDD, U1.CLK, "U1.d2d_f[0..2]", VSS]\n' in whole


@pytest.mark.parametrize("edits,needle", [
    ([SetEntries("top", ("U1.CLK",))], "not north, south, west, east or unplaced"),
    ([SetEntries("north", ("U1.CLK",)), SetEntries("north", ("VDD",))], "written twice"),
])
def test_a_row_the_file_cannot_hold_is_refused(edits, needle):
    with pytest.raises(ValueError, match=needle):
        patch_system(UNPLACED.encode(), edits)


def test_emptying_an_unplaced_block_the_file_does_not_have_changes_nothing():
    placed = _text(PINS_ONLY, TO_WEST)
    assert _text(placed, [SetEntries("unplaced", ())]) == placed


def test_crlf_endings_are_kept_through_a_placement():
    crlf = UNPLACED.replace("\n", "\r\n").encode()
    text = patch_system(crlf, PLACE_ALL).text
    assert text == _text(UNPLACED, PLACE_ALL).replace("\n", "\r\n").encode()


def test_renaming_a_rail_renames_it_in_unplaced_pins_too():
    text = _text(UNPLACED, [Replace("rails", "VDD", "name", "VDD_CORE")])
    assert "pins: [U2.STATUS, U1.CLK, VDD_CORE, VSS]" in text
    assert "{name: VDD_CORE, class: power," in text


def test_a_stamp_above_what_the_content_needs_stays_when_nothing_unplaced_went():
    """Only taking away the 1.3 content lowers the stamp; an edit of a 1.3 file
    that held none leaves the stamp as the author wrote it."""
    placed = _text(PINS_ONLY, TO_WEST).replace(
        'format_version: "1.1"', 'format_version: "1.3"')
    moved = _text(placed, [Replace("dies", "U2", "at", (1000, 1100))])
    assert moved.splitlines()[1] == 'format_version: "1.3"'
    changes = patch_system(placed.encode(), [Replace("dies", "U2", "at", (1000, 1100))]).changes
    assert changes == (Change("dies", "U2", "at", "[1000, 1000]", "[1000, 1100]", 16),)
