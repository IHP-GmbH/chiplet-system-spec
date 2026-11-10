# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Format 1.3, the unplaced source: read at tier 0, a note per unplaced thing at
tier 1 when the reader opts in, a refusal everywhere else, and the writer's stamp."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chiplet_system_description.model import PortRef
from chiplet_system_description.source import (Carrier, DieItem, External, SourceError,
                                               check_text, loads_source, read_source)
from chiplet_system_description.source import document, unplaced
from chiplet_system_description.source.check import main
from chiplet_system_description.source.elaborate import elaborate_source
from chiplet_system_description.source.plan import plan
from chiplet_system_description.source.unplaced import content_minor
from chiplet_system_description.source.writer import dumps_source

from tests.source_dies import SOURCE, checked, write_source

#: :data:`SOURCE` as a sheet import leaves it: U1 not placed, no carrier size,
#: and the external entries on no edge yet.
UNPLACED = (SOURCE.replace('format_version: "1.1"', 'format_version: "1.3"')
            .replace(', size: [2000, 3200]', '')
            .replace('at: [1000, 2200], rotation: 0, ', '')
            .replace('  alone:\n    - U2.STATUS\n    - U1.CLK\n',
                     '  external:\n    profile: intm4tm2_wire_bond_v0\n')
            .replace('flow:\n  packaging',
                     'unplaced:\n  pins: [U2.STATUS, U1.CLK, VDD, VSS]\nflow:\n  packaging'))
#: :data:`SOURCE` placed, with its two signal ports external and one still on
#: no edge: the only 1.3 content is the root key.
PINS_ONLY = (SOURCE.replace('format_version: "1.1"', 'format_version: "1.3"')
             .replace('  alone:\n    - U2.STATUS\n    - U1.CLK\n',
                      '  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
                      '      west: [VDD, VSS, U1.CLK]\n')
             .replace('flow:\n  packaging', 'unplaced:\n  pins: [U2.STATUS]\nflow:\n  packaging'))
#: A project file before its first sheet import: settings and no dies.
PROJECT = '''\
format_version: "1.3"
_metadata:
  finalize_required: true
  finalizer: "packaging-flow run"
assembly:
  name: classic
system:
  carrier: {technology: intm4tm2, top_cell: CF_CARRIER}
  external:
    profile: intm4tm2_wire_bond_v0
'''


def _codes(text):
    return [(f.line, f.code) for f in check_text(text)[1]]


def _read(root, text):
    """``text`` read from beside the fixture's die folders."""
    return read_source(write_source(root, text))


def _stamped(text, version):
    return text.replace('format_version: "1.3"', f'format_version: "{version}"')


# --- tier 0 -----------------------------------------------------------------
def test_an_unplaced_source_reads_at_tier_0():
    source, findings = check_text(UNPLACED)
    assert findings == []
    assert source.dies[0].at is None and source.dies[0].rotation == 0
    assert source.dies[1].at == (1000, 1000) and source.carrier.size is None
    assert source.external.edges == ()
    assert source.unplaced.pins == (PortRef("U2", "STATUS"), PortRef("U1", "CLK"), "VDD", "VSS")
    assert (source.unplaced.line, source.unplaced.pins_line) == (24, 25)
    assert check_text(PINS_ONLY)[1] == [] and check_text(PROJECT)[1] == []
    assert check_text(PROJECT.replace("  external", "  dies: []\n  external"))[1] == []


@pytest.mark.parametrize("version", ["1.1", "1.2"])
def test_every_unplaced_thing_below_1_3_is_refused_naming_1_3(version):
    assert _codes(_stamped(UNPLACED, version)) == [
        (13, "needs_minor"), (15, "needs_minor"), (22, "needs_minor"), (24, "needs_minor")]
    assert all('"1.3"' in f.message for f in check_text(_stamped(UNPLACED, version))[1])


def test_the_unplaced_key_alone_below_1_3_is_refused():
    assert _codes(_stamped(PINS_ONLY, "1.2")) == [(26, "needs_minor")]


