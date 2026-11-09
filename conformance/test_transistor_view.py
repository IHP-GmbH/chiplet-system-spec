# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The transistor-level view reader over its fixtures.

Two netlists pin ``deck_text`` byte for byte. The synthetic one carries the
shape of an Xschem netlist on purpose (a commented, hyphenated top; locals
defined after it; ``v+``, ``v-`` and ``sub!`` nodes; ``**`` lines that are not
the top pair; lowercase ``x`` cards; mixed-case pins; an inline comment; a
continuation; a trailing ``.end``) and instantiates only devices both engines
reach from an ``X`` line. CMP7345-final is vendored from the IHP__CMP7345
repository with only comment lines changed, so its expected sha256 is the one
the upstream bytes give, and the one a lock binds as ``deck_text_sha256``.

``cases.json`` holds one netlist per refusal, each refused with the reason it
names and no other, and the inputs that must read.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

from chiplet_format_io.eda_views import EdaViewsRefused  # noqa: E402
from chiplet_format_io.transistor_view import (REASONS, VIEW_FORMAT, TransistorViewRefused,  # noqa: E402
                                               deck_text, derive, transistor_view)

FIXTURES = ROOT / "conformance" / "fixtures" / "transistor_view"
SCHEMA = json.loads((ROOT / "schemas" / "eda_views_transistor.schema.json").read_text(encoding="utf-8"))
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))

SYNTHETIC_SHA256 = "9dda73c44d3223a7780c2fd91777d32ba8d9c14c179278771714c64263a2329c"
CMP7345_SHA256 = "8f2b72b76c682a96c88896bdf3367b4ba97ab8c7905b889c25b5e6c254c9e531"


def _schema_valid(view):
    cls = validator_for(SCHEMA)
    cls.check_schema(SCHEMA)
    return cls(SCHEMA).is_valid(view)


def _write(tmp_path, case):
    """The case's netlist files and its view under ``tmp_path``; the view's path."""
    view = json.loads(json.dumps(CASES["base_view"]))
    view.update(case.get("view", {}))
    view["files"] = []
    for entry in case["files"]:
        data = bytes.fromhex(entry["hex"]) if "hex" in entry else entry["text"].encode("utf-8")
        (tmp_path / entry["file"]).write_bytes(data)
        view["files"].append({"file": entry["file"], "sha256": hashlib.sha256(data).hexdigest()})
    for k, override in enumerate(case.get("view_files", ())):
        view["files"][k].update(override)
    path = tmp_path / "view.json"
    path.write_text(json.dumps(view), encoding="utf-8")
    return path


def test_the_synthetic_deck_text_is_its_expected_bytes():
    read = transistor_view(FIXTURES / "synthetic" / "view.json", "spice", "schematic")
    expected = (FIXTURES / "synthetic" / "expected.spice").read_bytes()
    assert read.deck.text.encode("utf-8") == expected
    assert read.deck.sha256 == hashlib.sha256(expected).hexdigest() == SYNTHETIC_SHA256
    assert read.deck.top == "syn_ota"


def test_the_synthetic_carries_the_xschem_shape_it_stands_for():
    netlist = (FIXTURES / "synthetic" / "syn_ota.spice").read_text(encoding="utf-8")
    view = json.loads((FIXTURES / "synthetic" / "view.json").read_text(encoding="utf-8"))
    assert "**.subckt syn-ota " in netlist and "\n**.ends\n" in netlist
    assert " v+ v- " in netlist and "sub!" in netlist and "\nRSUB sub! vss 1\n" in netlist
    assert "\nx1 " in netlist and " $ " in netlist and "\n+ " in netlist
    assert netlist.endswith("\n.end\n")
    assert sorted(view["devices"]) == ["cap_cmim", "sg13_lv_nmos", "sg13_lv_pmos"]
    assert len(view["local_subckts"]) == 3


def test_cmp7345_final_gives_the_sha256_the_lock_binds():
    read = transistor_view(FIXTURES / "cmp7345" / "view.json", "spice", "schematic")
    assert read.deck.sha256 == CMP7345_SHA256
    assert read.deck.top == "CMP7345_final"
    assert read.deck.text.startswith(".subckt CMP7345_final v- v+ vss vdd clk vout2 vout1\n")


