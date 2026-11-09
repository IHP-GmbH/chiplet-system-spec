# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The patcher: edits applied to a source's own bytes, every other byte kept."""
from __future__ import annotations

import builtins
import difflib
import hashlib
import io
import os
from pathlib import Path

import pytest

from chiplet_system_description.source import SourceError
from chiplet_system_description.source.patch import (Change, Delete, EditRefused, Insert,
                                                     Replace, item_line, patch_system)

FIXTURES = Path(__file__).parent / "fixtures" / "edit"
ECHO = (FIXTURES / "echo_shifted.chiplet").read_bytes()
MIXED = (FIXTURES / "mixed_gf180.chiplet").read_bytes()

#: A die moved 10 um on the board.
MOVE = [Replace("dies", "U2", "at", (1010, 1000))]
#: What a sheet edit gives: a port pair out of alone into a link, a rail
#: merged into another, a link renamed, and a move.
SHEET = [Delete("alone", "U1.CLK"), Delete("alone", "U2.clk_PAD"),
         Insert("links", (("a", "U1.CLK"), ("b", "U2.clk_PAD"), ("net", "bench_clk"))),
         Replace("links", "U1.d2d_f0", "net", "d2d_f0_fixture"),
         Replace("rails", "U1_VSS", "ports", ["U1.VSS", "U2.VSS"]),
         Delete("rails", "U2_VSS"),
         Replace("dies", "U2", "at", (3966.5, 3100))]


def _changed(old: bytes, new: bytes):
    return [line for line in difflib.ndiff(old.decode().splitlines(), new.decode().splitlines())
            if line[:1] in "+-"]


# --- guard 7: the golden files --------------------------------------------------
@pytest.mark.parametrize("source,edits,golden", [
    (ECHO, MOVE, "echo_shifted.golden.chiplet"),
    (MIXED, SHEET, "mixed_gf180.golden.chiplet"),
])
def test_a_patch_writes_its_golden_byte_for_byte(source, edits, golden):
    patch = patch_system(source, edits)
    assert patch.text == (FIXTURES / golden).read_bytes()
    assert patch.sha256_before == hashlib.sha256(source).hexdigest()
    assert patch.diff.startswith("--- a/system.chiplet\n+++ b/system.chiplet\n")


def test_a_move_touches_its_one_line_and_keeps_every_comment():
    patch = patch_system(ECHO, MOVE)
    assert _changed(ECHO, patch.text) == [
        "-     - {id: U2, die: inputs/dies/CF_ECHO_CX_BONDPAD, at: [1000, 1000], rotation: 0, "
        "method: gf180mcu_flow_demo_pillar, thickness: 725}",
        "+     - {id: U2, die: inputs/dies/CF_ECHO_CX_BONDPAD, at: [1010, 1000], rotation: 0, "
        "method: gf180mcu_flow_demo_pillar, thickness: 725}",
    ]
    assert patch.changes == (Change("dies", "U2", "at", "[1000, 1000]", "[1010, 1000]", 21),)


def test_the_changes_come_in_file_order_with_identities():
    changes = patch_system(MIXED, SHEET).changes
    assert [(c.block, c.item, c.field, c.line) for c in changes] == [
        ("dies", "U2", "at", 17), ("links", "U1.d2d_f0", "net", 19),
        ("links", "U1.CLK", None, 20), ("rails", "U1_VSS", "ports", 23),
        ("rails", "U2_VSS", None, 25), ("alone", "U1.CLK", None, 29),
        ("alone", "U2.clk_PAD", None, 40)]
    assert changes[2].new == "    - {a: U1.CLK, b: U2.clk_PAD, net: bench_clk}"
    assert changes[5].old == "    - U1.CLK   # driven from the bench"


