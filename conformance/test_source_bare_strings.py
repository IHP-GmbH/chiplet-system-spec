# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""Executable gate for the bare strings of a source (format 1.1).

The rule is written twice, in the spec (the line rules of System and validation
rule 7) and in fixtures/source_bare_strings_cases.json, which the source reader
in description/ reads and runs case by case. This file compares the two and proves
each case against PyYAML, the YAML 1.1 loader the rule is guarding against: a
refused bare value is either re-typed by it or outside the rule, its quoted line
reads back as the string, and an accepted bare value reads back unchanged.
"""
import datetime
import itertools
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "docs" / "CHIPLET_FORMAT_SPEC.md").read_text(encoding="utf-8")
CASES = json.loads((ROOT / "conformance" / "fixtures" / "source_bare_strings_cases.json")
                   .read_text(encoding="utf-8"))
PATTERN = re.compile(CASES["pattern"] + r"\Z")
WORDS = frozenset(CASES["retyped_words"])
TYPES = {"bool": bool, "int": int, "date": datetime.date, "null": type(None), "str": str}


def bare(text):
    return bool(PATTERN.match(text)) and text.lower() not in WORDS


def _value(line, key):
    (item,) = yaml.safe_load(line)
    return item[key]


def test_the_spec_states_the_pattern_and_the_words_this_file_holds():
    assert SPEC.count(f"`{CASES['pattern']}`") >= 2  # line rules and rule 7
    rule_7 = " ".join(SPEC[SPEC.index("7. Quoting is document semantics"):
                           SPEC.index("8. A pad's")].split())
    words = ", ".join(f"`{w}`" for w in CASES["retyped_words"])
    assert words in rule_7
    assert "both reader families agree on every allowlisted word" in rule_7
    assert "keeps this rule unchanged" in rule_7


def test_the_retyped_words_are_every_bool_and_null_word_of_yaml_1_1():
    # YAML 1.1 bool (y|Y|yes|...|off|OFF) and null (null|Null|NULL); ~ is outside the pattern.
    for word in ("y", "n", "yes", "no", "true", "false", "on", "off", "null"):
        for form in itertools.product(*((c.lower(), c.upper()) for c in word)):
            assert not bare("".join(form))
    assert not bare("~") and not bare("")


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: f"{c['key']}={c['bare']}")
def test_each_case_holds_under_pyyaml(case):
    assert bare(case["bare"]) is (case["verdict"] == "accept")
    got = _value(case["line"], case["key"])
    if case["verdict"] == "accept":
        assert got == case["bare"]
        return
    assert type(got) is TYPES[case["reads_as"]]
    if case["reads_as"] != "str":
        assert got != case["bare"]
    assert _value(case["write"], case["key"]) == case["bare"]
    assert case["write"] == case["line"].replace(f"{case['key']}: {case['bare']},",
                                                 f"{case['key']}: \"{case['bare']}\",")


def test_the_cases_name_the_four_the_rule_was_written_for():
    refused = {c["bare"] for c in CASES["cases"] if c["verdict"] == "refuse" and c["key"] == "id"}
    assert {"no", "0755", "on", "2026-03-22"} <= refused
