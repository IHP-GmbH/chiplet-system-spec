# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the CARRY rule on an unrecognised vocabulary member.

Panel ruling Q1 (SPEC-32, with SPEC-34 and SPEC-35): the reference readers carry
every enum-like field as the string the document wrote, the schema closes each
vocabulary and binds WRITERS, an unrecognised member is reported on the warn
channel, and it is refused by nobody in the library. A consumer that cannot act
on one refuses the ELEMENT that carries it, which is what makes an added enum
member a MINOR rather than a MAJOR (docs/VERSION_POLICY.md, "What bumps what").

The cross product is the specification, and the FAILURE PATTERN is what it is
for. Three axes, each of which had a live defect behind it:

* implementation {Python, C++}. Both run
  ``conformance/fixtures/unknown_vocabulary_cases.json``, never each other; the
  C++ cells run in its own test binary under ctest.
* ``validate`` {True, False}. The C++ refusal lived in ``parse_interface``, a
  function taking no options, so ``LoadOptions::validate = false`` never reached
  it and the two readers loaded different documents from one file (SPEC-34). An
  implementer who "fixes" the first arm by gating the C++ throw behind the flag
  still fails the ``validate=False`` cell here.
* path {load, load-then-dump}. A reader that carries a string its writer then
  drops has moved the loss, not removed it.

And two documents, because the interesting one is not the interface on its own:
it is the interface that meets an interposer pad with a KNOWN ``io_class``, which
is validation rule 8 and was the third place the library refused an unknown type.

What a green here does NOT cover (META-2): the C++ cells are asserted in the C++
binary, so this file's green says the Python side and the oracle agree, and ctest
says the C++ side does; and nothing here says a CONSUMER refuses the element,
which is the consumer's own gate and is what the exported vocabulary is for.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "conformance" / "fixtures"
CPP_TESTS = ROOT / "reference" / "cpp" / "tests" / "test_chiplet_format_io.cpp"

