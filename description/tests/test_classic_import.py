# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Classic sheets: a system drawn from blank, read into its project file.

The forms are built here, as a KiCad reader would write them for the sheet as
wired, against real die folders: the fixture dies of the source tests, and
example 8's two dies (``tests/fixtures/classic/example8``). The refusals are in
``test_classic_import_refusals.py``. Tests assert codes, never messages.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil

import pytest

from chiplet_system_description.classic_import import read_classic_sheet
from chiplet_system_description.source.compare import compare_nets
from chiplet_system_description.source.document import check_text
from chiplet_system_description.source.edit import EditRefused, patch_system, schematic_edits
from chiplet_system_description.source.inventory import read_die_folder
from chiplet_system_description.source.patch import Insert, Place, SetEntries
from chiplet_system_description.source.plan import plan

from tests.conftest import FIXTURES_DIR
from tests.source_dies import DRIVER, ECHO, SOURCE, write_die
from tests.test_source_plan import Resolver

EXAMPLE8 = FIXTURES_DIR / "classic" / "example8"
SHA = hashlib.sha256(b"x").hexdigest()
LANES = ("d2d_f0", "d2d_f1", "d2d_f2", "d2d_r0", "d2d_r1")
HEADER = SOURCE.split("system:\n")[0].replace('"1.1"', '"1.3"')
PROJECT = HEADER + '''\
system:
  carrier: {technology: intm4tm2, top_cell: CF_CARRIER}
  external:
    profile: intm4tm2_wire_bond_v0
flow:
  packaging_flow:
    checks: {template: workshop}
'''


def _end(text):
    ref, _, pin = text.partition(".")
    return [ref, pin]


class Drawing:
    """A classic sheet, symbol by symbol and net by net, and its project file."""

    def __init__(self, root, project=PROJECT):
        self.root = root
        self.path = root / "system.chiplet"
        if project is not None:
            self.path.write_text(project, encoding="utf-8")
        self.symbols, self.nets, self.rails, self.open, self.refusals = [], [], [], [], []
        self.form_path = root / "sheet" / "connectivity.json"
        self._keys = 0

    def _key(self):
        self._keys += 1
        return f"/{self._keys:08x}-0000-4000-8000-000000000001"

    # -- what a person places and wires
    def die(self, ref, die, *, part=None, method="cupillar_opt1", thickness="750"):
        folder = read_die_folder(self.root / die)
        cell = part or folder.top_cell
        fields = {"Die": die, "Reference": ref, "Value": cell,
                  "Footprint": f"chiplet_dies:{cell}"}
        if method:
            fields["Method"] = method
        if thickness:
            fields["Thickness_um"] = thickness
        self.symbols.append({
            "ref": ref, "identity": None, "key": self._key(),
            "value": cell, "footprint": f"chiplet_dies:{cell}",
            "part": {"lib": "chiplet_dies", "name": cell},
            "fields": fields,
            "pins": sorted(set(p.name for p in folder.pins))})

    def pad(self, ref, edge=None):
        fields = {"Reference": ref, "Value": "~", "Footprint": "chiplet_carrier:EXT_PAD"}
        if edge:
            fields["Edge"] = edge
        self.symbols.append({
            "ref": ref, "identity": None, "key": self._key(),
            "value": "~", "footprint": "chiplet_carrier:EXT_PAD",
            "part": {"lib": "chiplet_system", "name": "EXT_PAD"},
            "fields": fields, "pins": ["1"]})

    def net(self, name, *ends, auto=False):
        self.nets.append({"name": name, "auto_named": auto, "endpoints": [_end(e) for e in ends]})

    def rail(self, name, net_class, volts, *ends):
        self.rails.append({"name": name, "class": net_class, "volts": volts})
        self.net(name, *ends)

    def unwire(self, name):
        self.nets = [n for n in self.nets if n["name"] != name]

    def symbol(self, ref):
        return next(s for s in self.symbols if s["ref"] == ref)

    def fields(self, ref, **fields):
        names = {"die": "Die", "method": "Method", "thickness": "Thickness_um", "edge": "Edge"}
        box = self.symbol(ref)["fields"]
        for key, value in fields.items():
            name = names.get(key, key)
            if value:
                box[name] = value
            else:
                box.pop(name, None)

    # -- the form and the file
    def form(self):
        used = {tuple(ep) for n in self.nets for ep in n["endpoints"]}
        bare = dict.fromkeys((s["ref"], p) for s in self.symbols for p in s["pins"]
                             if (s["ref"], p) not in used)
        loose = [{"name": f"unconnected-({r}-Pad{p})", "auto_named": True,
                  "endpoints": [[r, p]]} for r, p in bare]
        return {
            "format": "chiplet_system.connectivity", "version": "1.2", "sheet": "classic",
            "base_sha256": None, "generator_report": None,
            "library": {"path": "classic/chiplet_dies.kicad_sym", "sha256": SHA},
            "system_library": {"path": "classic/chiplet_system.kicad_sym", "sha256": SHA},
            "front_end": {"tool": "kicad", "tool_version": "9.99", "reader": "test 1",
                          "inputs": []},
            "symbols": self.symbols, "nets": self.nets + loose, "rails": self.rails,
            "open": [_end(e) for e in self.open], "refusals": self.refusals,
        }

    def save(self):
        self.form_path.parent.mkdir(exist_ok=True)
        self.form_path.write_text(json.dumps(self.form(), indent=2), encoding="utf-8")
        return self.form_path

    def edits(self, **kw):
        return schematic_edits(self.path.read_bytes(), path=self.path, resolver=Resolver(),
                               form=self.save(), **kw)

    def write(self):
        self.path.write_bytes(patch_system(self.path.read_bytes(), self.edits()).text)
        return self.path.read_text(encoding="utf-8")

    def refused(self, **kw):
        with pytest.raises(EditRefused) as info:
            self.edits(**kw)
        return info.value.findings

    def codes(self):
        return sorted({f.code for f in self.refused()})


