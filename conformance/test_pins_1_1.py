# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""pins.json 1.1: role and direction are declared, type is deprecated.

The schema's role and direction words are the reader's vocabulary, in exact
capitals, and each maps onto the die contract's closed sets. A 1.0 file, which
may carry type alone, still validates; a mixed-case word does not.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

from chiplet_format_io import pins

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "pins.schema.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "schemas" / "examples" / "pins.example.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((ROOT / "schemas" / "die_contract.schema.json").read_text(encoding="utf-8"))
PIN = SCHEMA["properties"]["pins"]["items"]["properties"]


def _valid(instance) -> bool:
    cls = validator_for(SCHEMA)
    cls.check_schema(SCHEMA)
    return cls(SCHEMA).is_valid(instance)


def _with(**fields):
    doc = copy.deepcopy(EXAMPLE)
    doc["version"] = "1.1"
    doc["pins"][0].update(fields)
    return doc


def test_the_schema_declares_the_reader_s_vocabulary():
    assert tuple(PIN["role"]["enum"]) == pins.PIN_ROLES
    assert tuple(PIN["direction"]["enum"]) == pins.PIN_DIRECTIONS


@pytest.mark.parametrize("key,position", [("role", 0), ("direction", 1)])
def test_each_word_has_a_word_in_the_die_contract(key, position):
    terminal = CONTRACT["properties"]["terminals"]["items"]["properties"][key]["enum"]
    keyed = {k[position] for k in pins.KICAD_TYPE_OF_TERMINAL} - {None}
    assert keyed == set(terminal)


def test_type_is_named_deprecated_with_its_replacement():
    assert PIN["type"]["description"].startswith("Deprecated from 1.1")
    assert "derived from role and direction" in PIN["type"]["description"]
    assert "Supported: 1.1" in SCHEMA["properties"]["version"]["description"]


def test_a_1_0_file_with_type_only_still_validates():
    assert EXAMPLE["version"] in ("1.0", 1)
    assert _valid(EXAMPLE)


@pytest.mark.parametrize("fields", [
    {"role": "SIGNAL", "direction": "INPUT"},
    {"role": "POWER"},
    {"role": "NO_CONNECT", "direction": "NONE"},
    {"role": "SIGNAL", "direction": "ANALOG", "type": "passive"},
])
def test_a_1_1_pin_with_role_and_direction_validates(fields):
    assert _valid(_with(**fields))


@pytest.mark.parametrize("fields", [
    {"role": "Signal"},
    {"role": "signal"},
    {"role": "SIGNAL", "direction": "Input"},
    {"role": "SIGNAL", "direction": "BIDIR"},
])
def test_a_word_outside_the_vocabulary_or_its_capitals_is_refused(fields):
    assert not _valid(_with(**fields))
