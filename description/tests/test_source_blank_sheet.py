# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Blank sheets: a sheet a person drew from the symbol library, written into
the given file it was drawn against, and the file compared with a reference.

The given file is the two-die source with its bus left out, so ten die ports
are unaccounted and only the sheet accounts for them. The front end is a
faithful one: the form a KiCad reader would write for the sheet as wired.
Tests assert codes, subjects and lines, never messages.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from chiplet_system_description.connectivity import SchematicImportRefused, parse_form
from chiplet_system_description.source.compare import compare_nets
from chiplet_system_description.source.document import SourceError
from chiplet_system_description.source.edit import (EditRefused, board_edits, patch_system,
                                                    schematic_edits)
from chiplet_system_description.source.elaborate import elaborate_source

from tests.source_dies import SOURCE, write_source
from tests.test_source_edit import EXTERNAL
from tests.test_source_plan import Resolver
from tests.test_source_sheet import PADS, Run

BUS = '  buses:\n    - {ends: [U1, U2], ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}\n'
GIVEN = SOURCE.replace(BUS, "")
LANES = ("d2d_f0", "d2d_f1", "d2d_f2", "d2d_r0", "d2d_r1")
PARTS = {"U1": "DRIVER", "U2": "ECHO"}
LIBRARY = "chiplet_dies"


def _pins(cid):
    return sorted(PADS[cid])


class Sheet:
    """A blank sheet of the given file, wired lane by lane as the diagram says."""

    def __init__(self, tmp_path, text=GIVEN):
        self.root = tmp_path
        self.path = write_source(tmp_path, text)
        self.symbols = [{"ref": c, "identity": None, "part": {"lib": LIBRARY, "name": n},
                         "pins": _pins(c)} for c, n in PARTS.items()]
        self.nets = [{"name": f"{lane}_n", "auto_named": False,
                      "endpoints": [["U1", f"PAD_{lane}_0"], ["U2", f"PAD_{lane}_0"]]}
                     for lane in LANES]
        self.version = "1.1"
        self.form_path = tmp_path / "sheet_first" / "connectivity.json"

    def form(self):
        used = {tuple(ep) for n in self.nets for ep in n["endpoints"]}
        # one node per (ref, pin), as a netlist has it, even under a shared ref
        bare = dict.fromkeys((s["ref"], p) for s in self.symbols for p in s["pins"]
                             if (s["ref"], p) not in used)
        loose = [{"name": f"unconnected-({r}-Pad{p})", "auto_named": True,
                  "endpoints": [[r, p]]} for r, p in bare]
        return {
            "format": "chiplet_system.connectivity", "version": self.version,
            "sheet": "blank", "base_sha256": None, "generator_report": None,
            # gds_to_kicad's reader writes no name: it is the file's stem
            "library": {"path": f"{LIBRARY}.kicad_sym",
                        "sha256": hashlib.sha256(b"library").hexdigest()},
            "front_end": {"tool": "kicad", "tool_version": "9.99", "reader": "test 1",
                          "inputs": []},
            "symbols": self.symbols, "nets": self.nets + loose,
        }

    def edits(self):
        self.form_path.parent.mkdir(exist_ok=True)
        self.form_path.write_text(json.dumps(self.form(), indent=2))
        return schematic_edits(self.path.read_bytes(), path=self.path, resolver=Resolver(),
                               form=self.form_path)

    def write(self):
        self.path.write_bytes(patch_system(self.path.read_bytes(), self.edits()).text)
        return self.path.read_text()

    def refusal(self):
        with pytest.raises(EditRefused) as info:
            self.edits()
        return [(f.code, f.port) for f in info.value.findings]

    def lines(self):
        with pytest.raises(EditRefused) as info:
            self.edits()
        return {(f.code, f.port): f.line for f in info.value.findings}

    # -- what a person does on the sheet
    def unwire(self, lane):
        self.nets = [n for n in self.nets if n["name"] != f"{lane}_n"]

    def symbol(self, ref):
        return next(s for s in self.symbols if s["ref"] == ref)


def _line(text, needle):
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


# --- the given file as a sheet base ------------------------------------------------------
def test_the_given_file_is_refused_unless_read_as_a_sheet_base(tmp_path):
    path = write_source(tmp_path, GIVEN)
    with pytest.raises(SourceError):
        elaborate_source(path, Resolver())
    system, _ = elaborate_source(path, Resolver(), sheet_base=True)
    assert {i.id for i in system.instances} >= {"U1", "U2"}


