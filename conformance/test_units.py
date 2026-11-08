# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the closed assembly.units vocabulary (format 1.1).

The one closed vocabulary a reader must understand: no reference reader
converts lengths, so a document in another unit has no correct reading here and
is refused, where an unrecognised interface type is carried. Absent means um,
the normative default. The list is written three times (the schema enum,
``chiplet_format_io.KNOWN_UNITS``, the C++ ``kKnownUnits``); this file compares
them and exercises the Python validator. The C++ behaviour is in its own test
binary, test_units_are_closed_to_um.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

CPP_HEADER = (ROOT / "reference" / "cpp" / "include" / "chiplet_format_io"
              / "chiplet_format_io.hpp")
UNITS = json.loads((ROOT / "schemas" / "chiplet.schema.json").read_text(
    encoding="utf-8"))["definitions"]["assembly"]["properties"]["units"]


def _cpp_units():
    text = CPP_HEADER.read_text(encoding="utf-8")
    found = re.search(r"std::array<const char\*,\s*(\d+)>\s*kKnownUnits\s*=\s*"
                      r"\{\{(.*?)\}\}", text, re.DOTALL)
    assert found, f"no kKnownUnits in {CPP_HEADER}"
    members = tuple(re.findall(r'"([^"]+)"', found.group(2)))
    assert len(members) == int(found.group(1))
    return members


def test_the_schema_and_the_two_readers_declare_one_list():
    assert tuple(UNITS["enum"]) == cfio.KNOWN_UNITS == _cpp_units() == ("um",)
    assert UNITS["default"] == "um"
    assert "KNOWN_UNITS" in cfio.__all__


def _doc(**assembly):
    return {"format_version": "1.1", "assembly": {"name": "a", **assembly}}


def test_um_and_absent_read():
    cfio.validate(_doc(units="um"))
    cfio.validate(_doc())


@pytest.mark.parametrize("units", ["mm", "nm", "UM", "", "um ", 1, None])
def test_any_other_unit_is_refused(units):
    with pytest.raises(cfio.ChipletFormatError, match="assembly.units"):
        cfio.validate(_doc(units=units))


@pytest.mark.filterwarnings("ignore::chiplet_format_io.FormatVersionWarning")
def test_the_refusal_is_not_a_version_question():
    # Refused at every stamp: no minor ever gave another unit a meaning.
    for version in ("1.0", "1.1", "1.2"):
        doc = _doc(units="mm")
        doc["format_version"] = version
        with pytest.raises(cfio.ChipletFormatError, match="mm"):
            cfio.validate(doc)


def test_validate_false_still_reads_it():
    # A reader with validation off is told not to judge; the bytes still parse.
    text = 'format_version: "1.1"\nassembly:\n  name: a\n  units: "mm"\n'
    assert cfio.loads(text, validate=False)["assembly"]["units"] == "mm"
    with pytest.raises(cfio.ChipletFormatError):
        cfio.loads(text)
