# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 1 and elaboration: a source read against its die folders.

The elaboration tests hold a source to the System the builder makes from the
same intent, through the one serializer, so "the file elaborates to what a
person would have written in Python" is a byte comparison.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from decimal import Decimal

import pytest

from chiplet_system_description import (
    CUPILLAR_OPT1,
    INTM4TM2,
    SG13G2,
    Die,
    Dimensions,
    InterfaceDefinition,
    Interposer,
    InterposerIoPad,
    Port,
    Position,
    Role,
    SystemBuilder,
    elaborate,
    load_pins_json,
)
from chiplet_system_description._cfio import cfio
from chiplet_system_description.source import SourceError, read_source, supply_pins
from chiplet_system_description.source.elaborate import elaborate_source
from chiplet_system_description.source.check import paste_block, run as check_run
from chiplet_system_description.source.plan import collapse, plan

from tests.source_dies import DRIVER, ECHO, SOURCE, checked, write_die, write_source


class Resolver:
    def __init__(self):
        self.calls = []

    def technology(self, id):
        return {"intm4tm2": INTM4TM2, "sg13g2": SG13G2}[id]

    def method(self, id, registry):
        self.calls.append(("method", id, registry))
        return {"cupillar_opt1": (CUPILLAR_OPT1, Decimal("44"))}[id]

    def plane(self):
        return Decimal("10.8103")

    def io_pads(self, profile, body, edges):
        self.calls.append(("io_pads", profile, body, edges))
        return tuple(InterposerIoPad(f"J_{net}", net, Position(10.0 * i, 0.0), 80.0, 80.0)
                     for i, net in enumerate(n for _, nets in edges for n in nets))


def _read(path):
    """The plan of the source at ``path``, less rule 4's notes: the fixture
    dies declare no volts, and the voltage check has its own tests."""
    p = plan(read_source(path))
    return dataclasses.replace(p, findings=checked(p.findings))


def _plan(tmp_path, text=SOURCE):
    return _read(write_source(tmp_path, text))


def _messages(p):
    return [(f.line, f.message) for f in p.findings]


def _has(p, needle):
    lines = [line for line, message in _messages(p) if needle in message]
    assert lines, _messages(p)
    return lines


# --- the topology ------------------------------------------------------------
def test_the_fixture_has_no_finding(tmp_path):
    p = _plan(tmp_path)
    assert p.refusals == () and p.notes == ()


def test_a_lane_is_oriented_output_end_first(tmp_path):
    p = _plan(tmp_path)
    ends = [(link.a.instance, link.a.port, link.b.instance) for link in p.links]
    assert ends == [("U1", "d2d_f0", "U2"), ("U1", "d2d_f1", "U2"), ("U1", "d2d_f2", "U2"),
                    ("U2", "d2d_r0", "U1"), ("U2", "d2d_r1", "U1")]
    assert [link.net for link in p.links][:2] == ["d2d_f0_n", "d2d_f1_n"]


def test_alone_ties_come_in_inventory_order_whatever_the_file_order(tmp_path):
    # The file writes U2.STATUS before U1.CLK; dies order puts U1 first.
    p = _plan(tmp_path)
    assert [(t.instance, t.net) for t in p.ties] == [("U1", "U1_PAD_CLK_0"),
                                                     ("U2", "U2_PAD_STATUS_0")]


def test_a_rail_joins_every_pad_by_ordinal(tmp_path):
    p = _plan(tmp_path)
    assert p.supplies[0].endpoints == (("U1", "PAD_VDD_0"), ("U1", "PAD_VDD_1"),
                                       ("U2", "PAD_VDD_0"))


def test_a_one_pad_rail_is_a_classed_tie_after_the_alone_ties(tmp_path):
    text = SOURCE.replace('{name: VDD, class: power, ports: [U1.VDD, U2.VDD]}',
                          '{name: V2, class: power, ports: [U2.VDD]}\n'
                          '    - {name: V1, class: power, ports: [U1.VDD]}')
    p = _plan(tmp_path, text)
    assert p.refusals == ()
    assert [(t.net, t.net_class) for t in p.ties] == [
        ("U1_PAD_CLK_0", "signal"), ("U2_PAD_STATUS_0", "signal"), ("V2", "power")]
    assert [s.net for s in p.supplies] == ["V1", "VSS"]


def test_collapse():
    assert collapse(["a0", "a1", "a2", "b", "a4", "a5"]) == ["a[0..2]", "b", "a[4..5]"]
    assert collapse(["x1_n", "x2_n"]) == ["x[1..2]_n"]
    # A collapsed entry always expands back to what it stands for.
    from chiplet_system_description.source import expand
    assert expand(collapse(["a01", "a02"])[0]) == ["a01", "a02"]


# --- tier 1 findings ------------------------------------------------------------
def test_an_unknown_port_is_named(tmp_path):
    p = _plan(tmp_path, SOURCE.replace('U1.CLK', 'U1.CLOCK'))
    assert _has(p, "die DRIVER serves no port 'CLOCK'") == [24]


