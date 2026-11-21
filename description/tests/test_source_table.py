# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Pin tables: a CSV table of die pins and their nets, written into a placed file.

A table is the blank sheet's second front end: the same given file, the same
held nets, the same lift. Where a blank sheet test has a table to match, the
table equal to its form gives the same edits, or the same refusals with a
missing symbol read as its missing pins and a lone label as a lone net. The
form layer is never entered: every table here is read with ``load_form``,
``parse_form`` and ``classic_edits`` patched to raise.
Tests assert codes, subjects and lines, never messages.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import sys

import pytest

from chiplet_system_description import classic_import, connectivity, elaborate, schematic_import
from chiplet_system_description._cfio import cfio
from chiplet_system_description.connectivity import SchematicImportRefused
from chiplet_system_description.schematic_import import GivenDie, read_blank_sheet
from chiplet_system_description.source import edit as edit_module
from chiplet_system_description.source.compare import compare_nets
from chiplet_system_description.source.edit import EditRefused, Replace, patch_system
from chiplet_system_description.source.elaborate import elaborate_source

from tests.source_dies import DRIVER, ECHO, SOURCE, write_source
from tests.test_source_blank_sheet import BUS, GIVEN, LANES, PARTS, Sheet
from tests.test_source_edit import EXTERNAL
from tests.test_source_plan import Resolver
from tests.test_source_sheet import PADS

NAME = "pins.csv"
HEAD = "die,pin,net"
#: The two-die source with its forward lanes as one link line over a range.
RANGED = SOURCE.replace(BUS, '  links:\n    - {a: "U1.d2d_f[0..2]", b: "U2.d2d_f[0..2]"}\n'
                             "    - {a: U2.d2d_r0, b: U1.d2d_r0, net: d2d_r0_n}\n"
                             "    - {a: U2.d2d_r1, b: U1.d2d_r1, net: d2d_r1_n}\n")
UNPLACED = GIVEN.replace('format_version: "1.1"', 'format_version: "1.3"').replace(
    "at: [1000, 2200], ", "")


def _pins(cid):
    return sorted(PADS[cid])


def _line(text, needle):
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def _wired(net="{}_n"):
    """The rows of the sheet wired as the diagram says: each lane on its net,
    every other pin on no net."""
    return [[cid, pin, next((net.format(lane) for lane in LANES if pin == f"PAD_{lane}_0"), "")]
            for cid in PARTS for pin in _pins(cid)]


def _rows(sheet):
    """The table a blank sheet's form says: one row per pin of each symbol,
    with each named net it is on, else an empty net."""
    on = {}
    for net in sheet.nets:
        if not net["auto_named"]:
            for ref, pin in net["endpoints"]:
                on.setdefault((ref, pin), []).append(net["name"])
    return [[sym["ref"], pin, net] for sym in sheet.symbols for pin in sym["pins"]
            for net in on.get((sym["ref"], pin), [""])]


class _FormRead(AssertionError):
    """The form layer was entered."""


@contextlib.contextmanager
def _no_form_layer():
    """``load_form``, ``parse_form`` and ``classic_edits`` raise while the
    block runs, under every name a module of the package holds them by."""
    forms = (connectivity.load_form, connectivity.parse_form, classic_import.classic_edits,
             edit_module.classic_edits)

    def entered(*args, **kwargs):
        raise _FormRead("the form layer was entered")

    with pytest.MonkeyPatch.context() as patch:
        for name, module in list(sys.modules.items()):
            if name.split(".")[0] != "chiplet_system_description":
                continue
            for attr, value in list(vars(module).items()):
                if any(value is form for form in forms):
                    patch.setattr(module, attr, entered)
        yield