@pytest.mark.parametrize("text", [PROJECT, PROJECT.replace("  external", "  dies: []\n  external")])
def test_a_source_with_no_dies_below_1_3_is_refused(text):
    assert ("needs_minor" in [code for _, code in _codes(_stamped(text, "1.2"))])


def test_a_rotation_without_at_is_refused_at_any_minor():
    text = UNPLACED.replace("die: dies/DRIVER,", "die: dies/DRIVER, rotation: 90,")
    assert _codes(text) == [(15, "rotation_without_at")]


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3"])
def test_a_rotation_without_at_is_refused_for_that_in_both_readers(version):
    """Not with "stamp it 1.3", which would not fix it, at any stamp."""
    text = _stamped(PINS_ONLY.replace("unplaced:\n  pins: [U2.STATUS]\n", "")
                    .replace("at: [1000, 2200], rotation: 0,", "rotation: 90,"), version)
    with pytest.raises(unplaced.cfio.SystemBlockError, match="rotation and no at"):
        unplaced.cfio.loads(text, allow_source=True)
    assert [code for _, code in _codes(text)] == ["rotation_without_at"]


def test_unplaced_pins_need_an_external_block():
    text = PINS_ONLY.replace('  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
                             '      west: [VDD, VSS, U1.CLK]\n', '  alone:\n    - U1.CLK\n')
    assert _codes(text) == [(24, "unplaced_without_external")]


def test_an_unplaced_entry_is_written_once_across_the_edges_and_the_ports():
    twice = PINS_ONLY.replace("pins: [U2.STATUS]", "pins: [U2.STATUS, VSS]")
    assert [line for line, _ in _codes(twice)] == [27]
    claimed = PINS_ONLY.replace("pins: [U2.STATUS]", "pins: [U2.STATUS, U2.d2d_f0]")
    assert [line for line, _ in _codes(claimed)] == [27]
    assert [line for line, _ in _codes(PINS_ONLY.replace("[U2.STATUS]", "[GND]"))] == [27]


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_newer_minor_s_key_inside_unplaced_is_a_note_and_a_root_key_is_refused():
    inside = PINS_ONLY.replace("unplaced:\n", "unplaced:\n  zone: north\n")
    assert [(f.note, f.code) for f in check_text(inside)[1]] == [(False, None)]
    newer = _stamped(inside, "1.6")
    assert [f.note for f in check_text(newer)[1]] == [True]
    assert loads_source(newer).unplaced.pins == (PortRef("U2", "STATUS"),)
    root = _stamped(PINS_ONLY, "1.6").replace("flow:\n  packaging", "probes: 2\nflow:\n  packaging")
    assert [f.note for f in check_text(root)[1]] == [False]


@pytest.mark.filterwarnings("ignore:format_version")
def test_a_walk_without_the_unplaced_key_refuses_it_in_a_higher_minor(monkeypatch):
    """The walk's half of a 1.2 reader: told it implements 1.2 and knows no
    ``unplaced`` root key, it refuses the key in a file of a higher minor
    rather than skip it, though the vendored reader here still holds 1.3 and
    accepts the file. The whole 1.2 reader is the next test's."""
    monkeypatch.setattr(unplaced.cfio, "ACCEPTED_FORMAT_VERSIONS", ("1.2",))
    monkeypatch.setattr(document, "ROOT_KEYS", document.ROOT_KEYS[:-1])
    source, findings = check_text(PINS_ONLY)
    assert [(f.line, f.note) for f in findings] == [(26, False)]
    with pytest.raises(SourceError):
        loads_source(PINS_ONLY)


#: The format 1.2 reader, kept as files (see its PROVENANCE.md).
READER_1_2 = Path(__file__).resolve().parent / "fixtures" / "reader_1_2"