def test_a_lane_needs_one_output_end_and_one_input_end(tmp_path):
    write_die(tmp_path / "dies" / "ECHO", "ECHO",
              [("d2d_f0", "OUTPUT", "SIGNAL", "d2d_f0", 1)] + list(ECHO[2:]))
    path = tmp_path / "system.chiplet"
    write_source(tmp_path)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", [
        ("STATUS", "OUTPUT", "SIGNAL", "STATUS", 1),
        ("d2d_f0", "OUTPUT", "SIGNAL", "d2d_f0", 1), ("d2d_f1", "INPUT", "SIGNAL", "d2d_f1", 2),
        ("d2d_f2", "INOUT", "SIGNAL", "d2d_f2", 1), ("d2d_r0", "INOUT", "SIGNAL", "d2d_r0", 1),
        ("d2d_r1", "OUTPUT", "SIGNAL", "d2d_r1", 1), ("VDD", "INOUT", "POWER", "VDD", 1),
        ("VSS", "INOUT", "GROUND", "VSS", 1)])
    p = _read(path)
    messages = [m for _, m in _messages(p)]
    assert any("U1.d2d_f0 is OUTPUT and U2.d2d_f0 is OUTPUT" in m for m in messages)
    assert any("U1.d2d_f1 has 1 pad(s) and U2.d2d_f1 has 2" in m for m in messages)
    # INOUT may be either end: it is the input under U1's OUTPUT, the output over U1's INPUT.
    oriented = {link.a.port: link.a.instance for link in p.links}
    assert oriented == {"d2d_f2": "U1", "d2d_r0": "U2", "d2d_r1": "U2"}


def test_a_rail_port_has_the_rail_class(tmp_path):
    p = _plan(tmp_path, SOURCE.replace('ports: [U1.VSS, U2.VSS]', 'ports: [U1.VSS, U2.VDD]')
              .replace('ports: [U1.VDD, U2.VDD]', 'ports: [U1.VDD]'))
    assert _has(p, "U2.VDD is a POWER port and rail 'VSS' is ground") == [21]


def test_a_supply_port_does_not_go_alone(tmp_path):
    p = _plan(tmp_path, SOURCE.replace('ports: [U1.VSS, U2.VSS]', 'ports: [U1.VSS]')
              .replace('    - U1.CLK\n', '    - U1.CLK\n    - U2.VSS\n'))
    assert _has(p, "U2.VSS is a GROUND port; it goes in rails") == [25]


def test_a_die_outside_the_carrier_or_over_another(tmp_path):
    p = _plan(tmp_path, SOURCE.replace("at: [1000, 1000]", "at: [1000, 400]"))
    assert _has(p, "U2 (1000 x 1000 um at 1000, 400) leaves the carrier") == [16]
    p = _plan(tmp_path, SOURCE.replace("at: [1000, 1000]", "at: [1500, 1500]"))
    assert _has(p, "U2 overlaps U1") == [16]
    # Touching edges do not overlap.
    assert _plan(tmp_path, SOURCE.replace("at: [1000, 1000]", "at: [1000, 1200]")).refusals == ()


def test_a_rotated_die_swaps_its_box(tmp_path):
    write_source(tmp_path)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, box=(400.0, 1000.0))
    upright = SOURCE.replace("at: [1000, 1000], rotation: 180", "at: [200, 600], rotation: 0")
    (tmp_path / "system.chiplet").write_text(upright, encoding="utf-8")
    assert _read(tmp_path / "system.chiplet").refusals == ()
    turned = upright.replace("rotation: 0, method", "rotation: 90, method", 2)
    (tmp_path / "system.chiplet").write_text(turned, encoding="utf-8")
    p = _read(tmp_path / "system.chiplet")
    assert _has(p, "U2 (1000 x 400 um at 200, 600) leaves the carrier") == [16]


@pytest.mark.parametrize("box,at,rot,size", [
    ((1730.265, 1000.0), "[10975.975, 1000]", 0, "1730.27 x 1000"),
    ((1000.0, 8913.925), "[-35353.8259600654, 1000]", 90, "8913.92 x 1000"),
    ((4902.715, 1000.0), "[-39905.237746070765, 1000]", 0, "4902.72 x 1000"),
    ((1000.5, 800.25), "[1000, 400]", 0, "1000.5 x 800.25"),
])
def test_a_non_integer_die_prints_its_own_extent(tmp_path, box, at, rot, size):
    write_source(tmp_path)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, box=box)
    text = SOURCE.replace("at: [1000, 1000], rotation: 180", f"at: {at}, rotation: {rot}")
    (tmp_path / "system.chiplet").write_text(text, encoding="utf-8")
    messages = [f.message for f in _read(tmp_path / "system.chiplet").findings]
    assert any(f"U2 ({size} um" in m for m in messages), messages
    assert not any("1730.26" in m for m in messages)