def two_die(root, project=PROJECT):
    """The source tests' two dies, wired as their bus, rails and externals."""
    write_die(root / "dies" / "DRIVER", "DRIVER", DRIVER)
    write_die(root / "dies" / "ECHO", "ECHO", ECHO)
    d = Drawing(root, project)
    d.die("U1", "dies/DRIVER")
    d.die("U2", "dies/ECHO", thickness="725.5")
    for lane in LANES:
        d.net(f"{lane}_n", f"U1.PAD_{lane}_0", f"U2.PAD_{lane}_0")
    d.pad("J1", "west")
    d.pad("J2", "east")
    d.pad("J3", "west")
    d.pad("J4")
    d.rail("VDD", "power", "1.2", "U1.PAD_VDD_0", "U1.PAD_VDD_1", "U2.PAD_VDD_0", "J1.1")
    d.rail("GND", "ground", None, "U1.PAD_VSS_0", "U1.PAD_VSS_1", "U2.PAD_VSS_0", "J2.1")
    d.net("CLK", "U1.PAD_CLK_0", "J3.1")
    d.net("Net-(J4-Pad1)", "U2.PAD_STATUS_0", "J4.1", auto=True)
    return d


def _tier1(path):
    source, findings = check_text(path.read_text(encoding="utf-8"), path)
    assert not findings
    topology = plan(source, unplaced=True)
    assert not topology.refusals
    return source, topology


# --- a two-die system ----------------------------------------------------------------------
def test_a_classic_sheet_writes_the_system_it_draws(tmp_path):
    d = two_die(tmp_path)
    text = d.write()
    system = text.split("system:\n", 1)[1]
    assert system.startswith("  carrier: {technology: intm4tm2, top_cell: CF_CARRIER}\n"
                             "  dies:\n"
                             "    - {id: U1, die: dies/DRIVER, method: cupillar_opt1, "
                             "thickness: 750}\n"
                             "    - {id: U2, die: dies/ECHO, method: cupillar_opt1, "
                             "thickness: 725.5}\n"
                             "  links:\n")
    assert "    - {a: U1.d2d_f0, b: U2.d2d_f0, net: d2d_f0_n}\n" in text
    assert "    - {a: U2.d2d_r1, b: U1.d2d_r1, net: d2d_r1_n}\n" in text   # the driving end first
    assert ("  rails:\n    - {name: GND, class: ground, ports: [U1.VSS, U2.VSS]}\n"
            "    - {name: VDD, class: power, volts: 1.2, ports: [U1.VDD, U2.VDD]}\n") in text
    assert "      west: [VDD, U1.CLK]\n      east: [GND]\n" in text
    assert "\nunplaced:\n  pins: [U2.STATUS]\nflow:\n" in text   # after system
    assert text.startswith(HEADER)   # every other byte kept, the stamp already 1.3
    source, topology = _tier1(d.path)
    assert len(topology.links) == 5 and len(topology.supplies) == 2
    assert "buses" not in text and "alone" not in text


def test_links_are_written_in_net_name_order(tmp_path):
    d = two_die(tmp_path)
    text = d.write()
    nets = [line.split("net: ")[1].rstrip("}") for line in text.splitlines() if "net: " in line]
    assert nets == [f"{lane}_n" for lane in LANES]