def test_a_replaced_token_keeps_the_rest_of_its_line():
    text = patch_system(ECHO, [Replace("dies", "U1", "at", (1000, 2400.25)),
                               Replace("rails", "V12", "ports", ["U1.VDD", "U1.IOVDD"]),
                               Replace("rails", "V33", "ports", ["U2.VDD"])]).text
    assert (b"at: [1000, 2400.25], rotation: 0, method: cupillar_opt1, thickness: 750}"
            b"  # sg13g2, 750 um bulk\n") in text
    assert b"    - {name: V12, class: power, ports: [U1.VDD, U1.IOVDD]}   # core, U1 only\n" in text


def test_an_edit_keeps_a_rail_s_volts():
    """A 1.2 rail's volts sits between its class and its ports, and no sheet
    edit replaces it: renaming the rail keeps it."""
    v12 = ECHO.replace(b'format_version: "1.1"', b'format_version: "1.2"').replace(
        b"{name: V12, class: power, ports", b"{name: V12, class: power, volts: 1.2, ports")
    text = patch_system(v12, [Replace("rails", "V12", "name", "VCORE")]).text
    assert b"{name: VCORE, class: power, volts: 1.2, ports: [U1.VDD]}" in text
    with pytest.raises(ValueError, match="'volts' cannot be replaced"):
        patch_system(v12, [Replace("rails", "V12", "volts", 1.25)])


def test_an_edit_keeps_a_rail_s_routing_requirement_and_a_new_rail_writes_one():
    """A 1.5 rail's width sits between its volts and its ports, no edit
    replaces it, and a rail inserted with a current writes its numbers and
    raises a 1.1 stamp to 1.5."""
    v15 = ECHO.replace(b'format_version: "1.1"', b'format_version: "1.5"').replace(
        b"{name: V12, class: power, ports", b"{name: V12, class: power, volts: 1.2, width_um: 20, ports")
    text = patch_system(v15, [Replace("rails", "V12", "name", "VCORE")]).text
    assert b"{name: VCORE, class: power, volts: 1.2, width_um: 20, ports: [U1.VDD]}" in text
    with pytest.raises(ValueError, match="'width_um' cannot be replaced"):
        patch_system(v15, [Replace("rails", "V12", "width_um", 25)])
    rail = (("name", "V12"), ("class", "power"), ("amps", 1.5),
            ("current_density_ma_per_um", 75.0), ("ports", ["U1.VDD"]))
    assert item_line("rails", rail) == (
        "{name: V12, class: power, amps: 1.5, current_density_ma_per_um: 75, ports: [U1.VDD]}")
    text = patch_system(ECHO, [Delete("rails", "V12"), Insert("rails", rail)]).text
    assert b'format_version: "1.5"' in text and item_line("rails", rail).encode() in text


# --- placements and rotations ------------------------------------------------------
@pytest.mark.parametrize("x", [1000.0000000001, 1000.0004, 999.9996])
def test_a_move_under_a_nanometre_writes_nothing(x):
    patch = patch_system(ECHO, [Replace("dies", "U2", "at", (x, 1000))])
    assert patch.text is ECHO and patch.diff == "" and patch.changes == ()


@pytest.mark.parametrize("x,written", [(1000.001, b"1000.001"), (1000.0006, b"1000.001"),
                                       (1010.000, b"1010"), (-2.5, b"-2.5")])
def test_a_move_is_written_on_the_grid_without_trailing_zeros(x, written):
    text = patch_system(ECHO, [Replace("dies", "U2", "at", (x, 1000))]).text
    assert b"at: [" + written + b", 1000], rotation: 0" in text


def test_a_rotation_is_replaced_or_inserted_after_at():
    assert b"at: [1000, 1000], rotation: 270, method:" in patch_system(
        ECHO, [Replace("dies", "U2", "rotation", 270)]).text
    assert b"at: [3966, 3100], rotation: 90, method:" in patch_system(
        MIXED.replace(b"at: [3966, 3100], rotation: 0,", b"at: [3966, 3100],"),
        [Replace("dies", "U2", "rotation", 90)]).text


@pytest.mark.parametrize("turn", [45, 360, -90])
def test_a_rotation_off_the_four_is_not_the_patchers_to_fold(turn):
    with pytest.raises(ValueError, match="is not 0, 90, 180 or 270"):
        patch_system(ECHO, [Replace("dies", "U2", "rotation", turn)])


