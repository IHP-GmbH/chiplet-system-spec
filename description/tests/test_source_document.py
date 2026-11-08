# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Tier 0: a source read from the file alone, every rule a finding on its line."""
from __future__ import annotations

import json

import pytest

from chiplet_system_description.model import PortRef
from chiplet_system_description.source import (
    SourceError,
    check_text,
    expand,
    loads_source,
    parse_ref,
)
from chiplet_system_description._cfio import cfio
from chiplet_system_description.source.document import check_relative_path

from tests.conftest import SPEC_ROOT
from tests.source_dies import SOURCE


def _lines(text):
    return [(f.line, f.message) for f in check_text(text)[1]]


def _one(text, needle):
    found = _lines(text)
    assert any(needle in message for _, message in found), found
    return [line for line, message in found if needle in message]


def test_the_fixture_reads_with_no_finding():
    source, findings = check_text(SOURCE)
    assert findings == []
    assert [d.id for d in source.dies] == ["U1", "U2"]
    assert source.dies[1].rotation == 180 and source.dies[0].thickness == 750
    assert source.buses[0].ports == ("d2d_f0", "d2d_f1", "d2d_f2", "d2d_r0", "d2d_r1")
    assert source.buses[0].net_suffix == "_n" and source.buses[0].line == 18
    assert [r.name for r in source.rails] == ["VDD", "VSS"]
    assert [i.written for i in source.alone] == ["U2.STATUS", "U1.CLK"]
    assert source.key_lines["dies"] == 14 and source.key_lines["carrier"] == 13
    assert source.data["flow"]["packaging_flow"]["checks"]["template"] == "workshop"


# --- names ----------------------------------------------------------------
def test_a_range_expands_inclusive_in_the_written_order():
    assert expand("d2d_f[0..2]") == ["d2d_f0", "d2d_f1", "d2d_f2"]
    assert expand("x[2..0]_n") == ["x2_n", "x1_n", "x0_n"]
    assert expand("plain") == ["plain"]
    assert expand("bus[3]") == ["bus[3]"]


@pytest.mark.parametrize("bad", ["a[01..3]", "a[0..x]", "a[0..1]b[0..1]", "a[..3]"])
def test_a_malformed_range_is_refused(bad):
    with pytest.raises(ValueError):
        expand(bad)


def test_a_port_ref_is_instance_dot_port():
    assert parse_ref("U1.d2d.x") == PortRef("U1", "d2d.x")
    for bad in ("U1", "U1.", "1U.x", ".x"):
        with pytest.raises(ValueError):
            parse_ref(bad)


@pytest.mark.parametrize("path,ok", [
    ("dies/a", True), ("a/b/c.json", True), ("/abs/a", False), ("${X}/a", False),
    ("../a", False), ("a/../../b", False), ("a\\b", False), ("C:/a", False),
])
def test_the_path_rule(path, ok):
    assert (check_relative_path(path) is None) is ok


# --- the document level -----------------------------------------------------
def test_an_assembly_is_not_a_source():
    text = 'format_version: "1.0"\nassembly:\n  name: a\n'
    assert _one(text, "not a source")


def test_a_derived_block_is_refused_by_the_vendored_reader():
    assert _one(SOURCE + "netlist:\n  nets: []\n", "netlist")


def test_an_unknown_root_key_is_refused():
    assert _one(SOURCE + "extra: x\n", "'extra' is not one")


def test_load_raises_with_every_finding(tmp_path):
    text = SOURCE.replace('method: cupillar_opt1, thickness: 750}\n  buses',
                          'method: off, thickness: 750}\n  buses').replace('U1.CLK', 'U9.CLK')
    with pytest.raises(SourceError) as info:
        loads_source(text, tmp_path / "s.chiplet")
    assert len(info.value.findings) == 2
    assert info.value.line == 16
    assert str(info.value).startswith(f"{tmp_path / 's.chiplet'}:16: ")


# --- line rules ----------------------------------------------------------------
U2_LINE = "- {id: U2, die: dies/ECHO, at: [1000, 1000], rotation: 180, method: cupillar_opt1, thickness: 750}"