def _reader_1_2_files():
    """The copy's files, checked against its SHA256SUMS: none differs, none is
    added, none is missing."""
    listed = {}
    for line in (READER_1_2 / "SHA256SUMS").read_text("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    present = {p.relative_to(READER_1_2).as_posix()
               for p in (READER_1_2 / "src").rglob("*") if p.is_file()
               and "__pycache__" not in p.parts}
    assert present == set(listed), "the 1.2 reader copy gained or lost a file"
    for name, digest in listed.items():
        assert hashlib.sha256((READER_1_2 / name).read_bytes()).hexdigest() == digest, name
    return READER_1_2 / "src"


def test_the_1_2_reader_as_released_refuses_a_file_with_unplaced(tmp_path):
    """The real one: the 1.2 reader, run as its own package."""
    src = _reader_1_2_files()
    path = tmp_path / "pins_only.chiplet"
    path.write_text(PINS_ONLY, encoding="utf-8")
    code = ("import json, sys, warnings\nwarnings.simplefilter('ignore')\n"
            "from chiplet_system_description.source import check_text\n"
            "_, found = check_text(open(sys.argv[1]).read())\n"
            "print(json.dumps([[f.line, f.note] for f in found]))\n")
    env = dict(os.environ, PYTHONPATH=str(src), PYTHONDONTWRITEBYTECODE="1")
    run = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True,
                         text=True, env=env, cwd=tmp_path, timeout=60, check=True)
    assert json.loads(run.stdout) == [[26, False]]


#: Sources at the edge of what 1.3 allows, each a file both readers judge.
ROTATION_ONLY = UNPLACED.replace("die: dies/DRIVER,", "die: dies/DRIVER, rotation: 90,")
NO_EXTERNAL = PINS_ONLY.replace('  external:\n    profile: intm4tm2_wire_bond_v0\n    edges:\n'
                                '      west: [VDD, VSS, U1.CLK]\n', '  alone:\n    - U1.CLK\n')


@pytest.mark.filterwarnings("ignore:format_version")
@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"])
@pytest.mark.parametrize("text", [UNPLACED, PINS_ONLY, PROJECT, ROTATION_ONLY, NO_EXTERNAL],
                         ids=["unplaced", "pins_only", "project", "rotation_only", "no_external"])
def test_tier_0_and_the_vendored_reader_agree_at_every_stamp(text, version):
    """What 1.3 lets a source leave open is one rule in two readers: tier 0
    refuses a file exactly when the vendored reader does."""
    text = _stamped(text, version)
    try:
        unplaced.cfio.loads(text, allow_source=True)
        vendored = True
    except unplaced.cfio.ChipletFormatError:
        vendored = False
    source, findings = check_text(text)
    assert vendored == (source is not None and all(f.note for f in findings))


def test_a_refusal_the_walk_does_not_state_is_the_vendored_reader_s():
    text = PINS_ONLY.replace("finalize_required: true", "finalize_required: false")
    assert [(f.line, f.code) for f in check_text(text)[1]] == [(0, None)]
    assert "finalize_required" in check_text(text)[1][0].message


@pytest.mark.parametrize("version", ["1.1", "1.2"])
@pytest.mark.parametrize("edit, says", [
    (lambda t: t.replace("finalize_required: true", "finalize_required: false"),
     "finalize_required: true"),
    (lambda t: t.replace("assembly:\n  name: classic\n", ""), "'assembly' section"),
    (lambda t: t.split("system:")[0] + "system: 5\n", "'system' must be a mapping"),
], ids=["finalized", "no_assembly", "system_scalar"])
def test_a_document_level_refusal_below_1_3_is_not_hidden_by_the_walk(version, edit, says):
    """A project file with no dies below 1.3 and a document-level defect: the
    walk does not run, so the refusal is the defect's, not "no dies"."""
    findings = check_text(edit(_stamped(PROJECT, version)))[1]
    assert [(f.line, f.code) for f in findings] == [(0, None)]
    assert says in findings[0].message