def test_a_missing_thickness_is_a_note(tmp_path):
    p = _plan(tmp_path, SOURCE.replace(', thickness: 750}\n  buses', '}\n  buses'))
    assert p.refusals == ()
    assert [(n.line, n.message) for n in p.notes] == [(16, "dies: U2: thickness not given; 0.0 used")]
    assert p.notes[0].render("s") == "s:16: note: dies: U2: thickness not given; 0.0 used"


def test_net_names_are_unique(tmp_path):
    text = SOURCE.replace('{name: VSS', '{name: U1_PAD_CLK_0')
    assert _has(_plan(tmp_path, text), "net 'U1_PAD_CLK_0' is also named on line 24") == [21]


def test_an_unreadable_die_folder_is_one_finding(tmp_path):
    p = _plan(tmp_path, SOURCE.replace('die: dies/ECHO', 'die: dies/NOPE'))
    lines = [line for line, _ in _messages(p)]
    assert lines == [16] and "cannot be read" in p.findings[0].message


def test_a_contract_whose_pdk_maps_nowhere_is_refused(tmp_path):
    write_source(tmp_path)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, pdk="nobody-pdk")
    p = _read(tmp_path / "system.chiplet")
    assert "maps to no technology" in p.findings[0].message


def test_a_die_that_states_no_port_names_no_pad(tmp_path):
    write_source(tmp_path)
    inv = tmp_path / "dies" / "DRIVER" / "pins.json"
    data = json.loads(inv.read_text(encoding="utf-8"))
    for row in data["pins"]:
        row.pop("port", None)
        row.pop("ordinal", None)
    inv.write_text(json.dumps(data), encoding="utf-8")
    p = _read(tmp_path / "system.chiplet")
    assert "U1" not in p.folders
    found = [f for f in p.findings if f.message.startswith("dies: U1:")]
    assert len(found) == 1 and found[0].code is None
    assert "states no ports" in found[0].message
    assert "serves no port" not in found[0].message


def test_a_spare_pad_is_refused_before_a_later_bad_row(tmp_path):
    write_source(tmp_path)
    inv = tmp_path / "dies" / "DRIVER" / "pins.json"
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["pins"].insert(0, {
        "name": "VDDX", "pad_index": 99,
        "center_x_dbu": 1000.0, "center_y_dbu": 1000.0,
        "width_dbu": 1000.0, "height_dbu": 1000.0,
        "net": "VDDX", "direction": "INOUT", "role": "POWER",
    })
    late = dict(data["pins"][1])
    late["name"] = "PAD_LATE"
    late["pad_index"] = 98
    del late["ordinal"]
    data["pins"].append(late)
    inv.write_text(json.dumps(data), encoding="utf-8")
    p = _read(tmp_path / "system.chiplet")
    found = [f for f in p.findings if "VDDX" in f.message or "ordinal" in f.message]
    assert len(found) == 1
    assert "pad 'VDDX' serves no port" in found[0].message
    assert "ordinal" not in found[0].message


def test_a_pad_that_serves_no_port_is_refused(tmp_path):
    """A portless pad is recorded, and the planner still refuses it."""
    write_source(tmp_path)
    inv = tmp_path / "dies" / "DRIVER" / "pins.json"
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["pins"].append({
        "name": "VDDX", "pad_index": 99,
        "center_x_dbu": 1000.0, "center_y_dbu": 1000.0,
        "width_dbu": 1000.0, "height_dbu": 1000.0,
        "net": "VDDX", "direction": "INOUT", "role": "POWER",
    })
    inv.write_text(json.dumps(data), encoding="utf-8")
    p = _read(tmp_path / "system.chiplet")
    assert "U1" not in p.folders
    found = [f for f in p.findings if "VDDX" in f.message]
    assert len(found) == 1 and found[0].code is None
    assert found[0].message.startswith("dies: U1:")
    assert "pad 'VDDX' serves no port; a source wires the ports its dies state" in found[0].message


def test_an_inventory_port_with_two_directions_is_refused(tmp_path):
    write_source(tmp_path)
    inv = tmp_path / "dies" / "ECHO" / "pins.json"
    data = json.loads(inv.read_text())
    data["pins"].append(dict(data["pins"][0], name="PAD_X", ordinal=1, direction="INPUT"))
    inv.write_text(json.dumps(data))
    p = _read(tmp_path / "system.chiplet")
    assert "is stated as" in p.findings[0].message


# --- unaccounted ports and their suggested lines -------------------------------------
def test_unaccounted_ports_come_with_lines_to_paste(tmp_path):
    text = (SOURCE.replace('"d2d_f[0..2]", "d2d_r[0..1]"', 'd2d_f0')
            .replace('ports: [U1.VDD, U2.VDD]', 'ports: [U1.VDD]')
            .replace('  rails:\n', '  rails:\n', 1)
            .replace('    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n', '')
            .replace('    - U1.CLK\n', ''))
    p = _plan(tmp_path, text)
    assert _has(p, "die port(s) are not accounted for")
    by_block = [(s.block, s.line, s.replace_line) for s in p.suggestions]
    assert by_block == [
        ("buses", '    - {ends: [U1, U2], ports: ["d2d_f[1..2]", "d2d_r[0..1]"]}', None),
        ("rails", '    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}', 20),
        ("rails", '    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}', None),
        ("alone", '    - U1.CLK', None),
    ]
    assert p.suggestions[0].records()[0] == {
        "port": "U1.d2d_f1", "block": "buses",
        "line": '    - {ends: [U1, U2], ports: ["d2d_f[1..2]", "d2d_r[0..1]"]}',
        "replace_line": None, "choice": None}