class Table:
    """A pin table of a file and the file it is for; wired as the diagram says
    unless given its rows."""

    def __init__(self, tmp_path, text=GIVEN, rows=None):
        self.path = write_source(tmp_path, text)
        self.rows = _wired() if rows is None else rows
        self.head = HEAD
        self.end = "\n"
        self.raw = None             # bytes read in place of the rows

    def data(self):
        if self.raw is not None:
            return self.raw
        out = io.StringIO()
        out.write(self.head + self.end)
        csv.writer(out, lineterminator=self.end).writerows(self.rows)
        return out.getvalue().encode("utf-8")

    def edits(self):
        from chiplet_system_description.source.edit import table_edits

        with _no_form_layer():
            return table_edits(self.path.read_bytes(), path=self.path, resolver=Resolver(),
                               table=self.data(), table_name=NAME)

    def write(self):
        self.path.write_bytes(patch_system(self.path.read_bytes(), self.edits()).text)
        return self.path.read_text()

    def refusal(self):
        with pytest.raises(EditRefused) as info:
            self.edits()
        return [(f.code, f.port) for f in info.value.findings]

    def found(self):
        with pytest.raises(EditRefused) as info:
            self.edits()
        return sorted(((f.code, f.line, f.port) for f in info.value.findings),
                      key=lambda f: (f[0], f[1], f[2] or ""))

    # -- what a person does in the table
    def net(self, die, pin, net):
        for row in self.rows:
            if row[:2] == [die, pin]:
                row[2] = net

    def drop(self, die, pin):
        self.rows = [row for row in self.rows if row[:2] != [die, pin]]

    def rename(self, old, new):
        for row in self.rows:
            if row[2] == old:
                row[2] = new


#: The sheet's codes that the table says otherwise, by design.
_CODES = {"missing_component": "missing_terminal", "lone_label": "lone_net"}


def _subjects(findings, text):
    """Findings as a sheet and its table are compared: the table's code for
    each, a missing symbol or pin as (code, die) only, any other as (code,
    line, port)."""
    die_at = {_line(text, f"{{id: {cid},"): cid for cid in PARTS}
    missing, rest = set(), []
    for f in findings:
        code = _CODES.get(f.code, f.code)
        if code == "missing_terminal":
            missing.add((code, f.port.split(".")[0] if f.port else die_at[f.line]))
        else:
            rest.append((code, f.line, f.port))
    return missing, sorted(rest, key=lambda f: (f[0], f[1], f[2] or ""))


def _outcome(edits):
    try:
        return edits(), None
    except EditRefused as exc:
        return None, exc.findings


def _pair(tmp_path, text=GIVEN):
    return Sheet(tmp_path / "sheet", text), Table(tmp_path / "table", text, rows=[])


def _same(sheet, table):
    """The table of the sheet as it stands gives the sheet's edits, or its
    refusals; the refusals, compared."""
    table.rows = _rows(sheet)
    want, refused = _outcome(sheet.edits)
    got, found = _outcome(table.edits)
    assert got == want
    if refused is None:
        return None
    got = _subjects(found, table.path.read_text())
    assert got == _subjects(refused, sheet.path.read_text())
    return got


def _written(sheet, table):
    """Both written, the table having given the sheet's edits."""
    assert _same(sheet, table) is None
    sheet.write()
    table.write()


def _same_written(sheet, table, reference):
    """Both written; the table's file compares with ``reference`` as the sheet's does."""
    _written(sheet, table)
    want = [(d.code, d.subject) for d in compare_nets(sheet.path, reference, Resolver())]
    got = [(d.code, d.subject) for d in compare_nets(table.path, reference, Resolver())]
    assert got == want
    return got


def _reference(tmp_path, text=SOURCE):
    return write_source(tmp_path / "reference", text)


def _sheet_edits(tmp_path):
    """The edits of the sheet wired as the diagram says, which its table gives."""
    edits = Sheet(tmp_path / "sheet").edits()
    assert edits
    return edits


def _given(tmp_path, text=GIVEN):
    """The given file's design bytes and its dies, as the edit passes them."""
    path = write_source(tmp_path, text)
    system, _ = elaborate_source(path, Resolver(), sheet_base=True)
    dies = {cid: GivenDie(PARTS[cid], frozenset(PADS[cid]),
                          {f"PAD_{port}_{n}": port for port, *_, count in spec
                           for n in range(count)})
            for cid, spec in (("U1", DRIVER), ("U2", ECHO))}
    return cfio.dumps(elaborate(system)).encode("utf-8"), dies


