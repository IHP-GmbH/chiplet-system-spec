# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for validation rule 8: io_class vs interfaces[].type (SPEC-22).

Two closed vocabularies describe one physical joint, and until now nothing
related them: a document could declare a wire_bond pad as the landing of a
copper_pillar interface and pass every check, because each vocabulary was
validated on its own. Rule 8 is the relation, and the table that carries it lives
in three places (the spec, ``chiplet_format_io.IO_CLASS_INTERFACE_TYPES`` and the
C++ ``kPadUsageTable``); the first half of this file reads all three, and the
second exercises the Python validator's behaviour on hand-built documents plus
the two corpus fixtures.

What a green here does NOT cover (META-2):

* the C++ validator's behaviour, which its own test binary owns
  (test_pad_usage_rule_refuses_a_mismatched_pad there); this file compares the
  C++ TABLE as text and never runs the C++ reader.
* an endpoint whose component carries no inline pads, i.e. every die endpoint.
  Rule 8 does not check it, by decision, and no test here can pretend otherwise
  until an explicit pad binding exists (SPEC-24).
* rule 9, the cross-artifact check that the METHOD behind a component's
  connection stack agrees with the interface type. The reference validators do
  not run it; the assembly-stage hosts do.
* whether any real assembly mixes usage classes on one layer under one
  interface, which rule 8 would refuse. The corpus was measured before the rule
  landed (55 documents in this repository, adk-tools examples, the studio fixtures
  and the plugin trees; one tripped, the all-blocks coverage fixture, and it was
  corrected in the same commit).