def test_the_same_sheet_read_again_writes_nothing(tmp_path):
    d = two_die(tmp_path)
    first = d.write()
    assert d.edits() == ()
    assert d.path.read_text(encoding="utf-8") == first


def test_the_same_sheet_read_again_keeps_a_die_s_admission(tmp_path):
    """The sheet does not show a die's admission (format 1.4): read again, it
    neither drops the key nor lowers the stamp the key needs."""
    d = two_die(tmp_path)
    d.write()
    _place(d)
    text = d.path.read_text(encoding="utf-8")
    text = re.sub(r'format_version: "[^"]+"', 'format_version: "1.4"', text)
    text = re.sub(r"(\{id: U2, [^}]*)\}", r"\1, admission: admission.json}", text)
    d.path.write_text(text, encoding="utf-8")
    (tmp_path / "admission.json").write_text(json.dumps(
        {"reason": "r", "scope": "s", "not_verified": ["die DRC"]}), encoding="utf-8")
    assert d.edits() == ()
    assert d.write() == text


def test_the_same_sheet_read_again_keeps_a_rail_s_routing_requirement(tmp_path):
    """The sheet does not show a rail's width (format 1.5): read again, a rail
    it still names keeps the key and its stamp. A rail renamed on the sheet is
    another rail to the file, so the width is not carried to it."""
    d = two_die(tmp_path)
    d.write()
    _place(d)
    text = d.path.read_text(encoding="utf-8")
    text = re.sub(r'format_version: "[^"]+"', 'format_version: "1.5"', text)
    text = text.replace("{name: GND, class: ground,", "{name: GND, class: ground, width_um: 40,")
    d.path.write_text(text, encoding="utf-8")
    assert d.edits() == ()
    assert d.write() == text
    for item in (*d.rails, *d.nets):
        if item["name"] == "GND":
            item["name"] = "VSS"
    renamed = d.write()
    assert "{name: VSS, class: ground, ports:" in renamed and "width_um" not in renamed


BUS = '    - {ends: [U1, U2], ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}\n'


def _bused(d):
    """The file a first import writes, its five links put back as the bus."""
    text = d.write()
    links = [line for line in text.splitlines(keepends=True) if line.startswith("    - {a: ")]
    text = text.replace("  links:\n" + "".join(links), "  buses:\n" + BUS)
    d.path.write_text(text, encoding="utf-8")
    return text


def test_a_bus_the_file_writes_stays_while_the_sheet_draws_it(tmp_path):
    d = two_die(tmp_path)
    text = _bused(d)
    assert d.edits() == ()
    assert d.path.read_text(encoding="utf-8") == text


def test_a_bus_with_a_lane_renamed_goes_and_its_lanes_are_links(tmp_path):
    d = two_die(tmp_path)
    _bused(d)
    d.nets[0]["name"] = "fast0"
    text = d.write()
    assert "buses" not in text
    assert "    - {a: U1.d2d_f0, b: U2.d2d_f0, net: fast0}\n" in text
    assert len(_tier1(d.path)[1].links) == 5


def test_read_classic_sheet_returns_the_system(tmp_path):
    d = two_die(tmp_path)
    sheet = read_classic_sheet(d.save(), tmp_path, "system.chiplet")
    assert [die.id for die in sheet.dies] == ["U1", "U2"]
    assert [(p.ref, p.entry, p.edge) for p in sheet.pads] == [
        ("J1", "VDD", "west"), ("J2", "GND", "east"), ("J3", "U1.CLK", "west"),
        ("J4", "U2.STATUS", None)]
    assert sheet.nets["U1_PAD_CLK_0"] == {("U1", "PAD_CLK_0")}


def test_unconnected_nets_for_flagged_pins_import_the_same(tmp_path):
    d = two_die(tmp_path)
    d.unwire("CLK")
    d.symbols = [s for s in d.symbols if s["ref"] != "J3"]
    d.open = ["U1.PAD_CLK_0"]
    full = d.form()
    bare = json.loads(json.dumps(full))
    bare["nets"] = [n for n in bare["nets"] if not str(n["name"]).startswith("unconnected-")]
    data = d.path.read_bytes()

    def written(doc, name):
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        edits = schematic_edits(data, path=d.path, resolver=Resolver(), form=path)
        return patch_system(data, edits).text

    assert written(full, "with.json") == written(bare, "without.json")


