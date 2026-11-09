# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""An edited system schematic read against the .chiplet it was drawn from.

Hermetic. A faithful front end is simulated by building the connectivity form
straight from the base, which is what generate-then-read-back-unedited yields;
the KiCad end-to-end case lives with the generator. Tests assert refusal codes,
never messages.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from chiplet_system_description import elaborate
from chiplet_system_description._cfio import cfio
from chiplet_system_description.connectivity import SchematicImportRefused
from chiplet_system_description.schematic_import import read_sheet
from tests.conftest import GOLDEN_CHIPLET, PINS_JSON


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Case:
    """A base .chiplet, its pin lists, the generator's report and a form that a
    faithful front end would write for the unedited sheet. Paths in the report
    and the form are relative, as the generator writes them. With ``carrier``
    the generator also drew the non-die components, their io pads as pins."""

    def __init__(self, root: Path, base_text: str, pins=None, base_name="design.chiplet",
                 carrier=False):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.base = root / base_name
        self.base.write_bytes(base_text.encode("utf-8"))
        doc = cfio.loads(base_text)
        self.drawn = [c["id"] for c in doc["components"] if carrier or c["type"] == "die"]
        pins = dict(pins or {})
        for comp in doc["components"]:
            if comp["id"] in self.drawn and comp["type"] != "die":
                pins.setdefault(comp["id"], [pad["id"] for pad in comp.get("io_pads") or []])
        (root / "pins").mkdir(exist_ok=True)
        (root / "pnr").mkdir(exist_ok=True)
        self.pin_files = {}
        for cid in self.drawn:
            path = root / "pins" / f"{cid}.json"
            if cid not in pins:
                shutil.copyfile(PINS_JSON, path)
            else:
                path.write_text(json.dumps({"pins": [{"name": n} for n in pins[cid]]}))
            self.pin_files[cid] = path
        self.report_path = root / "pnr" / "schematic.json"
        self.write_report()
        self.form_path = root / "pnr" / "connectivity.json"
        self.form = self.faithful_form(doc)

    def terminals(self, cid):
        return sorted(p["name"] for p in json.loads(self.pin_files[cid].read_text())["pins"])

    def write_report(self, **override):
        report = {
            "format": "gds2kicad.system-schematic", "version": "1.0", "word": "written",
            "chiplet": f"../{self.base.name}", "chiplet_sha256": sha(self.base.read_bytes()),
            "pins": {c: {"path": f"../pins/{c}.json", "sha256": sha(p.read_bytes())}
                     for c, p in self.pin_files.items()},
            "components": [{"id": c} for c in self.drawn],
        }
        report.update(override)
        self.report_path.write_text(json.dumps(report, indent=2))

    def faithful_form(self, doc):
        nets, used = [], set()
        for row in doc["netlist"]["nets"]:
            eps = [[c["component"], c["pin"]] for c in row["connections"]
                   if c["component"] in self.drawn]
            used.update(map(tuple, eps))
            nets.append({"name": row["name"], "auto_named": False, "endpoints": eps})
        for comp in doc["components"]:
            for pad in comp.get("io_pads") or []:
                term = (comp["id"], pad["id"])
                if comp["id"] not in self.drawn or not pad.get("net") or term in used:
                    continue
                if not any(n["name"] == pad["net"] for n in nets):
                    nets.append({"name": pad["net"], "auto_named": False, "endpoints": []})
                next(n for n in nets if n["name"] == pad["net"])["endpoints"].append(list(term))
                used.add(term)
        nets = [n for n in nets if n["endpoints"]]
        for cid in self.drawn:
            for pin in self.terminals(cid):
                if (cid, pin) not in used:
                    nets.append({"name": f"unconnected-({cid}-Pad{pin})", "auto_named": True,
                                 "endpoints": [[cid, pin]]})
        return {
            "format": "chiplet_system.connectivity", "version": "1.0",
            "base_sha256": sha(self.base.read_bytes()),
            "generator_report": {"path": "schematic.json",
                                 "sha256": sha(self.report_path.read_bytes())},
            "front_end": {"tool": "kicad", "tool_version": "9.99", "reader": "test 1",
                          "inputs": []},
            "symbols": [{"ref": c, "identity": c, "pins": self.terminals(c)}
                        for c in self.drawn],
            "nets": nets,
        }

    def resync(self):
        """Point the report and the form at the files as they are now."""
        self.write_report()
        self.form["base_sha256"] = sha(self.base.read_bytes())
        self.form["generator_report"]["sha256"] = sha(self.report_path.read_bytes())

    def run(self):
        self.form_path.write_text(json.dumps(self.form, indent=2))
        return read_sheet(self.form_path, self.base.name, self.base.read_bytes())

    # -- edits, as a person makes them on the sheet
    def net(self, name):
        return next(n for n in self.form["nets"] if n["name"] == name)

    def move(self, ref, pin, to):
        for net in self.form["nets"]:
            if [ref, pin] in net["endpoints"]:
                net["endpoints"].remove([ref, pin])
        self.form["nets"] = [n for n in self.form["nets"] if n["endpoints"]]
        if to is None:
            self.form["nets"].append({"name": f"unconnected-({ref}-Pad{pin})",
                                      "auto_named": True, "endpoints": [[ref, pin]]})
        elif any(n["name"] == to for n in self.form["nets"]):
            self.net(to)["endpoints"].append([ref, pin])
        else:
            self.form["nets"].append({"name": to, "auto_named": False,
                                      "endpoints": [[ref, pin]]})

    def rename(self, old, new):
        self.net(old)["name"] = new


