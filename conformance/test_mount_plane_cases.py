# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The mount-plane cases against the rule they encode.

``fixtures/mount_plane/cases.json`` states, per file, what an exporter and a viewer
do with it (coord_frame_contract section 3.4). The reference readers compute no z,
so they do not run the cases; each tool that computes z runs them in its own
tests. Here ``_seat`` writes the rule down in a few lines, from the file alone,
and every expectation must be what it gives: the file's plane and never a
stackup's, the thickness only as a viewer's warned guess, an explicit z kept and,
for an exporter, checked against the file's plane plus the die's connection stack
(section 1.5 sums), and nothing to seat when the interposer has no dies.
"""
import json
import sys
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "conformance" / "fixtures" / "mount_plane"
CHIPLET_SCHEMA = json.loads((ROOT / "schemas" / "chiplet.schema.json").read_text(encoding="utf-8"))
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))["cases"]
INTM4TM2_STACK = ROOT / "conformance" / "fixtures" / "technology_package" / "intm4tm2" / "libs.tech" / "intm4tm2_stack.json"
ROLES = ("exporter", "viewer")

sys.path.insert(0, str(ROOT / "reference" / "python"))
import chiplet_format_io as cfio  # noqa: E402


@lru_cache(maxsize=None)
def _load(name):
    return cfio.load(FIXTURES / name)


def _interposer(doc):
    (interposer,) = [c for c in doc["components"] if c["type"] == "interposer"]
    return interposer


def _height(doc, die):
    """The die's connection stack height, 0 when it names none the file defines."""
    stack = (doc.get("connection_stacks") or {}).get(die.get("connection"))
    return sum((Decimal(str(layer["height"])) for layer in (stack or {}).get("layers", [])),
               Decimal(0))


def _seat(doc, role):
    """(outcome, warns, {die id: bottom z}); warns is None where the rule leaves it open."""
    interposer = _interposer(doc)
    dies = [c for c in doc["components"] if c["type"] in ("die", "die_array")]
    if not dies:
        return "accept", False, {}
    plane = interposer.get("attachment_surface_z")
    if plane is None and role == "exporter":
        return "refuse", None, None
    base = (Decimal(str(interposer["position"]["z"]))
            + Decimal(str(interposer["dimensions"]["thickness"] if plane is None else plane)))
    seated, off_plane = {}, False
    for d in dies:
        on_plane = base + _height(doc, d)
        z = Decimal(str(d["position"]["z"])) or on_plane
        off_plane |= z != on_plane
        seated[d["id"]] = float(z)
    if off_plane and role == "exporter":
        return "refuse", None, None
    return "seat", True if plane is None else (None if off_plane else False), seated


def test_the_cases_cover_every_file_once():
    named = [case["file"] for case in CASES]
    assert len(named) == len(set(named))
    assert sorted(named) == sorted(p.name for p in FIXTURES.glob("*.chiplet"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["file"])
def test_the_file_reads_fits_the_schema_and_declares_the_named_plane(case):
    doc = _load(case["file"])
    cls = validator_for(CHIPLET_SCHEMA)
    cls(CHIPLET_SCHEMA).validate(yaml.safe_load((FIXTURES / case["file"]).read_text(encoding="utf-8")))
    interposer = _interposer(doc)
    assert interposer.get("attachment_surface_z") == case["plane"]
    assert interposer["dimensions"]["thickness"] != case["plane"]
    named = [c["id"] for c in doc["components"] if c.get("connection")]
    assert not named if not case["file"].startswith("explicit_z") else named == ["U1", "U2"]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["file"])
def test_the_expectation_is_what_the_rule_gives(case, role):
    want = case[role]
    assert _seat(_load(case["file"]), role) == (want["outcome"], want.get("warns"), want.get("dies"))


def test_the_cases_cover_every_outcome():
    seen = {(role, case[role]["outcome"]) for case in CASES for role in ROLES}
    assert {("exporter", "refuse"), ("viewer", "seat"), ("exporter", "accept")} <= seen
    assert any(case["viewer"].get("warns") for case in CASES)


@pytest.mark.parametrize("case", [case for case in CASES if "stackup_plane" in case],
                         ids=lambda case: case["file"])
def test_an_off_stackup_case_is_off_its_stackup(case):
    doc = _load(case["file"])
    stackup_file = doc["technologies"][_interposer(doc)["technology"]]["stackup"]
    stackup = yaml.safe_load((FIXTURES / stackup_file).read_text(encoding="utf-8"))
    assert stackup["attachment_surface_z"] == case["stackup_plane"]
    assert case["stackup_plane"] != case["plane"]


def test_the_refused_explicit_z_sits_on_the_stackup_plane():
    """The one refusal of an explicit z: each die at the stackup's plane plus its
    stack, which is what a tool that took the stackup's plane would accept."""
    (case,) = [case for case in CASES if case["file"].startswith("explicit_z")
               and case["exporter"]["outcome"] == "refuse"]
    doc = _load(case["file"])
    for die in [c for c in doc["components"] if c["type"] == "die"]:
        assert Decimal(str(die["position"]["z"])) == (Decimal(str(case["stackup_plane"]))
                                                      + _height(doc, die))


def test_the_intm4tm2_case_declares_the_interposer_pdk_key():
    key = json.loads(INTM4TM2_STACK.read_text(encoding="utf-8"))["beol"]["attachment_surface_z_um"]
    planes = [case["plane"] for case in CASES if _interposer(_load(case["file"]))["technology"] == "intm4tm2"]
    assert planes and set(planes) == {key}