def test_a_flagged_port_is_an_open_line(tmp_path):
    d = two_die(tmp_path)
    d.unwire("CLK")
    d.symbols = [s for s in d.symbols if s["ref"] != "J3"]
    d.open = ["U1.PAD_CLK_0"]
    text = d.write()
    assert "  open:\n    - U1.CLK\n" in text
    assert "west: [VDD]" in text


def test_dies_absent_from_a_sheet_drawn_again_go(tmp_path):
    d = two_die(tmp_path)
    d.write()
    d.symbols = [s for s in d.symbols if s["ref"] != "J4"]
    d.unwire("Net-(J4-Pad1)")
    d.open = ["U2.PAD_STATUS_0"]
    text = d.write()
    assert "unplaced" not in text and "  open:\n    - U2.STATUS\n" in text
    _tier1(d.path)


# --- four dies from two folders ------------------------------------------------------------
def test_four_dies_from_two_folders(tmp_path):
    d = two_die(tmp_path)
    d.die("U3", "dies/DRIVER")
    d.die("U4", "dies/ECHO", method="cupillar_opt1", thickness="700")
    for lane in LANES:
        d.net(f"{lane}_m", f"U3.PAD_{lane}_0", f"U4.PAD_{lane}_0")
    rails = {r["name"]: r for r in d.rails}
    for net in d.nets:
        if net["name"] == "VDD":
            net["endpoints"] += [["U3", "PAD_VDD_0"], ["U3", "PAD_VDD_1"], ["U4", "PAD_VDD_0"]]
        if net["name"] == "GND":
            net["endpoints"] += [["U3", "PAD_VSS_0"], ["U3", "PAD_VSS_1"], ["U4", "PAD_VSS_0"]]
    assert set(rails) == {"VDD", "GND"}
    d.pad("J10", "north")
    d.pad("J5", "north")
    d.net("CLK3", "U3.PAD_CLK_0", "J10.1")
    d.net("STATUS4", "U4.PAD_STATUS_0", "J5.1")
    text = d.write()
    assert "    - {id: U3, die: dies/DRIVER" in text and "    - {id: U4, die: dies/ECHO" in text
    assert "ports: [U1.VDD, U2.VDD, U3.VDD, U4.VDD]" in text
    assert "      north: [U4.STATUS, U3.CLK]\n" in text     # J5 before J10
    _, topology = _tier1(d.path)
    assert len(topology.links) == 10
    assert d.edits() == ()


# --- placement kept on a re-import ---------------------------------------------------------
def _place(d):
    """What packaging-flow place does: dies placed, the waiting entry on an edge."""
    data = d.path.read_bytes()
    edits = (Place("U1", (1000, 2200)), Place("U2", (1000, 1000), 180),
             SetEntries("unplaced", ()),
             SetEntries("south", ("U2.STATUS",)))
    d.path.write_bytes(patch_system(data, edits).text)
    return d.path.read_text(encoding="utf-8")


def test_placement_is_kept_on_a_re_import(tmp_path):
    d = two_die(tmp_path)
    d.write()
    placed = _place(d)
    assert "at: [1000, 1000], rotation: 180" in placed
    assert d.edits() == ()
    d.fields("U2", method="cupillar_opt2")   # any change: the dies are written again
    text = d.write()
    assert "{id: U2, die: dies/ECHO, at: [1000, 1000], rotation: 180, method: cupillar_opt2" in text
    assert "      south: [U2.STATUS]\n" in text and "unplaced" not in text


def test_a_die_drawn_from_another_folder_loses_its_place(tmp_path):
    d = two_die(tmp_path)
    d.write()
    _place(d)
    shutil.copytree(tmp_path / "dies" / "ECHO", tmp_path / "dies" / "ECHO_B")
    d.fields("U2", die="dies/ECHO_B")
    text = d.write()
    assert "{id: U2, die: dies/ECHO_B, method:" in text
    assert "{id: U1, die: dies/DRIVER, at: [1000, 2200]" in text
    assert "south" not in text and "\nunplaced:\n  pins: [U2.STATUS]\n" in text


def test_an_edge_on_the_sheet_overrides_the_file(tmp_path):
    d = two_die(tmp_path)
    d.write()
    _place(d)
    d.fields("J4", edge="north")
    text = d.write()
    assert "      north: [U2.STATUS]\n" in text and "south" not in text