sys.path.insert(0, str(ROOT / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402

ORACLE = json.loads(
    (FIXTURES / "unknown_vocabulary_cases.json").read_text(encoding="utf-8"))

UNKNOWN_TYPE = ORACLE["unknown_type"]
DOCUMENTS = ORACLE["documents"]
CELLS = ORACLE["cells"]
EXPECT = ORACLE["expect"]

#: The cross product, as the rows both implementations run.
MATRIX = [(doc, cell) for doc in DOCUMENTS for cell in CELLS]


def _cell_id(pair):
    doc, cell = pair
    return (f"{doc['file']}-validate_{cell['validate']}-{cell['path']}")


def test_the_oracle_is_wellformed():
    assert UNKNOWN_TYPE not in cfio.KNOWN_INTERFACE_TYPES, (
        "the oracle's unknown type has become a known one; pick another or the "
        "whole file certifies nothing")
    # Both axes present, in full. An axis that quietly collapses to one value
    # takes its half of the specification with it and leaves a green run.
    assert {c["validate"] for c in CELLS} == {True, False}
    assert {c["path"] for c in CELLS} == {"load", "load_then_dump"}
    assert len(CELLS) == 4
    assert len(DOCUMENTS) == 2
    for doc in DOCUMENTS:
        path = FIXTURES / doc["file"]
        assert path.is_file(), doc["file"]
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        types = [i["type"] for i in raw["interfaces"]]
        assert UNKNOWN_TYPE in types, doc["file"]
    # One of the two documents must exercise rule 8, or the third refusal site
    # is not covered and this file passes on a reader that still refuses it.
    with_pads = [
        d for d in DOCUMENTS
        if any(c.get("io_pads") for c in yaml.safe_load(
            (FIXTURES / d["file"]).read_text(encoding="utf-8"))["components"])]
    assert len(with_pads) == 1, "exactly one document must meet a pad"
    pads = yaml.safe_load(
        (FIXTURES / with_pads[0]["file"]).read_text(encoding="utf-8"))
    io_classes = {p["io_class"] for c in pads["components"]
                  for p in c.get("io_pads", [])}
    assert io_classes & set(cfio.IO_CLASS_INTERFACE_TYPES), (
        "the rule 8 document's pad must carry an io_class that HAS a row, or "
        "the rule skips it for the io_class reason and proves nothing about "
        "the type")


def test_the_cpp_binary_runs_the_same_oracle():
    # A text tripwire, and it is the honest kind: this file cannot execute the
    # C++ cells, so what it can do is fail when the C++ arm that does is gone.
    # Without it, deleting the C++ test function leaves this file green and the
    # cross product half-run.
    text = CPP_TESTS.read_text(encoding="utf-8")
    assert "unknown_vocabulary_cases.json" in text or \
        "CHIPLET_VOCABULARY_ORACLE" in text, (
            "the C++ test binary no longer names the shared oracle; the "
            "implementation axis of this cross product is not being run")
    assert "test_unknown_vocabulary_is_carried" in text


@pytest.mark.parametrize("pair", MATRIX, ids=_cell_id)
def test_the_python_reader_carries_the_unknown_type(pair):
    doc, cell = pair
    text = (FIXTURES / doc["file"]).read_text(encoding="utf-8")
    notes = []
    loaded = cfio.loads(text, validate=cell["validate"], on_warn=notes.append)
    assert EXPECT["raises"] is False

    iface = next(i for i in loaded["interfaces"] if i["id"] == doc["interface"])
    assert iface["type"] == UNKNOWN_TYPE
    assert isinstance(iface["type"], str)

    # Exactly one note per load, on the normative channel, whatever the flag.
    assert len(notes) == EXPECT["notes_per_load"], notes
    assert UNKNOWN_TYPE in notes[0]
    assert doc["interface"] in notes[0]

    if cell["path"] == "load_then_dump":
        written = cfio.dumps(loaded, validate=cell["validate"])
        assert UNKNOWN_TYPE in written
        reloaded = cfio.loads(written, validate=cell["validate"])
        again = next(i for i in reloaded["interfaces"]
                     if i["id"] == doc["interface"])
        assert again["type"] == UNKNOWN_TYPE
        # The writer must not have turned the note into a second event on the
        # same document: the count above is per LOAD, and a writer that warns
        # again makes it uncountable for a consumer holding one document.
        assert len(notes) == EXPECT["notes_per_load"], notes


@pytest.mark.parametrize("pair", MATRIX, ids=_cell_id)
def test_the_note_is_not_deduplicated(pair):
    # The dedup trap, stated as a test. Python's stdlib warnings channel fires
    # once per version per process, so a headless run reading many documents
    # would be told about the first and silent for the rest. on_warn is the
    # NORMATIVE channel precisely because it does not do that: two loads, two
    # notes, in one process.
    doc, cell = pair
    text = (FIXTURES / doc["file"]).read_text(encoding="utf-8")
    notes = []
    cfio.loads(text, validate=cell["validate"], on_warn=notes.append)
    cfio.loads(text, validate=cell["validate"], on_warn=notes.append)
    assert len(notes) == 2 * EXPECT["notes_per_load"], notes


@pytest.mark.parametrize("doc", DOCUMENTS, ids=lambda d: d["file"])
def test_a_reader_with_no_sink_still_loads_and_still_carries(doc):
    # on_warn is optional. A consumer that sets nothing gets no note and the
    # same document; the note is news, never a condition of reading.
    loaded = cfio.load(FIXTURES / doc["file"], validate=False)
    iface = next(i for i in loaded["interfaces"] if i["id"] == doc["interface"])
    assert iface["type"] == UNKNOWN_TYPE


def test_a_known_type_produces_no_note():
    # The control. A file whose vocabulary is entirely known must not warn, or
    # the count above measures nothing.
    notes = []
    cfio.load(FIXTURES / "v1_0_interface_pad_other_layer.chiplet",
              on_warn=notes.append)
    assert notes == []


def test_the_vocabulary_is_exported_so_a_consumer_can_refuse_the_element():
    # The other half of the ruling, and the half that makes the MINOR label true
    # rather than aspirational: the library carries the value, and the consumer
    # refuses the ELEMENT. It cannot do that without the list.
    assert "KNOWN_INTERFACE_TYPES" in cfio.__all__
    element_refusable = [
        i for i in cfio.load(
            FIXTURES / "v1_0_unknown_interface_type.chiplet")["interfaces"]
        if i["type"] not in cfio.KNOWN_INTERFACE_TYPES]
    assert [i["id"] for i in element_refusable] == ["link0"]