#: Hand edits that break the shape of what 1.3 adds: the vendored reader refuses
#: each file as a whole, and tier 0 still names the line.
_PINS = "unplaced:\n  pins: [U2.STATUS]\n"
MISSHAPEN = {
    "unplaced_scalar": (PINS_ONLY.replace(_PINS, "unplaced: 5\n"), [26]),
    "unplaced_list": (PINS_ONLY.replace(_PINS, "unplaced:\n  - U2.STATUS\n"), [26]),
    "unplaced_null": (PINS_ONLY.replace(_PINS, "unplaced:\n"), [26]),
    "pins_empty": (PINS_ONLY.replace("pins: [U2.STATUS]", "pins: []"), [27]),
    "pins_scalar": (PINS_ONLY.replace("pins: [U2.STATUS]", "pins: U2.STATUS"), [27]),
    "pins_empty_entry": (PINS_ONLY.replace("pins: [U2.STATUS]", 'pins: [U2.STATUS, ""]'), [27]),
    "no_pins": (PINS_ONLY.replace("pins: [U2.STATUS]", "groups: [a]"), [26, 27]),
    "no_pins_newer": (_stamped(PINS_ONLY.replace("pins: [U2.STATUS]", "groups: [a]"), "1.6"),
                      [26, 27]),
    "dies_null": (PROJECT.replace("  external:", "  dies:\n  external:"), [9]),
    "dies_scalar": (PROJECT.replace("  external:", "  dies: 3\n  external:"), [9]),
}


@pytest.mark.filterwarnings("ignore:format_version")
@pytest.mark.parametrize("name", MISSHAPEN)
def test_a_misshapen_1_3_block_is_refused_on_its_line(name):
    text, lines = MISSHAPEN[name]
    with pytest.raises(unplaced.cfio.ChipletFormatError):
        unplaced.cfio.loads(text, allow_source=True)
    source, findings = check_text(text)
    assert source is None and [f.line for f in findings] == lines
    assert not findings[0].note


# --- tier 1 -----------------------------------------------------------------
def test_tier_1_refuses_each_unplaced_thing_and_names_the_fix(tmp_path):
    source = _read(tmp_path, UNPLACED)
    refused = [(f.line, f.code) for f in plan(source).refusals]
    assert refused == [(13, "carrier_unsized"), (15, "die_unplaced"), (25, "pins_unplaced")]
    assert all("packaging-flow place" in f.message for f in plan(source).refusals)
    assert [(f.line, f.code) for f in plan(source).sheet_base_refusals] == refused


def test_an_opted_in_reader_gets_a_note_per_unplaced_thing(tmp_path):
    source = _read(tmp_path, UNPLACED)
    topology = plan(source, unplaced=True)
    assert topology.refusals == ()
    assert [(f.line, f.code) for f in checked(topology.notes)] == [
        (13, "carrier_unsized"), (15, "die_unplaced"), (25, "pins_unplaced")]
    # The unplaced pins are accounted as edge entries are: ties, and no edge.
    assert [t.net for t in topology.ties][:2] == ["U1_PAD_CLK_0", "U2_PAD_STATUS_0"]
    assert topology.edges == ()


def test_a_project_file_with_no_dies_is_a_note_naming_the_sheet_import(tmp_path):
    source = loads_source(PROJECT)
    assert [(f.line, f.code, f.note) for f in plan(source).findings] == [
        (8, "no_dies", False), (8, "carrier_unsized", False)]
    assert [(f.code, f.note) for f in plan(source, unplaced=True).findings] == [
        ("no_dies", True), ("carrier_unsized", True)]


def test_placement_checks_the_placed_dies_and_no_carrier_without_a_size(tmp_path):
    text = UNPLACED.replace("at: [1000, 1000]", "at: [9000, 9000]")
    assert plan(_read(tmp_path, text), unplaced=True).refusals == ()
    sized = text.replace("top_cell: CF_CARRIER}", "top_cell: CF_CARRIER, size: [2000, 3200]}")
    assert [f.line for f in plan(_read(tmp_path, sized), unplaced=True).refusals] == [16]


