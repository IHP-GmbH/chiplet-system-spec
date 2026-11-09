# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The EDA views reader over a record written by hand: what it answers, and
what it refuses rather than answer twice or from outside the record."""
import hashlib
import json

import pytest

from chiplet_format_io import ChipletFormatError
from chiplet_format_io.eda_views import (KINDS, READ_KINDS, SCHEMA, EdaViewsRefused, check_bound, gate_view,
                                         read)

CORNERS = ["nom_fast", "nom_slow"]
PINS = b'{"version": "1.0", "pins": []}\n'
LAYOUT = "ab" * 32


def sha(data):
    return hashlib.sha256(data).hexdigest()


def row(kind, file, data, corners=None):
    found = {"kind": kind, "file": file, "sha256": sha(data), "status": "recorded"}
    if corners is not None:
        found["corners"] = corners
    return found


class Die:
    """A record directory and the die contract it was written for."""

    def __init__(self, tmp_path):
        self.out, self.contract_dir = tmp_path / "views", tmp_path / "die"
        self.record = self.out / "eda-views.json"
        files = {
            "def/top.def": (b"DESIGN top ;\n", None),
            "tech_lef/tech.lef": (b"LAYER Metal1\n", None),
            "lef/cells.lef": (b"MACRO cell\n", None),
            "liberty/fast.lib": (b"library (fast) {}\n", ["nom_fast"]),
            "liberty/slow.lib": (b"library (slow) {}\n", ["nom_slow"]),
            "sdc/top.sdc": (b"create_clock -period 10\n", None),
            "pins/pins.json": (PINS, None),
            "sim_model/cells.v": (b"module cell (A);\n input A;\nendmodule\n", None),
            "netlist/top.nl.v": (b"module top (a);\n input a;\n cell u0 (.A(a));\nendmodule\n", None),
        }
        rows = []
        for name, (data, corners) in files.items():
            (self.out / name).parent.mkdir(parents=True, exist_ok=True)
            (self.out / name).write_bytes(data)
            rows.append(row(name.split("/")[0], name, data, corners))
        build_record = b'{"status": "built-not-qualified"}\n'
        self.contract_dir.mkdir()
        (self.contract_dir / "build-record.json").write_bytes(build_record)
        (self.contract_dir / "pins.json").write_bytes(PINS)
        self.contract = {
            "schema": "die-output-contract", "version": "1.1",
            "die": {"layout_sha256": LAYOUT, "top_cell": "top"},
            "build_record": {"path": "build-record.json", "sha256": sha(build_record)},
            "inventory": {"kind": "pins_json", "path": "pins.json", "sha256": sha(PINS)},
        }
        self.write_contract()
        self.document = {
            "schema": SCHEMA, "design": "top", "corners": CORNERS, "default_corner": CORNERS[0],
            "bound_to": {"layout_sha256": LAYOUT, "build_record_sha256": sha(build_record)},
            "views": rows, "not_recorded": [],
        }
        self.write()

    def write(self):
        self.record.write_text(json.dumps(self.document))

    def write_contract(self):
        (self.contract_dir / "die-contract.json").write_text(json.dumps(self.contract))


def test_read_answers_a_corner_and_refuses_a_tampered_copy(tmp_path):
    die = Die(tmp_path)
    found = read(die.record, "nom_slow")
    assert [p.name for p in found["liberty"]] == ["slow.lib"]
    assert [p.name for p in found["tech_lef"]] == ["tech.lef"] and found["spef"] == []
    assert all(p.is_absolute() and p.is_file() for paths in found.values() for p in paths)
    (die.out / "lef" / "cells.lef").write_text("MACRO moved\n")
    with pytest.raises(EdaViewsRefused, match="not the bytes its row binds"):
        read(die.record, "nom_slow")


def test_read_skips_the_transistor_level_kinds_and_keeps_its_keys(tmp_path):
    die = Die(tmp_path)
    before = read(die.record, "nom_slow")
    assert list(before) == list(READ_KINDS)
    # Rows of the new kinds whose files are absent or not their bytes: a reader
    # that re-hashed them would refuse, one that returned them would grow keys.
    (die.out / "spice").mkdir()
    (die.out / "spice" / "top.spice").write_bytes(b".subckt top a\n.ends\n")
    die.document["views"] += [
        {"kind": "spice", "file": "spice/top.spice", "sha256": "0" * 64, "status": "recorded"},
        {"kind": "cdl", "file": "cdl/absent.cdl", "sha256": "0" * 64, "status": "recorded"},
        {"kind": "transistor_view", "file": "../outside.json", "sha256": "0" * 64, "status": "recorded"},
    ]
    die.write()
    assert read(die.record, "nom_slow") == before
    assert set(KINDS) - set(READ_KINDS) == {"cdl", "spice", "transistor_view"}


def _another_version(die):
    die.document["schema"] = "chiplet-flow-eda-views/v2"


def _a_copy_linked_outside(die):
    copy = die.out / "lef" / "cells.lef"
    outside = die.out.parent / "cells.lef"
    outside.write_bytes(copy.read_bytes())
    copy.unlink()
    copy.symlink_to(outside)


def _a_path_that_climbs(die):
    die.document["views"][2]["file"] = "lef/../lef/cells.lef"


def _a_second_tech_lef_row(die):
    cells = next(r for r in die.document["views"] if r["file"] == "lef/cells.lef")
    die.document["views"].append(dict(cells, kind="tech_lef"))


def _no_sdc_row(die):
    die.document["views"] = [r for r in die.document["views"] if r["kind"] != "sdc"]


def _corners_not_names(die):
    die.document["views"][3]["corners"] = "nom_fast"


@pytest.mark.parametrize("edit, corner, reason", [
    (_another_version, CORNERS[0], "is not a chiplet-flow-eda-views/v1 record"),
    (_a_copy_linked_outside, CORNERS[0], "is not a file inside"),
    (_a_path_that_climbs, CORNERS[0], "is not a relative path inside"),
    (_a_second_tech_lef_row, CORNERS[0], "2 tech_lef rows"),
    (_no_sdc_row, CORNERS[0], "no sdc row"),
    (_corners_not_names, CORNERS[0], "states corners that are not a list of names"),
    (None, "nom_unknown", "has no corner"),
])
def test_read_refuses_what_it_cannot_answer_once(tmp_path, edit, corner, reason):
    die = Die(tmp_path)
    if edit:
        edit(die)
        die.write()
    with pytest.raises(EdaViewsRefused, match=reason):
        read(die.record, corner)


def test_read_ignores_a_kind_it_does_not_know(tmp_path):
    die = Die(tmp_path)
    die.document["views"].append({"kind": "future", "file": "/elsewhere"})
    die.write()
    assert "future" not in read(die.record, CORNERS[0])


def test_the_gate_view_is_the_models_then_the_netlist(tmp_path):
    design, files = gate_view(Die(tmp_path).record)
    assert design == "top" and [p.name for p in files] == ["cells.v", "top.nl.v"]


def test_gate_view_refuses_a_record_without_a_netlist_and_quotes_why(tmp_path):
    die = Die(tmp_path)
    die.document["views"] = [r for r in die.document["views"] if r["kind"] not in ("netlist", "sim_model")]
    die.document["not_recorded"] = [{"kind": "netlist", "reason": "not recorded yet"}]
    die.write()
    with pytest.raises(EdaViewsRefused, match="records no netlist: not recorded yet"):
        gate_view(die.record)


def test_check_bound_accepts_the_contract_the_record_was_written_for(tmp_path):
    die = Die(tmp_path)
    check_bound(die.record, die.contract_dir)


def _another_layout(die):
    die.contract["die"]["layout_sha256"] = "cd" * 32


def _another_design(die):
    die.contract["die"]["top_cell"] = "other"


def _another_pin_list(die):
    other = b'{"version": "1.0", "pins": [{"name": "a"}]}\n'
    (die.contract_dir / "pins.json").write_bytes(other)
    die.contract["inventory"]["sha256"] = sha(other)


def _a_rebuilt_pin_list(die):
    (die.contract_dir / "pins.json").write_bytes(b"{}\n")


def _another_major(die):
    die.contract["version"] = "2.0"


@pytest.mark.parametrize("edit, reason", [
    (_another_layout, "is not bound to the layout and build record of"),
    (_another_design, "describes top, and"),
    (_another_pin_list, "the pin list of"),
    (_a_rebuilt_pin_list, "the contract binds it at"),
    (_another_major, "this writer reads major version 1"),
])
def test_check_bound_refuses_a_record_written_for_another_die(tmp_path, edit, reason):
    die = Die(tmp_path)
    edit(die)
    die.write_contract()
    with pytest.raises(EdaViewsRefused, match=reason):
        check_bound(die.record, die.contract_dir)


def test_a_refusal_is_a_format_error():
    assert issubclass(EdaViewsRefused, ChipletFormatError)
