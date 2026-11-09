# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The interposer LVS reference netlist written from a .chiplet.

Hermetic: the golden design and edits of it, never a KLayout run. The names the
reader keeps and refuses are the ones measured on KLayout's SPICE reader, as the
module docstring states them. Tests assert refusal codes, never messages.
"""
from __future__ import annotations

import copy
import hashlib

import pytest
import yaml

from chiplet_system_description import interposer_reference as ir
from chiplet_system_description.interposer_reference import (InterposerReferenceRefused,
                                                              reference_text)
from tests.conftest import GOLDEN_CHIPLET

TOP = "INTERPOSER"


def golden():
    return yaml.safe_load(GOLDEN_CHIPLET.read_text(encoding="utf-8"))


def raw(doc) -> bytes:
    return yaml.safe_dump(doc, sort_keys=False).encode("utf-8")


def ports(text: str):
    subckt = text.splitlines()[1].split(" ")
    assert subckt[:2] == [".SUBCKT", TOP]
    return subckt[2:]


def codes(doc, top=TOP):
    with pytest.raises(InterposerReferenceRefused) as info:
        reference_text(raw(doc), top)
    return info.value.codes


def comp(doc, cid):
    return next(c for c in doc["components"] if c["id"] == cid)


def net(doc, name):
    return next(n for n in doc["netlist"]["nets"] if n["name"] == name)


def test_the_golden_reference():
    data = GOLDEN_CHIPLET.read_bytes()
    assert reference_text(data, TOP) == (
        f"* intended interposer connectivity, from design.chiplet sha256 "
        f"{hashlib.sha256(data).hexdigest()}\n"
        f".SUBCKT INTERPOSER GND VDD sig0 sig1 sig2 sig3 sig4 sig5\n"
        f".ENDS INTERPOSER\n")


def test_the_header_names_the_bytes_not_the_document():
    data = GOLDEN_CHIPLET.read_bytes()
    edited = data + b"# a comment changes the bytes, not the nets\n"
    one, other = reference_text(data, TOP), reference_text(edited, TOP)
    assert one.splitlines()[1:] == other.splitlines()[1:]
    assert other.splitlines()[0].endswith(hashlib.sha256(edited).hexdigest())


def test_the_file_is_three_ascii_lines(tmp_path):
    out = ir.write_reference(GOLDEN_CHIPLET, TOP, tmp_path / "ref.cir")
    data = out.read_bytes()
    data.decode("ascii")
    assert data.count(b"\n") == 3 and data.endswith(b"\n") and b"\r" not in data
    assert not any(line.startswith(b"+") for line in data.splitlines())


def test_the_cli_writes_once_and_never_overwrites(tmp_path, capsys):
    out = tmp_path / "ref.cir"
    argv = ["--design", str(GOLDEN_CHIPLET), "--top", TOP, "--out", str(out)]
    assert ir.main(argv) == ir.EXIT_OK
    first = out.read_bytes()
    assert ir.main(argv) == ir.EXIT_REFUSED
    assert out.read_bytes() == first
    assert "output_exists" in capsys.readouterr().err


def test_the_cli_writes_nothing_when_refused(tmp_path):
    out = tmp_path / "ref.cir"
    argv = ["--design", str(GOLDEN_CHIPLET), "--top", "A-B", "--out", str(out)]
    assert ir.main(argv) == ir.EXIT_REFUSED
    assert not out.exists()


# --- the net set ---
def test_a_net_only_an_io_pad_names_is_a_port_and_a_net_with_no_landing_is_not():
    doc = golden()
    doc["netlist"]["nets"] += [{"name": "PAD_ONLY", "connections": []},
                               {"name": "NOWHERE", "connections": []}]
    comp(doc, "interposer")["io_pads"].append(
        {"id": "J_X", "net": "PAD_ONLY", "position": {"x": 10.0, "y": 10.0}})
    found = ports(reference_text(raw(doc), TOP))
    assert "PAD_ONLY" in found and "NOWHERE" not in found


def test_a_pad_with_no_net_is_skipped():
    doc = golden()
    pads = comp(doc, "interposer")["io_pads"]
    pads.append({"id": "J_NC", "position": {"x": 10.0, "y": 10.0}})
    pads.append({"id": "J_EMPTY", "net": "", "position": {"x": 20.0, "y": 10.0}})
    assert ports(reference_text(raw(doc), TOP)) == ports(reference_text(raw(golden()), TOP))


def test_ports_are_in_byte_order():
    doc = golden()
    net(doc, "sig0")["name"] = "a"
    net(doc, "sig1")["name"] = "B"
    found = ports(reference_text(raw(doc), TOP))
    assert found == sorted(found, key=lambda n: n.encode()) and found.index("B") < found.index("a")


def _set(cid, key, value):
    def edit(doc):
        comp(doc, cid)[key] = value
    return edit


def _drop_orientation(doc):
    del comp(doc, "U2")["orientation"]


def _connect(component):
    def edit(doc):
        net(doc, "sig0")["connections"].append({"component": component, "pin": "P1"})
    return edit


def _add_substrate(doc):
    doc["components"].append({"id": "S1", "type": "substrate"})
    _connect("S1")(doc)


def _second_interposer(doc):
    second = copy.deepcopy(comp(doc, "interposer"))
    second["id"] = "interposer2"
    doc["components"].append(second)


def _no_interposer(doc):
    doc["components"] = [c for c in doc["components"] if c["type"] != "interposer"]


def _repeat_net(doc):
    doc["netlist"]["nets"].append({"name": "sig0", "connections": []})


def _terminal_on_two_nets(doc):
    net(doc, "sig1")["connections"].append(dict(net(doc, "sig0")["connections"][0]))


def _pad_names_unknown_net(doc):
    comp(doc, "interposer")["io_pads"][0]["net"] = "NO_SUCH_NET"


def _no_landing(doc):
    doc["netlist"]["nets"] = []
    for pad in comp(doc, "interposer")["io_pads"]:
        del pad["net"]


def _repeat_component(doc):
    doc["components"].append(copy.deepcopy(comp(doc, "U1")))


def _external_netlist(doc):
    doc["netlist"]["external_netlist"] = "nets.v"


def _intermediate(doc):
    doc["_metadata"] = {"finalize_required": True}


@pytest.mark.parametrize("edit, code", [
    (_set("U2", "orientation", "face_up"), "connection_off_carrier"),
    (_set("U2", "orientation", "face_down"), "connection_off_carrier"),
    (_drop_orientation, "connection_off_carrier"),
    (_set("U2", "type", "die_array"), "connection_off_carrier"),
    (_connect("interposer"), "connection_off_carrier"),
    (_add_substrate, "connection_off_carrier"),
    (_connect("NO_SUCH_DIE"), "connection_unknown"),
    (_external_netlist, "external_netlist"),
    (_second_interposer, "interposer_count"),
    (_no_interposer, "interposer_count"),
    (_repeat_net, "net_repeated"),
    (_terminal_on_two_nets, "terminal_on_two_nets"),
    (_pad_names_unknown_net, "io_pad_net_unknown"),
    (_no_landing, "no_ports"),
    (_repeat_component, "component_repeated"),
    (_intermediate, "design_unreadable"),
])
def test_what_the_net_set_rule_does_not_cover_is_refused(edit, code):
    doc = golden()
    edit(doc)
    assert code in codes(doc)


def test_every_refusal_is_reported_together():
    doc = golden()
    _external_netlist(doc)
    _set("U2", "orientation", "face_up")(doc)
    assert codes(doc, top="A-B") == ["connection_off_carrier", "external_netlist", "top_cell_name"]


# --- names ---
@pytest.mark.parametrize("name", [
    "A<0>", "~{RST}", "D[3]", "a/b", "x$y", "p;q", "n-1", "a(b)", "a,b", "a*b", "#x", "a:b",
])
def test_a_name_the_reader_keeps_is_written_raw(name):
    doc = golden()
    net(doc, "sig0")["name"] = name
    assert name in ports(reference_text(raw(doc), TOP))


@pytest.mark.parametrize("name", [
    "a b", "a\tb", "a=b", 'a"b', "a'b", "a\\b", "*ab", "PARAMS:x", "params:", "Ä", "a\x01b",
])
def test_a_name_the_reader_would_not_keep_is_refused(name):
    doc = golden()
    net(doc, "sig0")["name"] = name
    assert codes(doc) == ["port_name"]


def test_names_equal_but_for_case_are_refused():
    doc = golden()
    net(doc, "sig0")["name"] = "clk"
    net(doc, "sig1")["name"] = "CLK"
    assert codes(doc) == ["port_case_collision"]


@pytest.mark.parametrize("top", ["INTERPOSER", "carrier_lc", "A/B", "A[0]", "A.B", "A:B", "TOP_1"])
def test_a_top_cell_a_subckt_name_keeps(top):
    assert reference_text(GOLDEN_CHIPLET.read_bytes(), top).splitlines()[2] == f".ENDS {top}"


@pytest.mark.parametrize("top", [
    "A-B", "A(B)", 'A"B', "A=B", "A\\B", "A B", "", "PARAMS:TOP", "params:x", "Ä", "A$B",
])
def test_a_top_cell_a_subckt_name_would_not_keep_is_refused(top):
    with pytest.raises(InterposerReferenceRefused) as info:
        reference_text(GOLDEN_CHIPLET.read_bytes(), top)
    assert info.value.codes == ["top_cell_name"]
