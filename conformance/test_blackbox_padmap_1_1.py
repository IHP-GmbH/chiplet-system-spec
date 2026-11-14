# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the black-box pad map at 1.1 and the partner page.

Version 1.1 of ``schemas/blackbox_padmap.schema.json`` adds the keys a die owner
states when the pad map is handed over as a partner package: net, direction,
role and port per pad, the die thickness, the process ids, the supply volts, a
signoff statement and two references to outside descriptions of the same die.
Every added key is optional at the schema level, so every 1.0 file on disk stays
valid; the writer that turns a package into a ready die is the reader that
requires them. What the schema does hold is the shape of each added key: the two
closed vocabularies, a non-negative volts value, a 64-digit lowercase digest.

The partner page ``docs/partner_die_package.md`` names the keys in two tables.
A table that names a key the schema does not have is a page that has drifted
from the schema, so the first column of each is parsed and resolved here.

What a green here does NOT cover: the rules that span keys (every power and
ground net named in ``supply_volts`` and nothing else, ground at 0, power above
0, ``port`` and ``ordinal`` both or neither, unique pad names, pads inside the
box). Those are the writer's, as the die contract schema says of its own.
"""
import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "blackbox_padmap.schema.json"
EXAMPLE_PATH = ROOT / "schemas" / "examples" / "blackbox_padmap.example.json"
PACKAGE_PATH = ROOT / "conformance" / "fixtures" / "blackbox_padmap" / "fxdie.blackbox.json"
PAGE_PATH = ROOT / "docs" / "partner_die_package.md"

KEY_TABLE_HEADING = "What the package states"
MAPPING_TABLE_HEADING = "Mapping against CDXML, 3Dblox and LEF"
FIXTURE_IDS = ("fxdie", "fx-pdk", "fxtech")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMA = _load(SCHEMA_PATH)
EXAMPLE_1_0 = _load(EXAMPLE_PATH)
PACKAGE = _load(PACKAGE_PATH)


def _valid(instance) -> bool:
    cls = validator_for(SCHEMA)
    cls.check_schema(SCHEMA)
    return cls(SCHEMA).is_valid(instance)


def _package():
    return copy.deepcopy(PACKAGE)


# --- the page, parsed once ---------------------------------------------------
def _sections(text: str) -> dict:
    """Heading text -> the lines under it, up to the next heading of any level."""
    sections, current = {}, None
    for line in text.splitlines():
        found = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if found:
            current = found.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _first_table_first_column(lines) -> list:
    """The first column of the first Markdown table in ``lines``, header excluded."""
    rows = []
    for line in lines:
        if line.lstrip().startswith("|"):
            rows.append(line)
        elif rows:
            break
    assert len(rows) >= 3, "no table with a header, a rule and a row"
    cells = [row.strip().strip("|").split("|")[0].strip() for row in rows]
    assert re.fullmatch(r":?-{3,}:?", cells[1]), "the second row is not a rule"
    return cells[2:]


def _keys_in(cells) -> list:
    keys = []
    for cell in cells:
        keys.extend(re.findall(r"`([^`]+)`", cell))
    return keys


def _resolves(schema: dict, path: str) -> bool:
    """True when the dotted ``path`` (``pads[].name``) names a declared property."""
    node = schema
    for part in path.split("."):
        is_array = part.endswith("[]")
        name = part[:-2] if is_array else part
        properties = node.get("properties", {})
        if name not in properties:
            return False
        node = properties[name]
        if is_array:
            if "items" not in node:
                return False
            node = node["items"]
    return True


PAGE_TEXT = PAGE_PATH.read_text(encoding="utf-8") if PAGE_PATH.is_file() else ""
PAGE_SECTIONS = _sections(PAGE_TEXT)


# --- positives ---------------------------------------------------------------
def test_the_schema_stays_draft07_and_wellformed():
    cls = validator_for(SCHEMA)
    assert SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"
    cls.check_schema(SCHEMA)


def test_the_fixture_package_validates():
    assert PACKAGE["version"] == "1.1"
    assert _valid(PACKAGE)


def test_a_1_0_map_still_validates():
    assert "version" not in EXAMPLE_1_0
    assert _valid(EXAMPLE_1_0)
    stamped = copy.deepcopy(EXAMPLE_1_0)
    stamped["version"] = "1.0"
    assert _valid(stamped)


def test_every_added_key_is_optional_at_the_schema_level():
    # The top level still asks only for what 1.0 asked for.
    assert SCHEMA["required"] == ["die", "pads"]
    assert SCHEMA["properties"]["die"]["required"] == ["bbox_um"]
    assert SCHEMA["properties"]["pads"]["items"]["required"] == ["name", "x_um", "y_um"]
    bare = _package()
    for key in ("process", "supply_volts", "signoff", "cdxml_ref", "3dblox_ref"):
        bare.pop(key, None)
    bare["die"].pop("thickness_um")
    for pad in bare["pads"]:
        for key in ("net", "direction", "role", "port", "ordinal"):
            pad.pop(key, None)
    assert _valid(bare)


def test_the_objects_stay_open_to_unknown_keys():
    assert SCHEMA["additionalProperties"] is True
    assert SCHEMA["properties"]["die"]["additionalProperties"] is True
    assert SCHEMA["properties"]["pads"]["items"]["additionalProperties"] is True


DIE_CONTRACT_PATH = ROOT / "schemas" / "die_contract.schema.json"


@pytest.mark.parametrize("key", ["role", "direction"])
def test_the_pad_vocabularies_equal_the_die_contract_s(key):
    """The pad's role and direction words are the die contract's closed sets,
    not a copy that may drift from them."""
    terminal = _load(DIE_CONTRACT_PATH)["properties"]["terminals"]["items"]["properties"]
    pad = SCHEMA["properties"]["pads"]["items"]["properties"]
    assert pad[key]["enum"] == terminal[key]["enum"]


def test_the_version_description_names_1_1_as_supported():
    text = SCHEMA["properties"]["version"]["description"]
    assert "Supported: 1.1" in text
    assert "absent version means 1.0" in text


def test_the_two_references_validate_with_lowercase_digests():
    doc = _package()
    digest = "ab" * 32
    doc["cdxml_ref"] = {"mpn": "part-0001", "opn": "part-0001-t", "version": "1.0",
                        "uri": "refs/part.xml", "sha256": digest}
    doc["3dblox_ref"] = {"chiplet": "die_def", "uri": "refs/die.3dbv", "sha256": digest}
    assert _valid(doc)


# --- negatives at the schema -------------------------------------------------
@pytest.mark.parametrize("role", ["SIGNAL", "Power", "supply", "nc", ""])
def test_a_role_outside_the_set_refuses(role):
    doc = _package()
    doc["pads"][0]["role"] = role
    assert not _valid(doc)


@pytest.mark.parametrize("direction", ["inout", "INPUT", "in", "bidirectional", ""])
def test_a_direction_outside_the_set_refuses(direction):
    doc = _package()
    doc["pads"][0]["direction"] = direction
    assert not _valid(doc)


@pytest.mark.parametrize("volts", [-1.2, -0.001])
def test_a_negative_volts_refuses(volts):
    doc = _package()
    doc["supply_volts"]["VDD"] = volts
    assert not _valid(doc)


def test_a_volts_that_is_not_a_number_refuses():
    doc = _package()
    doc["supply_volts"]["VDD"] = "1.2"
    assert not _valid(doc)


@pytest.mark.parametrize("digest", [
    "0123456789abcdef" * 4 + "0",                 # 65 digits
    ("0123456789abcdef" * 4)[:-1],                # 63 digits
    "0123456789ABCDEF" * 4,                       # uppercase
    "0123456789abcdeg" * 4,                       # not hex
    "0123456789abcdef" * 4 + "\n",               # a trailing newline
])
def test_a_signoff_digest_of_the_wrong_shape_refuses(digest):
    doc = _package()
    doc["signoff"]["layout_sha256"] = digest
    assert not _valid(doc)


@pytest.mark.parametrize("ref,body", [
    ("cdxml_ref", {"mpn": "part-0001"}),
    ("3dblox_ref", {"chiplet": "die_def"}),
])
@pytest.mark.parametrize("digest", ["AB" * 32, "ab" * 31])
def test_a_reference_digest_of_the_wrong_shape_refuses(ref, body, digest):
    doc = _package()
    doc[ref] = dict(body, sha256=digest)
    assert not _valid(doc)


@pytest.mark.parametrize("field,value", [
    ("check", "erc"),
    ("check", "DRC"),
    ("result", "passed"),
    ("result", "waived"),
])
def test_a_check_or_result_outside_the_set_refuses(field, value):
    doc = _package()
    doc["signoff"]["checks"][0][field] = value
    assert not _valid(doc)


@pytest.mark.parametrize("thickness", [0, -300.0, "300"])
def test_a_thickness_that_is_not_positive_refuses(thickness):
    doc = _package()
    doc["die"]["thickness_um"] = thickness
    assert not _valid(doc)


@pytest.mark.parametrize("ordinal", [-1, 0.5, "0"])
def test_an_ordinal_that_is_not_a_non_negative_integer_refuses(ordinal):
    doc = _package()
    doc["pads"][3]["ordinal"] = ordinal
    assert not _valid(doc)


@pytest.mark.parametrize("key", ["pdk", "pdk_version", "technology"])
def test_an_empty_process_id_refuses(key):
    doc = _package()
    doc["process"][key] = ""
    assert not _valid(doc)


def test_an_empty_issuer_refuses():
    doc = _package()
    doc["signoff"]["issued_by"] = ""
    assert not _valid(doc)


# --- the partner page against the schema -------------------------------------
def test_the_page_has_both_tables():
    assert PAGE_PATH.is_file(), PAGE_PATH
    assert KEY_TABLE_HEADING in PAGE_SECTIONS
    assert MAPPING_TABLE_HEADING in PAGE_SECTIONS


def test_every_key_the_mapping_table_names_exists_in_the_schema():
    keys = _keys_in(_first_table_first_column(PAGE_SECTIONS[MAPPING_TABLE_HEADING]))
    # The parse reached the table: a green over an empty list would prove nothing.
    for expected in ("chiplet_name", "pads[].role", "supply_volts", "signoff"):
        assert expected in keys, expected
    assert [key for key in keys if not _resolves(SCHEMA, key)] == []


def test_every_key_the_key_table_names_exists_in_the_schema():
    keys = _keys_in(_first_table_first_column(PAGE_SECTIONS[KEY_TABLE_HEADING]))
    for expected in ("version", "die.thickness_um", "signoff.checks[].result"):
        assert expected in keys, expected
    assert [key for key in keys if not _resolves(SCHEMA, key)] == []


def test_the_resolver_refuses_a_key_the_schema_lacks():
    # Capability, so the two subset tests above cannot pass vacuously.
    assert _resolves(SCHEMA, "pads[].name")
    assert not _resolves(SCHEMA, "pads[].supply_domain")
    assert not _resolves(SCHEMA, "die.bbox_um[].x")


def test_the_page_example_is_a_valid_1_1_package_with_placeholder_names():
    blocks = re.findall(r"```json\n(.*?)\n```", PAGE_TEXT, flags=re.S)
    assert len(blocks) == 1
    example = json.loads(blocks[0])
    assert example["version"] == "1.1"
    assert _valid(example)


def test_the_page_never_names_the_test_fixture_ids():
    assert PAGE_TEXT
    for name in FIXTURE_IDS:
        assert name not in PAGE_TEXT, name