# --- names ---------------------------------------------------------------------
def test_a_rail_renamed_is_renamed_on_its_edge_too():
    patch = patch_system(ECHO, [Replace("rails", "V12", "name", "VCORE")])
    assert _changed(ECHO, patch.text) == [
        "-     - {name: V12, class: power, ports: [U1.VDD]}   # core, U1 only",
        "+     - {name: VCORE, class: power, ports: [U1.VDD]}   # core, U1 only",
        "-       west: [U1.RSTN, V12]",
        "+       west: [U1.RSTN, VCORE]",
    ]


def test_bus_affixes_are_inserted_replaced_and_dropped():
    text = patch_system(ECHO, [Replace("buses", "U1.d2d_f0", "net_prefix", "top_"),
                               Replace("buses", "U1.d2d_f0", "net_suffix", None)]).text
    assert b'"d2d_r[0..9]"], net_prefix: top_}\n' in text
    again = patch_system(text, [Replace("buses", "U1.d2d_f0", "net_prefix", "core_")]).text
    assert b'"d2d_r[0..9]"], net_prefix: core_}\n' in again


def test_a_name_the_bare_rule_refuses_is_written_quoted():
    text = patch_system(MIXED, [Replace("links", "U1.d2d_f3", "net", "on")]).text
    assert b'{a: U1.d2d_f3, b: U2.bidir_PAD_12, net: "on"}' in text


def test_bus_ports_compare_expanded_and_write_collapsed():
    same = patch_system(ECHO, [Replace("buses", "U1.d2d_f0", "ports",
                                       [f"d2d_f{i}" for i in range(10)]
                                       + ["d2d_fclk", "d2d_frst_n"]
                                       + [f"d2d_r{i}" for i in range(10)])])
    assert same.text is ECHO
    fewer = patch_system(ECHO, [Replace("buses", "U1.d2d_f0", "ports",
                                        [f"d2d_f{i}" for i in range(10)])]).text
    assert b'ports: ["d2d_f[0..9]"], net_suffix: _n}' in fewer


# --- items in and out ----------------------------------------------------------------
def test_an_insert_into_an_absent_block_writes_its_key_in_order():
    patch = patch_system(ECHO, [Insert("open", "U1.SPARE")])
    assert _changed(ECHO, patch.text) == ["+   open:", "+     - U1.SPARE"]
    lines = patch.text.decode().splitlines()
    assert lines[lines.index("  open:") - 1].startswith("    - {name: V33,")
    assert lines[lines.index("  open:") + 2] == "  external:"


def test_the_last_item_out_takes_its_block_key_with_it():
    patch = patch_system(MIXED, [Delete("links", "U1.d2d_f0"), Delete("links", "U1.d2d_f3")])
    assert _changed(MIXED, patch.text) == [
        "-   links:   # two fixture nets",
        "-     - {a: U1.d2d_f0, b: U2.bidir_PAD_11, net: fixture_d2d_f0}",
        "-     - {a: U1.d2d_f3, b: U2.bidir_PAD_12, net: fixture_d2d_f3}",
    ]


def test_a_block_emptied_and_refilled_keeps_its_key():
    patch = patch_system(MIXED, [Delete("links", "U1.d2d_f0"), Delete("links", "U1.d2d_f3"),
                                 Insert("links", (("a", "U1.d2d_f0"), ("b", "U2.bidir_PAD_12")))])
    assert b"  links:   # two fixture nets\n    - {a: U1.d2d_f0, b: U2.bidir_PAD_12}\n" in patch.text


def test_a_link_whose_net_is_the_default_is_inserted_without_it():
    text = patch_system(MIXED, [Delete("alone", "U1.CLK"), Delete("alone", "U2.clk_PAD"),
                                Insert("links", (("a", "U1.CLK"), ("b", "U2.clk_PAD"),
                                                 ("net", "U1_CLK")))]).text
    assert b"    - {a: U1.CLK, b: U2.clk_PAD}\n  rails:\n" in text