def test_a_rail_in_unplaced_pins_escapes_and_a_new_one_goes_there(tmp_path):
    assert plan(_read(tmp_path, UNPLACED), unplaced=True).refusals == ()
    gone = UNPLACED.replace(", VDD, VSS]", ", VSS]").replace(
        "    - {name: VDD, class: power, ports: [U1.VDD, U2.VDD]}\n", "")
    topology = plan(_read(tmp_path, gone), unplaced=True)
    lines = [(s.block, s.line, s.replace_line) for s in topology.suggestions]
    assert ("unplaced", "  pins: [U2.STATUS, U1.CLK, VSS, VDD]", 24) in lines
    missing = UNPLACED.replace(", VDD, VSS]", ", VSS]")
    assert [f.line for f in plan(_read(tmp_path, missing), unplaced=True).refusals] == [20]


def test_an_unplaced_pin_is_one_signal_pad(tmp_path):
    text = PINS_ONLY.replace("pins: [U2.STATUS]", "pins: [U2.STATUS, U1.VDD]").replace(
        "ports: [U1.VDD, U2.VDD]", "ports: [U2.VDD]")
    assert [f.line for f in plan(_read(tmp_path, text), unplaced=True).refusals] == [27]


def test_check_validates_an_unplaced_source_before_it_is_placed(tmp_path):
    path = write_source(tmp_path, UNPLACED)
    out = io.StringIO()
    assert main(["check", str(path)], out) == 0
    assert [line.split(": note: ")[1].split(";")[0] for line in out.getvalue().splitlines()
            if "voltage not checked" not in line] == [
        "carrier: no size yet", "dies: U1 is not placed",
        "unplaced: 4 external pin(s) are on no edge yet: U2.STATUS, U1.CLK, VDD, VSS"]


def test_elaboration_refuses_an_unplaced_source(tmp_path):
    path = write_source(tmp_path, UNPLACED)
    for sheet_base in (False, True):
        with pytest.raises(SourceError) as refused:
            elaborate_source(path, resolver=None, sheet_base=sheet_base)
        assert [f.code for f in refused.value.findings] == [
            "carrier_unsized", "die_unplaced", "pins_unplaced"]


# --- the writer ---------------------------------------------------------------
def _write(**changes):
    items = dict(
        name="two_die",
        carrier=Carrier("intm4tm2", "CF_CARRIER", (2000, 3200), 0),
        dies=[DieItem("U1", "dies/DRIVER", (1000, 2200), "cupillar_opt1", 0, 750, 0)],
        rails=[("VDD", "power", [PortRef("U1", "VDD")])],
        external=External("intm4tm2_wire_bond_v0", (), 0))
    items.update(changes)
    return dumps_source(**items)


def test_the_writer_stamps_the_lowest_minor_the_content_needs():
    unplaced_die = DieItem("U1", "dies/DRIVER", None, "cupillar_opt1", 0, 750, 0)
    placed = _write(external=None)
    assert placed.splitlines()[0] == 'format_version: "1.1"'
    for text in (_write(), _write(dies=[unplaced_die], external=None),
                 _write(carrier=Carrier("intm4tm2", "CF_CARRIER", None, 0), external=None),
                 _write(dies=[], rails=[], external=None),
                 _write(unplaced=["VDD", PortRef("U1", "CLK")])):
        assert text.splitlines()[0] == 'format_version: "1.3"', text
        assert content_minor(loads_source(text)) == "1.3"
    assert "    - {id: U1, die: dies/DRIVER, method: cupillar_opt1, thickness: 750}\n" in \
        _write(dies=[unplaced_die], external=None)
    assert _write(unplaced=["VDD", PortRef("U1", "CLK")]).endswith(
        "  external:\n    profile: intm4tm2_wire_bond_v0\nunplaced:\n  pins: [VDD, U1.CLK]\n")


def test_a_comment_naming_the_stamp_is_not_restamped():
    text = _write(external=None, comment='format_version: "1.3"')
    assert text.splitlines()[:2] == ['# format_version: "1.3"', 'format_version: "1.1"']
