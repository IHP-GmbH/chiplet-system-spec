# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for schemas/chiplet.schema.json, the .chiplet root document schema.

Three things are proved here.

1. The schema is well-formed draft-07 and the whole committed .chiplet corpus
   (examples/ plus conformance/fixtures/) validates against it, except for the
   four documents in DIVERGENCES below, which are pinned WITH a reason.
2. Every closed key set and every closed vocabulary actually rejects: an unknown
   root key, an interposer.adapter that is a path, an unquoted format_version, an
   unknown component anchor, and the rest.
3. Cross-parse: on the corpus, "validates against the schema" and "loads with
   chiplet_format_io" agree in both directions, except for exactly the pinned
   divergences. A NEW divergence fails this gate, which is the point: the schema
   is normative for structure and the reader for semantics, and the places where
   the two deliberately disagree are a fixed, reviewed list rather than drift.

jsonschema is a HARD import, as in test_schemas.py: a gate that skips itself when
its validator is missing is not a gate.
"""
import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "conformance" / "fixtures"
EXAMPLES = ROOT / "examples"

sys.path.insert(0, str(ROOT / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402

CHIPLET_SCHEMA = json.loads(
    (SCHEMAS / "chiplet.schema.json").read_text(encoding="utf-8"))

#: The complete .chiplet corpus committed in this repository.
CORPUS = sorted(EXAMPLES.glob("*.chiplet")) + sorted(FIXTURES.glob("*.chiplet"))

#: Corpus documents the schema REFUSES, each with the reason. Everything else in
#: the corpus must validate. Those the reader accepts are the deliberate
#: structure-vs-semantics divergences below; the reader refuses the rest as well.
SCHEMA_NEGATIVE = {
    "v1_0_additive_unknown_key.chiplet": "an undeclared root key",
    "v1_0_unquoted_numeric.chiplet": "format_version written unquoted",
    "v1_0_malformed_version.chiplet": "format_version is not MAJOR.MINOR",
    "v1_0_missing_version.chiplet": "no format_version at all",
    "v1_0_unknown_interface_type.chiplet":
        "an interfaces[].type outside the closed enum",
    "v1_0_unknown_type_meets_known_io_class.chiplet":
        "the same, on an interface that meets a pad with a known io_class",
    "v1_1_units_other.chiplet": "assembly.units outside the closed set",
    "v1_3_unplaced_assembly.chiplet": "the root unplaced without a system block",
}

#: The ONLY documents where the structural schema and the reference reader
#: disagree, each with the reason it is deliberate. schema/reader are the two
#: verdicts: True == accepts.
DIVERGENCES = {
    "v1_0_additive_unknown_key.chiplet": {
        "schema": False, "reader": True,
        "why": "An undeclared ROOT key. The reader is a passthrough and carries "
               "it additively (round-tripping it is a manifest expectation); the "
               "schema closes the root so an undeclared root key is a structural "
               "error, which is what makes the root key table enforceable.",
    },
    "v1_0_unquoted_numeric.chiplet": {
        "schema": False, "reader": True,
        "why": "format_version written unquoted, so YAML yields a float. The "
               "reader coerces through str() for back-compat; the spec says the "
               "field MUST be a quoted string (unquoted 1.10 reads as 1.1 under "
               "PyYAML and 1.10 under yaml-cpp) and the schema holds that line.",
    },
    "v2_0_higher_major.chiplet": {
        "schema": True, "reader": False,
        "why": "Structurally a valid document; refused on the tolerant version "
               "POLICY (different major). Version tolerance is reader semantics, "
               "not structure, so the schema does not pin the major.",
    },
    "v0_9_lower_major.chiplet": {
        "schema": True, "reader": False,
        "why": "Same as v2_0_higher_major, on the low side.",
    },
    "v1_0_unknown_interface_type.chiplet": {
        "schema": False, "reader": True,
        "why": "An interfaces[].type outside the closed vocabulary. This is the "
               "divergence the SPEC-32 ruling is: the schema closes the "
               "vocabulary and binds WRITERS, and the reference readers carry "
               "the string, because a reader that refuses the DOCUMENT turns "
               "every future enum member into a MAJOR for everyone downstream. "
               "A consumer that cannot act on the member refuses the ELEMENT, "
               "which is what KNOWN_INTERFACE_TYPES and the C++ "
               "kKnownInterfaceTypes are exported for.",
    },
    "v1_0_unknown_type_meets_known_io_class.chiplet": {
        "schema": False, "reader": True,
        "why": "The same divergence at the THIRD refusal site: the interface "
               "meets a pad whose io_class has a row in rule 8's table, so an "
               "unrecognised type matched no allowed entry and read as a "
               "violation. Rule 8 relates two closed vocabularies and a member "
               "of neither is outside its domain, so it is skipped the way an "
               "unrecognised io_class already was.",
    },
    "v1_0_system_block.chiplet": {
        "schema": True, "reader": False,
        "why": "A source document stamped 1.0. The schema validates the system "
               "block's structure and does not pin the minor it appeared in, "
               "for the reason it does not pin the major: version tolerance is "
               "reader semantics. The reader, asked to read a source, refuses "
               "it because the system block does not exist before 1.1.",
    },
    "v1_0_interface_pad_mismatch.chiplet": {
        "schema": True, "reader": False,
        "why": "A wire_bond pad on the layer a copper_pillar interface lands "
               "on. Validation rule 8 relates io_pads[].io_class to "
               "interfaces[].type, two fields in different blocks, which a "
               "structural schema cannot see at once; the reader can and "
               "refuses it. This is the divergence the fixture exists to pin.",
    },
}


def _validator(schema: dict):
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _valid(instance) -> bool:
    return _validator(CHIPLET_SCHEMA).is_valid(instance)


def _errors(instance):
    return sorted(_validator(CHIPLET_SCHEMA).iter_errors(instance),
                  key=lambda e: list(e.path))


def _doc(name: str) -> dict:
    """Parse one corpus document by file name."""
    for path in CORPUS:
        if path.name == name:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
    raise AssertionError(f"no corpus document named {name!r}")


#: The all-blocks coverage fixture: the base every negative below mutates, so a
#: negative always starts from something that really validates.
ALL_BLOCKS = _doc("v1_0_all_blocks.chiplet")

#: The same for the system block (format 1.1): every system key, used once.
SYSTEM_SOURCE = _doc("v1_1_system_source.chiplet")


def _reader_accepts(data) -> bool:
    """Whether the reference reader loads this document (intermediate allowed).

    allow_intermediate=True so the _metadata guard, which is a workflow rule and
    not a structural one, never shows up as a cross-parse divergence. Likewise
    allow_source=True: refusing a source document on the assembly path is a
    workflow rule too, and the schema describes both kinds.
    """
    try:
        cfio.validate(copy.deepcopy(data), allow_intermediate=True,
                      allow_source=True)
    except cfio.ChipletFormatError:
        return False
    return True


# --- (a) the schema itself -------------------------------------------------
def test_chiplet_schema_is_wellformed_draft07():
    cls = validator_for(CHIPLET_SCHEMA)
    assert cls is jsonschema.Draft7Validator  # the file declares draft-07
    cls.check_schema(CHIPLET_SCHEMA)


def test_root_is_closed_and_declares_the_thirteen_spec_keys():
    # The root key table in docs/CHIPLET_FORMAT_SPEC.md is the contract; this is
    # that table, executable. A key added to one and not the other fails here.
    assert CHIPLET_SCHEMA["additionalProperties"] is False
    assert set(CHIPLET_SCHEMA["properties"]) == {
        "format_version", "assembly", "technologies", "connection_stacks",
        "components", "interconnect", "interposer", "interfaces", "netlist",
        "flow", "system", "unplaced", "_metadata",
    }
    assert set(CHIPLET_SCHEMA["required"]) == {"format_version", "assembly"}


def test_flow_stays_opaque_and_extensible():
    # The spec calls flow opaque host build configuration; closing it would make
    # the format an authority on build steps it deliberately does not model.
    assert CHIPLET_SCHEMA["properties"]["flow"]["additionalProperties"] is True


def test_component_metadata_stays_extensible():
    comp = CHIPLET_SCHEMA["definitions"]["component"]
    assert comp["properties"]["metadata"]["additionalProperties"] is True


# --- (b) the whole committed corpus ----------------------------------------
def test_every_committed_chiplet_validates_except_pinned_divergences():
    assert CORPUS, "no .chiplet corpus found"
    unexpected = []
    for path in CORPUS:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        expected = path.name not in SCHEMA_NEGATIVE
        ok = _valid(data)
        if ok != expected:
            detail = "; ".join(f"{list(e.path)}: {e.message}"
                               for e in _errors(data)[:3])
            unexpected.append(f"{path.name}: schema={ok}, expected={expected} "
                              f"({detail})")
    assert not unexpected, "\n".join(unexpected)


def test_all_blocks_fixture_exercises_every_root_key():
    # A coverage fixture that stops covering is worse than none: it reports green
    # over blocks nothing validates any more.
    # `system` and `unplaced` are the root keys it cannot carry: they are a
    # source document's, which holds no derived block, so the system block has
    # its own coverage fixture.
    present = set(ALL_BLOCKS) | {"_metadata"}
    assert present == set(CHIPLET_SCHEMA["properties"]) - {"system", "unplaced"}
    system_keys = set(CHIPLET_SCHEMA["definitions"]["system"]["properties"])
    assert set(SYSTEM_SOURCE["system"]) == system_keys


def test_intermediate_metadata_block_validates():
    # The KiCad GUI export shape: canonical files carry no _metadata, so no
    # corpus document has one; the block still has to validate where it appears.
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["_metadata"] = {"frame": "pcb-bbox-corner", "finalize_required": True,
                        "finalizer": "hyp_to_gds.py --update-chiplet-file"}
    assert _valid(doc)
    doc["_metadata"]["unknown"] = 1
    assert not _valid(doc)


def test_finalize_required_must_be_boolean():
    # A reader that took the string "false" as truthy would refuse a finalized
    # file; the schema keeps the string out of the format.
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["_metadata"] = {"finalize_required": "true"}
    assert not _valid(doc)


# --- (c) negatives: the root ----------------------------------------------
def test_unknown_root_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["future_block"] = {"knob": 7}
    assert not _valid(doc)


def test_missing_format_version_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc.pop("format_version")
    assert not _valid(doc)


def test_unquoted_format_version_is_rejected():
    # YAML `format_version: 1.0` parses to a float. PyYAML renders unquoted 1.10
    # as "1.1" and yaml-cpp as "1.10", so the unquoted spelling is not one value.
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["format_version"] = 1.0
    assert not _valid(doc)
    doc["format_version"] = "1.0"
    assert _valid(doc)


def test_malformed_format_version_is_rejected():
    for bad in ("1", "1.0.0", "v1.0", "1.x", ""):
        doc = copy.deepcopy(ALL_BLOCKS)
        doc["format_version"] = bad
        assert not _valid(doc), bad


def test_assembly_without_name_is_rejected():
    for mutate in (lambda a: a.pop("name"), lambda a: a.update(name="")):
        doc = copy.deepcopy(ALL_BLOCKS)
        mutate(doc["assembly"])
        assert not _valid(doc)


def test_unknown_assembly_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["assembly"]["units_um"] = 1
    assert not _valid(doc)


# --- (d) negatives: the interposer block ----------------------------------
def test_interposer_requires_an_adapter():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["interposer"] = {}
    assert not _valid(doc)
    doc["interposer"] = {"description": "intm4tm2"}
    assert not _valid(doc)


#: The exported adapter-id oracle. One file, run by every implementation's
#: parity test (adk_registry, the KiCad plugin, Mosaic, chiplet-studio), so the
#: proposition "rejects everything the schema rejects" is exercised on the same
#: set everywhere. The tests below prove the file against the schema itself.
ADAPTER_ID_CASES = json.loads(
    (FIXTURES / "adapter_id_cases.json").read_text(encoding="utf-8"))


def test_adapter_id_oracle_is_wellformed():
    acc, rej = ADAPTER_ID_CASES["accept"], ADAPTER_ID_CASES["reject"]
    assert acc and rej
    assert all(isinstance(x, str) for x in acc + rej)
    assert not set(acc) & set(rej)
    assert len(acc) == len(set(acc)) and len(rej) == len(set(rej))
    # The two defects that motivated the file must be in it by name: a deck
    # file name (accepted by a pattern-only implementation) and a trailing
    # newline (accepted by a $-anchored one).
    assert "evil.drc" in rej and "intm4tm2\n" in rej


@pytest.mark.parametrize("field", ["interposer", "interconnect"])
def test_adapter_oracle_rejects_are_rejected_by_the_schema(field):
    # A registry id the ADK resolves, never a filesystem path, never a deck
    # name. The trailing-newline entries are here because with a plain ``$``
    # anchor Python's re matches before a single trailing newline while an
    # ECMA-262 validator rejects it, so the schema meant two things depending
    # on who read it; the pattern ends in (?![\s\S]) for that reason.
    for bad in ADAPTER_ID_CASES["reject"]:
        doc = copy.deepcopy(ALL_BLOCKS)
        doc[field]["adapter"] = bad
        assert not _valid(doc), (field, bad)


@pytest.mark.parametrize("field", ["interposer", "interconnect"])
def test_adapter_oracle_accepts_are_accepted_by_the_schema(field):
    for good in ADAPTER_ID_CASES["accept"]:
        doc = copy.deepcopy(ALL_BLOCKS)
        doc[field]["adapter"] = good
        assert _valid(doc), (field, good)


def test_interposer_adapter_must_be_a_string():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["interposer"]["adapter"] = ["intm4tm2"]
    assert not _valid(doc)


def test_unknown_interposer_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["interposer"]["drc"] = "./intm4tm2.drc"
    assert not _valid(doc)


def test_interconnect_adapter_follows_the_same_rule():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["interconnect"]["adapter"] = "./adapters/ihp_cupillar.drc"
    assert not _valid(doc)
    doc["interconnect"].pop("adapter")
    assert not _valid(doc)


# --- (e) negatives: components --------------------------------------------
def test_component_with_unknown_anchor_is_rejected():
    # An unknown anchor silently changes where geometry lands, so it is a
    # structural error even though an ABSENT anchor is legal (reader defaults it
    # to bbox_center and warns).
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["anchor"] = "gds_bbox"
    assert not _valid(doc)
    doc["components"][0]["anchor"] = "GDS_ORIGIN"
    assert not _valid(doc)
    doc["components"][0].pop("anchor")
    assert _valid(doc)


def test_component_without_id_or_type_is_rejected():
    for key in ("id", "type"):
        doc = copy.deepcopy(ALL_BLOCKS)
        doc["components"][0].pop(key)
        assert not _valid(doc), key
        doc = copy.deepcopy(ALL_BLOCKS)
        doc["components"][0][key] = ""
        assert not _valid(doc), key


def test_component_type_outside_the_canonical_four_is_accepted():
    # Deliberately open: every reference reader accepts any non-empty type, so a
    # schema that rejected one would be stricter than the format.
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["type"] = "spacer"
    assert _valid(doc)


def test_component_with_unknown_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["z_offset"] = 3.0
    assert not _valid(doc)


def test_unknown_orientation_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][1]["orientation"] = "upside_down"
    assert not _valid(doc)


def test_position_is_closed_and_numeric():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["position"]["w"] = 1.0
    assert not _valid(doc)
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["position"]["x"] = "3246.156"
    assert not _valid(doc)


def test_negative_dimension_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["dimensions"]["thickness"] = -1.0
    assert not _valid(doc)


def test_array_count_below_one_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][2]["array"]["count"]["x"] = 0
    assert not _valid(doc)
    doc["components"][2]["array"]["count"]["x"] = 1.5
    assert not _valid(doc)


def test_io_pad_needs_an_id_and_a_position():
    for key in ("id", "position"):
        doc = copy.deepcopy(ALL_BLOCKS)
        doc["components"][0]["io_pads"][0].pop(key)
        assert not _valid(doc), key


def test_io_pad_position_needs_both_axes_and_no_z():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["io_pads"][0]["position"].pop("y")
    assert not _valid(doc)
    doc = copy.deepcopy(ALL_BLOCKS)
    # Pads are 2D points in the canonical frame; a z here would be a second,
    # unread mounting story.
    doc["components"][0]["io_pads"][0]["position"]["z"] = 0.0
    assert not _valid(doc)


# The contract statement. The schemas are checked AGAINST it, and the positive
# cases run over what the schemas declare, so a typo in an enum member, a
# member added to one schema and not the other, or a member added without a
# test all fail here rather than travelling.
IO_CLASSES = ("wire_bond", "flipped_bump", "tsv_bump")


def _declared_io_classes(schema_file):
    schema = json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8"))
    hits = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "io_class" and isinstance(value, dict) and "enum" in value:
                    hits.append(tuple(value["enum"]))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert len(hits) == 1, (schema_file, hits)
    return hits[0]


def test_both_schemas_declare_the_contract_io_classes():
    assert _declared_io_classes("chiplet.schema.json") == IO_CLASSES
    assert _declared_io_classes("io_pads.schema.json") == IO_CLASSES


@pytest.mark.parametrize("io_class", _declared_io_classes("chiplet.schema.json"))
def test_io_class_accepts_every_usage_class_the_emitters_enforce(io_class):
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["io_pads"][0]["io_class"] = io_class
    assert _valid(doc)


@pytest.mark.parametrize("io_class", ["probe_pad", "bump", "WIRE_BOND", "wire_bond\n", ""])
def test_io_class_is_a_closed_vocabulary(io_class):
    # Deliberate contract change (2026-09-05): this test used to assert the
    # OPPOSITE, with "probe_pad" as an arbitrary string proving the field was
    # free-form. Every governed emitter already refused anything outside the
    # three (KiCad exporter, plugin writer) and the C++ reader throws on an
    # unknown value, so a free-form schema validated documents that then
    # failed at load. The schema now says what the readers do.
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["components"][0]["io_pads"][0]["io_class"] = io_class
    assert not _valid(doc), io_class


def test_technology_dbu_must_be_positive():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["technologies"]["sg13g2"]["dbu"] = 0
    assert not _valid(doc)


def test_unknown_technology_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["technologies"]["sg13g2"]["lyp"] = "./tech/sg13g2.lyp"
    assert not _valid(doc)


def test_unknown_connection_stack_layer_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["connection_stacks"]["cupillar_opt1"]["layers"][0]["radius"] = 22.0
    assert not _valid(doc)


# --- (f) negatives: interfaces and netlist --------------------------------
def test_unknown_interface_type_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["interfaces"][0]["type"] = "hybrid_bond"
    assert not _valid(doc)


def test_interface_without_id_or_type_is_rejected():
    for key in ("id", "type"):
        doc = copy.deepcopy(ALL_BLOCKS)
        doc["interfaces"][0].pop(key)
        assert not _valid(doc), key


def test_net_without_name_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["netlist"]["nets"][0].pop("name")
    assert not _valid(doc)


def test_unknown_netlist_key_is_rejected():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["netlist"]["source"] = "./design.net"
    assert not _valid(doc)


def test_flow_content_is_never_validated():
    doc = copy.deepcopy(ALL_BLOCKS)
    doc["flow"] = {"anything": {"a host wrote": [1, 2, 3]}}
    assert _valid(doc)


# --- (g) cross-parse: schema vs the reference reader ----------------------
def test_schema_and_reader_agree_on_the_corpus_except_pinned_divergences():
    mismatches = []
    for path in CORPUS:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema_ok, reader_ok = _valid(data), _reader_accepts(data)
        pinned = DIVERGENCES.get(path.name)
        if pinned is None:
            if schema_ok != reader_ok:
                mismatches.append(
                    f"{path.name}: schema={schema_ok}, reader={reader_ok}; "
                    f"a NEW structure/semantics divergence. Either fix it or add "
                    f"it to DIVERGENCES with a reason.")
            continue
        if (schema_ok, reader_ok) != (pinned["schema"], pinned["reader"]):
            mismatches.append(
                f"{path.name}: schema={schema_ok}, reader={reader_ok}, pinned "
                f"{pinned['schema']}/{pinned['reader']}: {pinned['why']}")
    assert not mismatches, "\n".join(mismatches)


def test_every_pinned_entry_names_a_real_corpus_file():
    names = {p.name for p in CORPUS}
    assert set(DIVERGENCES) <= names, set(DIVERGENCES) - names
    assert set(SCHEMA_NEGATIVE) <= names, set(SCHEMA_NEGATIVE) - names
    # A divergence where the schema refuses must be listed as a schema negative,
    # so the two tables cannot tell different stories about one file.
    for name, pinned in DIVERGENCES.items():
        assert (not pinned["schema"]) == (name in SCHEMA_NEGATIVE), name


def test_schema_valid_documents_load_with_the_reference_reader():
    # The forward direction on its own, stated plainly: a schema-valid, same-major
    # document is loadable, EXCEPT where DIVERGENCES pins a deliberate semantic
    # refusal. Two kinds of exception are pinned there and they are different: a
    # version-policy refusal (a different major, also filtered by the major test
    # below) and a cross-field rule the schema cannot express (rule 8). Reading
    # the exceptions off DIVERGENCES rather than re-deriving them keeps this test
    # and the cross-parse test above telling one story.
    checked = 0
    for path in CORPUS:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not _valid(data):
            continue
        if not str(data["format_version"]).startswith(
                cfio.SUPPORTED_FORMAT_VERSION.split(".")[0] + "."):
            continue
        pinned = DIVERGENCES.get(path.name)
        if pinned is not None and pinned["reader"] is False:
            continue
        checked += 1
        assert _reader_accepts(data), path.name
    assert checked, "no schema-valid same-major document left to check"


# --- (h) the system block (format 1.1) -------------------------------------
def test_system_source_fixture_validates():
    assert _valid(SYSTEM_SOURCE), [e.message for e in _errors(SYSTEM_SOURCE)]


@pytest.mark.parametrize("block", list(cfio.DERIVED_BLOCKS))
def test_system_beside_a_derived_block_is_rejected(block):
    # A source names what the elaborator derives; a file that holds both has two
    # authorities for one fact.
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc[block] = copy.deepcopy(ALL_BLOCKS[block])
    assert not _valid(doc)


def test_derived_blocks_match_the_schema_root():
    assert set(cfio.DERIVED_BLOCKS) < set(CHIPLET_SCHEMA["properties"])
    assert not {"format_version", "assembly", "interposer", "flow", "system",
                "_metadata"} & set(cfio.DERIVED_BLOCKS)


def test_system_requires_finalize_required_true():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    del doc["_metadata"]
    assert not _valid(doc)
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["_metadata"]["finalize_required"] = False
    assert not _valid(doc)


def test_system_needs_a_carrier_and_dies():
    for key in ("carrier", "dies"):
        doc = copy.deepcopy(SYSTEM_SOURCE)
        del doc["system"][key]
        assert not _valid(doc), key
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["dies"] = []
    assert not _valid(doc)


def test_unknown_system_key_is_rejected():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["nets"] = []
    assert not _valid(doc)


def test_die_rotation_is_a_right_angle():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["dies"][0]["rotation"] = 45
    assert not _valid(doc)


@pytest.mark.parametrize("path", ["/abs/die", "../die", "a/../b", "${HOME}/die"])
def test_system_paths_are_relative_and_literal(path):
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["dies"][0]["die"] = path
    assert not _valid(doc), path


def test_a_port_ref_names_an_instance_and_a_port():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["alone"] = ["STATUS"]
    assert not _valid(doc)


def test_rail_class_is_closed():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["rails"][0]["class"] = "signal"
    assert not _valid(doc)


def test_an_edge_outside_the_four_is_rejected():
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["system"]["external"]["edges"]["top"] = ["U1.CLK"]
    assert not _valid(doc)


# --- (i) the unplaced source (format 1.3) ----------------------------------
def _unplaced_pins(system, doc):
    doc["unplaced"] = {"pins": system.pop("alone")}


#: Each thing a 1.3 source may leave to placement, as an edit of SYSTEM_SOURCE.
LEFT_OPEN = {
    "no dies": lambda s, d: s.pop("dies"),
    "empty dies": lambda s, d: s.update(dies=[]),
    "a die with no at": lambda s, d: [s["dies"][0].pop(k) for k in ("at", "rotation")],
    "a carrier with no size": lambda s, d: s["carrier"].pop("size"),
    "external with no edges": lambda s, d: s["external"].pop("edges"),
    "the root unplaced": _unplaced_pins,
}
#: Edits refused at every stamp, by the schema and the reader alike.
NEVER = {
    "a rotation with no at": lambda s, d: s["dies"][0].pop("at"),
    "unplaced with no external": lambda s, d: (_unplaced_pins(s, d), s.pop("external")),
    "unplaced with no pins": lambda s, d: d.update(unplaced={"pins": []}),
    "unplaced as a list": lambda s, d: d.update(unplaced=["U2.STATUS"]),
}


@pytest.mark.filterwarnings("ignore::chiplet_format_io.FormatVersionWarning")
@pytest.mark.parametrize("stamp", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"])
@pytest.mark.parametrize("edit", sorted(LEFT_OPEN) + sorted(NEVER))
def test_schema_and_reader_agree_on_what_a_stamp_lets_a_source_leave_open(edit, stamp):
    """The schema's 1.3 branch and the reference reader give one verdict on
    each thing a source may leave to placement, at each stamp: refused below
    1.3, read from 1.3 on; and on the edits refused at every stamp."""
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["format_version"] = stamp
    {**LEFT_OPEN, **NEVER}[edit](doc["system"], doc)
    expected = edit in LEFT_OPEN and \
        cfio.parse_format_version(stamp) >= cfio.parse_format_version(cfio.UNPLACED_SINCE)
    assert (_valid(doc), _reader_accepts(doc)) == (expected, expected)


@pytest.mark.filterwarnings("ignore::chiplet_format_io.FormatVersionWarning")
@pytest.mark.parametrize("stamp", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"])
@pytest.mark.parametrize("admission, shaped", [
    ("admission.json", True), ("admissions/U1.json", True), ("C:/admission.json", True),
    ("dies\\admission.json", True), ("x:a.json", True), ("\\\\srv\\a.json", True),
    ("admission.yaml", False), ("../admission.json", False), ("dies/../a.json", False),
    ("/abs/admission.json", False), ("${HOME}/a.json", False), ("", False), (3, False),
])
def test_schema_and_reader_agree_on_a_die_s_admission(admission, shaped, stamp):
    """A die row's admission (format 1.4): the schema and the reference reader
    refuse it below 1.4, and from 1.4 read it when it names a relative .json
    file, at each stamp. The forms a source reader may refuse on its own
    (drive letters, backslashes) are in the list, so the two rules stay one."""
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["format_version"] = stamp
    doc["system"]["dies"][0]["admission"] = admission
    expected = shaped and \
        cfio.parse_format_version(stamp) >= cfio.parse_format_version("1.4")
    assert (_valid(doc), _reader_accepts(doc)) == (expected, expected)


@pytest.mark.filterwarnings("ignore::chiplet_format_io.FormatVersionWarning")
@pytest.mark.parametrize("stamp", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"])
@pytest.mark.parametrize("rail, requirement, shaped", [
    (1, {"width_um": 20}, True), (1, {"width_um": 0.5}, True),
    (0, {"width_um": 20}, True), (1, {"amps": 1.5, "current_density_ma_per_um": 75}, True),
    (1, {"amps": 1.5}, False), (1, {"current_density_ma_per_um": 75}, False),
    (1, {"width_um": 20, "amps": 1.5}, False),
    (1, {"width_um": 20, "current_density_ma_per_um": 75}, False),
    (1, {"width_um": 20, "amps": 1.5, "current_density_ma_per_um": 75}, False),
    (1, {"width_um": 0}, False), (1, {"width_um": -1}, False), (1, {"width_um": "20"}, False),
    (1, {"width_um": True}, False), (1, {"amps": 0, "current_density_ma_per_um": 75}, False),
    (1, {"amps": 1.5, "current_density_ma_per_um": -1}, False),
])
def test_schema_and_reader_agree_on_a_rail_s_requirement(rail, requirement, shaped, stamp):
    """A rail's routing requirement (format 1.5), on the ground rail (0) and
    the power rail (1): the schema and the reference reader refuse it below
    1.5, and from 1.5 read a width or a current with its density, each
    positive. Non-finite values are the reader's alone, since a schema cannot
    say finite, and are in its own tests."""
    doc = copy.deepcopy(SYSTEM_SOURCE)
    doc["format_version"] = stamp
    doc["system"]["rails"][rail].update(requirement)
    expected = shaped and \
        cfio.parse_format_version(stamp) >= cfio.parse_format_version("1.5")
    assert (_valid(doc), _reader_accepts(doc)) == (expected, expected)
