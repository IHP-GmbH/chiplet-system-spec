# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Classic sheets refused: every code, its subject, and its line.

Each test draws the two-die system of ``test_classic_import.py`` and breaks
one thing on it, as a person could on the sheet. Tests assert codes, subjects
and lines, never messages.
"""
from __future__ import annotations

import json

import pytest

from chiplet_system_description._vendor.chiplet_format_io import ContractVersionWarning
from chiplet_system_description.connectivity import SchematicImportRefused
from chiplet_system_description.classic_import import classic_edits, read_classic_sheet
from chiplet_system_description.source.document import check_text
from chiplet_system_description.source.edit import EditRefused, schematic_edits
from chiplet_system_description.source.inventory import InventoryError, read_die_folder

from tests.source_dies import ECHO, write_die
from tests.test_classic_import import HEADER, PROJECT, Drawing, two_die
from tests.test_source_plan import Resolver


def _found(d):
    return sorted((f.code, f.port) for f in d.refused())


# --- the symbols ---------------------------------------------------------------------------
def test_an_unannotated_symbol(tmp_path):
    d = two_die(tmp_path)
    d.symbol("J4")["ref"] = "J?"
    d.unwire("Net-(J4-Pad1)")
    assert "not_annotated" in d.codes()


def test_a_shared_designator_is_named_once(tmp_path):
    d = two_die(tmp_path)
    d.symbol("J4")["ref"] = "J3"
    d.unwire("Net-(J4-Pad1)")
    d.refusals = [{"code": "duplicate_reference", "message": "front end", "component": "J3"}]
    found = d.refused()
    assert [f.code for f in found].count("duplicate_reference") == 1


def test_a_symbol_from_no_library_of_the_system(tmp_path):
    d = two_die(tmp_path)
    d.symbols.append({"ref": "R1", "identity": None,
                      "key": "/00000009-0000-4000-8000-000000000001",
                      "value": "R", "footprint": "Device:R",
                      "part": {"lib": "Device", "name": "R"},
                      "fields": {"Reference": "R1", "Value": "R"},
                      "pins": ["1", "2"]})
    assert d.codes() == ["unknown_symbol"]


@pytest.mark.parametrize("ref", ["U-1", "1U"])
def test_a_designator_that_is_no_instance_id(tmp_path, ref):
    d = two_die(tmp_path)
    d.symbol("U2")["ref"] = ref
    d.nets = [dict(n, endpoints=[[ref if r == "U2" else r, p] for r, p in n["endpoints"]])
              for n in d.nets]
    assert d.codes() == ["unmappable_name"]


@pytest.mark.parametrize("die", ["dies/NOPE", "/abs/dies/ECHO", "../outside"])
def test_a_die_field_naming_no_die_folder(tmp_path, die):
    d = two_die(tmp_path)
    d.fields("U2", die=die)
    assert d.codes() == ["foreign_part"]


def test_a_die_field_naming_another_die(tmp_path):
    d = two_die(tmp_path)
    d.fields("U2", die="dies/DRIVER")
    assert d.codes() == ["wrong_die"]


def test_a_die_symbol_with_a_pin_more(tmp_path):
    d = two_die(tmp_path)
    d.symbol("U2")["pins"].append("PAD_EXTRA_0")
    assert _found(d) == [("terminal_set_changed", None)]


def test_an_ext_pad_with_two_pins(tmp_path):
    d = two_die(tmp_path)
    d.symbol("J4")["pins"].append("2")
    assert ("terminal_set_changed", "J4") in _found(d)


def test_a_die_with_no_method(tmp_path):
    d = two_die(tmp_path)
    d.fields("U2", method=None)
    assert d.codes() == ["method_missing"]


class Catalog(Resolver):
    """The test resolver, listing methods as a host's registry does."""

    def methods(self, technology, registry):
        self.calls.append(("methods", technology, registry))
        return ("cupillar_opt1", "ubump_opt2")