@pytest.mark.parametrize("edits,needle", [
    ([Replace("dies", "U9", "at", (0, 0))], "dies has no item 'U9'"),
    ([Replace("dies", "U2", "die", "inputs/dies/OTHER")], "field 'die' cannot be replaced"),
    ([Replace("rails", "GND", "ports", None)], "field 'ports' is required"),
    ([Insert("dies", (("id", "U3"),))], "cannot be inserted into 'dies'"),
    ([Insert("rails", (("name", "GND"), ("class", "ground"), ("ports", ["U1.VSS"])))],
     "rails would hold two items 'GND'"),
    ([Replace("rails", "GND", "name", "VSS"), Delete("rails", "GND")],
     "is both replaced and deleted"),
    (["not an edit"], "not an edit"),
])
def test_an_edit_the_file_has_no_place_for_is_a_caller_defect(edits, needle):
    with pytest.raises((ValueError, TypeError), match=needle):
        patch_system(ECHO, edits)


@pytest.mark.parametrize("edits", [[Insert("alone", "U1")],
                                   [Replace("rails", "GND", "ports", [])]])
def test_a_patch_tier_0_refuses_raises_and_returns_nothing(edits):
    with pytest.raises(SourceError):
        patch_system(MIXED, edits) if edits[0].block == "alone" else patch_system(ECHO, edits)


# --- the bytes ---------------------------------------------------------------------
def test_nothing_to_change_returns_the_same_bytes_and_no_diff():
    patch = patch_system(ECHO, [Replace("dies", "U2", "at", (1000, 1000)),
                                Replace("dies", "U2", "rotation", 0),
                                Replace("rails", "GND", "name", "GND")])
    assert patch.text is ECHO and patch.diff == "" and patch.changes == ()
    assert patch_system(ECHO, []).text is ECHO


def test_crlf_endings_are_kept():
    crlf = ECHO.replace(b"\n", b"\r\n")
    golden = (FIXTURES / "echo_shifted.golden.chiplet").read_bytes()
    assert patch_system(crlf, MOVE).text == golden.replace(b"\n", b"\r\n")


@pytest.mark.parametrize("data", [ECHO.replace(b"\n", b"\r\n", 3), ECHO.replace(b"\n", b"\r", 1)])
def test_mixed_endings_are_refused(data):
    with pytest.raises(EditRefused) as info:
        patch_system(data, MOVE)
    assert [f.code for f in info.value.findings] == ["line_endings_mixed"]


def test_a_missing_final_newline_stays_missing():
    data = ECHO.rstrip(b"\n")
    assert patch_system(data, MOVE).text == (
        FIXTURES / "echo_shifted.golden.chiplet").read_bytes().rstrip(b"\n")
    tail = MIXED[:MIXED.index(b"flow:\n")].rstrip(b"\n")
    text = patch_system(tail, [Insert("alone", "U2.spare_PAD")]).text
    assert text == tail + b"\n    - U2.spare_PAD"


@pytest.mark.parametrize("data", [b"\xff\xfe not text", b"system: [\n", b"format_version: 1\n"])
def test_bytes_that_are_not_a_source_are_refused(data):
    with pytest.raises(EditRefused) as info:
        patch_system(data, MOVE)
    assert {f.code for f in info.value.findings} == {"not_a_source"}


def test_the_patcher_reads_no_file_and_no_environment(monkeypatch):
    def nothing(*args, **kwargs):
        raise AssertionError("the patcher opened a file")
    for target, name in ((builtins, "open"), (io, "open"), (os, "open"), (Path, "open"),
                         (Path, "read_bytes"), (Path, "read_text"), (os, "getenv")):
        monkeypatch.setattr(target, name, nothing)
    monkeypatch.setattr(os, "environ", {})
    patch = patch_system(MIXED, SHEET)
    assert patch.changes and patch.text != MIXED
