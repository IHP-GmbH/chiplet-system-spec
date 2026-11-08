# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The converter: a System a spec built, written as a source that elaborates back to it."""
from __future__ import annotations

import dataclasses
import json

import pytest

from chiplet_system_description import InterposerIoPad, Position, Tie
from chiplet_system_description.source import check_text, read_source
from chiplet_system_description.source.elaborate import elaborate_source
from chiplet_system_description.source.convert import (ConversionError, same_design,
                                                        source_from_system)
from chiplet_system_description.source.inventory import InventoryError

from tests.source_dies import SOURCE, checked, write_source
from tests.test_source_plan import Resolver, _by_hand

FOLDERS = {"U1": "dies/DRIVER", "U2": "dies/ECHO"}
FLOW = "flow:\n  packaging_flow:\n    checks: {template: workshop}\n"


class SouthRow(Resolver):
    """Carrier pads along the south edge, in the order the edges name them."""

    def io_pads(self, profile, body, edges):
        return tuple(InterposerIoPad(f"J_{net}", net, Position(100.0 + 100.0 * i, 40.0), 80.0, 80.0)
                     for i, net in enumerate(n for _, nets in edges for n in nets))


def _system(tmp_path):
    write_source(tmp_path)  # the die folders the builder reads too
    return _by_hand(tmp_path)


def _convert(tmp_path, system, **kw):
    kw.setdefault("interposer_adapter", "intm4tm2")
    text = source_from_system(system, FOLDERS, base=tmp_path, **kw)
    path = tmp_path / "converted.chiplet"
    path.write_text(text, encoding="utf-8")
    return text, path


def test_the_builders_system_converts_to_the_fixture(tmp_path):
    system = _system(tmp_path)
    text, path = _convert(tmp_path, system, flow_text=FLOW,
                          comment="A two-die source the tests mutate line by line.")
    # The fixture writes U2 first in alone; the converter sorts by die. Nothing else differs.
    assert text == SOURCE.replace('    - U2.STATUS\n    - U1.CLK\n',
                                  '    - U1.CLK\n    - U2.STATUS\n')
    assert same_design(system, path, Resolver()) is None


def test_alone_is_written_by_die_then_name_whatever_the_tie_order(tmp_path):
    system = _system(tmp_path)
    text, path = _convert(tmp_path, dataclasses.replace(system,
                                                        ties=tuple(reversed(system.ties))))
    assert "  alone:\n    - U1.CLK\n    - U2.STATUS\n" in text
    # A source ties alone ports in inventory order, so it comes back in that order.
    assert same_design(system, path, Resolver()) is None


def test_a_pad_that_serves_no_port_is_refused_before_a_source_is_written(tmp_path):
    system = _system(tmp_path)
    inv = tmp_path / "dies" / "DRIVER" / "pins.json"
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["pins"].append({
        "name": "VDDX", "pad_index": 99,
        "center_x_dbu": 1000.0, "center_y_dbu": 1000.0,
        "width_dbu": 1000.0, "height_dbu": 1000.0,
        "net": "VDDX", "direction": "INOUT", "role": "POWER",
    })
    inv.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InventoryError, match="pad 'VDDX' serves no port"):
        source_from_system(system, FOLDERS, base=tmp_path)


def test_a_port_nothing_wires_is_written_open(tmp_path):
    system = _system(tmp_path)
    system = dataclasses.replace(system, ties=tuple(t for t in system.ties if t.instance != "U1"))
    text, path = _convert(tmp_path, system)
    assert '  open:\n    - U1.CLK\n' in text
    assert same_design(system, path, Resolver()) is None


def test_links_lift_to_a_bus_only_until_one_cannot(tmp_path):
    system = _system(tmp_path)
    links = list(system.links)
    links[3] = dataclasses.replace(links[3], net_prefix="back0")
    system = dataclasses.replace(system, links=tuple(links))
    text, path = _convert(tmp_path, system)
    assert '    - {ends: [U1, U2], ports: ["d2d_f[0..2]"], net_suffix: _n}\n' in text
    # Each link keeps its ends in the System's order (the builder puts the manager first).
    assert ('  links:\n    - {a: U2.d2d_r0, b: U1.d2d_r0, net: back0}\n'
            '    - {a: U2.d2d_r1, b: U1.d2d_r1, net: d2d_r1_n}\n') in text
    assert same_design(system, path, Resolver()) is None


