# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the committed geometry schemas (pins, black-box padmap, io_pads).

This is NOT part of the .chiplet reader-parity corpus (run_conformance.py /
manifest.yaml). It proves those JSON Schemas are well-formed, that the canonical
example instances validate, that every documented negative is rejected, and that the
artifacts never cross-parse (a pins.json is not a padmap and vice versa).

Section (e) covers the rest of the directory. Deep coverage is per-artifact and only
three files have it, but well-formedness is not a matter of taste, so EVERY committed
schema is held to it. Without that, a file could sit in ``schemas/`` for months
declaring a dialect no validator accepts and nothing would say so: a governed-looking
directory whose governance reaches a minority of its contents is worse than an
ungoverned one, because readers trust it.

jsonschema is a HARD import here on purpose: a gate that silently skips itself when
its validator is missing is not a gate. The validator class is chosen by
``validator_for`` off each file's own ``$schema``, never hardcoded: the directory is
deliberately mixed, some files declaring draft-07 and some 2020-12, and which spelling
each one uses is a live question (DEEP_REVIEW_CLOSURE SEAM-8b), not something this
gate should pre-empt by assuming a dialect.
"""
import copy
import json
from pathlib import Path

import jsonschema
from jsonschema.validators import validator_for

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
EXAMPLES = SCHEMAS / "examples"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


PINS_SCHEMA = _load(SCHEMAS / "pins.schema.json")
PADMAP_SCHEMA = _load(SCHEMAS / "blackbox_padmap.schema.json")
IO_PADS_SCHEMA = _load(SCHEMAS / "io_pads.schema.json")
PINS_EXAMPLE = _load(EXAMPLES / "pins.example.json")
PADMAP_EXAMPLE = _load(EXAMPLES / "blackbox_padmap.example.json")
IO_PADS_BOARD_ABS_EXAMPLE = _load(EXAMPLES / "io_pads.board_absolute.example.json")
IO_PADS_CANONICAL_EXAMPLE = _load(EXAMPLES / "io_pads.canonical.example.json")


def _validator(schema: dict):
    """The draft-appropriate validator for ``schema`` (draft-07 for these files),
    picked off its ``$schema`` so the 2020-12 dialect is never assumed."""
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _valid(schema: dict, instance) -> bool:
    return _validator(schema).is_valid(instance)


# --- (a) both schemas are well-formed draft-07 -----------------------------
def test_both_schemas_are_wellformed_draft07():
    for schema in (PINS_SCHEMA, PADMAP_SCHEMA, IO_PADS_SCHEMA):
        cls = validator_for(schema)
        assert cls is jsonschema.Draft7Validator  # the files declare draft-07
        cls.check_schema(schema)  # SchemaError on a malformed schema


# --- (b) the canonical example instances validate --------------------------
def test_pins_example_validates():
    assert _valid(PINS_SCHEMA, PINS_EXAMPLE)


def test_padmap_example_validates():
    assert _valid(PADMAP_SCHEMA, PADMAP_EXAMPLE)


# --- (c) negatives: mutate a valid example, assert it is rejected -----------
def test_pins_pin_review_artifact_is_rejected():
    # A name-review artifact (source_type pin_review, no dbu_um, no per-pin
    # coordinates) must fail the GEOMETRY schema: that separation is the point.
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["source_type"] = "pin_review"
    doc.pop("dbu_um")
    for pin in doc["pins"]:
        pin.pop("center_x_dbu", None)
        pin.pop("center_y_dbu", None)
    assert not _valid(PINS_SCHEMA, doc)


def test_pins_pin_without_pad_index_is_rejected():
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["pins"][0].pop("pad_index")
    assert not _valid(PINS_SCHEMA, doc)


def test_pins_pin_without_name_is_rejected():
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["pins"][0].pop("name")
    assert not _valid(PINS_SCHEMA, doc)


def test_pins_without_version_is_rejected():
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc.pop("version")
    assert not _valid(PINS_SCHEMA, doc)


def test_pins_zero_dbu_is_rejected():
    doc = copy.deepcopy(PINS_EXAMPLE)
    doc["dbu_um"] = 0
    assert not _valid(PINS_SCHEMA, doc)


def test_padmap_three_element_bbox_is_rejected():
    doc = copy.deepcopy(PADMAP_EXAMPLE)
    doc["die"]["bbox_um"] = doc["die"]["bbox_um"][:3]
    assert not _valid(PADMAP_SCHEMA, doc)


def test_padmap_pad_without_x_um_is_rejected():
    doc = copy.deepcopy(PADMAP_EXAMPLE)
    doc["pads"][0].pop("x_um")
    assert not _valid(PADMAP_SCHEMA, doc)


def test_padmap_without_die_is_rejected():
    doc = copy.deepcopy(PADMAP_EXAMPLE)
    doc.pop("die")
    assert not _valid(PADMAP_SCHEMA, doc)


# --- (d) the two artifacts never cross-parse -------------------------------
def test_pins_example_fails_the_padmap_schema():
    assert not _valid(PADMAP_SCHEMA, PINS_EXAMPLE)


def test_padmap_example_fails_the_pins_schema():
    assert not _valid(PINS_SCHEMA, PADMAP_EXAMPLE)


# --- io_pads manifest: positives -------------------------------------------
def test_io_pads_board_absolute_example_validates():
    assert _valid(IO_PADS_SCHEMA, IO_PADS_BOARD_ABS_EXAMPLE)


def test_io_pads_canonical_example_validates():
    assert _valid(IO_PADS_SCHEMA, IO_PADS_CANONICAL_EXAMPLE)


def test_io_pads_empty_array_validates():
    # The canonicalizer always writes the file, so an empty io_pads is the loud
    # "board yielded 0" signal (distinct from an absent file), and must validate.
    doc = copy.deepcopy(IO_PADS_CANONICAL_EXAMPLE)
    doc["io_pads"] = []
    assert _valid(IO_PADS_SCHEMA, doc)


# --- io_pads manifest: negatives -------------------------------------------
def test_io_pads_missing_schema_const_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc.pop("schema")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_wrong_schema_const_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["schema"] = "adk-boundary-manifest"
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_missing_version_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc.pop("version")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_malformed_version_is_rejected():
    # The schema gates the SHAPE of the version (quoted MAJOR.MINOR(.PATCH)), not
    # which versions a consumer accepts: that is the shared policy applied by
    # chiplet_format_io.check_contract_version, gated in test_version_policy.py.
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["version"] = "1"
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_missing_frame_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc.pop("frame")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_unknown_frame_is_rejected():
    # Consumers key on frame and hard-refuse; an unlisted frame must never validate.
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["frame"] = "hyp_absolute"
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_pad_without_ref_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"][0].pop("ref")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_empty_ref_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"][0]["ref"] = ""
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_zero_size_is_rejected():
    # An unresolvable pad size is an emitter error, never a 0.0 entry.
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"][0]["size_x_um"] = 0
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_pad_without_layer_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"][0].pop("layer")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_no_net_pad_is_valid_but_missing_net_is_not():
    # net "" is legal (a no-net pad); only a MISSING net key is rejected.
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"][0]["net"] = ""
    assert _valid(IO_PADS_SCHEMA, doc)
    doc["io_pads"][0].pop("net")
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_object_instead_of_array_is_rejected():
    doc = copy.deepcopy(IO_PADS_BOARD_ABS_EXAMPLE)
    doc["io_pads"] = {}
    assert not _valid(IO_PADS_SCHEMA, doc)


def test_io_pads_frame_origin_missing_key_is_rejected():
    doc = copy.deepcopy(IO_PADS_CANONICAL_EXAMPLE)
    doc["frame_origin_board_um"].pop("y")
    assert not _valid(IO_PADS_SCHEMA, doc)


# --- io_pads never cross-parses with pins or padmap ------------------------
def test_io_pads_example_fails_pins_and_padmap_schemas():
    assert not _valid(PINS_SCHEMA, IO_PADS_BOARD_ABS_EXAMPLE)
    assert not _valid(PADMAP_SCHEMA, IO_PADS_BOARD_ABS_EXAMPLE)


def test_pins_and_padmap_examples_fail_the_io_pads_schema():
    assert not _valid(IO_PADS_SCHEMA, PINS_EXAMPLE)
    assert not _valid(IO_PADS_SCHEMA, PADMAP_EXAMPLE)


# --- (e) every committed schema is well-formed under its own dialect -------
def _committed_schemas():
    """Every ``*.schema.json`` in ``schemas/``, discovered, never hand-listed.

    A hand-list is how the previous coverage gap happened: three files were named
    at the top of this module and the other six joined the directory unnoticed.
    """
    return sorted(SCHEMAS.glob("*.schema.json"))


def test_the_directory_is_not_empty():
    # Guards the glob itself: a typo'd path would make every test below vacuous.
    assert len(_committed_schemas()) >= 7


def test_every_committed_schema_declares_a_dialect():
    # validator_for falls back to the latest draft when $schema is absent, so an
    # undeclared file is silently validated against a dialect its author never
    # chose. Declaring it is the cheapest thing a schema can do.
    undeclared = [p.name for p in _committed_schemas()
                  if "$schema" not in json.loads(p.read_text(encoding="utf-8"))]
    assert undeclared == []


def test_every_committed_schema_is_wellformed():
    for path in _committed_schemas():
        schema = _load(path)
        cls = validator_for(schema)
        cls.check_schema(schema)  # SchemaError names the offending file


def test_non_schema_files_are_not_schemas_in_disguise():
    # chiplet_pads.json is a data vocabulary that lives here on purpose. The
    # naming carries that distinction, so nothing may claim to be a schema
    # without the name that puts it under the checks above.
    for path in sorted(SCHEMAS.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert "$schema" not in doc, path.name
        assert "properties" not in doc, path.name
