# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The transistor-level view reader: the scanner's text rules, which move every
deck text sha256, and the selector over a record. The netlists that pin
``deck_text`` byte for byte and the refusal per reason are conformance
fixtures (``conformance/test_transistor_view.py``)."""
import hashlib
import json

import pytest

from chiplet_format_io.eda_views import SCHEMA
from chiplet_format_io.transistor_view import (TransistorViewRefused, card_word, cards, deck_text,
                                               instance_subckt, subckt_ports, transistor_view)

PDK = {"name": "ihp-sg13g2", "revision": "0" * 40}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def view(files, top="blk", pins=("a", "b"), devices=("dev",), local=(), dialect="spice",
         netlist_kind="schematic", top_form="subckt"):
    return {"format": "chiplet-eda-views-transistor/v1", "dialect": dialect, "netlist_kind": netlist_kind,
            "top": top, "top_form": top_form, "pins": [{"name": p, "role": "signal"} for p in pins],
            "devices": list(devices), "local_subckts": list(local), "pdk": PDK,
            "files": [{"file": name, "sha256": sha(data)} for name, data in files]}


# ----------------------------------------------------------------------
# The scanner
# ----------------------------------------------------------------------
def test_cards_split_on_newline_only():
    assert cards("x1 a b\x85c dev\n") == ["x1 a b\x85c dev"]
    assert cards("x1 a b dev\r\n.ends\r\n") == ["x1 a b dev", ".ends"]


def test_cards_split_tokens_on_ascii_whitespace_only():
    assert cards("X1 a b   dev\t\tw=1\n") == ["X1 a b dev w=1"]


def test_cards_drop_an_inline_comment_marked_by_space_or_tab():
    assert cards("X1 a b dev\t$\tnote\nX2 a b dev ; note\n") == ["X1 a b dev", "X2 a b dev"]


def test_cards_join_a_continuation_across_comment_lines():
    assert cards("X1 a b\n* between\n+ dev w=1\n") == ["X1 a b dev w=1"]


@pytest.mark.parametrize("card", ["X1 a b dev $", "X1 a b dev;x", "X1 a b dev //x", "X1 a b dev w=1,$2"])
def test_cards_refuse_a_comment_terminator_left_in_a_card(card):
    with pytest.raises(TransistorViewRefused) as excinfo:
        cards(card + "\n")
    assert excinfo.value.reason == "comment_in_card"


def test_card_word_folds_ascii_and_names_prefixed_cards():
    assert [card_word(w) for w in (".INCL", ".Library", ".controls", ".ENDC", ".ends", "XM1")] == \
        [".include", ".lib", ".control", ".endc", ".ends", "xm1"]
    assert card_word("K1") == "K1"


def test_subckt_ports_and_instance_subckt_stop_at_parameters():
    assert subckt_ports(".subckt blk a b params: w=1") == ("blk", ["a", "b"])
    assert subckt_ports(".subckt blk a b w=1") == ("blk", ["a", "b"])
    tokens = "X1 a b dev PARAMS: w=1".split()
    assert tokens[instance_subckt(tokens)] == "dev"
    tokens = "X1 a b dev w=1 l=2".split()
    assert tokens[instance_subckt(tokens)] == "dev"


def test_names_fold_as_ascii_not_as_unicode(tmp_path):
    data = ".subckt K1 a b\nX1 a b dev\n.ends\n".encode("utf-8")
    (tmp_path / "blk.spice").write_bytes(data)
    with pytest.raises(TransistorViewRefused) as excinfo:
        deck_text(view([("blk.spice", data)], top="k1"), tmp_path)
    assert excinfo.value.reason == "subckt_absent"


def test_the_mapping_keeps_each_tokens_own_case(tmp_path):
    data = b".subckt blk a b\nX1 a b Loc-1\n.ends\n.subckt loc-1 c d\n.ends LOC-1\n"
    (tmp_path / "blk.spice").write_bytes(data)
    text = deck_text(view([("blk.spice", data)], devices=(), local=("loc-1",)), tmp_path).text
    assert text == ".subckt blk a b\nX1 a b Loc_1\n.ends\n.subckt loc_1 c d\n.ends LOC_1\n"


def test_a_duplicate_top_is_a_name_clash(tmp_path):
    data = b".subckt blk a b\nX1 a b dev\n.ends\n.subckt BLK a b\n.ends\n"
    (tmp_path / "blk.spice").write_bytes(data)
    with pytest.raises(TransistorViewRefused) as excinfo:
        deck_text(view([("blk.spice", data)]), tmp_path)
    assert excinfo.value.reason == "name_clash"


# ----------------------------------------------------------------------
# The record
# ----------------------------------------------------------------------
class Record:
    """A record with two spice views of one block, a schematic and a pnr netlist."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "views"
        (self.dir / "spice").mkdir(parents=True)
        (self.dir / "transistor_view").mkdir()
        self.rows, self.views = [], {}
        for kind in ("schematic", "pnr"):
            name = f"spice/blk.{kind}.spice"
            data = f"* {kind}\n.subckt blk a b\nX1 a b dev\n.ends\n".encode()
            (self.dir / name).write_bytes(data)
            self.rows.append({"kind": "spice", "file": name, "sha256": sha(data), "status": "recorded"})
            self.views[kind] = view([(name, data)], netlist_kind=kind)
        self.write()

    def write(self):
        rows = list(self.rows)
        for kind, body in self.views.items():
            name = f"transistor_view/{kind}.json"
            data = json.dumps(body).encode()
            (self.dir / name).write_bytes(data)
            rows.append({"kind": "transistor_view", "file": name, "sha256": sha(data), "status": "recorded"})
        self.path = self.dir / "eda-views.json"
        self.path.write_text(json.dumps({"schema": SCHEMA, "design": "blk", "corners": ["nom"],
                                         "default_corner": "nom", "views": rows, "not_recorded": []}))


