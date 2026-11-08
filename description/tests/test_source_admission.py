# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Format 1.4, a die's admission: the key on a ``dies`` line at tier 0, its file
at tier 1, and what the elaborator hands the flow."""
from __future__ import annotations

import dataclasses
import json

import pytest

from chiplet_system_description._cfio import cfio
from chiplet_system_description.source import SourceError, check_text, read_source
from chiplet_system_description.source.elaborate import elaborate_source
from chiplet_system_description.source.plan import plan
from chiplet_system_description.source.writer import dumps_source

from tests.source_dies import SOURCE, checked, write_source
from tests.test_source_plan import Resolver
from tests.test_source_writer import _items

U1 = "{id: U1, die: dies/DRIVER, at: [1000, 2200], rotation: 0, method: cupillar_opt1, thickness: 750"
#: SOURCE with U1 admitted, at the minor that adds the key.
ADMITTED = SOURCE.replace('format_version: "1.1"', 'format_version: "1.4"').replace(
    U1 + "}", U1 + ", admission: admission.json}")
U1_LINE = 15
GOOD = {"reason": "a die built by a stock pad-ring flow",
        "scope": "placement and composition only; no fab statement",
        "not_verified": ["die DRC", "die LVS"]}


def _admitted(tmp_path, data=GOOD, name="admission.json"):
    path = write_source(tmp_path, ADMITTED)
    if data is not None:
        text = data if isinstance(data, str) else json.dumps(data)
        (tmp_path / name).write_text(text, encoding="utf-8")
    return path


def test_a_die_names_its_admission_at_format_1_4():
    source, findings = check_text(ADMITTED)
    assert checked(findings) == ()
    assert [(d.id, d.admission) for d in source.dies] == [("U1", "admission.json"), ("U2", None)]


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3"])
def test_below_1_4_the_key_is_refused_naming_the_minor(version):
    text = ADMITTED.replace('format_version: "1.4"', f'format_version: "{version}"')
    _, findings = check_text(text)
    refusals = [f for f in findings if not f.note]
    assert [f.line for f in refusals] == [0]
    assert "system.dies[].admission is format 1.4" in refusals[0].message


@pytest.mark.parametrize("value, says", [
    ('"/abs/admission.json"', "is absolute"),
    ('"../admission.json"', "has a '..' segment"),
    ("admission.yaml", "is not a .json file"),
    ('"${HOME}/admission.json"', "holds a variable"),
    ("[admission.json]", "dies.admission must be a non-empty string"),
])
def test_an_admission_is_a_relative_json_path(value, says):
    _, findings = check_text(ADMITTED.replace("admission: admission.json", f"admission: {value}"))
    assert [(f.line, says in f.message) for f in checked(findings)] == [(U1_LINE, True)], findings


@pytest.mark.parametrize("value", [
    "admission.json", "admissions/U1.json", '"x:a.json"', '"C:/a.json"', "'dies\\a.json'",
    "admission.yaml", '"../a.json"', '"/a.json"', '"${HOME}/a.json"',
])
def test_tier_0_refuses_on_the_die_s_line_every_path_the_vendored_reader_refuses(value):
    """Tier 0 holds the path rule stricter than the format (drive letters,
    backslashes) and never looser, so a path is never refused by the vendored
    reader alone, on line 0."""
    text = ADMITTED.replace("admission: admission.json", f"admission: {value}")
    try:
        cfio.loads(text, allow_source=True)
        vendored = True
    except cfio.ChipletFormatError:
        vendored = False
    lines = [f.line for f in checked(check_text(text)[1]) if not f.note]
    assert lines in (([], [U1_LINE]) if vendored else ([U1_LINE],))


def test_admission_is_the_last_key_of_a_die():
    moved = ADMITTED.replace("thickness: 750, admission: admission.json",
                             "admission: admission.json, thickness: 750")
    _, findings = check_text(moved)
    assert [(f.line, "keys are written in the order" in f.message)
            for f in checked(findings)] == [(U1_LINE, True)], findings


def test_tier_1_reads_a_good_admission_and_the_elaborator_hands_it_over(tmp_path):
    path = _admitted(tmp_path, dict(GOOD, about="the file's own key, not read"))
    assert checked(plan(read_source(path)).refusals) == ()
    _, extras = elaborate_source(path, Resolver())
    assert extras.admissions == (("U1", tmp_path / "admission.json"),)


def test_a_source_without_admissions_hands_over_none(tmp_path):
    _, extras = elaborate_source(write_source(tmp_path), Resolver())
    assert extras.admissions == ()


def test_the_elaborator_hands_over_each_rail_s_routing_requirement(tmp_path):
    """Format 1.5: one entry per rail that states one, in file order, with its
    line."""
    text = SOURCE.replace('format_version: "1.1"', 'format_version: "1.5"').replace(
        "{name: VSS, class: ground,", "{name: VSS, class: ground, width_um: 40,")
    _, extras = elaborate_source(write_source(tmp_path, text), Resolver())
    assert extras.rail_requirements == (("VSS", 40, None, None, 21),)
    _, extras = elaborate_source(write_source(tmp_path / "plain"), Resolver())
    assert extras.rail_requirements == ()


@pytest.mark.parametrize("data, says", [
    (None, "no such file beside the source"),
    ("{not json", "cannot be read as JSON"),
    ('["reason"]', "is not a JSON object"),
    ({"reason": "r", "scope": "s"}, "missing not_verified"),
    ({"scope": "s", "not_verified": ["x"]}, "missing reason"),
    (dict(GOOD, reason=" "), "reason is a non-empty string"),
    (dict(GOOD, scope=3), "scope is a non-empty string"),
    (dict(GOOD, not_verified=[]), "not_verified is a non-empty list"),
    (dict(GOOD, not_verified="die DRC"), "not_verified is a non-empty list"),
    (dict(GOOD, not_verified=["die DRC", ""]), "not_verified is a non-empty list"),
    ("[" * 100000, "cannot be read as JSON"),
])
def test_tier_1_refuses_an_admission_that_admits_nothing_on_the_die_s_line(tmp_path, data, says):
    path = _admitted(tmp_path, data)
    refusals = checked(plan(read_source(path)).refusals)
    assert [(f.line, says in f.message) for f in refusals] == [(U1_LINE, True)], refusals
    with pytest.raises(SourceError, match=says):
        elaborate_source(path, Resolver())


def test_a_finding_names_the_admission_as_the_source_writes_it(tmp_path):
    path = write_source(tmp_path, ADMITTED.replace("admission: admission.json",
                                                   "admission: admissions/U1.json"))
    assert [f.message for f in checked(plan(read_source(path)).refusals)] == [
        "dies: U1: admission admissions/U1.json: no such file beside the source"]


def test_the_writer_writes_the_key_and_stamps_1_4():
    items = _items()
    items["dies"] = [dataclasses.replace(items["dies"][0], admission="admission.json"),
                     items["dies"][1]]
    text = dumps_source(**items)
    assert text.startswith("# ") and 'format_version: "1.4"\n' in text
    assert ", thickness: 750, admission: admission.json}\n" in text
    source, findings = check_text(text)
    assert checked(findings) == () and source.dies[0].admission == "admission.json"