@pytest.mark.parametrize("bare,reads_as", [
    ("no", "reads as a bool"), ("on", "reads as a bool"), ("0755", "reads as a number"),
    ("2026-03-22", "reads as a date"), ("Yes", "reads as a bool"), ("~", "reads as a null"),
    ("y", "is a word YAML 1.1 readers take for a bool or a null"),
    ("nULL", "is a word YAML 1.1 readers take for a bool or a null"),
    ("9V", "is not a bare word ([A-Za-z_][A-Za-z0-9_./-]*)"),
    ("U2+", "is not a bare word ([A-Za-z_][A-Za-z0-9_./-]*)"),
])
def test_a_bare_string_the_rule_does_not_allow_is_refused_with_its_quoted_line(bare, reads_as):
    found = _lines(SOURCE.replace("{id: U2,", f"{{id: {bare},"))
    quoted = U2_LINE.replace("id: U2,", f'id: "{bare}",')
    assert found[0] == (16, f"dies.id: bare {bare!r} {reads_as}"
                            f"{', not a string' if reads_as.startswith('reads') else ''}; "
                            f"write the line as: {quoted}")


#: The spec's cases for the rule, read from the repository this package lives in.
BARE_CASES = json.loads((SPEC_ROOT / "conformance" / "fixtures" / "source_bare_strings_cases.json")
                        .read_text("utf-8"))


@pytest.mark.parametrize("case", BARE_CASES["cases"], ids=lambda c: f"{c['key']}={c['bare']}")
def test_the_conformance_cases_for_bare_strings(case):
    text = SOURCE.replace(U2_LINE, case["line"])
    assert f"    {case['line']}\n" in text
    found = _lines(text)
    if case["verdict"] == "accept":
        assert found == []
        return
    first = [message for line, message in found if line == 16][0]
    assert first.startswith(f"dies.{case['key']}: bare {case['bare']!r} ")
    assert first.endswith(f"; write the line as: {case['write']}")


def test_every_refused_scalar_on_a_line_is_quoted_in_the_line_to_write():
    found = _lines(SOURCE.replace("die: dies/ECHO", "die: off").replace("method: cupillar_opt1, "
                                                                        "thickness: 750}\n  buses",
                                                                        "method: no, thickness: 750}"
                                                                        "\n  buses"))
    want = U2_LINE.replace("die: dies/ECHO", 'die: "off"').replace("cupillar_opt1", '"no"')
    assert [m.split("write the line as: ")[1] for line, m in found if line == 16] == [want, want]


def test_a_bare_value_outside_the_system_block_is_held_to_the_same_rule():
    assert _one(SOURCE.replace('format_version: "1.1"', "format_version: 1.1"),
                "write the line as: format_version: \"1.1\"") == [2]
    assert _one(SOURCE.replace("name: two_die", "name: no"),
                'write the line as: name: "no"') == [7]


def test_a_string_reads_the_same_bare_or_quoted():
    quoted = (SOURCE.replace("id: U2,", 'id: "U2",').replace("class: power", 'class: "power"')
              .replace("ports: [U1.VDD, U2.VDD]", 'ports: ["U1.VDD", "U2.VDD"]')
              .replace("units: um", 'units: "um"').replace("    - U1.CLK", '    - "U1.CLK"'))
    a, b = check_text(SOURCE), check_text(quoted)
    assert a[1] == b[1] == []
    assert a[0].dies == b[0].dies and a[0].alone == b[0].alone
    assert [(r.name, r.net_class, r.ports) for r in a[0].rails] == \
        [(r.name, r.net_class, r.ports) for r in b[0].rails]
    assert a[0].data == b[0].data


def test_the_flow_block_is_opaque_to_the_quoting_rule():
    assert check_text(SOURCE)[1] == []  # `template: workshop` is bare in flow


def test_an_item_spans_one_line():
    text = SOURCE.replace('    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}',
                          '    - {name: VDD, class: power,\n       ports: [U1.VDD, U2.VDD]}')
    assert _one(text, "each item is one line") == [20]
    # Reported once: the rail still resolves, so nothing else names it.
    assert len(_lines(text)) == 1


def test_an_item_is_a_flow_mapping():
    text = SOURCE.replace('    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}',
                          '    - name: VSS\n      class: ground\n      ports: [U1.VSS, U2.VSS]')
    assert _one(text, "flow mapping") == [21]