def test_a_block_the_file_lacks_puts_its_findings_on_dies(tmp_path):
    p = _plan(tmp_path, SOURCE.replace("  alone:\n    - U2.STATUS\n    - U1.CLK\n", ""))
    assert _has(p, "are not accounted for") == [14, 14]
    assert SOURCE.split("\n")[13] == "  dies:"


def test_pasting_every_suggestion_passes_tiers_0_and_1(tmp_path):
    text = (SOURCE.replace('"d2d_f[0..2]", "d2d_r[0..1]"', 'd2d_f0')
            .replace('    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n', '')
            .replace('    - U1.CLK\n', ''))
    p = _plan(tmp_path, text)
    lines = text.split("\n")
    for s in p.suggestions:
        if s.replace_line:
            lines[s.replace_line - 1] = s.line
    for s in p.suggestions:
        if s.replace_line:
            continue
        key = lines.index(f"  {s.block}:")
        lines.insert(key + 1, s.line)
    pasted = "\n".join(lines)
    (tmp_path / "system.chiplet").write_text(pasted, encoding="utf-8")
    again = _read(tmp_path / "system.chiplet")
    assert again.findings == (), [f.message for f in again.findings]


# --- rails and the external edges ------------------------------------------------------
LS = tuple(DRIVER) + (("IOVDD", "INOUT", "POWER", "IOVDD", 1),)
#: Example 8's rails and edges over the fixture dies: two power rails, each on an edge.
PADDED = (SOURCE
          .replace("  rails:\n    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}\n"
                   "    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n"
                   "  alone:\n    - U2.STATUS\n    - U1.CLK\n",
                   "  rails:\n    - {name: GND, class: ground, ports: [U1.VSS, U2.VSS]}\n"
                   "    - {name: V12, class: power, ports: [U1.VDD]}\n"
                   "    - {name: V33, class: power, ports: [U1.IOVDD, U2.VDD]}\n"
                   "  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n"
                   "      north: [U1.CLK]\n      south: [U2.STATUS]\n"
                   "      west: [V12]\n      east: [V33, GND]\n"))


def _padded(tmp_path, text):
    path = write_source(tmp_path, text)
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS)
    return _read(path)


def _paste(tmp_path, text, suggestions):
    lines = text.split("\n")
    for s in suggestions:
        if s.replace_line:
            lines[s.replace_line - 1] = s.line
    for s in suggestions:
        if not s.replace_line:
            key = lines.index("    edges:" if s.block == "external" else f"  {s.block}:")
            lines.insert(key + 1, s.line)
    pasted = "\n".join(lines)
    (tmp_path / "system.chiplet").write_text(pasted, encoding="utf-8")
    return pasted, _read(tmp_path / "system.chiplet")


def test_the_padded_fixture_has_no_finding(tmp_path):
    assert _padded(tmp_path, PADDED).findings == ()


def test_a_rail_on_no_edge_is_a_finding(tmp_path):
    text = PADDED.replace('      west: [V12]\n', "")
    assert _has(_padded(tmp_path, text), "'V12' is on no external edge") == [21]
    # Without an external block nothing escapes, and no rail is held to an edge.
    assert _plan(tmp_path).findings == ()


def test_a_power_port_left_out_goes_on_a_rail_an_edge_carries(tmp_path):
    """Example 8 minus U2.VDD: the guess was a new rail VDD, which no edge carries."""
    text = PADDED.replace('ports: [U1.IOVDD, U2.VDD]', 'ports: [U1.IOVDD]')
    p = _padded(tmp_path, text)
    sentence = ("U2.VDD joins one of V12, V33; the file holds no voltage, so pick the one "
                "the die needs.")
    assert [(s.block, s.line, s.replace_line, s.choice) for s in p.suggestions] == [
        ("rails", '    - {name: V12, class: power, ports: [U1.VDD, U2.VDD]}', 21, sentence),
        ("rails", '    - {name: V33, class: power, ports: [U1.IOVDD, U2.VDD]}', 22,
         sentence)]
    # One finding, on the first line it may replace, and it carries the choice.
    assert _messages(p) == [(21, f"1 die port(s) are not accounted for: U2.VDD; {sentence}")]
    # The port is counted once, though two lines may account for it.
    block = paste_block(Path("s.chiplet"), p.suggestions)
    assert block[0].startswith("s.chiplet: 1 die port(s) are not accounted for")
    assert block[1:] == [
        f"  {sentence} Paste one of:",
        "  rails, replace line 21:",
        '    - {name: V12, class: power, ports: [U1.VDD, U2.VDD]}',
        "  rails, replace line 22:",
        '    - {name: V33, class: power, ports: [U1.IOVDD, U2.VDD]}']
    # Either one, pasted, passes.
    for pick in p.suggestions:
        assert _paste(tmp_path, text, [pick])[1].findings == ()
    # The old guess, pasted as it was, is refused now.
    old = text.replace('    - {name: V33, class: power, ports: [U1.IOVDD]}\n',
                       '    - {name: V33, class: power, ports: [U1.IOVDD]}\n'
                       '    - {name: VDD, class: power, ports: [U2.VDD]}\n')
    assert _has(_padded(tmp_path, old), "'VDD' is on no external edge") == [23]


