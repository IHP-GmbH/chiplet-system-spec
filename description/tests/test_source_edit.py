# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Board edits: a reconciled board report lifted to the edits the file's one writer applies."""
from __future__ import annotations

import pytest

from chiplet_system_description import elaborate
from chiplet_system_description.errors import SystemSpecError
from chiplet_system_description.source import edit as edit_module
from chiplet_system_description.source.edit import (BOARD_REPORT, EditRefused, Replace,
                                                    board_edits, patch_system)
from chiplet_system_description.source.elaborate import elaborate_source

from tests.source_dies import SOURCE, write_source
from tests.test_source_plan import Resolver

EXTERNAL = (SOURCE.replace("    - U1.CLK\n", "")
            .replace("\nflow:\n", "\n  external:\n    profile: intm4tm2_wire_bond_v0\n"
                                    "    edges:\n      south: [U1.CLK, VDD, VSS]\nflow:\n"))


def _setup(tmp_path, text=SOURCE):
    path = write_source(tmp_path, text)
    system, _ = elaborate_source(path, Resolver())
    return path, system


def _report(system, *, moved=None, renamed=None, **top):
    moved, renamed = moved or {}, renamed or {}
    placements = []
    for inst in system.instances:
        x, y, turn = moved.get(inst.id, (inst.position.x, inst.position.y, inst.rotation_z))
        placements.append({"component": inst.id, "position_um": [x, y], "rotation_deg": turn})
    nets = [dict(row, name=renamed.get(row["name"], row["name"]))
            for row in elaborate(system)["netlist"]["nets"]]
    report = {"format": BOARD_REPORT, "version": 1, "units": "um", "frame": "gds_y_up",
              "copper_policy": "reroute", "placements": placements, "netlist": {"nets": nets}}
    report.update(top)
    return report


def _edits(path, report):
    return board_edits(path.read_bytes(), path=path, resolver=Resolver(), reconciliation=report)


def _refusal(path, report):
    with pytest.raises(EditRefused) as info:
        _edits(path, report)
    return [(f.code, f.line, f.port) for f in info.value.findings]


# --- what a board writes ----------------------------------------------------------
def test_an_unchanged_board_gives_no_edits(tmp_path):
    path, system = _setup(tmp_path)
    assert _edits(path, _report(system)) == ()


def test_a_move_is_one_replace_that_patches_one_line(tmp_path):
    path, system = _setup(tmp_path)
    edits = _edits(path, _report(system, moved={"U2": (1010.0, 1000.0, 180.0)}))
    assert edits == (Replace("dies", "U2", "at", (1010, 1000)),)
    patch = patch_system(path.read_bytes(), edits)
    assert [c.line for c in patch.changes] == [16]
    assert patch.diff.count("\n-    ") == 1 and patch.diff.count("\n+    ") == 1


@pytest.mark.parametrize("x,written", [(1000.0000000001, None), (1000.0004, None),
                                       (999.9996, None), (1000.001, "1000.001")])
def test_board_noise_under_a_nanometre_writes_nothing(tmp_path, x, written):
    path, system = _setup(tmp_path)
    edits = _edits(path, _report(system, moved={"U2": (x, 1000.0, 180.0)}))
    if written is None:
        assert edits == ()
    else:
        assert f"at: [{written}, 1000]".encode() in patch_system(path.read_bytes(), edits).text


@pytest.mark.parametrize("turn,written", [(-90.0, 270), (270.0, 270), (450.0, 90),
                                          (-180.0, None), (540.0, None), (180.0000000001, None)])
def test_a_rotation_is_taken_modulo_a_turn(tmp_path, turn, written):
    path, system = _setup(tmp_path)
    edits = _edits(path, _report(system, moved={"U2": (1000.0, 1000.0, turn)}))
    assert edits == (() if written is None else (Replace("dies", "U2", "rotation", written),))


def test_a_die_turned_in_place_writes_its_rotation_only(tmp_path):
    # The KiCad board round trip: U2 turned 90 degrees about its own place.
    path, system = _setup(tmp_path)
    edits = _edits(path, _report(system, moved={"U2": (1000.0, 1000.0, 90.0)}))
    assert edits == (Replace("dies", "U2", "rotation", 90),)
    assert b"at: [1000, 1000], rotation: 90, method:" in patch_system(path.read_bytes(),
                                                                      edits).text


def test_a_full_turn_is_written_as_none(tmp_path):
    path, system = _setup(tmp_path)
    edits = _edits(path, _report(system, moved={"U1": (1000.0, 2200.0, 360.0)}))
    assert edits == ()