@pytest.fixture()
def cfio_base(built_system):
    return cfio.dumps(elaborate(built_system))


@pytest.fixture()
def foreign_base():
    return GOLDEN_CHIPLET.read_text(encoding="utf-8")


def refused(case):
    with pytest.raises(SchematicImportRefused) as info:
        case.run()
    return set(info.value.codes)


def unchanged(sheet):
    return sheet.nets == sheet.base_nets and sheet.renamed == {}


def renamed_in(base_text, old, new):
    """The base's nets with ``old`` renamed to ``new``, as a sheet edit gives them."""
    nets = cfio.loads(base_text)["netlist"]["nets"]
    next(n for n in nets if n["name"] == old)["name"] = new
    return nets


# ------------------------------------------------------------- identity

@pytest.mark.parametrize("which", ["cfio_base", "foreign_base"])
def test_unedited_sheet_reads_as_the_base_and_writes_nothing(tmp_path, request, which):
    case = Case(tmp_path, request.getfixturevalue(which))
    case.form_path.write_text(json.dumps(case.form, indent=2))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    sheet = case.run()
    assert unchanged(sheet)
    assert sheet.base_nets == cfio.load(case.base)["netlist"]["nets"]
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_a_crlf_base_reads(tmp_path, foreign_base):
    base = foreign_base.replace("\n", "\r\n")
    assert unchanged(Case(tmp_path / "same", base).run())
    case = Case(tmp_path / "edit", base)
    case.rename("sig0", "lane0")
    assert case.run().nets == renamed_in(base, "sig0", "lane0")


# ------------------------------------------------------------- the carrier

def _carrier_in_netlist(base_text):
    """The base with a carrier pad J_X in the netlist on sig1, not in io_pads."""
    doc = cfio.loads(base_text)
    sig1 = next(n for n in doc["netlist"]["nets"] if n["name"] == "sig1")
    sig1["connections"].append({"component": "interposer", "pin": "J_X", "layer": "TopMetal2"})
    return cfio.dumps(doc)


CARRIER_PINS = {"interposer": ["J_GND", "J_VDD", "J_X"]}


@pytest.mark.parametrize("which", ["cfio_base", "foreign_base"])
def test_carrier_on_the_sheet_reads_as_the_base(tmp_path, request, which):
    base = request.getfixturevalue(which)
    assert unchanged(Case(tmp_path / "pads", base, carrier=True).run())
    case = Case(tmp_path / "netlist", _carrier_in_netlist(base), pins=CARRIER_PINS,
                carrier=True)
    assert ["interposer", "J_X"] in case.net("sig1")["endpoints"]
    assert ["interposer", "J_VDD"] in case.net("VDD")["endpoints"]
    sheet = case.run()
    assert unchanged(sheet)
    assert {"sig1", "VDD"} <= sheet.held


def test_die_edits_with_the_carrier_on_the_sheet(tmp_path, cfio_base):
    base = _carrier_in_netlist(cfio_base)
    case = Case(tmp_path, base, pins=CARRIER_PINS, carrier=True)
    case.rename("sig5", "d2d5")
    assert case.run().nets == renamed_in(base, "sig5", "d2d5")


@pytest.mark.parametrize("pin, to", [
    ("J_VDD", "GND"),           # bound by its io pad
    ("J_VDD", None),
    ("J_X", "sig2"),            # bound by its netlist connection
    ("J_X", None),
    ("J_GND", "sig3"),
])
def test_a_moved_carrier_terminal_is_refused(tmp_path, cfio_base, pin, to):
    case = Case(tmp_path, _carrier_in_netlist(cfio_base), pins=CARRIER_PINS, carrier=True)
    case.move("interposer", pin, to)
    with pytest.raises(SchematicImportRefused) as info:
        case.run()
    assert [(r["code"], r.get("component"), r.get("terminal")) for r in info.value.refusals] == [
        ("bound_net_changed", "interposer", pin)]