def test_a_power_port_no_line_can_take_is_still_a_finding(tmp_path):
    """A rail name holding U+FEFF moves the column YAML reports for the ports
    list's closing bracket, so neither rail U2.VDD may join can be extended as
    written: no line is suggested, the port is still a finding, on the rails
    key, and the paste block has nothing to paste."""
    text = PADDED.replace('ports: [U1.IOVDD, U2.VDD]', 'ports: [U1.IOVDD]')
    for name in ("V12", "V33"):
        text = text.replace(f"name: {name},", f'name: "{name}\ufeff",').replace(
            f"[{name}", f'["{name}\ufeff"')
    p = _padded(tmp_path, text)
    assert p.suggestions == ()
    rails = text.split("\n").index("  rails:") + 1
    assert _messages(p) == [(rails, "1 die port(s) are not accounted for: U2.VDD; no line is "
                                    "suggested, so add each to the rail it joins")]
    assert paste_block(Path("s.chiplet"), p.suggestions) == []


def test_one_rail_of_the_class_on_an_edge_is_the_one_line(tmp_path):
    text = (PADDED.replace('ports: [U1.IOVDD, U2.VDD]', 'ports: [U1.IOVDD]')
            .replace('      west: [V12]\n', '').replace('    - {name: V12, class: power, '
                                                         'ports: [U1.VDD]}\n', '')
            .replace('    - {name: V33, class: power, ports: [U1.IOVDD]}',
                     '    - {name: V33, class: power, ports: [U1.IOVDD, U1.VDD]}'))
    p = _padded(tmp_path, text)
    assert [(s.line, s.replace_line, s.choice) for s in p.suggestions] == [
        ('    - {name: V33, class: power, ports: [U1.IOVDD, U1.VDD, U2.VDD]}', 21, None)]


def test_a_new_rail_comes_with_the_edge_line_that_carries_it(tmp_path):
    text = (PADDED.replace('    - {name: GND, class: ground, ports: [U1.VSS, U2.VSS]}\n', "")
            .replace('[V33, GND]', '[V33]'))
    p = _padded(tmp_path, text)
    assert [(s.block, s.line, s.replace_line) for s in p.suggestions] == [
        ("rails", '    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}', None),
        ("external", '      west: [V12, VSS]', 27),
    ]
    # The edge line accounts for the same ports, so the rail's key is the one finding.
    assert _messages(p) == [(19, "2 die port(s) are not accounted for: U1.VSS, U2.VSS")]
    assert _paste(tmp_path, text, p.suggestions)[1].findings == ()
    # An edge the file does not name yet is a new line under edges.
    bare = text.replace('      west: [V12]\n', "").replace('[V33]', '[V33, V12]')
    p = _padded(tmp_path, bare)
    assert p.suggestions[-1].line == '      west: [VSS]' and p.suggestions[-1].replace_line is None
    assert _paste(tmp_path, bare, p.suggestions)[1].findings == ()


# --- supply volts ----------------------------------------------------------------------
#: Example 8 with its voltages: V12 on U1's core, V33 on U1's I/O ring and U2.
VOLTED = (PADDED.replace('format_version: "1.1"', 'format_version: "1.2"')
          .replace("{name: V12, class: power,", "{name: V12, class: power, volts: 1.2,")
          .replace("{name: V33, class: power,", "{name: V33, class: power, volts: 3.3,"))
U1_SUPPLY = {"VDD": 1.2, "IOVDD": 3.3, "VSS": 0}
U2_SUPPLY = {"VDD": 3.3, "VSS": 0}


def _volted(tmp_path, text=VOLTED, u1=U1_SUPPLY, u2=U2_SUPPLY):
    path = write_source(tmp_path, text)
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS, supply=u1)
    write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, supply=u2)
    return plan(read_source(path))


def test_rails_whose_volts_match_their_domains_have_no_finding(tmp_path):
    assert _volted(tmp_path).findings == ()


def test_a_rail_whose_volts_differ_from_a_domain_it_reaches_is_refused(tmp_path):
    """Rule 2, the contract note's own case: V33 moved to 1.2 V."""
    p = _volted(tmp_path, VOLTED.replace("volts: 3.3,", "volts: 1.2,"))
    assert _messages(p) == [(22, "rails: V33 is 1.2 V and reaches U1 IOVDD 3.3 V, U2 VDD 3.3 V")]