"""
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

SPEC = ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md"
CPP_SOURCE = ROOT / "reference" / "cpp" / "src" / "chiplet_format_io.cpp"
FIXTURES = ROOT / "conformance" / "fixtures"
CHIPLET_SCHEMA = json.loads(
    (ROOT / "schemas" / "chiplet.schema.json").read_text(encoding="utf-8"))


def _spec_table():
    """The normative table under 'Usage class and interface type', as a dict."""
    text = SPEC.read_text(encoding="utf-8")
    section = re.search(
        r"#### Usage class and interface type \(normative\)(.*?)\n### ",
        text, re.DOTALL)
    assert section, "no 'Usage class and interface type' section in the spec"
    table = {}
    for line in section.group(1).split("\n"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 2 or not cells[0].startswith("`"):
            continue
        io_class = cells[0].strip("`")
        # The header row is `io_class` (usage), which is not an identifier; the
        # separator row has no backticks at all and never gets here.
        if not re.fullmatch(r"[a-z_]+", io_class):
            continue
        table[io_class] = tuple(re.findall(r"`([a-z_]+)`", cells[1]))
    assert table, "the spec table parsed to nothing"
    return table


def _cpp_table():
    """kPadUsageTable, read out of the C++ source text (nullptr padding dropped)."""
    text = CPP_SOURCE.read_text(encoding="utf-8")
    body = re.search(r"kPadUsageTable\s*=\s*\{\{(.*?)\}\};", text, re.DOTALL)
    assert body, f"no kPadUsageTable initializer in {CPP_SOURCE}"
    table = {}
    for row in re.finditer(r'\{"([a-z_]+)",\s*\{([^}]*)\}\}', body.group(1)):
        table[row.group(1)] = tuple(re.findall(r'"([a-z_]+)"', row.group(2)))
    assert table, "the C++ table parsed to nothing"
    return table


def test_the_spec_and_both_readers_carry_one_table():
    assert _spec_table() == cfio.IO_CLASS_INTERFACE_TYPES
    assert _cpp_table() == cfio.IO_CLASS_INTERFACE_TYPES


def test_the_table_covers_every_io_class_and_only_known_types():
    # The table is a total function on the io_class vocabulary: a usage class
    # with no row would silently be exempt from rule 8, which is the failure mode
    # that is invisible in a green run.
    declared = tuple(CHIPLET_SCHEMA["definitions"]["io_pad"]["properties"]
                     ["io_class"]["enum"])
    assert set(cfio.IO_CLASS_INTERFACE_TYPES) == set(declared)
    for allowed in cfio.IO_CLASS_INTERFACE_TYPES.values():
        assert allowed, "an io_class that allows nothing is not a row, it is a bug"
        assert set(allowed) <= set(cfio.KNOWN_INTERFACE_TYPES)


def test_every_known_interface_type_is_reachable_from_some_io_class():
    # The other direction: an interface type no usage class allows could never
    # appear on an endpoint with inline pads, which would be a table defect
    # rather than a document one.
    reachable = set()
    for allowed in cfio.IO_CLASS_INTERFACE_TYPES.values():
        reachable |= set(allowed)
    assert reachable == set(cfio.KNOWN_INTERFACE_TYPES)


def _doc(io_class, pad_layer, iface_type, port_layer):
    """A hand-built two-component document: one pad, one interface.

    Hand-built rather than mutated from a corpus specimen: a fixture is a
    document, not the specification, and a test that reads the rule off one pins
    whatever that file happens to contain. Stamped at the content's floor, so a
    1.1 member (``solder_bump``) tests rule 8 rather than its own minor.
    """
    doc = {
        "format_version": "1.0",
        "assembly": {"name": "rule 8"},
        "components": [
            {"id": "interposer", "type": "interposer",
             "io_pads": [{"id": "P1", "io_class": io_class,
                          "position": {"x": 0.0, "y": 0.0},
                          "layer": pad_layer}]},
            {"id": "U1", "type": "die"},
        ],
        "interfaces": [
            {"id": "link0", "type": iface_type,
             "from": {"component": "U1", "port_layer": port_layer},
             "to": {"component": "interposer", "port_layer": port_layer}},
        ],
    }
    doc["format_version"] = cfio.content_format_floor(doc) or "1.0"
    return doc


@pytest.mark.parametrize("io_class,allowed", sorted(
    cfio.IO_CLASS_INTERFACE_TYPES.items()))
def test_every_allowed_pairing_in_the_table_is_accepted(io_class, allowed):
    for iface_type in allowed:
        assert cfio.validate(_doc(io_class, "TopMetal2", iface_type, "TopMetal2"))


@pytest.mark.parametrize("io_class", sorted(cfio.IO_CLASS_INTERFACE_TYPES))
def test_every_pairing_outside_the_table_is_refused(io_class):
    forbidden = [t for t in cfio.KNOWN_INTERFACE_TYPES
                 if t not in cfio.IO_CLASS_INTERFACE_TYPES[io_class]]
    assert forbidden, io_class
    for iface_type in forbidden:
        with pytest.raises(cfio.ChipletFormatError):
            cfio.validate(_doc(io_class, "TopMetal2", iface_type, "TopMetal2"))


def test_the_refusal_names_the_interface_the_pad_the_class_and_the_type():
    # A refusal a designer cannot act on is a refusal they will disable.
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.validate(_doc("wire_bond", "TopMetal2", "copper_pillar", "TopMetal2"))
    message = str(excinfo.value)
    for token in ("link0", "P1", "wire_bond", "copper_pillar", "rule 8"):
        assert token in message, token


def test_a_pad_on_another_layer_is_not_in_the_endpoint_pad_set():
    # The layer scoping, on its own: same pad, same interface, different layer.
    assert cfio.validate(_doc("wire_bond", "Metal4", "copper_pillar", "TopMetal2"))


def test_an_endpoint_with_no_pads_at_all_is_vacuous():
    doc = _doc("wire_bond", "TopMetal2", "copper_pillar", "TopMetal2")
    doc["components"][0].pop("io_pads")
    assert cfio.validate(doc)


def test_a_die_endpoint_is_out_of_scope():
    # The die carries no inline pads in the document, so the rule has nothing to
    # read on that side. Stated as a test so the limit is visible rather than
    # inferred from an absence (SPEC-24 is the row that closes it).
    doc = _doc("wire_bond", "TopMetal2", "wire_bond", "TopMetal2")
    doc["interfaces"][0]["from"] = {"component": "U1", "port_layer": "TopMetal2"}
    assert cfio.validate(doc)


def test_an_io_class_outside_the_table_is_left_to_the_schema():
    # The closed io_class vocabulary is the schema's business. Rule 8 judging an
    # unknown value would smuggle a second vocabulary check into the reader and
    # report it under the wrong rule.
    doc = _doc("probe_pad", "TopMetal2", "copper_pillar", "TopMetal2")
    assert cfio.validate(doc)


def test_an_interface_naming_an_absent_component_is_not_a_rule_8_error():
    # Cross-reference existence is consumer-level, not this rule's; rule 8 must
    # not grow a second job while nobody is looking.
    doc = _doc("wire_bond", "TopMetal2", "copper_pillar", "TopMetal2")
    doc["interfaces"][0]["to"]["component"] = "nowhere"
    assert cfio.validate(doc)


# --- the corpus fixtures, one case seen from both sides --------------------

def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_the_mismatch_fixture_is_refused():
    with pytest.raises(cfio.ChipletFormatError) as excinfo:
        cfio.loads(_fixture("v1_0_interface_pad_mismatch.chiplet"))
    assert "rule 8" in str(excinfo.value)


def test_the_other_layer_fixture_is_accepted():
    assert cfio.loads(_fixture("v1_0_interface_pad_other_layer.chiplet"))


def test_the_two_fixtures_differ_only_in_the_layer():
    # The pair is only evidence about the layer scoping while it stays a pair:
    # if someone edits one of them into a different case, the two stop being one
    # experiment with one variable and this says so.
    bad = yaml.safe_load(_fixture("v1_0_interface_pad_mismatch.chiplet"))
    good = yaml.safe_load(_fixture("v1_0_interface_pad_other_layer.chiplet"))
    assert bad["interfaces"][0]["type"] == good["interfaces"][0]["type"]
    bad_pad = bad["components"][0]["io_pads"][0]
    good_pad = good["components"][0]["io_pads"][0]
    assert bad_pad["io_class"] == good_pad["io_class"] == "wire_bond"
    assert bad_pad["layer"] == bad["interfaces"][0]["to"]["port_layer"]
    assert good_pad["layer"] != good["interfaces"][0]["to"]["port_layer"]
