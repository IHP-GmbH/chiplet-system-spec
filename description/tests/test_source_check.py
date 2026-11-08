# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""``chiplet-system-description check``: what a person reads, and the JSON a tool reads."""
from __future__ import annotations

import io
import json

import pytest

from chiplet_system_description.source.check import main

from tests.source_dies import DECLARED, SOURCE, SUPPLY, write_source


def _run(argv):
    out = io.StringIO()
    code = main(argv, out=out)
    return code, out.getvalue()


def test_a_clean_source_exits_0_and_prints_nothing(tmp_path):
    code, text = _run(["check", str(write_source(tmp_path, DECLARED, supply=SUPPLY))])
    assert (code, text) == (0, "")


def test_a_source_with_no_volts_passes_with_one_note_per_rail(tmp_path):
    """Rule 4: a source or a die written before V5 validates as it did, and
    each rail says why its voltage went unchecked."""
    path = write_source(tmp_path)
    assert _run(["check", str(path)]) == (0, (
        f"{path}:20: note: rails: voltage not checked for VDD: the rail declares no volts; "
        "U1, U2 declare no supply_domains\n"
        f"{path}:21: note: rails: voltage not checked for VSS: U1, U2 declare no supply_domains\n"))


def test_a_note_does_not_refuse(tmp_path):
    path = write_source(tmp_path, DECLARED.replace(', thickness: 750}\n  buses', '}\n  buses'),
                        supply=SUPPLY)
    code, text = _run(["check", str(path)])
    assert code == 0
    assert text == f"{path}:16: note: dies: U2: thickness not given; 0.0 used\n"


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_key_from_a_newer_minor_is_a_note_and_tier_1_still_runs(tmp_path):
    newer = DECLARED.replace('format_version: "1.2"', 'format_version: "1.6"').replace(
        'volts: 1.2,', 'volts: 1.2, tolerance: 0.05,')
    path = write_source(tmp_path, newer.replace(', thickness: 750}\n  buses', '}\n  buses'),
                        supply=SUPPLY)
    code, text = _run(["check", str(path)])
    assert code == 0
    assert text.splitlines() == [
        f"{path}:20: note: rails: key 'tolerance' is not in format 1.5, which this reader "
        "implements; skipped",
        f"{path}:16: note: dies: U2: thickness not given; 0.0 used"]


def test_tier_1_waits_for_tier_0(tmp_path):
    path = write_source(tmp_path, SOURCE.replace("method: cupillar_opt1, thickness: 750}\n  buses",
                                                 "method: off, thickness: 750}\n  buses")
                        .replace('U1.CLK', 'U1.CLOCK'))
    code, text = _run(["check", str(path)])
    assert code == 1
    assert text.splitlines() == [
        f"{path}:16: dies.method: bare 'off' reads as a bool, not a string; write the line as: "
        '- {id: U2, die: dies/ECHO, at: [1000, 1000], rotation: 180, method: "off", thickness: 750}']


def test_tier_0_alone_does_not_read_the_dies(tmp_path):
    path = write_source(tmp_path, SOURCE.replace('die: dies/ECHO', 'die: dies/NOPE'))
    assert _run(["check", "--tier", "0", str(path)]) == (0, "")
    assert _run(["check", str(path)])[0] == 1


def test_unaccounted_ports_print_a_paste_block(tmp_path):
    text = (DECLARED.replace('"d2d_f[0..2]", "d2d_r[0..1]"', 'd2d_f0')
            .replace('ports: [U1.VDD, U2.VDD]', 'ports: [U1.VDD]')
            .replace('    - U1.CLK\n', ''))
    path = write_source(tmp_path, text, supply=SUPPLY)
    code, out = _run(["check", str(path)])
    assert code == 1
    lines = out.splitlines()
    # One finding per line to paste, on the line it replaces or its block's key.
    assert lines[:3] == [
        f"{path}:17: 8 die port(s) are not accounted for: U1.d2d_f1, U2.d2d_f1, "
        "U1.d2d_f2, U2.d2d_f2, U1.d2d_r0, U2.d2d_r0, U1.d2d_r1, U2.d2d_r1",
        f"{path}:20: 1 die port(s) are not accounted for: U2.VDD",
        f"{path}:22: 1 die port(s) are not accounted for: U1.CLK",
    ]
    assert lines[3:] == [
        f"{path}: 10 die port(s) are not accounted for (tier 1). Paste each line into its block:",
        "  buses:",
        '    - {ends: [U1, U2], ports: ["d2d_f[1..2]", "d2d_r[0..1]"]}',
        "  rails, replace line 20:",
        '    - {name: VDD, class: power, volts: 1.2, ports: [U1.VDD, U2.VDD]}',
        "  alone:",
        '    - U1.CLK',
    ]


def test_json_carries_findings_and_one_record_per_port(tmp_path):
    path = write_source(tmp_path, DECLARED.replace('    - U1.CLK\n', ''), supply=SUPPLY)
    code, out = _run(["check", "--json", str(path)])
    assert code == 1
    doc = json.loads(out)
    assert doc["format"] == "chiplet_system.check" and doc["version"] == 1
    assert doc["findings"][0]["tier"] == 1 and doc["findings"][0]["note"] is False
    assert doc["suggestions"] == [{"port": "U1.CLK", "block": "alone",
                                   "line": '    - U1.CLK', "replace_line": None,
                                   "choice": None}]


def test_an_unreadable_file_is_a_finding(tmp_path):
    code, out = _run(["check", str(tmp_path / "missing.chiplet")])
    assert code == 1 and ":0: cannot be read" in out


def test_a_bad_command_line_exits_2():
    with pytest.raises(SystemExit) as info:
        main(["check"])
    assert info.value.code == 2