def test_a_block_is_a_block_list():
    text = SOURCE.replace('  alone:\n    - U2.STATUS\n    - U1.CLK\n',
                          '  alone: [U2.STATUS, U1.CLK]\n')
    assert _one(text, "block list") == [22]


def test_keys_inside_an_item_keep_the_schema_order():
    text = SOURCE.replace('{name: VDD, class: power,', '{class: power, name: VDD,')
    assert _one(text, "keys are written in the order") == [20]


def test_system_keys_keep_their_order():
    text = SOURCE.replace(
        '  rails:\n    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}\n'
        '    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n', "")
    text = text.replace("  alone:\n", '  alone:\n', 1).replace(
        '    - U1.CLK\n',
        '    - U1.CLK\n  rails:\n    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}\n'
        '    - {name: VSS, class: ground, ports: [U1.VSS, U2.VSS]}\n')
    assert _one(text, "system: keys are written in the order")


@pytest.mark.parametrize("old,new,needle", [
    ('rotation: 180', 'rotation: 45', "is not one of 0, 90, 180, 270"),
    ('180, method: cupillar_opt1, thickness: 750}', '180, method: cupillar_opt1, thickness: -1}',
     "thickness is 0 or more"),
    ('at: [1000, 1000]', 'at: [1000]', "two numbers"),
    ('die: dies/ECHO', 'die: "../ECHO"', "'..' segment"),
    ('die: dies/ECHO', 'die: "/abs/ECHO"', "absolute"),
    ('id: U2', 'id: U1', "already used on line 15"),
    ('id: U2', 'id: "2U"', "is not [A-Za-z]"),
    (', method: cupillar_opt1, thickness: 750}\n  buses', ', thickness: 750}\n  buses', "missing required key 'method'"),
    ('rotation: 180,', 'rotation: 180, color: red,', "unknown key 'color'"),
])
def test_die_lines(old, new, needle):
    assert old in SOURCE
    assert _one(SOURCE.replace(old, new), needle) == [16]


@pytest.mark.parametrize("old,new,needle", [
    ('ends: [U1, U2]', 'ends: [U1, U1]', "two different instances"),
    ('ends: [U1, U2]', 'ends: [U1, U3]', "names no instance"),
    ('"d2d_r[0..1]"]', '"U1.d2d_r[0..1]"]', "is a port name, not a ref"),
    ('"d2d_r[0..1]"]', '"d2d_r[0..1]", d2d_r1]', "'d2d_r1' is written twice"),
    ('"d2d_r[0..1]"]', '"d2d_r[00..1]"]', "leading zeros"),
])
def test_bus_lines(old, new, needle):
    assert old in SOURCE
    assert _one(SOURCE.replace(old, new), needle) == [18]


def test_a_port_accounted_twice_is_named_with_both_lines():
    text = SOURCE.replace('    - U1.CLK\n', '    - U1.CLK\n    - U1.d2d_f1\n')
    assert _one(text, "U1.d2d_f1 is already accounted for on line 18") == [25]


def test_rail_lines():
    text = SOURCE.replace('{name: VSS, class: ground', '{name: VDD, class: ground')
    assert _one(text, "already a rail on line 20") == [21]
    text = SOURCE.replace('class: ground', 'class: signal')
    assert _one(text, "is not power or ground") == [21]
    text = SOURCE.replace('{name: VSS', '{name: "VSS[0..1]"')
    assert _one(text, "holds no range") == [21]


V12 = SOURCE.replace('format_version: "1.1"', 'format_version: "1.2"').replace(
    '{name: VDD, class: power,', '{name: VDD, class: power, volts: 1.2,')


def test_a_power_rail_declares_its_volts_at_format_1_2():
    source, findings = check_text(V12)
    assert findings == [] and [(r.name, r.volts) for r in source.rails] == [("VDD", 1.2),
                                                                            ("VSS", None)]
    assert _one(V12.replace('format_version: "1.2"', 'format_version: "1.1"'),
                "may not be written below the minor that adds it") == [0]