def test_a_sheet_base_still_refuses_every_other_defect(tmp_path):
    path = write_source(tmp_path, GIVEN.replace("ports: [U1.VDD, U2.VDD]",
                                                "ports: [U1.VDD, U2.NOPE]"))
    with pytest.raises(SourceError):
        elaborate_source(path, Resolver(), sheet_base=True)


@pytest.mark.parametrize("text", [
    GIVEN.replace("ports: [U1.VDD, U2.VDD]", "ports: [U1.VDD]"),   # a rail port
    GIVEN.replace("    - U1.CLK\n", ""),                           # a port with no partner
], ids=["rail", "alone"])
def test_a_sheet_base_refuses_a_port_the_sheet_cannot_wire(tmp_path, text):
    path = write_source(tmp_path, text)
    with pytest.raises(SourceError):
        elaborate_source(path, Resolver(), sheet_base=True)


def test_a_board_edit_of_the_given_file_is_still_refused(tmp_path):
    path = write_source(tmp_path, GIVEN)
    with pytest.raises(EditRefused):
        board_edits(path.read_bytes(), path=path, resolver=Resolver(), reconciliation={})


# --- a sheet wired as the diagram says ---------------------------------------------------
def test_a_wired_sheet_writes_the_bus_and_matches_the_reference(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.write()
    reference = write_source(tmp_path / "reference")
    assert compare_nets(sheet.path, reference, Resolver()) == ()


def test_a_reimport_of_the_same_sheet_writes_nothing(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.write()
    assert sheet.edits() == ()


def test_power_symbols_on_held_pins_are_accepted(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.nets.append({"name": "VDD", "auto_named": False,
                       "endpoints": [["U1", "PAD_VDD_0"], ["U1", "PAD_VDD_1"]]})
    sheet.write()
    assert compare_nets(sheet.path, write_source(tmp_path / "reference"), Resolver()) == ()


# --- what the student is told --------------------------------------------------------------
def test_an_unwired_lane_names_each_pin_on_its_die_line(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.unwire("d2d_f2")
    assert sheet.refusal() == [("pin_not_wired", "U1.d2d_f2"), ("pin_not_wired", "U2.d2d_f2")]
    lines = sheet.lines()
    assert lines[("pin_not_wired", "U1.d2d_f2")] == _line(GIVEN, "{id: U1,")
    assert lines[("pin_not_wired", "U2.d2d_f2")] == _line(GIVEN, "{id: U2,")


def test_a_lane_unwired_on_a_reimport_is_refused_too(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.write()
    sheet.unwire("d2d_r1")
    assert {code for code, _ in sheet.refusal()} == {"pin_not_wired"}


def test_a_missing_symbol_is_named_on_its_die_line(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbols = [s for s in sheet.symbols if s["ref"] != "U2"]
    for net in sheet.nets:
        net["endpoints"] = [ep for ep in net["endpoints"] if ep[0] != "U2"]
    assert ("missing_component", None) in sheet.refusal()
    assert sheet.lines()[("missing_component", None)] == _line(GIVEN, "{id: U2,")


def test_an_unannotated_symbol_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbol("U2")["ref"] = "U?"
    for net in sheet.nets:
        net["endpoints"] = [["U?", p] if r == "U2" else [r, p] for r, p in net["endpoints"]]
    assert sheet.refusal() == [("not_annotated", None)]
    assert sheet.lines()[("not_annotated", None)] == _line(GIVEN, "{id: U2,")


def test_two_unannotated_symbols_are_each_refused(tmp_path):
    sheet = Sheet(tmp_path)
    for sym in sheet.symbols:
        sym["ref"] = "U?"
    sheet.nets = []
    codes = [code for code, _ in sheet.refusal()]
    assert codes.count("not_annotated") == 2 and "malformed_form" not in codes


def test_two_symbols_named_alike_are_refused_once(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbol("U2")["ref"] = "U1"
    sheet.nets = []
    codes = [code for code, _ in sheet.refusal()]
    assert codes == ["duplicate_reference"]     # not U2 missing: it is placed, misnamed


def test_one_die_placed_twice_is_refused_on_the_second(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbol("U2").update(part={"lib": LIBRARY, "name": "DRIVER"}, pins=_pins("U1"))
    sheet.nets = []
    assert [code for code, _ in sheet.refusal()] == ["wrong_die"]


def test_swapped_designators_are_refused_on_both_dies(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbol("U1")["part"]["name"], sheet.symbol("U2")["part"]["name"] = "ECHO", "DRIVER"
    assert sorted(code for code, _ in sheet.refusal()) == ["wrong_die", "wrong_die"]


def test_a_symbol_that_is_no_die_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbols.append({"ref": "U3", "identity": None,
                          "part": {"lib": LIBRARY, "name": "DRIVER"}, "pins": _pins("U1")})
    assert [code for code, _ in sheet.refusal()] == ["unknown_symbol"]


def test_a_symbol_from_another_library_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbols.append({"ref": "R1", "identity": None,
                          "part": {"lib": "Device", "name": "R"}, "pins": ["1", "2"]})
    assert [code for code, _ in sheet.refusal()] == ["foreign_part"]


def test_the_library_registered_under_another_nickname_is_accepted(tmp_path):
    sheet = Sheet(tmp_path)
    for sym in sheet.symbols:
        sym["part"]["lib"] = "my_dies"
    sheet.write()
    assert compare_nets(sheet.path, write_source(tmp_path / "reference"), Resolver()) == ()


def test_a_symbol_with_a_pin_missing_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.symbol("U1")["pins"].remove("PAD_CLK_0")
    assert [code for code, _ in sheet.refusal()] == ["terminal_set_changed"]


def test_a_wire_with_no_label_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.nets[0].update(name="Net-(U1-PadPAD_d2d_f0_0)", auto_named=True)
    assert sheet.refusal() == [("unnamed_net", None)]


def test_a_label_misspelled_at_one_end_is_refused_at_both(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.unwire("d2d_f1")
    sheet.nets += [{"name": "d2d_f1_n", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f1_0"]]},
                   {"name": "d2d_f1_m", "auto_named": False,
                    "endpoints": [["U2", "PAD_d2d_f1_0"]]}]
    assert sorted(sheet.refusal()) == [("lone_label", "U1.d2d_f1"), ("lone_label", "U2.d2d_f1")]


def test_a_rail_pin_wired_into_a_lane_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.nets[0]["endpoints"].append(["U1", "PAD_VDD_0"])
    assert ("held_pin_changed", "U1.VDD") in sheet.refusal()


def test_a_lane_pin_put_on_a_rail_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.unwire("d2d_f0")
    sheet.nets.append({"name": "VSS", "auto_named": False,
                       "endpoints": [["U1", "PAD_VSS_0"], ["U1", "PAD_d2d_f0_0"]]})
    assert ("held_pin_changed", "U1.d2d_f0") in sheet.refusal()


def test_a_carrier_bound_pin_is_held(tmp_path):
    sheet = Sheet(tmp_path, EXTERNAL.replace(BUS, ""))
    sheet.nets[0]["endpoints"].append(["U1", "PAD_CLK_0"])
    assert ("held_pin_changed", "U1.CLK") in sheet.refusal()


# --- the form ------------------------------------------------------------------------------
def test_a_blank_sheet_given_a_run_design_is_refused(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.form_path.parent.mkdir(exist_ok=True)
    sheet.form_path.write_text(json.dumps(sheet.form()))
    with pytest.raises(EditRefused) as info:
        schematic_edits(sheet.path.read_bytes(), path=sheet.path, resolver=Resolver(),
                        form=sheet.form_path, design=b"x")
    assert [f.code for f in info.value.findings] == ["sheet_kind"]


def test_a_generated_sheet_given_no_design_is_refused(tmp_path):
    run = Run(tmp_path)
    run.case.form_path.write_text(json.dumps(run.case.form))
    with pytest.raises(EditRefused) as info:
        schematic_edits(run.path.read_bytes(), path=run.path, resolver=Resolver(),
                        form=run.case.form_path)
    assert [f.code for f in info.value.findings] == ["sheet_kind"]


def test_a_blank_sheet_needs_form_1_1(tmp_path):
    sheet = Sheet(tmp_path)
    sheet.version = "1.0"
    assert sheet.refusal() == [("malformed_form", None)]


def test_a_blank_form_carries_no_digest_or_report(tmp_path):
    doc = Sheet(tmp_path).form()
    parse_form(doc)
    for key in ("base_sha256", "generator_report"):
        bad = dict(doc, **{key: "x"})
        with pytest.raises(SchematicImportRefused) as info:
            parse_form(bad)
        assert info.value.codes == ["malformed_form"]


def test_a_blank_symbol_needs_its_part(tmp_path):
    doc = Sheet(tmp_path).form()
    del doc["symbols"][0]["part"]
    with pytest.raises(SchematicImportRefused) as info:
        parse_form(doc)
    assert info.value.codes == ["malformed_form"]


def test_the_library_is_named_by_its_stem_unless_named(tmp_path):
    doc = Sheet(tmp_path).form()
    assert parse_form(doc).library.name == LIBRARY
    doc["library"]["name"] = "my_dies"
    assert parse_form(doc).library.name == "my_dies"
    doc["library"]["name"] = None
    assert parse_form(doc).library.name == LIBRARY


def test_a_1_0_form_still_reads_as_generated(tmp_path):
    doc = Sheet(tmp_path).form()
    doc.update(version="1.0", base_sha256="a" * 64,
               generator_report={"path": "schematic.json", "sha256": "b" * 64})
    del doc["sheet"], doc["library"]
    assert not parse_form(doc).blank


# --- compare-nets --------------------------------------------------------------------------
def _reference(tmp_path, text=SOURCE):
    return write_source(tmp_path / "reference", text)


def test_crossed_lanes_are_reported_by_port(tmp_path):
    sheet = Sheet(tmp_path)
    by_name = {n["name"]: n for n in sheet.nets}
    by_name["d2d_f0_n"]["endpoints"][1] = ["U2", "PAD_d2d_f1_0"]
    by_name["d2d_f1_n"]["endpoints"][1] = ["U2", "PAD_d2d_f0_0"]
    sheet.nets = [n for n in sheet.nets if n["name"] not in ("d2d_f0_n", "d2d_f1_n")]
    # crossed lanes no longer pair as one bus, so they are written as links
    sheet.nets += [{"name": "x0", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f0_0"], ["U2", "PAD_d2d_f1_0"]]},
                   {"name": "x1", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f1_0"], ["U2", "PAD_d2d_f0_0"]]}]
    sheet.write()
    found = compare_nets(sheet.path, _reference(tmp_path), Resolver())
    assert [(d.code, d.subject) for d in found] == [("joined_otherwise", "x0"),
                                                    ("joined_otherwise", "x1")]


def test_lanes_named_otherwise_are_reported_with_both_names(tmp_path):
    sheet = Sheet(tmp_path)
    for net in sheet.nets:
        net["name"] = net["name"][:-2]          # the diagram's suffix left off
    sheet.write()
    found = compare_nets(sheet.path, _reference(tmp_path), Resolver())
    assert sorted((d.code, d.subject) for d in found) == [("named_otherwise", lane)
                                                          for lane in LANES]


def test_a_die_moved_is_not_equal(tmp_path):
    sheet = Sheet(tmp_path, GIVEN.replace("at: [1000, 2200]", "at: [1000, 2300]"))
    sheet.write()
    found = compare_nets(sheet.path, _reference(tmp_path), Resolver())
    assert [(d.code, d.subject) for d in found] == [("component_differs", "U1")]


def test_a_file_not_drawn_yet_reports_each_port_unconnected(tmp_path):
    given = write_source(tmp_path, GIVEN)
    found = compare_nets(given, _reference(tmp_path), Resolver())
    assert sorted((d.code, d.subject) for d in found) == sorted(
        ("not_connected", f"{die}.{lane}") for die in ("U1", "U2") for lane in LANES)


def test_a_reference_not_whole_is_refused(tmp_path):
    with pytest.raises(SourceError):
        compare_nets(_reference(tmp_path), write_source(tmp_path / "given", GIVEN), Resolver())


def test_compare_leaves_out_how_the_file_writes_the_nets(tmp_path):
    links = SOURCE.replace(BUS, "  links:\n" + "".join(
        f"    - {{a: U1.{lane}, b: U2.{lane}, net: {lane}_n}}\n" for lane in LANES))
    got = write_source(tmp_path / "links", links)
    assert compare_nets(got, _reference(tmp_path), Resolver()) == ()
