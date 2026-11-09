# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Forms the real KiCad front end wrote, frozen, read by :func:`read_sheet`.

``tests/fixtures/kicad_form`` holds what gds_to_kicad's reader
(``kicad_sch_to_connectivity.py``) wrote from
kicad-cli 9.0.2 netlists of two packaging-flow examples, together with the
bases, generator reports and pin lists those forms name, byte for byte and at
the relative paths they name them by (``trees/pf`` is where the generator
found packaging-flow). The netlists and sheets listed as front-end inputs are
not carried; the reader never reads them.

- ``two_die_ready``: the sheet as drawn, and the same sheet with the global
  label U1_PAD_STATUS renamed U1_STATUS.
- ``two_die_wire_bond``: the sheet as drawn, with the carrier and its eight io
  pads on it.

If the reader stops accepting what the real front end writes, this fails here,
without KiCad. Tests assert refusal codes, never messages.
"""
from __future__ import annotations

import json
import shutil

import pytest

from chiplet_system_description._cfio import cfio
from chiplet_system_description.connectivity import SchematicImportRefused
from chiplet_system_description.schematic_import import read_sheet
from tests.conftest import FIXTURES_DIR

FIXTURE = FIXTURES_DIR / "kicad_form"
BASES = {"two_die_ready": "cf_two_die_bondpad.chiplet",
         "two_die_wire_bond": "cf_two_die_wire_bond.chiplet"}


@pytest.fixture()
def tree(tmp_path):
    """A copy of the fixture, so an edited form is never written beside the frozen files."""
    return shutil.copytree(FIXTURE, tmp_path / "kicad_form")


def base_of(tree, case):
    return tree / case / "base" / BASES[case]


def form_path(tree, case, sheet):
    return tree / case / "kicad" / sheet / "connectivity.json"


def run(tree, case, sheet, form=None):
    if form is not None:
        form_path(tree, case, sheet).write_text(json.dumps(form, indent=2))
    base = base_of(tree, case)
    return read_sheet(form_path(tree, case, sheet), base.name, base.read_bytes())


def wire_bond_form(tree):
    return json.loads(form_path(tree, "two_die_wire_bond", "unedited").read_text())


def test_every_form_is_the_kicad_readers(tree):
    forms = sorted(tree.glob("*/kicad/*/connectivity.json"))
    assert len(forms) == 3
    for path in forms:
        form = json.loads(path.read_text())
        assert form["front_end"]["tool"] == "kicad"
        assert form["front_end"]["tool_version"] == "Eeschema 9.0.2"
        assert form["front_end"]["reader"] == "kicad_sch_to_connectivity 1.0"
        assert form["refusals"] == []


@pytest.mark.parametrize("case", ["two_die_ready", "two_die_wire_bond"])
def test_an_unedited_sheet_reads_as_the_base(tree, case):
    sheet = run(tree, case, "unedited")
    assert (sheet.nets, sheet.renamed) == (sheet.base_nets, {})


def test_the_carrier_is_drawn_with_every_io_pad_on_its_net(tree):
    carrier = next(c for c in cfio.load(base_of(tree, "two_die_wire_bond"))["components"]
                   if c["type"] == "interposer")
    form = wire_bond_form(tree)
    (symbol,) = [s for s in form["symbols"] if s["identity"] == carrier["id"]]
    assert symbol["pins"] == sorted(pad["id"] for pad in carrier["io_pads"])
    net_of = {tuple(ep): net["name"] for net in form["nets"] for ep in net["endpoints"]}
    assert {pad["id"]: net_of[(carrier["id"], pad["id"])] for pad in carrier["io_pads"]} == {
        pad["id"]: pad["net"] for pad in carrier["io_pads"]}


def test_one_renamed_label_is_that_rename_and_nothing_else(tree):
    sheet = run(tree, "two_die_ready", "edited")
    assert sheet.renamed == {"U1_PAD_STATUS": "U1_STATUS"}
    expected = cfio.load(base_of(tree, "two_die_ready"))["netlist"]["nets"]
    next(n for n in expected if n["name"] == "U1_PAD_STATUS")["name"] = "U1_STATUS"
    assert sheet.nets == expected


def _move(form, ref, pin, to):
    for net in form["nets"]:
        if [ref, pin] in net["endpoints"]:
            net["endpoints"].remove([ref, pin])
    if to is None:
        form["nets"].append({"name": f"unconnected-({ref}-Pad{pin})", "auto_named": True,
                             "endpoints": [[ref, pin]]})
    else:
        next(n for n in form["nets"] if n["name"] == to)["endpoints"].append([ref, pin])


@pytest.mark.parametrize("pad, to", [("J_U1_PAD_STATUS", "VDD"), ("J_VSS", None)])
def test_a_carrier_pad_moved_on_the_real_form_is_refused(tree, pad, to):
    form = wire_bond_form(tree)
    _move(form, "interposer", pad, to)
    with pytest.raises(SchematicImportRefused) as info:
        run(tree, "two_die_wire_bond", "unedited", form)
    assert [(r["code"], r.get("component"), r.get("terminal"))
            for r in info.value.refusals] == [("bound_net_changed", "interposer", pad)]


def test_renaming_a_net_bound_to_an_io_pad_is_refused(tree):
    form = wire_bond_form(tree)
    next(n for n in form["nets"] if n["name"] == "U1_PAD_STATUS")["name"] = "U1_STATUS"
    with pytest.raises(SchematicImportRefused) as info:
        run(tree, "two_die_wire_bond", "unedited", form)
    assert set(info.value.codes) == {"bound_net_changed"}
    assert ("interposer", "J_U1_PAD_STATUS") in {
        (r.get("component"), r.get("terminal")) for r in info.value.refusals}