@pytest.mark.parametrize("old,new,needle", [
    ('volts: 1.2,', 'volts: 0,', "rails.volts is greater than 0"),
    ('volts: 1.2,', 'volts: "1.2",', "rails.volts must be a number"),
    ('class: power, volts: 1.2,', 'volts: 1.2, class: power,', "keys are written in the order"),
    ('{name: VSS, class: ground,', '{name: VSS, class: ground, volts: 1,', "a ground rail is 0"),
])
def test_volts_lines(old, new, needle):
    assert old in V12
    assert _one(V12.replace(old, new), needle)


V15 = SOURCE.replace('format_version: "1.1"', 'format_version: "1.5"').replace(
    '{name: VDD, class: power,', '{name: VDD, class: power, amps: 1.5, current_density_ma_per_um: 75,'
).replace('{name: VSS, class: ground,', '{name: VSS, class: ground, width_um: 40,')


def test_a_rail_states_its_routing_requirement_at_format_1_5():
    """A power or a ground rail states a width, or a current and its density."""
    source, findings = check_text(V15)
    assert findings == []
    assert [(r.name, r.width_um, r.amps, r.current_density_ma_per_um) for r in source.rails] == [
        ("VDD", None, 1.5, 75), ("VSS", 40, None, None)]
    assert _one(V15.replace('format_version: "1.5"', 'format_version: "1.4"'),
                "may not be written below the minor that adds it") == [0]


# (old, new, tier 0's words, the rail's line, its index, whether the vendored
# reader refuses it too: it does not judge the key order).
REQUIREMENT_LINES = [
    ('width_um: 40,', 'width_um: 0,', "rails.width_um is greater than 0", 21, 1, True),
    ('width_um: 40,', 'width_um: -1,', "rails.width_um is greater than 0", 21, 1, True),
    ('width_um: 40,', 'width_um: .inf,', "rails.width_um must be a number", 21, 1, True),
    ('width_um: 40,', 'width_um: .nan,', "rails.width_um must be a number", 21, 1, True),
    ('width_um: 40,', 'width_um: true,', "rails.width_um must be a number", 21, 1, True),
    ('width_um: 40,', f'width_um: {10 ** 400},', "rails.width_um must be a number", 21, 1, True),
    ('amps: 1.5,', 'amps: "1.5",', "rails.amps must be a number", 20, 0, True),
    ('current_density_ma_per_um: 75,', 'current_density_ma_per_um: 0,',
     "rails.current_density_ma_per_um is greater than 0", 20, 0, True),
    ('amps: 1.5, current_density_ma_per_um: 75,', 'amps: 1.5,',
     "rails: amps without current_density_ma_per_um", 20, 0, True),
    ('amps: 1.5, current_density_ma_per_um: 75,', 'current_density_ma_per_um: 75,',
     "rails: current_density_ma_per_um without amps", 20, 0, True),
    ('width_um: 40,', 'width_um: 40, amps: 1, current_density_ma_per_um: 75,',
     "width_um and a current are both stated", 21, 1, True),
    ('width_um: 40,', 'width_um: 40, current_density_ma_per_um: 75,',
     "width_um and a current are both stated", 21, 1, True),
    ('width_um: 40,', 'width_um: 0, amps: 1,', "width_um and a current are both stated", 21, 1,
     True),
    ('amps: 1.5, current_density_ma_per_um: 75,', 'current_density_ma_per_um: 75, amps: 1.5,',
     "keys are written in the order", 20, 0, False),
]


