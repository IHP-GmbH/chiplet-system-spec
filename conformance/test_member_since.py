# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the since-minor of closed-vocabulary members.

A closed enum gains members by MINOR, and a member may not be written in a
document stamped below the minor that added it. That prohibition is executable
from the document alone because each member added after 1.0 carries its minor
as schema data: an ``x-since`` object beside its enum in
``schemas/chiplet.schema.json``. The map is written three times (the schema,
``chiplet_format_io.MEMBER_SINCE``, the C++ ``kMemberSince``), so this file
reads all three and compares them, runs the shared verdict oracle through the
Python validator, and checks that the writer never stamps below the content.

What a green here does NOT cover: the C++ reader's behaviour on the oracle (its
own test binary runs the same file, test_member_since_matches_the_oracle).
"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "reference" / "python"))

import chiplet_format_io as cfio  # noqa: E402

FIXTURES = ROOT / "conformance" / "fixtures"
CPP_HEADER = (ROOT / "reference" / "cpp" / "include" / "chiplet_format_io"
              / "chiplet_format_io.hpp")
CHIPLET_SCHEMA = json.loads(
    (ROOT / "schemas" / "chiplet.schema.json").read_text(encoding="utf-8"))
ORACLE = json.loads(
    (FIXTURES / "member_since_cases.json").read_text(encoding="utf-8"))

#: Where each vocabulary of MEMBER_SINCE lives in the schema.
SCHEMA_PATHS = {
    "interfaces[].type": ("interface", "type"),
    "io_pads[].io_class": ("io_pad", "io_class"),
    "components[].anchor": ("component", "anchor"),
    "components[].orientation": ("component", "orientation"),
    "assembly.units": ("assembly", "units"),
}

#: Enums that need no since-map, each with the reason. Every enum in the schema
#: is in SCHEMA_PATHS or here, so a new one has to be classified.
NO_SINCE_MAP = {
    ("system_die", "rotation"): "the system block is format 1.1 as a whole",
    ("system_rail", "class"): "the system block is format 1.1 as a whole",
}


def _schema_enums():
    found = set()
    for name, definition in CHIPLET_SCHEMA["definitions"].items():
        for prop, body in definition.get("properties", {}).items():
            if isinstance(body, dict) and "enum" in body:
                found.add((name, prop))
    return found


def _schema_since(field):
    name, prop = SCHEMA_PATHS[field]
    return CHIPLET_SCHEMA["definitions"][name]["properties"][prop].get("x-since", {})


def _cpp_member_since():
    """The rows of kMemberSince, read out of the C++ header text."""
    text = CPP_HEADER.read_text(encoding="utf-8")
    found = re.search(
        r"std::array<MemberSince,\s*(\d+)>\s*kMemberSince\s*\{(.*?)\};",
        text, re.DOTALL)
    assert found, f"no kMemberSince initializer in {CPP_HEADER}"
    rows = re.findall(r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\}',
                      found.group(2))
    assert len(rows) == int(found.group(1)), "kMemberSince size and rows differ"
    out = {field: {} for field in cfio.MEMBER_SINCE}
    for field, member, since in rows:
        assert field in out, f"kMemberSince names an unknown field {field!r}"
        out[field][member] = since
    return out


def test_the_python_reference_exports_the_map():
    for name in ("MEMBER_SINCE", "check_member_since", "content_format_floor"):
        assert name in cfio.__all__
    assert set(cfio.MEMBER_SINCE) == set(SCHEMA_PATHS)


def test_every_schema_enum_is_classified():
    known = {SCHEMA_PATHS[f] for f in SCHEMA_PATHS} | set(NO_SINCE_MAP)
    assert _schema_enums() == known


def test_the_schema_and_the_two_readers_declare_one_map():
    schema = {field: _schema_since(field) for field in SCHEMA_PATHS}
    assert schema == cfio.MEMBER_SINCE
    assert _cpp_member_since() == cfio.MEMBER_SINCE


@pytest.mark.parametrize("field", sorted(SCHEMA_PATHS))
def test_every_tagged_member_is_in_its_enum_and_readable(field):
    # A tag on a non-member is dead, and a tag above the minor this reader
    # implements claims a member the reader cannot know about yet.
    name, prop = SCHEMA_PATHS[field]
    enum = CHIPLET_SCHEMA["definitions"][name]["properties"][prop]["enum"]
    for member, since in _schema_since(field).items():
        assert member in enum, (field, member)
        assert since != "1.0", f"{field} {member!r}: absent already means 1.0"
        cfio.check_format_version(since)  # malformed or other major raises


def test_no_member_postdates_this_schema_outside_an_x_since_block():
    for name in NO_SINCE_MAP:
        body = CHIPLET_SCHEMA["definitions"][name[0]]["properties"][name[1]]
        assert "x-since" not in body, name


