# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Sheet edits: an edited system sheet lifted to the edits the file's one writer applies.

The front end is the schematic import tests' faithful one: the form is built
from the design the run elaborated, then edited as a person edits the sheet.
Tests assert codes, never messages.
"""
from __future__ import annotations

import json

import pytest

from chiplet_system_description import elaborate
from chiplet_system_description._cfio import cfio
from chiplet_system_description.source.edit import (Delete, EditRefused, Insert, Replace,
                                                    patch_system, schematic_edits)
from chiplet_system_description.source.elaborate import elaborate_source

from tests.source_dies import DRIVER, ECHO, SOURCE, write_source
from tests.test_schematic_import import Case
from tests.test_source_edit import EXTERNAL
from tests.test_source_plan import Resolver

PADS = {die: [f"PAD_{port}_{n}" for port, *_, count in spec for n in range(count)]
        for die, spec in (("U1", DRIVER), ("U2", ECHO))}
LINKED = (SOURCE.replace('  alone:\n    - U2.STATUS\n    - U1.CLK\n', '')
          .replace("  rails:\n", '  links:\n    - {a: U2.STATUS, b: U1.CLK}\n  rails:\n', 1))
SPLIT_RAIL = SOURCE.replace(
    "    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}\n",
    "    - {name: VDD, class: power, ports: [U1.VDD]}   # the driver's core\n"
    "    - {name: VDD_ECHO, class: power, ports: [U2.VDD]}\n")


class Run:
    """A source, the design a run elaborated from it, and the sheet drawn from that."""

    def __init__(self, tmp_path, text=SOURCE):
        self.path = write_source(tmp_path, text)
        system, _ = elaborate_source(self.path, Resolver())
        self.design = cfio.dumps(elaborate(system))
        self.case = Case(tmp_path / "run", self.design, pins=PADS)

    def edits(self, design=None):
        self.case.form_path.write_text(json.dumps(self.case.form, indent=2))
        return schematic_edits(self.path.read_bytes(), path=self.path, resolver=Resolver(),
                               form=self.case.form_path,
                               design=(design or self.design).encode("utf-8"))

    def refusal(self, design=None):
        with pytest.raises(EditRefused) as info:
            self.edits(design)
        return [(f.code, f.port) for f in info.value.findings]

    def patched(self):
        return patch_system(self.path.read_bytes(), self.edits()).text.decode()


def test_an_unedited_sheet_gives_no_edits(tmp_path):
    assert Run(tmp_path).edits() == ()


def test_two_lone_ports_joined_become_a_link(tmp_path):
    run = Run(tmp_path)
    run.case.move("U2", "PAD_STATUS_0", "status")
    run.case.move("U1", "PAD_CLK_0", "status")
    assert run.edits() == (
        Insert("links", (("a", "U2.STATUS"), ("b", "U1.CLK"), ("net", "status"))),
        Delete("alone", "U2.STATUS"), Delete("alone", "U1.CLK"))
    text = run.patched()
    assert "  alone:" not in text
    assert ('ports: ["d2d_f[0..2]", "d2d_r[0..1]"], net_suffix: _n}\n'
            '  links:\n    - {a: U2.STATUS, b: U1.CLK, net: status}\n  rails:\n') in text


def test_a_rail_gaining_a_port_keeps_its_line_and_comment(tmp_path):
    run = Run(tmp_path, SPLIT_RAIL)
    run.case.move("U2", "PAD_VDD_0", "VDD")
    assert run.edits() == (Replace("rails", "VDD", "ports", ["U1.VDD", "U2.VDD"]),
                           Delete("rails", "VDD_ECHO"))
    assert ("    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}   # the driver's core\n"
            "    - {name: VSS,") in run.patched()


def test_a_rail_renamed_on_the_sheet_renames_the_rail(tmp_path):
    run = Run(tmp_path)
    run.case.rename("VDD", "VCC")
    assert run.edits() == (Replace("rails", "VDD", "name", "VCC"),)


def test_a_deleted_link_leaves_its_ports_open(tmp_path):
    run = Run(tmp_path, LINKED)
    run.case.move("U2", "PAD_STATUS_0", None)
    run.case.move("U1", "PAD_CLK_0", None)
    assert run.edits() == (Delete("links", "U2.STATUS"), Insert("open", "U1.CLK"),
                           Insert("open", "U2.STATUS"))
    assert "  open:\n    - U1.CLK\n    - U2.STATUS\nflow:\n" in run.patched()


def test_a_link_renamed_on_the_sheet_writes_its_net(tmp_path):
    run = Run(tmp_path, LINKED)
    run.case.rename("U2_STATUS", "ready")
    assert run.edits() == (Replace("links", "U2.STATUS", "net", "ready"),)


def test_a_lane_leaving_a_bus_shrinks_the_bus(tmp_path):
    run = Run(tmp_path)
    run.case.move("U1", "PAD_d2d_f2_0", None)
    run.case.move("U2", "PAD_d2d_f2_0", None)
    assert run.edits() == (
        Replace("buses", "U1.d2d_f0", "ports", ["d2d_f0", "d2d_f1", "d2d_r0", "d2d_r1"]),
        Insert("open", "U1.d2d_f2"), Insert("open", "U2.d2d_f2"))
    assert '    - {ends: [U1, U2], ports: ["d2d_f[0..1]", "d2d_r[0..1]"], net_suffix: _n}\n' \
        in run.patched()


def test_a_whole_bus_renamed_on_the_sheet_moves_its_affixes(tmp_path):
    run = Run(tmp_path)
    for lane in ("f0", "f1", "f2", "r0", "r1"):
        run.case.rename(f"d2d_{lane}_n", f"top_d2d_{lane}_n")
    assert run.edits() == (Replace("buses", "U1.d2d_f0", "net_prefix", "top_"),)


# --- what the sheet cannot write -------------------------------------------------------
def test_one_pad_of_a_port_moved_alone_is_refused(tmp_path):
    run = Run(tmp_path)
    run.case.move("U1", "PAD_VDD_1", "VSS")
    assert {code for code, _ in run.refusal()} == {"pad_split"}
    assert ("pad_split", "U1.VDD") in run.refusal()


def test_a_lone_port_renamed_is_refused_naming_the_port(tmp_path):
    run = Run(tmp_path)
    run.case.rename("U1_PAD_CLK_0", "clk")
    assert run.refusal() == [("rename_no_field", "U1.CLK")]


def test_one_bus_lane_renamed_alone_is_refused(tmp_path):
    run = Run(tmp_path)
    run.case.rename("d2d_f1_n", "sideband")
    assert run.refusal() == [("rename_no_field", "U1.d2d_f1")]


def test_a_signal_net_over_three_ports_is_refused(tmp_path):
    run = Run(tmp_path)
    run.case.move("U1", "PAD_CLK_0", "d2d_f0_n")
    assert run.refusal() == [("not_expressible", "U1.d2d_f0")]


def test_a_net_on_one_die_is_refused(tmp_path):
    run = Run(tmp_path)
    run.case.move("U1", "PAD_d2d_r0_0", "inputs")
    run.case.move("U1", "PAD_CLK_0", "inputs")
    run.case.move("U2", "PAD_d2d_r0_0", None)
    assert run.refusal() == [("not_expressible", "U1.d2d_r0")]


def test_a_link_the_dies_refuse_is_refused_with_the_planners_reason(tmp_path):
    # Two inputs: the file can write the line, tier 1 refuses it.
    run = Run(tmp_path)
    run.case.move("U1", "PAD_CLK_0", "two_inputs")
    run.case.move("U2", "PAD_d2d_f0_0", "two_inputs")
    run.case.move("U1", "PAD_d2d_f0_0", None)
    assert {code for code, _ in run.refusal()} == {"not_expressible"}


def test_a_carrier_pads_net_is_held(tmp_path):
    run = Run(tmp_path, EXTERNAL)
    run.case.move("U1", "PAD_CLK_0", "U2_PAD_STATUS_0")
    assert {code for code, _ in run.refusal()} == {"carrier_net_changed"}


def test_a_sheet_from_another_run_is_refused(tmp_path):
    run = Run(tmp_path)
    other = run.design + "\n"  # any other bytes: another run's design
    assert {code for code, _ in run.refusal(design=other)} == {"sheet_other_run"}


def test_a_design_that_is_not_this_files_is_refused(tmp_path):
    run = Run(tmp_path)
    run.path.write_text(SOURCE.replace("{name: VDD,", "{name: VCC,"))
    assert run.refusal() == [("design_not_of_source", None)]