# --- the entry -----------------------------------------------------------------------------
def test_both_functions_are_exported():
    assert "read_table_sheet" in schematic_import.__all__
    assert "table_edits" in edit_module.__all__


def test_the_table_reads_to_the_nets_the_wired_sheet_reads_to(tmp_path):
    from chiplet_system_description.schematic_import import read_table_sheet

    base_raw, dies = _given(tmp_path)
    sheet = Sheet(tmp_path / "sheet")
    sheet.form_path.parent.mkdir(exist_ok=True)
    sheet.form_path.write_text(json.dumps(sheet.form(), indent=2))
    want = read_blank_sheet(sheet.form_path, "system.chiplet", base_raw, dies)
    with _no_form_layer():
        got = read_table_sheet(Table(tmp_path / "table").data(), NAME, "system.chiplet",
                               base_raw, dies)
    assert got == want


def test_the_form_layer_patch_stops_a_sheet_and_not_a_table(tmp_path):
    sheet = Sheet(tmp_path / "sheet")
    with _no_form_layer(), pytest.raises(_FormRead):
        sheet.edits()
    assert Table(tmp_path / "table").edits() == sheet.edits()


# --- as the blank sheet does ---------------------------------------------------------------
def test_like_the_sheet_a_wired_table_writes_the_links_and_matches_the_reference(tmp_path):
    sheet, table = _pair(tmp_path)
    assert _same_written(sheet, table, _reference(tmp_path)) == []


def test_like_the_sheet_a_reimport_of_the_same_table_writes_nothing(tmp_path):
    sheet, table = _pair(tmp_path)
    _written(sheet, table)
    assert _same(sheet, table) is None
    assert table.edits() == ()