# ------------------------------------------------------------- edits

def test_one_renamed_net_changes_that_name_and_nothing_else(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.rename("sig5", "d2d5")
    sheet = case.run()
    assert sheet.renamed == {"sig5": "d2d5"}
    assert sheet.nets == renamed_in(cfio_base, "sig5", "d2d5")
    assert sheet.base_nets == cfio.loads(cfio_base)["netlist"]["nets"]


@pytest.mark.parametrize("name", ["U1_IO_BOND_bidirs[0].bidir_pad", "x[3]", "a[1].b[2]"])
def test_a_net_named_with_subscripts_keeps_its_name(tmp_path, cfio_base, name):
    case = Case(tmp_path, cfio_base)
    case.rename("sig5", name)
    sheet = case.run()
    assert sheet.renamed == {"sig5": name}
    assert sheet.nets == renamed_in(cfio_base, "sig5", name)
    doc = cfio.loads(cfio_base)
    doc["netlist"]["nets"] = sheet.nets
    assert name in [n["name"] for n in cfio.loads(cfio.dumps(doc))["netlist"]["nets"]]


@pytest.mark.parametrize("name", ["sig[0..3]", "[0]sig"])
def test_a_range_or_a_bracket_first_is_no_net_name(tmp_path, cfio_base, name):
    case = Case(tmp_path, cfio_base)
    case.rename("sig5", name)
    assert refused(case) == {"unmappable_name"}


def test_moved_and_newly_connected_terminals_carry_their_layer(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.move("U1", "A0", "sig1")      # from sig0
    case.move("U1", "B0", "sig1")      # unconnected in the base
    sheet = case.run()
    assert sheet.renamed == {}
    nets = {n["name"]: n for n in sheet.nets}
    assert nets["sig0"]["connections"] == [
        {"component": "U2", "pin": "B0", "layer": "TopMetal2"}]
    assert nets["sig1"]["connections"] == [
        {"component": "U1", "pin": "A1", "layer": "TopMetal2"},
        {"component": "U2", "pin": "B1", "layer": "TopMetal2"},
        {"component": "U1", "pin": "A0", "layer": "TopMetal2"},
        {"component": "U1", "pin": "B0", "layer": "TopMetal2"},
    ]
    assert [n["name"] for n in sheet.nets] == [n["name"] for n in sheet.base_nets]


def test_rename_with_an_endpoint_change_is_a_removal_and_an_addition(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.rename("sig2", "probe")
    case.move("U2", "B2", None)
    sheet = case.run()
    assert sheet.renamed == {}
    assert "sig2" not in [n["name"] for n in sheet.nets]
    assert sheet.nets[-1] == {"name": "probe", "connections": [
        {"component": "U1", "pin": "A2", "layer": "TopMetal2"}]}


# ------------------------------------------------------------- refusals

def _external_base(case):
    text = case.base.read_text().replace("netlist:\n", "netlist:\n  external_netlist: nets.v\n")
    case.base.write_text(text)
    case.resync()


def _unreadable_base(case):
    case.base.write_text("format_version: '1.0'\ncomponents: []\n")
    case.resync()


def _drop_u2(case):
    case.form["symbols"] = [s for s in case.form["symbols"] if s["ref"] != "U2"]
    for net in case.form["nets"]:
        net["endpoints"] = [ep for ep in net["endpoints"] if ep[0] != "U2"]
    case.form["nets"] = [n for n in case.form["nets"] if n["endpoints"]]


def _mislabel_u2(case):
    for sym in case.form["symbols"]:
        if sym["ref"] == "U2":
            sym["ref"] = "U9"
    for net in case.form["nets"]:
        net["endpoints"] = [["U9", p] if r == "U2" else [r, p] for r, p in net["endpoints"]]


def _unnamed(case):
    case.move("U1", "B0", "Net-(U1-PadB0)")
    case.move("U2", "A0", "Net-(U1-PadB0)")
    case.net("Net-(U1-PadB0)")["auto_named"] = True


def _rewrite_report(case, **override):
    case.write_report(**override)
    case.form["generator_report"]["sha256"] = sha(case.report_path.read_bytes())


def _touch_report(case):
    case.report_path.write_text(case.report_path.read_text() + "\n")


REFUSALS = {
    "malformed_form": lambda c: c.form.update(nets="x"),
    "malformed_form_version": lambda c: c.form.update(version=1),
    "malformed_form_pin_twice": lambda c: c.form["symbols"][0]["pins"].append("A0"),
    "malformed_form_nan": lambda c: c.form["front_end"].update(clock=float("nan")),
    "generator_not_written": lambda c: _rewrite_report(c, word="refused"),
    "generator_report_changed": _touch_report,
    "malformed_report": lambda c: _rewrite_report(c, pins=None),
    "base_digest_mismatch": lambda c: c.form.update(base_sha256="0" * 64),
    "base_digest_mismatch_report": lambda c: _rewrite_report(c, chiplet_sha256="0" * 64),
    "pin_list_changed": lambda c: c.pin_files["U1"].write_text(
        c.pin_files["U1"].read_text() + "\n"),
    "base_unreadable": _unreadable_base,
    "external_netlist": _external_base,
    "unknown_symbol": lambda c: c.form["symbols"].append(
        {"ref": "#PWR01", "identity": None, "pins": []}),
    "reference_mismatch": _mislabel_u2,
    "duplicate_identity": lambda c: c.form["symbols"].append(
        {"ref": "U3", "identity": "U2", "pins": c.terminals("U2")}),
    "missing_component": _drop_u2,
    "terminal_set_changed": lambda c: c.form["symbols"][0]["pins"].remove("B5"),
    "unknown_ref": lambda c: c.net("sig0")["endpoints"].append(["U7", "A0"]),
    "unknown_terminal": lambda c: c.net("sig0")["endpoints"].append(["U1", "ZZ"]),
    "terminal_on_two_nets": lambda c: c.net("sig1")["endpoints"].append(["U1", "A0"]),
    "unnamed_net": _unnamed,
    "unmappable_name": lambda c: c.rename("sig0", "sig 0"),
    "bound_net_changed": lambda c: c.rename("VDD", "VDD1"),
    "front_end": lambda c: c.form.update(
        refusals=[{"code": "local_label", "message": "label /x is local"}]),
}

EXPECTED = {"malformed_form_version": "malformed_form", "malformed_form_pin_twice": "malformed_form",
            "malformed_form_nan": "malformed_form",
            "base_digest_mismatch_report": "base_digest_mismatch", "front_end": "local_label"}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_each_refusal_by_code(tmp_path, cfio_base, name):
    case = Case(tmp_path, cfio_base)
    REFUSALS[name](case)
    assert refused(case) == {EXPECTED.get(name, name)}


def test_no_layer_to_carry_for_a_newly_connected_terminal(tmp_path, built_system):
    doc = elaborate(built_system)
    vdd = next(n for n in doc["netlist"]["nets"] if n["name"] == "VDD")
    vdd["connections"][0]["layer"] = "TopMetal1"        # U1 now lands on two layers
    case = Case(tmp_path, cfio.dumps(doc))
    case.move("U1", "B0", "sig0")
    assert refused(case) == {"no_layer"}
    case.rename("VDD", "VDD1")
    assert refused(case) == {"no_layer", "bound_net_changed"}     # one stage, both reported


def test_external_net_is_held(tmp_path, built_system):
    doc = elaborate(built_system)
    doc["netlist"]["nets"][0]["external"] = True        # sig0
    case = Case(tmp_path, cfio.dumps(doc))
    case.move("U1", "A0", None)
    assert refused(case) == {"bound_net_changed"}


def test_form_and_report_in_different_directories(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.form_path = tmp_path / "edit" / "connectivity.json"
    case.form_path.parent.mkdir()
    case.form["generator_report"]["path"] = "../pnr/schematic.json"
    case.rename("sig5", "d2d5")
    assert case.run().renamed == {"sig5": "d2d5"}


def test_inputs_symbols_and_nets_are_collected_together(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.form["symbols"].append({"ref": "#PWR01", "identity": None, "pins": []})
    case.net("sig0")["endpoints"].append(["U1", "ZZ"])
    case.rename("sig1", "sig 1")
    case.form["refusals"] = [{"code": "local_label", "message": "label /x is local"}]
    assert refused(case) == {"unknown_symbol", "unknown_terminal", "unmappable_name",
                             "local_label"}


def test_a_form_that_is_not_json_is_refused(tmp_path, cfio_base):
    case = Case(tmp_path, cfio_base)
    case.form_path.write_text("{")
    with pytest.raises(SchematicImportRefused) as info:
        read_sheet(case.form_path, case.base.name, case.base.read_bytes())
    assert info.value.codes == ["malformed_form"]