def test_a_missing_method_lists_what_the_host_offers(tmp_path):
    d = two_die(tmp_path)
    d.fields("U1", method=None)
    d.fields("U2", method=None)
    tech = read_die_folder(tmp_path / "dies" / "ECHO").technology
    asked = []
    with pytest.raises(SchematicImportRefused) as info:
        read_classic_sheet(d.save(), tmp_path, "system.chiplet",
                           methods=lambda t: asked.append(t) or ("a", "b"))
    assert [(r["code"], r["methods"]) for r in info.value.refusals] == [
        ("method_missing", ["a", "b"])] * 2
    assert asked == [tech]      # once per technology


@pytest.mark.parametrize("version", ["2.0", None, "1"])
def test_a_die_contract_of_another_major_is_refused(tmp_path, version):
    folder = write_die(tmp_path / "ECHO", "ECHO", ECHO)
    contract = json.loads((folder / "die-contract.json").read_text(encoding="utf-8"))
    contract["version"] = version
    (folder / "die-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(InventoryError, match="die-contract.json: "):
        read_die_folder(folder)


def test_the_resolver_lists_the_methods_through_the_edit(tmp_path):
    d = two_die(tmp_path)
    d.fields("U2", method=None)
    host = Catalog()
    with pytest.raises(EditRefused) as info:
        schematic_edits(d.path.read_bytes(), path=d.path, resolver=host, form=d.save())
    assert [f.code for f in info.value.findings] == ["method_missing"]
    tech = read_die_folder(tmp_path / "dies" / "ECHO").technology
    assert ("methods", tech, None) in host.calls


@pytest.mark.parametrize("thickness", [None, "0", "-5", "750um", "1e3"])
def test_a_die_with_no_usable_thickness(tmp_path, thickness):
    d = two_die(tmp_path)
    d.fields("U1", thickness=thickness)
    assert d.codes() == ["thickness_missing"]


@pytest.mark.parametrize("edge", ["top", "North"])
def test_an_edge_that_is_none(tmp_path, edge):
    d = two_die(tmp_path)
    d.fields("J3", edge=edge)
    assert _found(d) == [("bad_edge", "J3")]


def test_refusals_land_on_the_die_line_on_a_re_import(tmp_path):
    d = two_die(tmp_path)
    text = d.write()
    d.fields("U2", method=None)
    d.fields("U1", thickness=None)
    lines = {f.code: f.line for f in d.refused()}
    numbered = text.splitlines()
    assert numbered[lines["method_missing"] - 1].startswith("    - {id: U2,")
    assert numbered[lines["thickness_missing"] - 1].startswith("    - {id: U1,")
    d2 = two_die(tmp_path / "first")
    d2.fields("U2", method=None)
    assert [f.line for f in d2.refused()] == [0]


# --- the nets ------------------------------------------------------------------------------
def test_a_link_on_an_auto_named_net(tmp_path):
    d = two_die(tmp_path)
    d.nets[0].update(name="Net-(U1-PadPAD_d2d_f0_0)", auto_named=True)
    assert d.codes() == ["unnamed_net"]


@pytest.mark.parametrize("name", ["d2d f0", "d2d[0..3]"])
def test_a_net_name_that_is_no_name(tmp_path, name):
    d = two_die(tmp_path)
    d.nets[0]["name"] = name
    assert d.codes() == ["unmappable_name"]


def test_a_net_named_with_subscripts_keeps_its_name(tmp_path):
    d = two_die(tmp_path)
    d.nets[0]["name"] = "U1_IO_BOND_outputs[0].output_pad"
    text = d.write()
    assert '    - {a: U1.d2d_f0, b: U2.d2d_f0, net: "U1_IO_BOND_outputs[0].output_pad"}\n' in text
    assert d.edits() == ()


def test_a_rail_name_with_a_dot(tmp_path):
    d = two_die(tmp_path)
    d.rails[0]["name"] = "V.DD"
    next(n for n in d.nets if n["name"] == "VDD")["name"] = "V.DD"
    assert d.codes() == ["unmappable_name"]


def test_a_label_on_one_die_pin(tmp_path):
    d = two_die(tmp_path)
    d.nets[0]["endpoints"] = d.nets[0]["endpoints"][:1]
    assert "lone_label" in d.codes()


def test_a_net_over_three_die_pins(tmp_path):
    d = two_die(tmp_path)
    d.nets[0]["endpoints"].append(["U1", "PAD_d2d_r0_0"])
    d.unwire("d2d_r0_n")
    assert "multi_drop" in d.codes()


def test_an_ext_pad_on_two_die_pins(tmp_path):
    d = two_die(tmp_path)
    d.unwire("Net-(J4-Pad1)")
    d.nets[0]["endpoints"].append(["J4", "1"])
    assert ("external_fanout", "J4") in _found(d)


def test_an_ext_pad_on_no_die_pin(tmp_path):
    d = two_die(tmp_path)
    d.unwire("Net-(J4-Pad1)")
    d.open = ["U2.PAD_STATUS_0"]
    assert _found(d) == [("external_fanout", "J4")]


def test_two_ext_pads_on_one_pin(tmp_path):
    d = two_die(tmp_path)
    d.pad("J5", "north")
    next(n for n in d.nets if n["name"] == "CLK")["endpoints"].append(["J5", "1"])
    assert {code for code, _ in _found(d)} == {"external_fanout"}


def test_a_rail_with_two_ext_pads(tmp_path):
    d = two_die(tmp_path)
    d.pad("J5", "north")
    next(n for n in d.nets if n["name"] == "VDD")["endpoints"].append(["J5", "1"])
    assert d.codes() == ["not_expressible"]


def test_a_rail_on_no_die_pin(tmp_path):
    d = two_die(tmp_path)
    d.pad("J5", "north")
    d.rail("V33", "power", "3.3", "J5.1")
    assert d.codes() == ["not_expressible"]


def test_a_net_on_one_die_twice(tmp_path):
    d = two_die(tmp_path)
    d.unwire("CLK")
    d.symbols = [s for s in d.symbols if s["ref"] != "J3"]
    d.net("loop", "U1.PAD_CLK_0", "U1.PAD_d2d_f0_0")
    d.unwire("d2d_f0_n")
    d.open = ["U2.PAD_d2d_f0_0"]
    assert d.codes() == ["not_expressible"]


def test_a_port_wired_apart(tmp_path):
    d = two_die(tmp_path)
    vdd = next(n for n in d.nets if n["name"] == "VDD")
    vdd["endpoints"].remove(["U1", "PAD_VDD_1"])
    d.open = ["U1.PAD_VDD_1"]
    assert _found(d) == [("pad_split", "U1.VDD")]


def test_a_port_linked_to_two_ports(tmp_path):
    write_die(tmp_path / "dies" / "WIDE", "WIDE", (("bus", "OUTPUT", "SIGNAL", "bus", 2),))
    write_die(tmp_path / "dies" / "NARROW", "NARROW", (("bus", "INPUT", "SIGNAL", "bus", 1),))
    d = two_die(tmp_path)
    d.die("U3", "dies/WIDE")
    d.die("U4", "dies/NARROW")
    d.die("U5", "dies/NARROW")
    d.net("x", "U3.PAD_bus_0", "U4.PAD_bus_0")
    d.net("y", "U3.PAD_bus_1", "U5.PAD_bus_0")
    assert ("pad_split", "U3.bus") in _found(d)


def test_lanes_named_out_of_order(tmp_path):
    write_die(tmp_path / "dies" / "WIDE", "WIDE", (("bus", "OUTPUT", "SIGNAL", "bus", 2),))
    write_die(tmp_path / "dies" / "WIDE_IN", "WIDE_IN", (("bus", "INPUT", "SIGNAL", "bus", 2),))
    d = two_die(tmp_path)
    d.die("U3", "dies/WIDE")
    d.die("U4", "dies/WIDE_IN")
    d.net("bus1", "U3.PAD_bus_0", "U4.PAD_bus_0")
    d.net("bus0", "U3.PAD_bus_1", "U4.PAD_bus_1")
    assert _found(d) == [("rename_no_field", "U3.bus")]
    d.nets[-2]["name"], d.nets[-1]["name"] = "bus0", "bus1"
    text = d.write()
    assert "    - {a: U3.bus, b: U4.bus, net: bus}\n" in text


def test_lanes_crossed_between_ordinals(tmp_path):
    write_die(tmp_path / "dies" / "WIDE", "WIDE", (("bus", "OUTPUT", "SIGNAL", "bus", 2),))
    write_die(tmp_path / "dies" / "WIDE_IN", "WIDE_IN", (("bus", "INPUT", "SIGNAL", "bus", 2),))
    d = two_die(tmp_path)
    d.die("U3", "dies/WIDE")
    d.die("U4", "dies/WIDE_IN")
    d.net("bus0", "U3.PAD_bus_0", "U4.PAD_bus_1")
    d.net("bus1", "U3.PAD_bus_1", "U4.PAD_bus_0")
    assert _found(d) == [("not_expressible", "U3.bus")]


# --- the wiring ----------------------------------------------------------------------------
def test_every_unwired_pin_is_named(tmp_path):
    d = two_die(tmp_path)
    d.unwire("d2d_f0_n")
    assert _found(d) == [("pin_not_wired", "U1.d2d_f0"), ("pin_not_wired", "U2.d2d_f0")]


@pytest.mark.parametrize("flag", ["U1.PAD_TYPO", "J4.2"])
def test_a_no_connect_flag_on_no_pin_of_its_symbol(tmp_path, flag):
    d = two_die(tmp_path)
    d.open = [flag]
    assert _found(d) == [("unknown_terminal", None)]


def test_a_pin_flagged_and_wired(tmp_path):
    d = two_die(tmp_path)
    d.open = ["U1.PAD_CLK_0"]
    assert _found(d) == [("not_expressible", "U1.CLK")]


# --- the file ------------------------------------------------------------------------------
def test_a_project_with_no_external_block(tmp_path):
    d = two_die(tmp_path, project=PROJECT.replace(
        "  external:\n    profile: intm4tm2_wire_bond_v0\n", ""))
    assert d.codes() == ["not_expressible"]


def test_what_the_planner_refuses_is_not_expressible(tmp_path):
    d = two_die(tmp_path)
    # a ground rail on power ports: the sheet reads, the file cannot hold it
    d.rails[0]["class"], d.rails[0]["volts"] = "ground", None
    found = d.refused()
    assert {f.code for f in found} == {"not_expressible"}
    assert all(f.line > 0 for f in found)


def test_a_project_that_is_no_source(tmp_path):
    d = two_die(tmp_path, project=HEADER + "system: [\n")
    assert d.codes() == ["not_a_source"]


def _rename(d, old, new):
    d.symbol(old)["ref"] = new
    for net in d.nets:
        net["endpoints"] = [[new if ref == old else ref, pin] for ref, pin in net["endpoints"]]


def test_a_reannotated_symbol_is_refused_against_the_previous_form(tmp_path):
    d = two_die(tmp_path)
    prior = tmp_path / "previous.json"
    prior.write_text(json.dumps(d.form()), encoding="utf-8")
    _rename(d, "U2", "U9")
    with pytest.raises(SchematicImportRefused) as info:
        read_classic_sheet(d.save(), tmp_path, "system.chiplet", previous=prior)
    assert info.value.codes == ["reference_changed"]
    assert [r.get("was") for r in info.value.refusals] == ["U2"]
    assert [die.id for die in read_classic_sheet(d.form_path, tmp_path, "system.chiplet").dies] == [
        "U1", "U9"]


def test_schematic_edits_puts_a_reannotation_on_the_old_die(tmp_path):
    d = two_die(tmp_path)
    d.write()
    prior = tmp_path / "previous.json"
    prior.write_text(json.dumps(d.form()), encoding="utf-8")
    source, findings = check_text(d.path.read_text(encoding="utf-8"), d.path)
    assert source is not None and not findings
    want = next(die.line for die in source.dies if die.id == "U2")
    _rename(d, "U2", "U9")
    with pytest.raises(EditRefused) as info:
        schematic_edits(d.path.read_bytes(), path=d.path, resolver=Resolver(),
                        form=d.save(), previous=prior)
    assert [f.code for f in info.value.findings] == ["reference_changed"]
    assert info.value.findings[0].line == want and want != 0


def test_previous_on_a_blank_or_generated_sheet_is_refused(tmp_path):
    from tests.test_source_blank_sheet import Sheet

    sheet = Sheet(tmp_path)
    sheet.form_path.parent.mkdir(exist_ok=True)
    sheet.form_path.write_text(json.dumps(sheet.form()), encoding="utf-8")
    with pytest.raises(EditRefused) as info:
        schematic_edits(sheet.path.read_bytes(), path=sheet.path, resolver=Resolver(),
                        form=sheet.form_path, previous=sheet.form_path)
    assert [f.code for f in info.value.findings] == ["sheet_kind"]
    doc = sheet.form()
    doc.update(version="1.0", sheet="generated", base_sha256="ab",
               generator_report={"path": "report.json", "sha256": "cd"})
    generated = tmp_path / "generated.json"
    generated.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(EditRefused) as info:
        schematic_edits(sheet.path.read_bytes(), path=sheet.path, resolver=Resolver(),
                        form=generated, design=b"x", previous=generated)
    assert [f.code for f in info.value.findings] == ["sheet_kind"]


def test_classic_edits_refuses_a_reannotated_symbol(tmp_path):
    d = two_die(tmp_path)
    prior = tmp_path / "previous.json"
    prior.write_text(json.dumps(d.form()), encoding="utf-8")
    _rename(d, "J3", "J9")
    with pytest.raises(EditRefused) as info:
        classic_edits(d.path.read_bytes(), path=d.path, form=d.save(), previous=prior)
    assert [f.code for f in info.value.findings] == ["reference_changed"]


def test_an_open_port_whose_pads_share_a_name_is_refused(tmp_path):
    folder = tmp_path / "dies" / "RAIL"
    write_die(folder, "RAIL", (("GND", "INOUT", "GROUND", "GND", 2),))
    pins = folder / "pins.json"
    doc = json.loads(pins.read_text(encoding="utf-8"))
    for row in doc["pins"]:
        row["name"] = "GND"
    pins.write_text(json.dumps(doc), encoding="utf-8")
    contract_path = folder / "die-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for term in contract["terminals"]:
        term["name"] = "GND"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    d = Drawing(tmp_path)
    d.die("U1", "dies/RAIL")
    d.open = ["U1.GND"]
    assert d.codes() == ["open_shared_pads"]


def test_a_contract_whose_pads_are_not_the_inventory_is_refused(tmp_path):
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["terminals"] = contract["terminals"][:-1]
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert d.codes() == ["contract_pads_differ"]


def _drop_port(folder, port, *, rename=None):
    """Take ``port`` off the inventory and the contract together."""
    pins_path = folder / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    for row in pins["pins"]:
        if row.get("port") == port:
            if rename is not None:
                row["name"] = rename
            del row["port"]
            del row["ordinal"]
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    contract_path = folder / "die-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for term in contract["terminals"]:
        if term.get("port") == port:
            if rename is not None:
                term["name"] = rename
            del term["port"]
            del term["ordinal"]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")


def _nc_sheet(tmp_path):
    """CLK plus two non-port pads named NC. The import refuses the die at read."""
    folder = tmp_path / "dies" / "N"
    write_die(folder, "N", (
        ("CLK", "INPUT", "SIGNAL", "CLK", 1),
        ("NC", "INPUT", "SIGNAL", "NC", 2),
    ))
    d = Drawing(tmp_path)
    d.die("U1", "dies/N")
    _drop_port(folder, "NC", rename="NC")
    return d


def test_a_die_with_a_pad_that_serves_no_port_is_unported_pads(tmp_path):
    d = _nc_sheet(tmp_path)
    assert d.codes() == ["unported_pads"]


def test_a_die_that_states_no_port_names_no_pad(tmp_path):
    folder = tmp_path / "dies" / "N"
    write_die(folder, "N", (("CLK", "INPUT", "SIGNAL", "CLK", 1),))
    d = Drawing(tmp_path)
    d.die("U1", "dies/N")
    _drop_port(folder, "CLK")
    found = d.refused()
    assert [f.code for f in found] == ["unported_pads"]
    assert "states no ports" in found[0].message


def test_a_contract_port_that_is_not_the_inventory_port_is_refused(tmp_path):
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    for term in contract["terminals"]:
        if term.get("port") == "CLK":
            term["port"] = "OTHER"
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert d.codes() == ["contract_pads_differ"]


def test_a_power_port_the_contract_leaves_unported_is_refused(tmp_path):
    """Inventory VDD with no port on the contract must not become a rail."""
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    for term in contract["terminals"]:
        if term.get("port") == "VDD":
            del term["port"]
            del term["ordinal"]
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert d.codes() == ["contract_pads_differ"]


def test_a_pad_set_that_differs_is_refused_before_a_later_contract_error(tmp_path):
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["version"] = "1.1"
    contract["terminals"][0]["role"] = "future"
    contract["terminals"].pop()
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert d.codes() == ["contract_pads_differ"]


def test_a_contract_with_no_port_identity_is_not_expressible(tmp_path):
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["version"] = "1.0"
    for term in contract["terminals"]:
        term.pop("port", None)
        term.pop("ordinal", None)
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert d.codes() == ["not_expressible"]


def test_an_incomplete_newer_contract_is_not_expressible(tmp_path):
    d = two_die(tmp_path)
    path = tmp_path / "dies" / "DRIVER" / "die-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["version"] = "1.3"
    contract["terminals"][0]["role"] = "future"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.warns(ContractVersionWarning, match="'1.3' is newer"):
        assert d.codes() == ["not_expressible"]


def test_an_open_port_of_distinct_pads_stays_open(tmp_path):
    write_die(tmp_path / "dies" / "WIDE", "WIDE", (("bus", "OUTPUT", "SIGNAL", "bus", 2),))
    d = Drawing(tmp_path)
    d.die("U1", "dies/WIDE")
    d.open = ["U1.PAD_bus_0", "U1.PAD_bus_1"]
    assert "  open:\n    - U1.bus\n" in d.write()


def test_read_classic_sheet_refuses_another_kind(tmp_path):
    d = two_die(tmp_path)
    form = d.form()
    form["sheet"] = "blank"
    for key in ("system_library", "rails", "open"):
        del form[key]
    for sym in form["symbols"]:
        for key in ("fields", "key", "value", "footprint"):
            del sym[key]
    d.form_path.parent.mkdir()
    d.form_path.write_text(__import__("json").dumps(form))
    with pytest.raises(SchematicImportRefused) as info:
        read_classic_sheet(d.form_path, tmp_path, "system.chiplet")
    assert info.value.codes == ["sheet_kind"]


@pytest.mark.parametrize("rails", ["oops", [{"name": "VDD", "class": "power", "volts": "3V3"}]])
def test_a_form_that_does_not_load_is_refused_for_itself(tmp_path, rails):
    """The project file is unplaced and does not plan; the form's own defect
    is what the person is told, not the file's state."""
    d = two_die(tmp_path)
    d.rails = rails
    assert d.codes() == ["malformed_form"]
    d2 = two_die(tmp_path / "again")
    d2.write()
    d2.rails = rails
    assert {(f.code, f.line) for f in d2.refused()} == {("malformed_form", 0)}