def test_carrier_pads_become_edges_and_take_their_ports_out_of_alone(tmp_path):
    system = _system(tmp_path)
    pads = SouthRow().io_pads(None, None, [("south", ("U1_PAD_CLK_0", "VDD", "VSS"))])
    system = dataclasses.replace(system, interposer=dataclasses.replace(system.interposer,
                                                                          io_pads=pads))
    text, path = _convert(tmp_path, system, profile="intm4tm2_wire_bond_v0")
    source, findings = check_text(text)
    assert findings == []
    assert '  alone:\n    - U2.STATUS\n' in text
    assert ('  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
            '      south: [U1.CLK, VDD, VSS]\n') in text
    assert same_design(system, path, SouthRow()) is None
    with pytest.raises(ConversionError, match="no profile"):
        _convert(tmp_path, system)


def test_a_one_pad_classed_tie_is_a_rail(tmp_path):
    system = _system(tmp_path)
    system = dataclasses.replace(system, supplies=system.supplies[1:], ties=system.ties + (
        Tie("VDD_ECHO", "U2", "PAD_VDD_0", net_class="power"),))
    text, path = _convert(tmp_path, system)
    assert ('  rails:\n    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n'
            '    - {name: VDD_ECHO, class: power, ports: [U2.VDD]}\n') in text
    assert '  open:\n    - U1.VDD\n' in text
    assert same_design(system, path, Resolver()) is None


def test_a_design_that_does_not_come_back_is_a_diff(tmp_path):
    system = _system(tmp_path)
    _, path = _convert(tmp_path, system)
    path.write_text(path.read_text().replace("at: [1000, 2200]", "at: [1000, 2100]"))
    diff = same_design(system, path, Resolver())
    assert diff.startswith("--- system\n") and "2100" in diff


@pytest.mark.parametrize("change,needle", [
    (lambda s: dataclasses.replace(s, ties=(dataclasses.replace(s.ties[0], net="U1_CLK_PAD"),)
                                   + s.ties[1:]),
     "tied to a net not named U1_PAD_CLK_0"),
    (lambda s: dataclasses.replace(s, instances=(dataclasses.replace(s.instances[0],
                                                                     rotation_z=45.0),)
                                   + s.instances[1:]),
     "rotation 45 is not"),
    (lambda s: dataclasses.replace(s, supplies=(dataclasses.replace(
        s.supplies[0], endpoints=s.supplies[0].endpoints[1:]),) + s.supplies[1:]),
     "supply VDD: takes pads ['PAD_VDD_1'] of U1.VDD"),
])
def test_what_a_source_cannot_say_is_refused(tmp_path, change, needle):
    with pytest.raises(ConversionError) as info:
        _convert(tmp_path, change(_system(tmp_path)))
    assert needle in str(info.value)


@pytest.mark.parametrize("halo", [(("M4", 20.0), ("M5", 20.0), ("TM1", 60.0), ("TM2", 60.0)),
                                  {"M4": 20.0, "M5": 20.0, "TM1": 60.0, "TM2": 60.0}])
def test_a_fill_keepout_is_written_as_one_line_and_comes_back(tmp_path, halo):
    system = _system(tmp_path)
    text, path = _convert(tmp_path, system, fill_keepout=halo)
    assert "  fill_keepout: {halo_um: {M4: 20, M5: 20, TM1: 60, TM2: 60}}\n" in text
    assert read_source(path).fill_keepout.halo_um == (("M4", 20), ("M5", 20), ("TM1", 60),
                                                      ("TM2", 60))
    _, extras = elaborate_source(path, Resolver())
    assert extras.fill_keepout == dict(halo)
    assert list(extras.fill_keepout) == ["M4", "M5", "TM1", "TM2"]
    assert same_design(system, path, Resolver()) is None


def test_a_fractional_halo_width_keeps_its_digits(tmp_path):
    text, path = _convert(tmp_path, _system(tmp_path), fill_keepout={"TM1": 60.125})
    assert "  fill_keepout: {halo_um: {TM1: 60.125}}\n" in text
    assert elaborate_source(path, Resolver())[1].fill_keepout == {"TM1": 60.125}


def test_a_thickness_not_given_is_left_out_and_comes_back_as_its_note(tmp_path):
    system = _system(tmp_path)
    u2 = system.instances[1]
    die = dataclasses.replace(u2.die, dimensions=dataclasses.replace(u2.die.dimensions,
                                                                     thickness=0.0))
    system = dataclasses.replace(system, instances=(system.instances[0],
                                                    dataclasses.replace(u2, die=die)))
    text, path = _convert(tmp_path, system)
    assert ('    - {id: U2, die: dies/ECHO, at: [1000, 1000], rotation: 180, '
            'method: cupillar_opt1}\n') in text
    _, extras = elaborate_source(path, Resolver())
    assert checked(extras.notes) == (f"{path}:15: note: dies: U2: thickness not given; 0.0 used",)
    assert same_design(system, path, Resolver()) is None