def test_a_rail_joining_domains_of_different_volts_is_refused(tmp_path):
    """Rule 1, with or without the rail's own volts."""
    message = "rails: V33 joins supply domains of different volts: U1 IOVDD 3.3 V, U2 VDD 1.2 V"
    u2 = {"VDD": 1.2, "VSS": 0}
    assert _messages(_volted(tmp_path / "a", u2=u2)) == [(22, message)]
    bare = VOLTED.replace("volts: 3.3, ", "")
    assert _messages(_volted(tmp_path / "b", bare, u2=u2)) == [
        (22, message), (22, "rails: voltage not checked for V33: the rail declares no volts")]


def test_a_ground_rail_reaching_a_domain_above_0_is_refused(tmp_path):
    """Rule 3. A ground domain above 0 is refused with its contract, so the
    rule meets a pad the inventory serves as ground and the contract's
    terminal names as a power pad."""
    path = write_source(tmp_path, VOLTED)
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS, supply=U1_SUPPLY)
    echo = write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, supply={"VDD": 3.3})
    contract = json.loads((echo / "die-contract.json").read_text(encoding="utf-8"))
    for term in contract["terminals"]:
        term.update(role="power", supply_domain="VDD")
    (echo / "die-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    p = plan(read_source(path))
    assert _messages(p) == [(20, "rails: GND is a ground rail and reaches U2 VDD 3.3 V")]


def test_what_is_not_declared_is_a_note_naming_it(tmp_path):
    """Rule 4: a die with a 1.1 contract, or a pad whose terminal names no
    domain, leaves the rail unchecked where it reaches them, never refused."""
    p = _volted(tmp_path / "a", u2=None)
    assert p.refusals == ()
    assert [(n.line, n.message) for n in p.notes] == [
        (20, "rails: voltage not checked for GND: U2 declares no supply_domains"),
        (22, "rails: voltage not checked for V33: U2 declares no supply_domains")]
    path = write_source(tmp_path / "b", VOLTED)
    write_die(tmp_path / "b" / "dies" / "ECHO", "ECHO", ECHO, supply=U2_SUPPLY)
    driver = write_die(tmp_path / "b" / "dies" / "DRIVER", "DRIVER", LS, supply=U1_SUPPLY)
    contract = json.loads((driver / "die-contract.json").read_text(encoding="utf-8"))
    contract["terminals"] = [t for t in contract["terminals"] if t["name"] != "PAD_VSS_1"]
    (driver / "die-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    p = plan(read_source(path))
    assert [(f.line, f.message, f.note) for f in p.findings] == [
        (20, "rails: voltage not checked for GND: U1.PAD_VSS_1 names no supply domain", True)]


def test_a_contract_s_terminals_that_are_not_a_list_name_no_domain(tmp_path):
    """The inventory reads the contract raw; a 1.1 contract with no
    supply_domains checked as it did before them, whatever its terminals."""
    path = write_source(tmp_path)
    contract = json.loads((tmp_path / "dies/ECHO/die-contract.json").read_text(encoding="utf-8"))
    (tmp_path / "dies/ECHO/die-contract.json").write_text(json.dumps({**contract, "terminals": 1}),
                                                          encoding="utf-8")
    assert _read(path).findings == ()


@pytest.mark.parametrize("change, said", [
    ({"version": "1.1"}, "supply_domains is a die contract 1.2 key, and this contract is '1.1'"),
    ({"supply_domains": {"VDD": {"volts": "3.3"}, "VSS": {"volts": 0}}},
     "supply_domains is not {domain: {volts: number}}"),
    ({"supply_domains": {"VDD": {"volts": 3.3}, "AVDD": {"volts": 3.3}}},
     "supply_domains does not match the terminals' domains: no volts for VSS; AVDD named by "
     "no power or ground terminal"),
    ({"supply_domains": {"VDD": {"volts": 3.3}, "VSS": {"volts": 0.5}}},
     "supply domain 'VSS' is a ground domain at 0.5 volts; a ground domain is 0"),
])
def test_a_contract_admission_refuses_for_its_domains_is_refused(tmp_path, change, said):
    path = write_source(tmp_path, VOLTED)
    write_die(tmp_path / "dies" / "DRIVER", "DRIVER", LS, supply=U1_SUPPLY)
    echo = write_die(tmp_path / "dies" / "ECHO", "ECHO", ECHO, supply=U2_SUPPLY)
    contract = json.loads((echo / "die-contract.json").read_text(encoding="utf-8"))
    (echo / "die-contract.json").write_text(json.dumps({**contract, **change}), encoding="utf-8")
    assert _has(plan(read_source(path)), said) == [16]


def _row(instance, pad, port, domain, rail, volts):
    return {"instance": instance, "pad": pad, "port": port, "domain": domain, "rail": rail,
            "volts": volts}


def test_supply_pins_label_each_pin_with_its_rail_and_volts(tmp_path):
    _volted(tmp_path)
    assert supply_pins(tmp_path / "system.chiplet") == [
        _row("U1", "PAD_IOVDD_0", "IOVDD", "IOVDD", "V33", 3.3),
        _row("U1", "PAD_VDD_0", "VDD", "VDD", "V12", 1.2),
        _row("U1", "PAD_VDD_1", "VDD", "VDD", "V12", 1.2),
        _row("U1", "PAD_VSS_0", "VSS", "VSS", "GND", 0),
        _row("U1", "PAD_VSS_1", "VSS", "VSS", "GND", 0),
        _row("U2", "PAD_VDD_0", "VDD", "VDD", "V33", 3.3),
        _row("U2", "PAD_VSS_0", "VSS", "VSS", "GND", 0)]


def test_a_supply_pin_its_die_declares_no_volts_for_is_labelled_none(tmp_path):
    """U2's 1.1 contract names no domain: its power pin is None even though
    V33 states 3.3 V, since that is the pin validate leaves unchecked; a
    ground pin is 0 by its class."""
    _volted(tmp_path, u2=None)
    rows = {(r["instance"], r["pad"]): r for r in supply_pins(tmp_path / "system.chiplet")}
    assert rows["U2", "PAD_VDD_0"] == _row("U2", "PAD_VDD_0", "VDD", None, "V33", None)
    assert rows["U2", "PAD_VSS_0"] == _row("U2", "PAD_VSS_0", "VSS", None, "GND", 0)
    assert rows["U1", "PAD_IOVDD_0"]["volts"] == 3.3


def test_supply_pins_of_a_refused_source_raise(tmp_path):
    _volted(tmp_path, VOLTED.replace("volts: 3.3,", "volts: 1.2,"))
    with pytest.raises(SourceError) as info:
        supply_pins(tmp_path / "system.chiplet")
    assert [f.line for f in info.value.findings] == [22]


# --- elaboration ------------------------------------------------------------------------
def _by_hand(tmp_path):
    """The fixture's system, written with the builder as a spec would."""
    pins = {i: load_pins_json(tmp_path / "dies" / d / "pins.json")
            for i, d in (("U1", "DRIVER"), ("U2", "ECHO"))}
    defs = {n: InterfaceDefinition(n, 1) for n in ("f0", "f1", "f2", "r0", "r1")}

    def port(name, role):
        return Port(f"d2d_{name}", defs[name], role, (f"PAD_d2d_{name}_0",))

    driver = Die("DRIVER", "sg13g2", "DRIVER", Dimensions(1000.0, 1000.0, 750.0), pins["U1"],
                 tuple(port(n, Role.MANAGER) for n in ("f0", "f1", "f2"))
                 + tuple(port(n, Role.SUBORDINATE) for n in ("r0", "r1")))
    echo = Die("ECHO", "sg13g2", "ECHO", Dimensions(1000.0, 1000.0, 750.0), pins["U2"],
               tuple(port(n, Role.SUBORDINATE) for n in ("f0", "f1", "f2"))
               + tuple(port(n, Role.MANAGER) for n in ("r0", "r1")))
    b = SystemBuilder("two_die", Interposer("intm4tm2", "CF_CARRIER",
                                            Dimensions(2000.0, 3200.0, 10.8103),
                                            Position(1000.0, 1600.0)),
                      description="Driver and echo on a carrier")
    b.add_technology(INTM4TM2)
    b.add_technology(SG13G2)
    b.add_method(CUPILLAR_OPT1)
    b.add_die(driver, "U1", Position(1000.0, 2200.0, 54.8103))
    b.add_die(echo, "U2", Position(1000.0, 1000.0, 54.8103), rotation_z=180.0)
    for n in ("f0", "f1", "f2", "r0", "r1"):
        b.connect(("U1", f"d2d_{n}"), ("U2", f"d2d_{n}"), net_prefix=f"d2d_{n}_n")
    b.tie("U1", "PAD_CLK_0", "U1_PAD_CLK_0")
    b.tie("U2", "PAD_STATUS_0", "U2_PAD_STATUS_0")
    b.supply("VDD", [("U1", "PAD_VDD_0"), ("U1", "PAD_VDD_1"), ("U2", "PAD_VDD_0")])
    b.supply("VSS", [("U1", "PAD_VSS_0"), ("U1", "PAD_VSS_1"), ("U2", "PAD_VSS_0")],
             net_class="ground")
    return b.build()


def test_the_source_elaborates_to_the_bytes_the_builder_gives(tmp_path):
    path = write_source(tmp_path)
    system, extras = elaborate_source(path, Resolver())
    assert cfio.dumps(elaborate(system)) == cfio.dumps(elaborate(_by_hand(tmp_path)))
    assert extras.interposer_adapter == "intm4tm2" and extras.io_profile is None
    assert extras.flow == {"checks": {"template": "workshop"}}
    assert extras.dies == (("U1", tmp_path / "dies" / "DRIVER"), ("U2", tmp_path / "dies" / "ECHO"))
    assert checked(extras.notes) == ()


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_key_the_reader_skipped_reaches_the_elaboration_s_notes(tmp_path):
    newer = SOURCE.replace('format_version: "1.1"', 'format_version: "1.6"').replace(
        "{name: VSS, class: ground,", "{name: VSS, class: ground, tolerance: 0.05,")
    path = write_source(tmp_path, newer)
    _, extras = elaborate_source(path, Resolver())
    assert checked(extras.notes) == (f"{path}:21: note: rails: key 'tolerance' is not in "
                                     "format 1.5, which this reader implements; skipped",)


def test_a_build_binding_in_the_flow_block_changes_nothing_the_source_says(tmp_path, monkeypatch):
    """A die built from RTL: its binding rides in the flow block, which packaging-flow
    reads, and the die row still names the earlier build's ready folder. The files it
    names are packaging-flow's to check, so none exists here, and the variable is
    packaging-flow's to expand."""
    monkeypatch.setenv("CHIPLET_FLOW_DIE_PROJECT", "/elsewhere")
    binding = {"run_dir": "${CHIPLET_FLOW_DIE_PROJECT}", "pins_json": "plans/driver.pins.json",
               "padmap_plan": "plans/driver.blackbox.json", "opening_purpose": "aperture"}
    checks = "    checks: {template: workshop}\n"
    assert SOURCE.count(checks) == 1
    text = SOURCE.replace(checks, checks + "    builds:\n      U1: {"
                          + ", ".join(f'{k}: "{v}"' for k, v in binding.items()) + "}\n")
    plain = write_source(tmp_path / "plain")
    built = write_source(tmp_path / "built", text)
    assert check_run(built) == check_run(plain)
    assert checked(check_run(plain)[0]) == () and check_run(plain)[1] == ()
    (system, extras), (plain_system, plain_extras) = (
        elaborate_source(built, Resolver()), elaborate_source(plain, Resolver()))
    assert cfio.dumps(elaborate(system)) == cfio.dumps(elaborate(plain_system))
    assert extras.flow == {**plain_extras.flow, "builds": {"U1": binding}}


def test_the_resolver_gets_the_registry_and_the_pad_row(tmp_path):
    text = SOURCE.replace('  dies:\n', '  registry: registry/ipdk\n  dies:\n', 1).replace(
        '  alone:\n    - U2.STATUS\n    - U1.CLK\n',
        '  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
        '      west: [U1.CLK, VDD]\n      east: [U2.STATUS, VSS]\n'
        '  fill_keepout: {halo_um: {M4: 20}}\n')
    path = write_source(tmp_path, text)
    resolver = Resolver()
    system, extras = elaborate_source(path, resolver)
    assert ("method", "cupillar_opt1", tmp_path / "registry" / "ipdk") in resolver.calls
    pads = [c for c in resolver.calls if c[0] == "io_pads"]
    assert pads == [("io_pads", "intm4tm2_wire_bond_v0", (2000.0, 3200.0),
                     (("west", ("U1_PAD_CLK_0", "VDD")), ("east", ("U2_PAD_STATUS_0", "VSS"))))]
    assert [p.net for p in system.interposer.io_pads] == ["U1_PAD_CLK_0", "VDD", "U2_PAD_STATUS_0",
                                                          "VSS"]
    assert [t.net for t in system.ties] == ["U1_PAD_CLK_0", "U2_PAD_STATUS_0"]
    assert extras.io_profile == "intm4tm2_wire_bond_v0" and extras.fill_keepout == {"M4": 20}


def test_a_refused_source_raises_with_its_lines(tmp_path):
    path = write_source(tmp_path, SOURCE.replace('U1.CLK', 'U1.CLOCK'))
    with pytest.raises(SourceError) as info:
        elaborate_source(path, Resolver())
    assert 24 in [f.line for f in info.value.findings]


def test_a_resolver_error_passes_through(tmp_path):
    class Broken(Resolver):
        def plane(self):
            raise LookupError("no plane here")

    with pytest.raises(LookupError):
        elaborate_source(write_source(tmp_path), Broken())


def test_a_model_refusal_names_the_file(tmp_path):
    class NoProcess(Resolver):
        def method(self, id, registry):
            return type(CUPILLAR_OPT1)(id, 75.0, die_processes=("gf180mcu",)), Decimal("44")

    with pytest.raises(SourceError, match="DieProcessIncompatible"):
        elaborate_source(write_source(tmp_path), NoProcess())


def test_a_carrier_other_than_intm4tm2_is_refused_on_its_line(tmp_path):
    # Until a carrier is read from its technology package, IntM4TM2 is the only one.
    stated = "carrier: {technology: intm4tm2,"
    assert SOURCE.count(stated) == 1
    text = SOURCE.replace(stated, "carrier: {technology: gf180mcu,")
    with pytest.raises(SourceError) as info:
        elaborate_source(write_source(tmp_path, text), Resolver())
    line = text[:text.index("carrier:")].count("\n") + 1
    assert [(f.line, f.code) for f in info.value.findings] == [
        (line, "carrier_unsupported_technology")]