def test_like_the_sheet_power_nets_on_held_pins_are_accepted(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.nets.append({"name": "VDD", "auto_named": False,
                       "endpoints": [["U1", "PAD_VDD_0"], ["U1", "PAD_VDD_1"]]})
    assert _same_written(sheet, table, _reference(tmp_path)) == []


def test_like_the_sheet_an_unwired_lane_names_each_pin_on_its_die_line(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.unwire("d2d_f2")
    assert _same(sheet, table) == (set(), [
        ("pin_not_wired", _line(GIVEN, "{id: U1,"), "U1.d2d_f2"),
        ("pin_not_wired", _line(GIVEN, "{id: U2,"), "U2.d2d_f2")])


def test_like_the_sheet_a_lane_unwired_on_a_reimport_is_refused_too(tmp_path):
    sheet, table = _pair(tmp_path)
    _written(sheet, table)
    sheet.unwire("d2d_r1")
    _, found = _same(sheet, table)
    assert {code for code, _, _ in found} == {"pin_not_wired"}


def test_like_the_sheet_a_die_left_out_is_named_pin_by_pin(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.symbols = [s for s in sheet.symbols if s["ref"] != "U2"]
    for net in sheet.nets:
        net["endpoints"] = [ep for ep in net["endpoints"] if ep[0] != "U2"]
    missing, _ = _same(sheet, table)
    assert missing == {("missing_terminal", "U2")}
    assert sorted(port for code, port in table.refusal() if code == "missing_terminal") == \
        sorted(f"U2.{port}" for port, *_ in ECHO)


def test_like_the_sheet_a_net_misspelled_on_one_row_is_refused_at_both(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.unwire("d2d_f1")
    sheet.nets += [{"name": "d2d_f1_n", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f1_0"]]},
                   {"name": "d2d_f1_m", "auto_named": False,
                    "endpoints": [["U2", "PAD_d2d_f1_0"]]}]
    assert _same(sheet, table) == (set(), [
        ("lone_net", _line(GIVEN, "{id: U1,"), "U1.d2d_f1"),
        ("lone_net", _line(GIVEN, "{id: U2,"), "U2.d2d_f1")])


def test_like_the_sheet_a_rail_pin_put_into_a_lane_is_refused(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.nets[0]["endpoints"].append(["U1", "PAD_VDD_0"])
    _, found = _same(sheet, table)
    assert ("held_pin_changed", _line(GIVEN, "{name: VDD,"), "U1.VDD") in found


def test_like_the_sheet_a_lane_pin_put_on_a_rail_is_refused(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.unwire("d2d_f0")
    sheet.nets.append({"name": "VSS", "auto_named": False,
                       "endpoints": [["U1", "PAD_VSS_0"], ["U1", "PAD_d2d_f0_0"]]})
    _, found = _same(sheet, table)
    assert ("held_pin_changed", _line(GIVEN, "{id: U1,"), "U1.d2d_f0") in found


def test_like_the_sheet_a_carrier_bound_pin_is_held(tmp_path):
    text = EXTERNAL.replace(BUS, "")
    sheet, table = _pair(tmp_path, text)
    sheet.nets[0]["endpoints"].append(["U1", "PAD_CLK_0"])
    _, found = _same(sheet, table)
    assert ("held_pin_changed", _line(text, "south: [U1.CLK"), "U1.CLK") in found


def test_like_the_sheet_crossed_lanes_are_reported_by_port(tmp_path):
    sheet, table = _pair(tmp_path)
    sheet.nets = [n for n in sheet.nets if n["name"] not in ("d2d_f0_n", "d2d_f1_n")]
    sheet.nets += [{"name": "x0", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f0_0"], ["U2", "PAD_d2d_f1_0"]]},
                   {"name": "x1", "auto_named": False,
                    "endpoints": [["U1", "PAD_d2d_f1_0"], ["U2", "PAD_d2d_f0_0"]]}]
    assert _same_written(sheet, table, _reference(tmp_path)) == [("joined_otherwise", "x0"),
                                                                 ("joined_otherwise", "x1")]


def test_like_the_sheet_lanes_named_otherwise_are_reported_with_both_names(tmp_path):
    sheet, table = _pair(tmp_path)
    for net in sheet.nets:
        net["name"] = net["name"][:-2]
    found = _same_written(sheet, table, _reference(tmp_path))
    assert sorted(found) == [("named_otherwise", lane) for lane in LANES]


def test_like_the_sheet_a_die_moved_is_not_equal(tmp_path):
    sheet, table = _pair(tmp_path, GIVEN.replace("at: [1000, 2200]", "at: [1000, 2300]"))
    assert _same_written(sheet, table, _reference(tmp_path)) == [("component_differs", "U1")]


# --- edit, then undo -----------------------------------------------------------------------
def test_a_rename_and_its_undo_give_the_file_back_byte_for_byte(tmp_path):
    table = Table(tmp_path)
    text = table.write()
    start = table.path.read_bytes()
    for lane in LANES:
        a, b = ("U1", "U2") if lane.startswith("d2d_f") else ("U2", "U1")
        assert f"    - {{a: {a}.{lane}, b: {b}.{lane}, net: {lane}_n}}\n" in text
    table.rename("d2d_f0_n", "d2d_f0_m")
    assert table.edits() == (Replace("links", "U1.d2d_f0", "net", "d2d_f0_m"),)
    table.write()
    assert table.path.read_bytes() != start
    table.rows = _wired()
    assert table.edits() == (Replace("links", "U1.d2d_f0", "net", "d2d_f0_n"),)
    table.write()
    assert table.path.read_bytes() == start


def test_a_rename_to_the_default_name_and_its_undo_connect_alike(tmp_path):
    table = Table(tmp_path / "table")
    start = write_source(tmp_path / "start", table.write())
    table.rename("d2d_f0_n", "U1_d2d_f0")
    assert table.edits() == (Replace("links", "U1.d2d_f0", "net", None),)
    assert "    - {a: U1.d2d_f0, b: U2.d2d_f0}\n" in table.write()
    table.rows = _wired()
    assert table.edits() == (Replace("links", "U1.d2d_f0", "net", "d2d_f0_n"),)
    table.write()
    assert compare_nets(table.path, start, Resolver()) == ()


def test_one_bus_lane_renamed_alone_is_refused(tmp_path):
    table = Table(tmp_path, SOURCE)
    assert table.edits() == ()
    table.rename("d2d_f1_n", "sideband")
    assert table.found() == [("rename_no_field", _line(SOURCE, "{ends: [U1, U2]"), "U1.d2d_f1")]


# --- the table -----------------------------------------------------------------------------
def test_a_byte_order_mark_is_dropped(tmp_path):
    table = Table(tmp_path)
    want = _sheet_edits(tmp_path)
    assert table.edits() == want
    table.raw = b"\xef\xbb\xbf" + table.data()
    assert table.edits() == want


def test_crlf_line_ends_read_as_lf(tmp_path):
    table = Table(tmp_path)
    want = _sheet_edits(tmp_path)
    table.end = "\r\n"
    assert table.edits() == want
    table.raw = b"\xef\xbb\xbf" + table.data()
    assert table.edits() == want


def test_quoted_cells_read_as_written(tmp_path):
    table = Table(tmp_path)
    want = _sheet_edits(tmp_path)
    table.raw = (HEAD + "\n" + "".join(f'"{d}","{p}","{n}"\n' for d, p, n in table.rows)
                 ).encode("utf-8")
    assert table.edits() == want


def test_rows_with_no_cell_or_only_empty_cells_are_skipped(tmp_path):
    table = Table(tmp_path)
    want = _sheet_edits(tmp_path)
    table.rows[3:3] = [[], [""], ["", ""], ["", "", ""], ["", "", "", ""], ["", "", "", "", ""]]
    table.rows.append([])
    assert table.edits() == want


@pytest.mark.parametrize("row", [["U1", "PAD_CLK_0"], ["U1", "PAD_CLK_0", "", "x"],
                                 ["U1"], [" ", "", "", ""]],
                         ids=["two", "four", "one", "four-with-a-space"])
def test_a_row_of_other_than_three_cells_is_refused(tmp_path, row):
    table = Table(tmp_path)
    table.rows.insert(2, row)
    assert table.found() == [("table_row", 0, None)]


@pytest.mark.parametrize("head", ["Die,Pin,Net", "die,pin,net,side", "die, pin, net",
                                  "die,pin,net ", "pin,die,net", "", "die,pin",
                                  '"die","pin","net"'])
def test_a_first_line_other_than_the_header_is_refused(tmp_path, head):
    table = Table(tmp_path)
    table.head = head
    assert table.found() == [("table_header", 0, None)]


def test_an_empty_table_is_refused_for_its_header(tmp_path):
    table = Table(tmp_path)
    table.raw = b""
    assert table.found() == [("table_header", 0, None)]


@pytest.mark.parametrize("raw", [
    (HEAD + "\nU1,PAD_CLK_0,\xff\n").encode("latin-1"),
    (HEAD + "\nU1,PAD_CLK_0," + "x" * 200_000 + "\n").encode("utf-8"),
    (HEAD + '\nU1,"PAD_CLK_0,\nU1,PAD_VDD_0,\n').encode("utf-8"),
    (HEAD + '\nU1,"PAD_CLK"_0,\n').encode("utf-8"),
    (HEAD + "\nU1,PAD_CLK_0\0,\n").encode("utf-8"),
], ids=["not-utf-8", "field-over-the-csv-limit", "a-quote-left-open",
        "a-quote-closed-before-its-cell-ends", "a-nul-byte"])
def test_a_table_that_does_not_read_as_utf_8_csv_is_refused(tmp_path, raw):
    table = Table(tmp_path)
    table.raw = raw
    assert table.found() == [("table_unreadable", 0, None)]


def test_cells_are_not_trimmed(tmp_path):
    table = Table(tmp_path)
    table.rows.append([" U1", "PAD_CLK_0", ""])
    assert table.found() == [("unknown_die", 0, None)]
    table = Table(tmp_path / "pin")
    table.rows.append(["U1", "PAD_CLK_0 ", ""])
    assert table.found() == [("unknown_terminal", _line(GIVEN, "{id: U1,"), None)]
    table = Table(tmp_path / "net")
    table.net("U1", "PAD_CLK_0", "U1_PAD_CLK_0 ")
    assert [code for code, _ in table.refusal()] == ["unmappable_name"]
    table = Table(tmp_path / "skip")
    table.rows.append([" ", "", ""])
    assert table.found() == [("unknown_die", 0, None)]


def test_a_quoted_newline_keeps_its_row_whole(tmp_path):
    table = Table(tmp_path)
    table.rows.insert(1, ["U\n1", "PAD_CLK_0", ""])
    assert table.found() == [("unknown_die", 0, None)]
    table = Table(tmp_path / "net")
    table.net("U1", "PAD_CLK_0", "U1_PAD\nCLK_0")
    assert [code for code, _ in table.refusal()] == ["unmappable_name"]


# --- what the table cannot say -------------------------------------------------------------
@pytest.mark.parametrize("text,die", [(GIVEN, "U3"), (GIVEN, "u1"), (GIVEN, "interposer"),
                                      (EXTERNAL.replace(BUS, ""), "interposer"),
                                      (EXTERNAL.replace(BUS, ""), "J_U1_PAD_CLK_0"),
                                      (EXTERNAL.replace(BUS, ""), "J_VDD")],
                         ids=["no-die", "case", "carrier", "carrier-with-pads", "io-pad",
                              "rail-io-pad"])
def test_a_die_cell_that_names_no_die_is_refused(tmp_path, text, die):
    table = Table(tmp_path, text)
    table.rows.append([die, "PAD_CLK_0", ""])
    assert table.found() == [("unknown_die", 0, None)]


@pytest.mark.parametrize("pin", ["PAD_NOPE_0", "d2d_f0", "pad_clk_0", "PAD_STATUS_0"])
def test_a_pin_not_in_the_dies_pin_list_is_refused(tmp_path, pin):
    table = Table(tmp_path)
    table.rows.append(["U1", pin, ""])
    assert table.found() == [("unknown_terminal", _line(GIVEN, "{id: U1,"), None)]


@pytest.mark.parametrize("bad", [("U2", "PAD_d2d_f0_O"), ("U3", "PAD_d2d_f0_0")],
                         ids=["a-pin-misspelled", "a-die-misspelled"])
def test_a_refused_row_is_not_told_again_as_a_lone_net(tmp_path, bad):
    # As a blank sheet tells an end on a refused symbol with that symbol only.
    table = Table(tmp_path)
    (row,) = [row for row in table.rows if row[:2] == ["U2", "PAD_d2d_f0_0"]]
    row[:2] = bad
    codes = {code for code, _, _ in table.found()}
    assert "lone_net" not in codes and codes <= {"unknown_terminal", "unknown_die",
                                                 "missing_terminal"}


def test_a_pin_on_two_nets_is_refused(tmp_path):
    table = Table(tmp_path)
    table.rows.append(["U1", "PAD_d2d_f0_0", "d2d_f1_n"])
    assert table.found() == [("terminal_on_two_nets", _line(GIVEN, "{id: U1,"), "U1.d2d_f0")]


def test_a_pin_listed_again_on_its_net_is_refused(tmp_path):
    table = Table(tmp_path)
    table.rows.append(["U2", "PAD_d2d_r0_0", "d2d_r0_n"])
    assert table.found() == [("terminal_on_two_nets", _line(GIVEN, "{id: U2,"), "U2.d2d_r0")]


def test_a_held_pin_listed_again_is_refused_on_its_line(tmp_path):
    table = Table(tmp_path)
    table.rows.append(["U1", "PAD_VDD_1", ""])
    assert table.found() == [("terminal_on_two_nets", _line(GIVEN, "{name: VDD,"), "U1.VDD")]


def test_each_pin_left_out_is_refused_on_its_line(tmp_path):
    table = Table(tmp_path)
    table.drop("U1", "PAD_CLK_0")
    table.drop("U1", "PAD_VDD_1")
    table.drop("U1", "PAD_d2d_f0_0")
    table.drop("U2", "PAD_d2d_f0_0")
    assert table.found() == [("missing_terminal", _line(GIVEN, "{id: U1,"), "U1.d2d_f0"),
                             ("missing_terminal", _line(GIVEN, "{id: U2,"), "U2.d2d_f0"),
                             ("missing_terminal", _line(GIVEN, "{name: VDD,"), "U1.VDD"),
                             ("missing_terminal", _line(GIVEN, "- U1.CLK"), "U1.CLK")]


@pytest.mark.parametrize("net", ["U1 CLK", "B[0..3]", "x[]", "[0]", "a,b"])
def test_a_net_that_is_no_sheet_name_is_refused(tmp_path, net):
    table = Table(tmp_path)
    table.net("U1", "PAD_CLK_0", net)
    assert [code for code, _ in table.refusal()] == ["unmappable_name"]


def test_a_net_on_one_pin_is_refused(tmp_path):
    table = Table(tmp_path)
    table.net("U2", "PAD_d2d_f1_0", "d2d_f1_m")
    assert table.found() == [("lone_net", _line(GIVEN, "{id: U1,"), "U1.d2d_f1"),
                             ("lone_net", _line(GIVEN, "{id: U2,"), "U2.d2d_f1")]


def test_held_pins_may_name_the_nets_the_file_has_for_them(tmp_path):
    table = Table(tmp_path)
    want = _sheet_edits(tmp_path)
    for die, pin, net in (("U1", "PAD_CLK_0", "U1_PAD_CLK_0"), ("U2", "PAD_STATUS_0",
                                                                 "U2_PAD_STATUS_0"),
                          ("U1", "PAD_VDD_0", "VDD"), ("U1", "PAD_VDD_1", "VDD"),
                          ("U2", "PAD_VDD_0", "VDD"), ("U1", "PAD_VSS_0", "VSS"),
                          ("U1", "PAD_VSS_1", "VSS"), ("U2", "PAD_VSS_0", "VSS")):
        table.net(die, pin, net)
    assert table.edits() == want


def test_a_held_pin_moved_is_refused(tmp_path):
    table = Table(tmp_path)
    table.net("U1", "PAD_CLK_0", "U2_PAD_STATUS_0")
    found = table.found()
    assert {code for code, _, _ in found} == {"held_pin_changed"}
    assert ("held_pin_changed", _line(GIVEN, "- U1.CLK"), "U1.CLK") in found


# --- what the lift and the file refuse, passed through -------------------------------------
def test_a_signal_net_over_four_pins_is_refused(tmp_path):
    table = Table(tmp_path)
    table.net("U1", "PAD_d2d_f1_0", "d2d_f0_n")
    table.net("U2", "PAD_d2d_f1_0", "d2d_f0_n")
    assert table.found() == [("not_expressible", 0, "U1.d2d_f0")]


def test_a_net_on_one_die_is_refused(tmp_path):
    table = Table(tmp_path)
    for die, net in (("U1", "inputs"), ("U2", "outputs")):
        table.net(die, "PAD_d2d_f0_0", net)
        table.net(die, "PAD_d2d_f1_0", net)
    assert table.found() == [("not_expressible", 0, "U1.d2d_f0"),
                             ("not_expressible", 0, "U2.d2d_f0")]


def test_a_range_of_links_changed_in_one_lane_is_refused(tmp_path):
    table = Table(tmp_path, RANGED)
    for lane in LANES[:3]:
        table.rename(f"{lane}_n", f"U1_{lane}")    # the range's lanes take default names
    assert table.edits() == ()
    table.rename("U1_d2d_f1", "other")
    assert table.found() == [("not_expressible", _line(RANGED, '{a: "U1.d2d_f[0..2]"'), None)]


def test_an_unplaced_file_is_refused_as_the_sheet_refuses_it(tmp_path):
    sheet = Sheet(tmp_path / "sheet", UNPLACED)
    with pytest.raises(EditRefused) as info:
        sheet.edits()
    table = Table(tmp_path / "table", UNPLACED)
    assert table.found() == sorted(((f.code, f.line, f.port) for f in info.value.findings),
                                   key=lambda f: (f[0], f[1], f[2] or ""))
    assert {code for code, _, _ in table.found()} == {"not_a_source"}


def test_an_unreadable_base_is_refused(tmp_path):
    from chiplet_system_description.schematic_import import read_table_sheet

    _, dies = _given(tmp_path)
    with _no_form_layer(), pytest.raises(SchematicImportRefused) as info:
        read_table_sheet(Table(tmp_path / "table").data(), NAME, "system.chiplet",
                         b"\xff not a design", dies)
    assert info.value.codes == ["base_unreadable"]
