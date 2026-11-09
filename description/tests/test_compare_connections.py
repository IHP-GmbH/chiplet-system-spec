# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""compare_nets in the connections scope: two sources equal when they connect
the same pins on the same dies, wherever those dies sit and whether or not the
file is placed yet (format 1.3)."""
from __future__ import annotations

import pytest

from chiplet_system_description.source.compare import compare_nets
from chiplet_system_description.source.document import SourceError

from tests.source_dies import DRIVER, ECHO, SOURCE, write_die, write_source
from tests.test_source_plan import Resolver

LANES = ("d2d_f0", "d2d_f1", "d2d_f2", "d2d_r0", "d2d_r1")
BUS = '    - {ends: [U1, U2], ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}\n'
#: :data:`SOURCE` with a wire-bond external: U1.CLK and both rails on the south edge.
REFERENCE = (SOURCE.replace("    - U1.CLK\n", "")
             .replace("\nflow:\n", "\n  external:\n    profile: intm4tm2_wire_bond_v0\n"
                                   "    edges:\n      south: [U1.CLK, VDD, VSS]\nflow:\n"))
#: :data:`REFERENCE` placed otherwise: a bigger carrier, U1 moved and turned,
#: U2 turned back, the pads on another edge in another order, thinner dies.
MOVED = (REFERENCE.replace("size: [2000, 3200]", "size: [2400, 3600]")
         .replace("at: [1000, 2200], rotation: 0", "at: [1200, 2500], rotation: 90")
         .replace("at: [1000, 1000], rotation: 180", "at: [1200, 1000], rotation: 0")
         .replace("thickness: 750", "thickness: 700")
         .replace("south: [U1.CLK, VDD, VSS]", "north: [VSS, VDD, U1.CLK]"))
#: :data:`REFERENCE` as a classic sheet's import leaves it: U1 not placed, no
#: carrier size, and every external pin on no edge yet.
UNPLACED = (REFERENCE.replace('format_version: "1.1"', 'format_version: "1.3"')
            .replace(", size: [2000, 3200]", "")
            .replace("at: [1000, 2200], rotation: 0, ", "")
            .replace("    edges:\n      south: [U1.CLK, VDD, VSS]\n", "")
            .replace("flow:\n  packaging", "unplaced:\n  pins: [VSS, U1.CLK, VDD]\nflow:\n"
                                           "  packaging"))


def _compare(tmp_path, text, reference=REFERENCE, scope="connections"):
    got = write_source(tmp_path / "file", text)
    ref = write_source(tmp_path / "reference", reference)
    return compare_nets(got, ref, Resolver(), scope=scope)


def _codes(found):
    return [(d.code, d.subject) for d in found]


def _volts(text, volts):
    return text.replace('format_version: "1.1"', 'format_version: "1.2"').replace(
        "{name: VDD, class: power, ports:", f"{{name: VDD, class: power, volts: {volts}, ports:")


def test_a_copy_placed_otherwise_is_equal_as_connections_only(tmp_path):
    assert _compare(tmp_path, MOVED) == ()
    found = _compare(tmp_path, MOVED, scope="system")
    assert {"U1", "U2"} <= {d.subject for d in found if d.code == "component_differs"}


def test_an_unplaced_copy_is_equal_either_way_round(tmp_path):
    assert _compare(tmp_path, UNPLACED) == ()
    assert _compare(tmp_path, REFERENCE, reference=UNPLACED) == ()
    assert _compare(tmp_path, UNPLACED, reference=MOVED) == ()


def test_the_system_scope_still_refuses_an_unplaced_file(tmp_path):
    with pytest.raises(SourceError):
        _compare(tmp_path, UNPLACED, scope="system")


def test_the_connections_scope_needs_no_resolver(tmp_path):
    got = write_source(tmp_path / "file", UNPLACED)
    ref = write_source(tmp_path / "reference", REFERENCE)
    assert compare_nets(got, ref, None, scope="connections") == ()


def test_an_unknown_scope_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _compare(tmp_path, REFERENCE, scope="placement")


def test_crossed_lanes_are_joined_otherwise(tmp_path):
    crossed = ('    - {ends: [U1, U2], ports: [d2d_f2, "d2d_r[0..1]"], net_suffix: _n}\n'
               "  links:\n"
               "    - {a: U1.d2d_f0, b: U2.d2d_f1, net: d2d_f0_n}\n"
               "    - {a: U1.d2d_f1, b: U2.d2d_f0, net: d2d_f1_n}\n")
    found = _compare(tmp_path, UNPLACED.replace(BUS, crossed))
    assert _codes(found) == [("joined_otherwise", "d2d_f0_n"), ("joined_otherwise", "d2d_f1_n")]


def test_a_renamed_rail_is_named_otherwise(tmp_path):
    text = (MOVED.replace("{name: VDD,", "{name: VCC,")
            .replace("VSS, VDD, U1.CLK", "VSS, VCC, U1.CLK"))
    assert _codes(_compare(tmp_path, text)) == [("named_otherwise", "VCC")]


def test_rails_with_no_pad_are_external_differences(tmp_path):
    # An edge names a rail once, and a file with an external block whose rail
    # is on no edge is refused by the planner; a file with no external block
    # at all leaves every rail with no pad.
    found = _compare(tmp_path, SOURCE)
    assert _codes(found) == [("external_differs", "U1_PAD_CLK_0"), ("external_differs", "VDD"),
                             ("external_differs", "VSS")]


def test_a_rail_on_no_edge_is_refused_rather_than_compared(tmp_path):
    with pytest.raises(SourceError):
        _compare(tmp_path, UNPLACED.replace("pins: [VSS, U1.CLK, VDD]", "pins: [VSS, U1.CLK]"))


def test_a_port_alone_rather_than_external_is_an_external_difference(tmp_path):
    text = (REFERENCE.replace("south: [U1.CLK, VDD, VSS]", "south: [VDD, VSS]")
            .replace("    - U2.STATUS\n", "    - U2.STATUS\n    - U1.CLK\n"))
    assert _codes(_compare(tmp_path, text)) == [("external_differs", "U1_PAD_CLK_0")]


def test_different_volts_are_a_class_difference(tmp_path):
    found = _compare(tmp_path, _volts(MOVED, 3.3), _volts(REFERENCE, 1.2))
    assert _codes(found) == [("class_differs", "VDD")]


def test_volts_declared_on_one_side_only_are_not_compared(tmp_path):
    assert _compare(tmp_path, _volts(MOVED, 3.3)) == ()


def _width(text, width):
    return text.replace('format_version: "1.1"', 'format_version: "1.5"').replace(
        "{name: VDD, class: power, ports:", f"{{name: VDD, class: power, width_um: {width}, ports:")


def test_a_routing_requirement_is_compared_where_either_side_states_one(tmp_path):
    """Unlike volts, a width stated on one side only differs: the rail routes
    narrow on the other."""
    assert _compare(tmp_path, _width(MOVED, 20), _width(REFERENCE, 20)) == ()
    found = _compare(tmp_path, _width(MOVED, 20), _width(REFERENCE, 40))
    assert [d.message for d in found] == [
        "rail VDD states width_um 20; the reference's VDD states width_um 40"]
    found = _compare(tmp_path, _width(MOVED, 20))
    assert [(d.code, d.subject, d.message) for d in found] == [
        ("class_differs", "VDD",
         "rail VDD states width_um 20; the reference's VDD states no routing requirement")]


def test_a_die_swapped_for_another_is_a_die_difference(tmp_path):
    # the same ports, served by another die
    write_die(tmp_path / "file" / "dies" / "DRIVER_B", "DRIVER_B", DRIVER)
    text = MOVED.replace("die: dies/DRIVER,", "die: dies/DRIVER_B,")
    assert _codes(_compare(tmp_path, text)) == [("die_differs", "U1")]


def test_a_die_the_file_lacks_is_a_die_difference(tmp_path):
    write_die(tmp_path / "reference" / "dies" / "ECHO", "ECHO", ECHO)
    third = ("    - {id: U3, die: dies/ECHO, at: [1000, 3400], rotation: 0, "
             "method: cupillar_opt1, thickness: 750}\n  buses:\n")
    reference = (REFERENCE.replace("size: [2000, 3200]", "size: [2000, 4000]")
                 .replace("  buses:\n", third, 1)
                 .replace("ports: [U1.VDD, U2.VDD]", "ports: [U1.VDD, U2.VDD, U3.VDD]")
                 .replace("ports: [U1.VSS, U2.VSS]", "ports: [U1.VSS, U2.VSS, U3.VSS]")
                 .replace("    - U2.STATUS\n", "    - U2.STATUS\n    - U3.STATUS\n"
                          + "".join(f"    - U3.{p}\n" for p in LANES)))
    found = _compare(tmp_path, MOVED, reference)
    assert found[0].code == "die_differs" and found[0].subject == "U3"
    assert ("not_connected", "U3.STATUS") in _codes(found)