@pytest.mark.parametrize("turn", [45.0, 89.5, -30.0])
def test_a_rotation_off_the_four_is_refused_not_rounded(tmp_path, turn):
    path, system = _setup(tmp_path)
    assert _refusal(path, _report(system, moved={"U2": (1000.0, 1000.0, turn)})) == [
        ("rotation_not_manhattan", 16, None)]


def test_a_rail_renamed_on_the_board_renames_the_rail(tmp_path):
    path, system = _setup(tmp_path)
    assert _edits(path, _report(system, renamed={"VDD": "VCC"})) == (
        Replace("rails", "VDD", "name", "VCC"),)


def test_a_whole_bus_renamed_moves_its_affixes(tmp_path):
    path, system = _setup(tmp_path)
    renamed = {f"d2d_{n}_n": f"top_d2d_{n}_b" for n in ("f0", "f1", "f2", "r0", "r1")}
    assert _edits(path, _report(system, renamed=renamed)) == (
        Replace("buses", "U1.d2d_f0", "net_prefix", "top_"),
        Replace("buses", "U1.d2d_f0", "net_suffix", "_b"))


# --- what a board cannot write ------------------------------------------------------
def test_an_alone_port_renamed_is_refused_naming_the_port(tmp_path):
    path, system = _setup(tmp_path)
    assert _refusal(path, _report(system, renamed={"U1_PAD_CLK_0": "clk"})) == [
        ("rename_no_field", 24, "U1.CLK")]


def test_one_bus_lane_renamed_alone_is_refused_naming_the_lane(tmp_path):
    path, system = _setup(tmp_path)
    assert _refusal(path, _report(system, renamed={"d2d_f1_n": "sideband"})) == [
        ("rename_no_field", 18, "U1.d2d_f1")]


def test_two_nets_under_one_name_are_a_malformed_report(tmp_path):
    # A rail renamed onto another rail would merge them; the report cannot say that.
    path, system = _setup(tmp_path)
    assert _refusal(path, _report(system, renamed={"VDD": "VSS"})) == [
        ("report_malformed", 0, None)]


def test_a_board_that_rewires_is_refused(tmp_path):
    path, system = _setup(tmp_path)
    report = _report(system)
    nets = {n["name"]: n for n in report["netlist"]["nets"]}
    # U2.d2d_f1 leaves its lane for the next one: the endpoints are the same set.
    f1, f2 = nets["d2d_f1_n"]["connections"], nets["d2d_f2_n"]["connections"]
    moved = next(c for c in f1 if c["component"] == "U2")
    f1.remove(moved)
    f2.append(moved)
    assert [c for c, _, _ in _refusal(path, report)] == ["connection_changed"]


def test_a_carrier_pads_net_renamed_on_the_board_is_refused(tmp_path):
    path, system = _setup(tmp_path, EXTERNAL)
    assert _refusal(path, _report(system, renamed={"VDD": "VCC"})) == [
        ("carrier_net_changed", 0, None)]


@pytest.mark.parametrize("change", [
    {"format": "interposer_pnr.something_else"}, {"version": 2}, {"version": True},
    {"units": "mm"}, {"frame": "gds_y_down"}, {"copper_policy": "keep"}, {"netlist": []},
])
def test_a_report_that_is_not_a_board_revision_v1_is_refused(tmp_path, change):
    path, system = _setup(tmp_path)
    assert _refusal(path, _report(system, **change)) == [("report_malformed", 0, None)]
    assert _refusal(path, None) == [("report_malformed", 0, None)]


def test_a_file_that_is_not_a_source_is_refused_before_the_board_is_read(tmp_path):
    path = write_source(tmp_path, SOURCE.replace("  dies:\n", "  dies: 3\n  extra:\n"))
    with pytest.raises(EditRefused) as info:
        _edits(path, None)
    assert {f.code for f in info.value.findings} == {"not_a_source"}


# --- the proof --------------------------------------------------------------------
def test_a_patch_that_elaborates_differently_raises_and_writes_nothing(tmp_path, monkeypatch):
    path, system = _setup(tmp_path)
    before = path.read_bytes()
    report = _report(system, moved={"U2": (1010.0, 1000.0, 180.0)})
    monkeypatch.setattr(edit_module, "revision_edits",
                        lambda *a: (Replace("dies", "U2", "at", (1020, 1000)),))
    with pytest.raises(SystemSpecError, match="does not elaborate to the edited system"):
        _edits(path, report)
    assert path.read_bytes() == before