# --- the oracle ------------------------------------------------------------
@pytest.mark.parametrize("case", ORACLE["cases"], ids=lambda c: c["name"])
def test_check_member_since_matches_the_oracle(case):
    since = {}
    for field, member, minor in case["since"]:
        since.setdefault(field, {})[member] = minor
    call = lambda: cfio.check_member_since(  # noqa: E731
        case["declared"], case["field"], case["member"], since)
    if case["verdict"] == "refuse":
        with pytest.raises(cfio.ChipletFormatError, match=re.escape(case["member"])):
            call()
    else:
        assert case["verdict"] == "ok"
        call()


def test_the_oracle_has_both_verdicts():
    verdicts = {c["verdict"] for c in ORACLE["cases"]}
    assert verdicts == {"ok", "refuse"}


def test_a_non_string_member_is_left_to_other_rules():
    cfio.check_member_since("1.0", "interfaces[].type", 7,
                            {"interfaces[].type": {"7": "1.1"}})


# --- the validator and the writer, with a map patched in --------------------
def _doc(version, itype):
    return {"format_version": version, "assembly": {"name": "a"},
            "interfaces": [{"id": "i1", "type": itype}]}


@pytest.fixture
def tagged(monkeypatch):
    patched = copy.deepcopy(cfio.MEMBER_SINCE)
    patched["interfaces[].type"]["copper_pillar"] = "1.1"
    monkeypatch.setattr(cfio, "MEMBER_SINCE", patched)


def test_the_validator_refuses_a_member_below_its_minor(tagged):
    with pytest.raises(cfio.ChipletFormatError, match="copper_pillar"):
        cfio.validate(_doc("1.0", "copper_pillar"))
    cfio.validate(_doc("1.1", "copper_pillar"))
    cfio.validate(_doc("1.0", "micro_bump"))


def test_the_content_floor_is_the_highest_member_minor(tagged):
    assert cfio.content_format_floor(_doc("1.0", "micro_bump")) is None
    assert cfio.content_format_floor(_doc("1.1", "copper_pillar")) == "1.1"


def test_the_writer_never_stamps_below_the_content(tagged):
    # Unvalidated, so the out-of-contract input reaches the writer: the stamp
    # it writes must still describe the bytes.
    out = cfio.dumps(_doc("1.0", "copper_pillar"), validate=False)
    assert cfio.loads(out)["format_version"] == "1.1"
    out = cfio.dumps({"assembly": {"name": "a"},
                      "interfaces": [{"id": "i1", "type": "copper_pillar"}]},
                     validate=False)
    assert cfio.loads(out)["format_version"] == "1.1"
    out = cfio.dumps(_doc("1.0", "micro_bump"))
    assert cfio.loads(out)["format_version"] == cfio.SUPPORTED_FORMAT_VERSION


def test_the_validator_refuses_nothing_with_the_real_map_on_the_corpus():
    # The real map's members are in contract wherever the corpus uses them.
    for path in sorted(FIXTURES.glob("*.chiplet")):
        data = cfio.yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        version = str(data.get("format_version"))
        for field, member in cfio._vocabulary_members(data):
            try:
                cfio.check_member_since(version, field, member)
            except cfio.ChipletFormatError as exc:
                pytest.fail(f"{path.name}: {exc}")


#: Where each source key of SOURCE_KEY_SINCE lives in the schema: a definition
#: and its property, or None and a root property.
SOURCE_KEY_PATHS = {"system.rails[].volts": ("system_rail", "volts"),
                    "unplaced": (None, "unplaced"),
                    "system.dies[].admission": ("system_die", "admission"),
                    "system.rails[].width_um": ("system_rail", "width_um"),
                    "system.rails[].amps": ("system_rail", "amps"),
                    "system.rails[].current_density_ma_per_um":
                        ("system_rail", "current_density_ma_per_um")}


def test_the_source_key_since_map_is_the_schema_s():
    """A key added to the source after 1.1 carries its minor as a string
    ``x-since`` on its property, and SOURCE_KEY_SINCE is that data: every such
    property, root keys included, is mapped, and every mapped key says the
    schema's minor. The C++ reader has no source path, so it has no copy to
    compare."""
    owners = [(None, CHIPLET_SCHEMA)] + list(CHIPLET_SCHEMA["definitions"].items())
    tagged = {(name, prop): body["x-since"]
              for name, definition in owners
              for prop, body in definition.get("properties", {}).items()
              if isinstance(body, dict) and isinstance(body.get("x-since"), str)}
    assert set(tagged) == set(SOURCE_KEY_PATHS.values())
    assert {path: tagged[where] for path, where in SOURCE_KEY_PATHS.items()} == \
        cfio.SOURCE_KEY_SINCE
