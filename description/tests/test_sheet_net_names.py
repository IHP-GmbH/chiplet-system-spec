# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Each system net the planner names, mapped onto the classic sheet's own net.

The sheet is the two-die classic drawing. Tests assert codes, never messages.
"""
from __future__ import annotations

import json

import pytest

from chiplet_system_description.connectivity import SchematicImportRefused, parse_form
from chiplet_system_description.source import read_source, sheet_net_names
from chiplet_system_description.source.document import check_text
from chiplet_system_description.source.plan import ALONE, EDGE, UNPLACED, planned_nets

from tests.source_dies import write_die, write_source
from tests.test_classic_import import LANES, Drawing, two_die


def _pair(tmp_path):
    d = two_die(tmp_path)
    d.write()
    source, findings = check_text(d.path.read_text(encoding="utf-8"), d.path)
    assert source is not None and not findings
    return d, source


def test_each_system_net_maps_to_the_sheet_net_of_its_endpoints(tmp_path):
    d, source = _pair(tmp_path)
    assert sheet_net_names(source, parse_form(d.form())) == {
        **{f"{lane}_n": f"{lane}_n" for lane in LANES},
        "VDD": "VDD",
        "GND": "GND",
        "U1_PAD_CLK_0": "CLK",
        "U2_PAD_STATUS_0": "Net-(J4-Pad1)",
    }


def test_a_system_net_split_across_two_sheet_nets_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    net = next(n for n in d.nets if n["name"] == "d2d_f0_n")
    (a_ref, a_pin), (b_ref, b_pin) = net["endpoints"]
    d.nets = [n for n in d.nets if n["name"] != "d2d_f0_n"]
    d.net("left", f"{a_ref}.{a_pin}")
    d.net("right", f"{b_ref}.{b_pin}")
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_two_system_nets_in_one_sheet_net_are_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    ends = [ep for n in d.nets if n["name"] in ("d2d_f0_n", "d2d_f1_n") for ep in n["endpoints"]]
    d.nets = [n for n in d.nets if n["name"] not in ("d2d_f0_n", "d2d_f1_n")]
    d.nets.append({"name": "joined", "auto_named": False, "endpoints": ends})
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


@pytest.mark.parametrize("bad", ['a"b', "has\rbreak", "has\nbreak"])
def test_a_sheet_net_name_the_board_cannot_write_is_refused(tmp_path, bad):
    d, source = _pair(tmp_path)
    next(n for n in d.nets if n["name"] == "CLK")["name"] = bad
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["unwritable_net_name"]


def test_an_escaped_sheet_net_name_is_kept(tmp_path):
    d, source = _pair(tmp_path)
    next(n for n in d.nets if n["name"] == "CLK")["name"] = "{slash}"
    mapped = sheet_net_names(source, parse_form(d.form()))
    assert mapped["U1_PAD_CLK_0"] == "{slash}"


def test_a_quote_on_an_unconnected_net_the_map_does_not_return_is_not_unwritable(tmp_path):
    d, source = _pair(tmp_path)
    d.nets.append({"name": 'unconnected-(U1-Pad"x)', "auto_named": True,
                   "endpoints": [["U1", "PAD"]]})
    mapped = sheet_net_names(source, parse_form(d.form()))
    assert mapped["U1_PAD_CLK_0"] == "CLK"


def test_a_rail_split_across_two_sheet_nets_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    net = next(n for n in d.nets if n["name"] == "VDD")
    first, rest = net["endpoints"][:1], net["endpoints"][1:]
    d.nets = [n for n in d.nets if n["name"] != "VDD"]
    d.nets.append({"name": "VDD_a", "auto_named": False, "endpoints": first})
    d.nets.append({"name": "VDD_b", "auto_named": False, "endpoints": rest})
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_a_rail_merged_with_a_link_pin_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    link = next(n for n in d.nets if n["name"] == "d2d_f0_n")
    vdd = next(n for n in d.nets if n["name"] == "VDD")
    vdd["endpoints"] = vdd["endpoints"] + link["endpoints"][:1]
    d.nets = [n for n in d.nets if n["name"] != "d2d_f0_n"]
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_a_rail_no_sheet_net_holds_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    d.nets = [n for n in d.nets if n["name"] != "VDD"]
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_a_one_pad_rail_tie_maps_to_its_sheet_net(tmp_path):
    write_die(tmp_path / "dies" / "ONE", "ONE", (("VDD", "INOUT", "POWER", "VDD", 1),))
    d = Drawing(tmp_path)
    d.die("U1", "dies/ONE")
    d.pad("J1", "west")
    d.rail("VDD", "power", "1.2", "U1.PAD_VDD_0", "J1.1")
    d.write()
    source, findings = check_text(d.path.read_text(encoding="utf-8"), d.path)
    assert source is not None and not findings
    assert sheet_net_names(source, parse_form(d.form()))["VDD"] == "VDD"


def test_a_multi_lane_link_keeps_its_lane_indexed_names(tmp_path):
    spec = (("bus", "OUTPUT", "SIGNAL", "bus", 2),)
    other = (("bus", "INPUT", "SIGNAL", "bus", 2),)
    write_die(tmp_path / "dies" / "A", "A", spec)
    write_die(tmp_path / "dies" / "B", "B", other)
    d = Drawing(tmp_path)
    d.die("U1", "dies/A")
    d.die("U2", "dies/B")
    d.net("bus0", "U1.PAD_bus_0", "U2.PAD_bus_0")
    d.net("bus1", "U1.PAD_bus_1", "U2.PAD_bus_1")
    d.write()
    source, findings = check_text(d.path.read_text(encoding="utf-8"), d.path)
    assert source is not None and not findings
    mapped = sheet_net_names(source, parse_form(d.form()))
    assert mapped["bus0"] == "bus0" and mapped["bus1"] == "bus1"


def test_an_alone_pad_maps_to_its_unconnected_net(tmp_path):
    d, _source = _pair(tmp_path)
    text = d.path.read_text(encoding="utf-8")
    text = text.replace("west: [VDD, U1.CLK]", "west: [VDD]")
    text = text.replace("  external:\n", "  alone:\n    - U1.CLK\n  external:\n")
    d.path.write_text(text, encoding="utf-8")
    source, findings = check_text(text, d.path)
    assert source is not None and not findings
    clk = next(n for n in d.nets if n["name"] == "CLK")
    clk["name"] = "unconnected-(U1-PadPAD_CLK_0)"
    clk["auto_named"] = True
    clk["endpoints"] = [ep for ep in clk["endpoints"] if ep[0] != "J3"]
    mapped = sheet_net_names(source, parse_form(d.form()))
    assert mapped["U1_PAD_CLK_0"] == "unconnected-(U1-PadPAD_CLK_0)"


def test_a_planner_refusal_returns_no_map(tmp_path):
    d, _source = _pair(tmp_path)
    text = d.path.read_text(encoding="utf-8").replace(
        "{a: U1.d2d_f0, b: U2.d2d_f0, net: d2d_f0_n}",
        "{a: U1.NOPE, b: U2.d2d_f0, net: d2d_f0_n}")
    d.path.write_text(text, encoding="utf-8")
    source, findings = check_text(text, d.path)
    assert source is not None and not findings
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_expressible"]


def test_a_sheet_net_with_unclaimed_die_pins_is_refused(tmp_path):
    d, source = _pair(tmp_path)
    d.nets.append({"name": "stray", "auto_named": False,
                   "endpoints": [["U1", "PAD_NO"], ["U2", "PAD_NO"]]})
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["unclaimed_pins"]


def test_a_j_on_a_link_net_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    d.pad("J9", "west")
    link = next(n for n in d.nets if n["name"] == "d2d_f0_n")
    link["endpoints"].append(["J9", "1"])
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_an_alone_pad_drawn_to_a_j_is_not_one_to_one(tmp_path):
    d, _source = _pair(tmp_path)
    text = d.path.read_text(encoding="utf-8")
    text = text.replace("west: [VDD, U1.CLK]", "west: [VDD]")
    text = text.replace("  external:\n", "  alone:\n    - U1.CLK\n  external:\n")
    d.path.write_text(text, encoding="utf-8")
    source, findings = check_text(text, d.path)
    assert source is not None and not findings
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_a_tie_with_two_js_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    d.pad("J9", "east")
    clk = next(n for n in d.nets if n["name"] == "CLK")
    clk["endpoints"].append(["J9", "1"])
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]


def test_a_ref_that_is_not_a_die_or_an_ext_pad_is_refused(tmp_path):
    d, source = _pair(tmp_path)
    d.symbols.append({
        "ref": "R1", "identity": None, "key": d._key(),
        "value": "R", "footprint": "Device:R",
        "part": {"lib": "Device", "name": "R"},
        "fields": {"Reference": "R1", "Value": "R", "Footprint": "Device:R"},
        "pins": ["1"],
    })
    d.net("stray", "R1.1")
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["foreign_ref"]


def _spare(folder):
    """A signal port the planner can account, and no planned net claims."""
    pins_path = folder / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins["pins"].append({
        "name": "PAD_SPARE_0", "pad_index": 99,
        "center_x_dbu": 1000.0, "center_y_dbu": 1000.0,
        "width_dbu": 1000.0, "height_dbu": 1000.0,
        "net": "SPARE", "direction": "INPUT", "role": "SIGNAL",
        "port": "SPARE", "ordinal": 0,
    })
    pins_path.write_text(json.dumps(pins), encoding="utf-8")


def test_a_sheet_net_mixing_claimed_and_unclaimed_pins_is_unclaimed(tmp_path):
    d, _source = _pair(tmp_path)
    _spare(tmp_path / "dies" / "DRIVER")
    text = d.path.read_text(encoding="utf-8")
    needle = "  external:\n"
    assert text.count(needle) == 1
    text = text.replace(needle, "  open:\n    - U1.SPARE\n" + needle, 1)
    d.path.write_text(text, encoding="utf-8")
    source, findings = check_text(text, d.path)
    assert source is not None and not findings
    clk = next(n for n in d.nets if n["name"] == "CLK")
    clk["endpoints"].append(["U1", "PAD_SPARE_0"])
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one", "unclaimed_pins"]


def test_leaving_is_the_tie_s_own_property(tmp_path):
    _d, source = _pair(tmp_path)
    topology, nets = planned_nets(source, unplaced=True)
    clk = next(t for t in topology.ties if t.port == "CLK")
    status = next(t for t in topology.ties if t.port == "STATUS")
    assert clk.origin == EDGE and clk.leaves
    assert status.origin == UNPLACED and status.leaves
    assert next(s for s in topology.supplies if s.net == "VDD").leaves
    assert not next(n for n in nets if n.name == "d2d_f0_n").leaves
    plain = read_source(write_source(tmp_path / "plain"))
    stayed, _named = planned_nets(plain)
    alone = next(t for t in stayed.ties if t.instance == "U1" and t.port == "CLK")
    assert alone.origin == ALONE and not alone.leaves
    assert not next(s for s in stayed.supplies if s.net == "VDD").leaves


def test_an_unwritable_name_does_not_hide_another_refusal(tmp_path):
    d, source = _pair(tmp_path)
    d.nets = [n for n in d.nets if n["name"] != "VDD"]
    next(n for n in d.nets if n["name"] == "CLK")["name"] = 'a"b'
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one", "unwritable_net_name"]


def test_a_tie_on_a_j_less_net_is_not_one_to_one(tmp_path):
    d, source = _pair(tmp_path)
    clk = next(n for n in d.nets if n["name"] == "CLK")
    clk["endpoints"] = [ep for ep in clk["endpoints"] if ep[0] != "J3"]
    with pytest.raises(SchematicImportRefused) as info:
        sheet_net_names(source, parse_form(d.form()))
    assert info.value.codes == ["not_one_to_one"]