@pytest.mark.filterwarnings("ignore:format_version")
@pytest.mark.parametrize("version", ["1.4", "1.5", "1.6"])
@pytest.mark.parametrize("old,new,needle,line,index,vendored", REQUIREMENT_LINES)
def test_routing_requirement_lines(old, new, needle, line, index, vendored, version):
    """At every minor tier 0 refuses, on the rail's line, every rail the
    vendored reader refuses, the pairing before the values as it does; it may
    be stricter (the key order), never looser."""
    assert old in V15
    text = V15.replace(old, new).replace('format_version: "1.5"', f'format_version: "{version}"')
    assert line in _one(text, needle)
    with pytest.raises(SourceError):
        loads_source(text)
    if vendored:
        with pytest.raises(cfio.SystemBlockError, match=fr"system\.rails\[{index}\]"):
            cfio.loads(text, allow_source=True)


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_rail_key_from_a_newer_minor_is_skipped_with_a_note():
    """The promise of a minor: a reader skips what it does not know. At or
    below the minor this reader implements, an unknown key is still refused."""
    newer = V12.replace('format_version: "1.2"', 'format_version: "1.6"').replace(
        'volts: 1.2,', 'volts: 1.2, tolerance: 0.05,')
    source, findings = check_text(newer)
    assert [(f.line, f.note) for f in findings] == [(20, True)]
    assert "key 'tolerance' is not in format 1.5" in findings[0].message
    assert loads_source(newer).notes == tuple(findings)
    assert source.rails[0].volts == 1.2
    current = newer.replace('format_version: "1.6"', 'format_version: "1.2"')
    assert _one(current, "unknown key 'tolerance'") == [20]
    with pytest.raises(SourceError):
        loads_source(current)


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_system_key_from_a_newer_minor_is_skipped_with_a_note():
    newer = V12.replace('format_version: "1.2"', 'format_version: "1.6"').replace(
        "  alone:\n", "  probes: {count: 2}\n  alone:\n")
    source, findings = check_text(newer)
    assert [(f.note, f.message) for f in findings] == [
        (True, "system: key 'probes' is not in format 1.5, which this reader implements; "
               "skipped")]
    assert len(source.alone) == 2
    current = newer.replace('format_version: "1.6"', 'format_version: "1.2"')
    assert len(_one(current, "system: unknown key 'probes'")) == 1


def test_at_the_minor_this_reader_implements_an_unknown_key_is_refused():
    """1.5 is now this reader's own minor, so its unknown keys are defects, not notes."""
    at_15 = V12.replace('format_version: "1.2"', 'format_version: "1.5"').replace(
        'volts: 1.2,', 'volts: 1.2, tolerance: 0.05,')
    assert _one(at_15, "unknown key 'tolerance'") == [20]
    with pytest.raises(SourceError):
        loads_source(at_15)


def test_link_lines():
    base = SOURCE.replace("  rails:\n", '  links:\n    - {a: U1.CLK, b: U2.STATUS}\n  rails:\n', 1)
    base = base.replace('    - U2.STATUS\n    - U1.CLK\n', '    - U1.VDD\n') \
        .replace('ports: [U1.VDD, U2.VDD]', 'ports: [U2.VDD]')
    source, findings = check_text(base)
    assert findings == [] and source.links[0].net is None
    ranged = base.replace('{a: U1.CLK, b: U2.STATUS}', '{a: "U1.x[0..1]", b: "U2.y[0..2]"}')
    assert _one(ranged, "a range pairs one to one") == [20]
    same = base.replace('b: U2.STATUS', 'b: U1.STATUS')
    assert _one(same, "are on one die") == [20]


EXTERNAL = SOURCE.replace(
    '  alone:\n    - U2.STATUS\n    - U1.CLK\n',
    '  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
    '      north: [U1.CLK]\n      east: [VDD, U2.STATUS, VSS]\n'
    '  fill_keepout: {halo_um: {M4: 20, TM1: 60}}\n')


def test_the_external_block():
    source, findings = check_text(EXTERNAL)
    assert findings == []
    assert [e.edge for e in source.external.edges] == ["north", "east"]
    assert source.external.edges[1].entries == ("VDD", PortRef("U2", "STATUS"), "VSS")
    assert source.fill_keepout.halo_um == (("M4", 20), ("TM1", 60))


@pytest.mark.parametrize("old,new,needle,line", [
    ('east: [VDD', 'east: [VDDX', "nor a rail in rails", 26),
    ('east: [VDD', 'top: [VDD', "is not north, south, west or east", 26),
    ('north: [U1.CLK]', 'north: [U1.CLK, VSS]', "VSS is already on an edge", 26),
    ('profile: intm4tm2_wire_bond_v0', 'profile: "../p.json"', "'..' segment", 23),
    ('east: [VDD, U2.STATUS, VSS]', 'east:\n        - VDD', "one line holding a flow list", 27),
])
def test_external_lines(old, new, needle, line):
    assert old in EXTERNAL
    assert _one(EXTERNAL.replace(old, new), needle) == [line]


def test_unreadable_yaml_is_one_finding_with_its_line():
    text = SOURCE.replace('  dies:\n', '  dies:\n    - {id: U0\n')
    found = _lines(text)
    assert len(found) == 1 and "not readable YAML" in found[0][1]
