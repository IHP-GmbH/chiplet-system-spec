# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Gate for SPEC-31: the spec's ``position`` prose must not outrank its contract.

``docs/CHIPLET_FORMAT_SPEC.md`` used to say, twice and without a condition, that
``position`` is the component's geometric center. That holds only for
``anchor: bbox_center``. ``docs/coord_frame_contract.md`` section 2.1, which the
very same section of the spec names as the source of truth for the anchor
convention, places a ``gds_origin`` component's own GDS (0, 0) at ``position``
with no extra centering. The two texts contradicted each other one paragraph
apart, and the wrong one was the one a reader meets first: a reader implemented
from the prose alone puts every ``gds_origin`` component half its own extent
away. That is not hypothetical, it was found and closed in a real consumer
(interposer-pnr's ``boundary_rect``).

What a green here does NOT cover (META-2): that any reader IMPLEMENTS the
contract correctly. This file compares two documents to each other and runs no
reader. It also cannot see a third document repeating the unconditional claim;
it gates the two sites that carried it.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

SPEC = (ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md").read_text(encoding="utf-8")
CONTRACT = (ROOT / "docs" / "coord_frame_contract.md").read_text(encoding="utf-8")


def test_the_contract_still_says_what_the_spec_was_corrected_against():
    """The correction is only right while its authority says this.

    Verified at the level it could be false: a gate reading only the spec would
    stay green if the CONTRACT moved and left the spec correct about nothing.
    """
    assert "The `position:` value is added to the GDS-origin of the cell" in CONTRACT
    assert "no extra centering" in CONTRACT
    assert "`position:` places the bbox center" in CONTRACT


def test_the_spec_never_claims_the_geometric_centre_unconditionally():
    for sentence in ("component's **geometric center**, not its corner",
                     "3D position of the component's **geometric center**"):
        assert sentence not in SPEC, sentence


def test_both_position_sites_name_the_anchor_that_decides():
    # The field table entry.
    table = [ln for ln in SPEC.split("\n") if ln.startswith("| `position` |")]
    assert len(table) == 1, table
    row = table[0]
    assert "`anchor:`" in row and "bbox_center" in row and "gds_origin" in row

    # The normative Frame bullet.
    assert "The frame fixes the" in SPEC and "it does not fix which point" in SPEC
    frame = SPEC.split("- **Frame.**", 1)[1].split("- **`anchor:`**", 1)[0]
    for token in ("bbox_center", "gds_origin", "no extra centering"):
        assert token in frame, token


def test_no_governed_text_claims_a_reference_reader_refuses_an_io_class():
    """Both panel members found this clause independently, in the schema.

    `schemas/chiplet.schema.json` justified closing the io_class vocabulary partly
    with "the C++ reader throws on an unknown value". Measured false: the C++
    reference stores it verbatim and rule 8 skips a pad whose class has no row,
    and the Python reference does the same. An earlier slice corrected the same
    falsehood in the spec prose and missed this copy, which is why it is gated
    rather than merely fixed: it is the one sentence in the tree that would
    justify someone re-adding a refusal here.

    What a green here does NOT cover: whether the readers still behave that way.
    This reads text. The behaviour is the conformance suite's and the panel's
    carry-not-refuse ruling owns changing it.
    """
    import json
    schema_text = (ROOT / "schemas" / "chiplet.schema.json").read_text(encoding="utf-8")
    assert "the C++ reader throws on an unknown value" not in schema_text
    json.loads(schema_text)  # the edit must leave the schema parseable
    io_class = json.loads(schema_text)["definitions"]["io_pad"]["properties"]["io_class"]
    assert io_class["enum"] == ["wire_bond", "flipped_bump", "tsv_bump"]
    assert "neither does" in io_class["description"]


def test_no_governed_text_says_a_reference_reader_refuses_an_interface_type():
    """The SPEC-32 half of the same defect, gated the same way.

    Three sentences said the readers enforce the closed interfaces[].type
    vocabulary: the schema's own type description ("an unknown interface type is
    rejected, by this schema and by both reference validators"), the spec's rule
    4 ("Both reference validators enforce it") and the "the reference readers
    accept it ahead of the 1.1 stamp" gloss on solder_bump, which stated the
    prohibition in terms of what a READER knows rather than what a DOCUMENT may
    carry, so the same bytes were valid or invalid according to which binary
    opened them. The panel ruling reverses all three: the vocabulary binds
    WRITERS and is enforced by the schema, the readers carry the string, and a
    consumer refuses the ELEMENT.

    Gated rather than merely fixed for the reason the io_class clause above is:
    each of these is a sentence that would justify someone re-adding a refusal.

    What a green here does NOT cover: the readers' behaviour, which is
    conformance/test_unknown_vocabulary_roundtrip.py and the C++ binary's own
    cross product over the same oracle.
    """
    import json
    schema_text = (ROOT / "schemas" / "chiplet.schema.json").read_text(
        encoding="utf-8")
    for governed in (SPEC, schema_text):
        assert "ahead of the 1.1 stamp" not in governed
        assert "rejected, by this schema" not in governed
    assert "Both reference validators enforce it" not in SPEC
    json.loads(schema_text)
    iface = json.loads(schema_text)["definitions"]["interface"]["properties"]["type"]
    # The vocabulary is still CLOSED in the schema: the ruling moved who
    # enforces it, not whether it exists. A green that let the enum go would be
    # the opposite mistake.
    assert iface["enum"] == list(cfio.KNOWN_INTERFACE_TYPES)
    assert "binds WRITERS" in SPEC


def test_the_closed_vocabulary_list_in_the_prose_is_complete():
    """The prose listed three closed vocabularies where the schema closes four.

    Both panel members reached the same place from it: the defect is not a
    missing criterion, it is artifacts violating the criterion they already have,
    and a list that quietly omits io_class is how one of them got away with it.
    """
    section = SPEC.split("### Machine-readable schema", 1)[1].split("---", 1)[0]
    for vocabulary in ("`anchor`", "`orientation`", "`interfaces[].type`",
                       "`io_pads[].io_class`"):
        assert vocabulary in section, vocabulary


def test_the_anchor_rule_covers_a_die_array():
    """SPEC-30: `anchor` applies per element, including inside a `die_array`.

    The gap this closes was invisible in exactly the way the position one was.
    The frame contract, which the spec names as the source of truth for the
    anchor convention, defined the anchor for a component's own mesh and never
    mentioned `die_array` or `array.start_position`, so both readings of
    `start_position` (the first element's centre, or its GDS origin) were
    admissible and consumers had silently picked one. Three interposer-pnr tests
    declared `anchor: gds_origin` on an array and then asserted the CENTRE
    reading, so fixture data had promoted one reading to a specification nobody
    wrote.

    Option (a) is the only reading under which `anchor` keeps ONE definition:
    `start_position` is a `$ref` to the same `position3d` as `position`, and the
    contract's rule attaches to that definition. The alternative would make
    `anchor` the one field whose effect depends on the component's type.

    What a green here does NOT cover (META-2): whether any reader implements it.
    This compares documents. interposer-pnr's own warning on an array declaring
    `gds_origin` is untouched by this commit and is that repository's row.
    """
    for token in ("per element", "start_position"):
        assert token in CONTRACT, token
    anchor_section = CONTRACT.split("### 2.1 The `anchor:` field", 1)[1].split(
        "### 2.2", 1)[0]
    assert "start_position` places the FIRST element's anchor point" \
        in anchor_section
    assert "die_array" in anchor_section
    # And the spec's own array section carries it, because that is where a
    # writer of an array looks and it must not have to find the contract first.
    array_section = SPEC.split(
        "### Array Configuration (for `die_array` type)", 1)[1].split(
            "### Coordinate frame", 1)[0]
    assert "anchor" in array_section and "per element" in array_section


def test_no_committed_document_declares_gds_origin_on_an_array():
    """The compatibility half of the ruling, kept executable.

    With the population at two (this repository's all-blocks fixture and a studio
    fixture that declares no anchor), the clarification reinterprets zero
    documents. That is only true while it stays true: a fixture that declares
    `gds_origin` on a `die_array` would be a document whose meaning the ruling
    changed after the fact, and the right response is to decide that on purpose
    rather than to discover it.
    """
    import yaml
    corpus = sorted((ROOT / "conformance" / "fixtures").glob("*.chiplet")) + \
        sorted((ROOT / "examples").glob("*.chiplet"))
    offenders = []
    for path in corpus:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for comp in doc.get("components") or []:
            if not isinstance(comp, dict):
                continue
            if comp.get("type") == "die_array" and \
                    comp.get("anchor") == "gds_origin":
                offenders.append(f"{path.name}:{comp.get('id')}")
    assert not offenders, offenders