def test_a_record_with_two_spice_views_reads_each(tmp_path):
    record = Record(tmp_path)
    for kind in ("schematic", "pnr"):
        read = transistor_view(record.path, "spice", kind)
        assert read.view["netlist_kind"] == kind and read.deck.top == "blk"


def _no_view_of_the_pair(record):
    del record.views["pnr"]
    record.rows = record.rows[:1]


def _a_row_no_view_names(record):
    record.views["pnr"]["files"] = record.views["schematic"]["files"]


def _a_row_two_views_name(record):
    record.views["pnr"]["files"] = record.views["schematic"]["files"] + record.views["pnr"]["files"]


def _files_out_of_row_order(record):
    record.views["pnr"]["files"] = record.views["pnr"]["files"] + record.views["schematic"]["files"]
    record.views["schematic"]["files"] = []


def _a_file_no_row_names(record):
    record.views["pnr"]["files"] = [{"file": "spice/other.spice", "sha256": "0" * 64}]


@pytest.mark.parametrize("edit", [_no_view_of_the_pair, _a_row_no_view_names, _a_row_two_views_name,
                                  _a_file_no_row_names])
def test_the_selector_refuses_unless_rows_split_exactly_into_views(tmp_path, edit):
    record = Record(tmp_path)
    edit(record)
    record.write()
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(record.path, "spice", "pnr")
    assert excinfo.value.reason == "selector"


def test_the_selector_refuses_files_out_of_row_order(tmp_path):
    record = Record(tmp_path)
    _files_out_of_row_order(record)
    del record.views["schematic"]
    record.write()
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(record.path, "spice", "pnr")
    assert excinfo.value.reason == "selector"


def _cdl_rows(record):
    """Two cdl rows beside the spice rows, as a die flow writes its LVS netlists."""
    (record.dir / "cdl").mkdir()
    files = []
    for k in range(2):
        name = f"cdl/blk.{k}.cdl"
        data = f"* cdl {k}\n.subckt blk a b\nX1 a b dev\n.ends\n".encode()
        (record.dir / name).write_bytes(data)
        record.rows.append({"kind": "cdl", "file": name, "sha256": sha(data), "status": "recorded"})
        files.append((name, data))
    return files


def _refuses_selector(record):
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(record.path, "spice", "pnr")
    assert excinfo.value.reason == "selector"


def test_a_record_with_no_cdl_view_may_leave_its_cdl_rows_bare(tmp_path):
    record = Record(tmp_path)
    _cdl_rows(record)
    record.write()
    for kind in ("schematic", "pnr"):
        assert transistor_view(record.path, "spice", kind).view["netlist_kind"] == kind


def test_a_record_with_a_cdl_view_holds_every_cdl_row_in_exactly_one(tmp_path):
    record = Record(tmp_path)
    cdl = _cdl_rows(record)
    record.views["cdl"] = view(cdl[:1], dialect="cdl")
    record.write()
    _refuses_selector(record)
    record.views["cdl"] = view(cdl, dialect="cdl")
    record.write()
    assert transistor_view(record.path, "spice", "pnr").view["netlist_kind"] == "pnr"
    record.views["cdl_again"] = view(cdl[1:], dialect="cdl", netlist_kind="pnr")
    record.write()
    _refuses_selector(record)


def test_bare_cdl_rows_leave_the_spice_rule_as_it_was(tmp_path):
    record = Record(tmp_path)
    _cdl_rows(record)
    _a_row_no_view_names(record)
    record.write()
    _refuses_selector(record)


def test_a_view_row_whose_file_moved_refuses(tmp_path):
    record = Record(tmp_path)
    flipped = dict(record.views["pnr"], pins=[{"name": "a", "role": "supply"}, {"name": "b", "role": "signal"}])
    (record.dir / "transistor_view" / "pnr.json").write_text(json.dumps(flipped))
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(record.path, "spice", "pnr")
    assert excinfo.value.reason == "digest"


def test_a_view_row_linked_outside_the_record_refuses(tmp_path):
    record = Record(tmp_path)
    inside = record.dir / "transistor_view" / "pnr.json"
    outside = tmp_path / "pnr.json"
    outside.write_bytes(inside.read_bytes())
    inside.unlink()
    inside.symlink_to(outside)
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(record.path, "spice", "pnr")
    assert excinfo.value.reason == "view_shape"


def test_a_standalone_view_of_another_pair_refuses(tmp_path):
    data = b".subckt blk a b\nX1 a b dev\n.ends\n"
    (tmp_path / "blk.spice").write_bytes(data)
    (tmp_path / "view.json").write_text(json.dumps(view([("blk.spice", data)])))
    assert transistor_view(tmp_path / "view.json", "spice", "schematic").deck.top == "blk"
    with pytest.raises(TransistorViewRefused) as excinfo:
        transistor_view(tmp_path / "view.json", "spice", "pnr")
    assert excinfo.value.reason == "selector"
