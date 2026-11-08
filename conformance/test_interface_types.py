# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the closed interfaces[].type vocabulary (rule 4, SPEC-23).

The vocabulary is written down four times: the enum in
``schemas/chiplet.schema.json``, the prose list in ``docs/CHIPLET_FORMAT_SPEC.md``,
``kKnownInterfaceTypes`` in the C++ reference source, and
``chiplet_format_io.KNOWN_INTERFACE_TYPES``. Four copies of one list is how a
member ends up accepted by one reader and refused by the other, which is the
state SPEC-23 found: the C++ reader threw on an unknown type, the Python
validator accepted it, and the spec carried an italic exception saying so. This
file reads all four and compares them, and then exercises the Python validator's
behaviour, which no text comparison can show.

What a green here does NOT cover (META-2): the C++ reader's BEHAVIOUR on each
member (its own test binary owns that, test_every_known_interface_type_is_accepted);
whether any producer emits ``solder_bump`` (none may before format 1.1, see
docs/VERSION_POLICY.md's sweep table); and which io_class a given type may meet,
which is rule 8 and lives in test_pad_usage_compatibility.py.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

SPEC = ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md"
#: The vocabulary moved from the translation unit into the HEADER when SPEC-32
#: made carrying the value the rule: a C++ consumer that has to refuse the
#: ELEMENT cannot do it without the list, and it could not reach it while the
#: array was private to the .cpp.
CPP_SOURCE = (ROOT / "reference" / "cpp" / "include" / "chiplet_format_io"
              / "chiplet_format_io.hpp")
CHIPLET_SCHEMA = json.loads(
    (ROOT / "schemas" / "chiplet.schema.json").read_text(encoding="utf-8"))


def _schema_interface_types():
    return tuple(
        CHIPLET_SCHEMA["definitions"]["interface"]["properties"]["type"]["enum"])


def _cpp_interface_types():
    """The members of kKnownInterfaceTypes, read out of the C++ source text."""
    text = CPP_SOURCE.read_text(encoding="utf-8")
    found = re.search(
        r"kKnownInterfaceTypes\s*=\s*\{(.*?)\}", text, re.DOTALL)
    assert found, f"no kKnownInterfaceTypes initializer in {CPP_SOURCE}"
    return tuple(re.findall(r'"([^"]+)"', found.group(1)))


def _cpp_declared_size():
    """The array's declared size, which must not drift from its contents."""
    text = CPP_SOURCE.read_text(encoding="utf-8")
    found = re.search(r"std::array<const char\*,\s*(\d+)>\s*kKnownInterfaceTypes",
                      text)
    assert found, f"no kKnownInterfaceTypes declaration in {CPP_SOURCE}"
    return int(found.group(1))


def _spec_interface_types():
    """The prose list under 'Known `type` values:', as a set of names.

    A set, not a tuple: the prose wraps across lines and carries a parenthetical
    gloss, so ORDER is the schema's and the code's business, membership is the
    prose's.
    """
    text = SPEC.read_text(encoding="utf-8")
    found = re.search(r"\*\*Known `type` values:\*\*(.*?)\n\n", text, re.DOTALL)
    assert found, "no 'Known `type` values' prose in the spec"
    return set(re.findall(r"`([a-z_]+)`", found.group(1)))


def test_the_python_reference_declares_the_vocabulary():
    # It has to be readable by a consumer, not only by the validator: a vendored
    # copy is the thing a host asks "which types may I write".
    assert "KNOWN_INTERFACE_TYPES" in cfio.__all__
    assert isinstance(cfio.KNOWN_INTERFACE_TYPES, tuple)
    assert len(set(cfio.KNOWN_INTERFACE_TYPES)) == len(cfio.KNOWN_INTERFACE_TYPES)


def test_the_schema_and_the_two_readers_declare_one_list():
    assert _schema_interface_types() == cfio.KNOWN_INTERFACE_TYPES
    assert _cpp_interface_types() == cfio.KNOWN_INTERFACE_TYPES


def test_the_cpp_array_size_matches_its_contents():
    # std::array's size is written by hand next to the initializer; a member
    # added without touching it either fails to compile or silently truncates.
    assert _cpp_declared_size() == len(_cpp_interface_types())


def test_the_spec_prose_names_the_same_members():
    assert _spec_interface_types() == set(cfio.KNOWN_INTERFACE_TYPES)


def test_solder_bump_is_in_the_vocabulary():
    # SPEC-23 by name, so the member cannot be dropped in a refactor without a
    # test saying so out loud.
    assert "solder_bump" in cfio.KNOWN_INTERFACE_TYPES


def _doc(**iface):
    """A hand-built minimal document carrying one interface.

    Hand-built on purpose: a corpus specimen is a document, not the
    specification, and asserting the rule against one would pin whatever that
    file happens to contain. Stamped at the lowest minor that may carry the
    member (``solder_bump`` is 1.1), so each case asks about rule 4 alone.
    """
    doc = {"format_version": "1.0", "assembly": {"name": "a"},
           "interfaces": [iface]}
    doc["format_version"] = cfio.content_format_floor(doc) or "1.0"
    return doc


@pytest.mark.parametrize("itype", cfio.KNOWN_INTERFACE_TYPES)
def test_the_python_validator_accepts_every_known_type(itype):
    assert cfio.validate(_doc(id="i1", type=itype))


def test_solder_bump_is_refused_below_its_minor():
    # The 1.1 member, in a document stamped 1.0: out of contract, refused by
    # the validator because the map says so, not because the reader is new.
    assert cfio.MEMBER_SINCE["interfaces[].type"]["solder_bump"] == "1.1"
    doc = _doc(id="i1", type="solder_bump")
    assert doc["format_version"] == "1.1"
    doc["format_version"] = "1.0"
    with pytest.raises(cfio.ChipletFormatError, match="solder_bump"):
        cfio.validate(doc)


@pytest.mark.parametrize("itype", ["hybrid_bond", "bogus_bond", "MICRO_BUMP",
                                   "micro_bump\n"])
def test_the_python_validator_carries_an_unknown_type(itype):
    # Inverted by the SPEC-32 ruling, and the PARITY assertion is restated
    # rather than deleted, because parity is what the predecessor of this test
    # was really about. It asserted a refusal so that a document refused by one
    # reference reader was refused by the other; the verdict moved and the
    # property did not, so the two readers must still agree on this document,
    # and now they agree by carrying it. The C++ twin is
    # reference/cpp/tests/test_chiplet_format_io.cpp,
    # test_unknown_interface_type_is_carried_not_refused; the behavioural cross
    # product for both is conformance/test_unknown_vocabulary_roundtrip.py.
    assert itype not in cfio.KNOWN_INTERFACE_TYPES
    doc = cfio.validate(_doc(id="i1", type=itype))
    assert doc["interfaces"][0]["type"] == itype


@pytest.mark.parametrize("itype", ["", None])
def test_the_python_validator_still_requires_a_type(itype):
    # Carrying an unknown type is not the same as accepting a missing one.
    # Rule 4 is an id and a type; only the "and it is a known one" clause went.
    with pytest.raises(cfio.ChipletFormatError):
        cfio.validate(_doc(id="i1", type=itype))


def test_the_note_names_the_interface_and_the_type():
    # What replaced the refusal: an event on the normative channel, produced at
    # parse. A message that named neither would leave a consumer with nothing to
    # act on, which is the state the C++ reader's silent io_class was in.
    notes = []
    cfio.loads('format_version: "1.0"\nassembly:\n  name: a\n'
               'interfaces:\n- id: link0\n  type: hybrid_bond\n',
               on_warn=notes.append)
    assert len(notes) == 1
    assert "link0" in notes[0] and "hybrid_bond" in notes[0]


def test_an_interface_without_an_id_is_refused():
    with pytest.raises(cfio.ChipletFormatError):
        cfio.validate(_doc(type="micro_bump"))


@pytest.mark.parametrize("block", ["micro_bump", {"id": "i1"}, 7])
def test_a_malformed_interfaces_block_is_refused(block):
    doc = {"format_version": "1.0", "assembly": {"name": "a"},
           "interfaces": block}
    with pytest.raises(cfio.ChipletFormatError):
        cfio.validate(doc)


def test_a_document_without_interfaces_is_untouched():
    # The block is optional; rule 4 must not turn its absence into an error.
    assert cfio.validate({"format_version": "1.0", "assembly": {"name": "a"}})