def test_cmp7345_final_with_crlf_line_ends_gives_the_same_sha256(tmp_path):
    data = (FIXTURES / "cmp7345" / "CMP7345-final.spice").read_bytes().replace(b"\n", b"\r\n")
    (tmp_path / "CMP7345-final.spice").write_bytes(data)
    view = json.loads((FIXTURES / "cmp7345" / "view.json").read_text(encoding="utf-8"))
    view["files"] = [{"file": "CMP7345-final.spice", "sha256": hashlib.sha256(data).hexdigest()}]
    assert deck_text(view, tmp_path).sha256 == CMP7345_SHA256


def test_the_vendored_copy_states_its_source_and_changes_only_comments():
    lines = (FIXTURES / "cmp7345" / "CMP7345-final.spice").read_text(encoding="utf-8").split("\n")
    header = "\n".join(lines[:4])
    for needed in ("https://github.com/KrzysztofHerman/IHP__CMP7345",
                   "688ee1cfb81b82e6058699e429825682b25d3002",
                   # split so that REUSE does not read this line as a license tag
                   "SPDX-License-" "Identifier: Apache-2.0", "Changed:"):
        assert needed in header, needed
    assert all(line.startswith("*") for line in lines[:4])
    assert not any("sch_path" in line or "sym_path" in line or "/home/" in line for line in lines[4:])


@pytest.mark.parametrize("name", ["synthetic", "cmp7345"])
def test_the_fixture_views_are_schema_valid(name):
    view = json.loads((FIXTURES / name / "view.json").read_text(encoding="utf-8"))
    assert view["format"] == VIEW_FORMAT
    assert _schema_valid(view)


@pytest.mark.parametrize("case", CASES["refusals"], ids=lambda c: c["name"])
def test_a_refusal_case_refuses_with_its_reason(tmp_path, case):
    assert case["reason"] in REASONS
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(_write(tmp_path, case), "spice", "schematic")
    assert excinfo.value.reason == case["reason"], str(excinfo.value)
    if "field" in case:
        assert excinfo.value.field == case["field"]
    assert isinstance(excinfo.value, EdaViewsRefused) and isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize("case", CASES["accepted"], ids=lambda c: c["name"])
def test_an_accepted_case_reads(tmp_path, case):
    read = transistor_view(_write(tmp_path, case), "spice", "schematic")
    assert read.deck.top == "blk"
    assert read.deck.text.endswith(".ends\n")


@pytest.mark.parametrize("name", ["synthetic", "ota1336", "cmp7345"])
def test_a_view_whose_meta_derive_states_reads(tmp_path, name):
    """``derive`` gives each fixture view's own pins, local subckts and devices,
    and a view stating them from it reads. Red on: derive returns no local subckts."""
    view = json.loads((FIXTURES / name / "view.json").read_text(encoding="utf-8"))
    pins, local, devices = derive(view, FIXTURES / name)
    assert pins == [pin["name"] for pin in view["pins"]]
    assert sorted(local) == sorted(view["local_subckts"])
    assert sorted(devices) == sorted(view["devices"])
    for entry in view["files"]:
        (tmp_path / entry["file"]).write_bytes((FIXTURES / name / entry["file"]).read_bytes())
    roles = {pin["name"]: pin["role"] for pin in view["pins"]}
    view.update(pins=[{"name": pin, "role": roles[pin]} for pin in pins], local_subckts=local, devices=devices)
    (tmp_path / "view.json").write_text(json.dumps(view), encoding="utf-8")
    assert transistor_view(tmp_path / "view.json", "spice", "schematic").view == view


def test_a_cdl_view_is_not_read_at_this_release(tmp_path):
    case = {"files": [{"file": "blk.spice", "text": ".subckt blk a b\nX1 a b dev\n.ends\n"}],
            "view": {"dialect": "cdl"}}
    path = _write(tmp_path, case)
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(path, "cdl", "schematic")
    assert excinfo.value.reason == "dialect"
    with pytest.raises(TransistorViewRefused) as excinfo:
        deck_text(json.loads(path.read_text(encoding="utf-8")), tmp_path)
    assert excinfo.value.reason == "dialect"


def test_the_schema_refuses_what_the_reader_refuses_by_shape():
    view = json.loads((FIXTURES / "synthetic" / "view.json").read_text(encoding="utf-8"))
    for key, value in (("dialect", "verilog"), ("top_form", "flat"), ("files", []),
                       ("pins", [{"name": "a", "role": "power"}])):
        assert not _schema_valid({**view, key: value}), key
    assert not _schema_valid({**view, "extra": 1})