# --- example 8 -----------------------------------------------------------------------------
def example8(root):
    """Example 8's system drawn on a classic sheet."""
    shutil.copytree(EXAMPLE8, root, dirs_exist_ok=True)
    d = Drawing(root, project=None)
    d.path = root / "project.chiplet"
    d.die("U1", "inputs/dies/CF_DRIVER_LS", thickness="750")
    d.die("U2", "inputs/dies/CF_ECHO_CX_BONDPAD", method="gf180mcu_flow_demo_pillar",
          thickness="725")
    pads = {p.name: p.pads for p in read_die_folder(root / "inputs/dies/CF_DRIVER_LS").ports}
    echo = {p.name: p.pads for p in read_die_folder(root / "inputs/dies/CF_ECHO_CX_BONDPAD").ports}
    ports = ([f"d2d_f{i}" for i in range(10)] + ["d2d_fclk", "d2d_frst_n"]
             + [f"d2d_r{i}" for i in range(10)])
    for port in ports:
        d.net(f"{port}_n", f"U1.{pads[port][0]}", f"U2.{echo[port][0]}")
    for ref, edge in (("J1", "north"), ("J2", "north"), ("J3", "north"), ("J4", "south"),
                      ("J5", "south"), ("J6", "west"), ("J7", "west"), ("J8", "east"),
                      ("J9", "east")):
        d.pad(ref, edge)
    for ref, (inst, port) in zip(("J1", "J2", "J3", "J4", "J5", "J6"),
                                 (("U1", "CFG_MODE"), ("U1", "CLK"), ("U1", "STATUS"),
                                  ("U2", "CFG_MODE"), ("U2", "STATUS"), ("U1", "RSTN"))):
        die = pads if inst == "U1" else echo
        d.net(f"Net-({ref}-Pad1)", f"{inst}.{die[port][0]}", f"{ref}.1", auto=True)
    d.rail("V12", "power", "1.2", *[f"U1.{p}" for p in pads["VDD"]], "J7.1")
    d.rail("V33", "power", "3.3", *[f"U1.{p}" for p in pads["IOVDD"]],
           *[f"U2.{p}" for p in echo["VDD"]], "J8.1")
    d.rail("GND", "ground", None, *[f"U1.{p}" for p in pads["VSS"]],
           *[f"U2.{p}" for p in echo["VSS"]], "J9.1")
    return d


def test_example_8_drawn_on_a_classic_sheet_connects_what_its_file_connects(tmp_path):
    d = example8(tmp_path)
    text = d.write()
    _, topology = _tier1(d.path)
    assert len(topology.links) == 22 and len(topology.supplies) == 3
    assert ("      north: [U1.CFG_MODE, U1.CLK, U1.STATUS]\n"
            "      south: [U2.CFG_MODE, U2.STATUS]\n"
            "      west: [U1.RSTN, V12]\n"
            "      east: [V33, GND]\n") in text
    assert "{name: V33, class: power, volts: 3.3, ports: [U1.IOVDD, U2.VDD]}" in text
    assert compare_nets(d.path, tmp_path / "system.chiplet", None, scope="connections") == ()
    assert d.edits() == ()


def test_example_8_with_no_edges_waits_for_placement(tmp_path):
    d = example8(tmp_path)
    for sym in d.symbols:
        if sym["part"]["name"] == "EXT_PAD":
            sym["fields"].pop("Edge", None)
    text = d.write()
    assert "edges" not in text
    assert ("\nunplaced:\n  pins: [U1.CFG_MODE, U1.CLK, U1.STATUS, U2.CFG_MODE, "
                         "U2.STATUS, U1.RSTN, V12, V33, GND]\nflow:\n") in text
    _tier1(d.path)
    assert compare_nets(d.path, tmp_path / "system.chiplet", None, scope="connections") == ()


# --- the writer's new edits ----------------------------------------------------------------
def test_a_die_inserted_whole_is_written(tmp_path):
    d = two_die(tmp_path)
    d.write()
    data = d.path.read_bytes()
    with pytest.raises(ValueError):
        patch_system(data, (Insert("dies", (("id", "U3"),)),))
    text = patch_system(data, (Insert("dies", (("id", "U3"), ("die", "dies/DRIVER"),
                                               ("method", "cupillar_opt1"))),)).text.decode()
    assert "    - {id: U3, die: dies/DRIVER, method: cupillar_opt1}\n  links:" in text


def test_set_entries_refuses_an_unknown_row(tmp_path):
    d = two_die(tmp_path)
    d.write()
    with pytest.raises(ValueError):
        patch_system(d.path.read_bytes(), (SetEntries("up", ("U1.CLK",)),))


def test_a_classic_sheet_takes_no_design(tmp_path):
    d = two_die(tmp_path)
    assert [f.code for f in d.refused(design=b"{}")] == ["sheet_kind"]
