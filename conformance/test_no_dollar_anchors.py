"""No schema regular expression ends in ``$``.

Under Python ``re`` a trailing ``$`` also matches before a final newline, so a
pattern anchored with it accepts ``"value\n"`` while the reference readers refuse
it (SPEC-11). The portable end anchor is ``(?![\\s\\S])``. This walks EVERY
committed schema and collects every regular expression it carries, both
``pattern`` values and ``patternProperties`` keys, because a grep for the word
``pattern`` missed the keys twice in one day. The list is derived, not
hand-maintained: a new schema or a new pattern is covered the moment it is
committed.

What this does not cover: patterns in prose, and regular expressions compiled in
the reference readers themselves (those are pinned by their own case tests).
The two policing tests are complements, not overlaps: a pattern that regresses
to ``$`` is caught by the dollar test and SKIPPED by the newline test (it is no
longer end-anchored in the recognised way). Never trim the dollar test.
"""
import json
import re
from pathlib import Path

import pytest

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def _regexes(node, where):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                yield f"{where}/pattern", value
            if key == "patternProperties" and isinstance(value, dict):
                for prop_pattern in value:
                    yield f"{where}/patternProperties", prop_pattern
            yield from _regexes(value, f"{where}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _regexes(value, f"{where}[{index}]")


def _all_regexes():
    found = []
    for schema_file in sorted(SCHEMAS.glob("*.schema.json")):
        doc = json.loads(schema_file.read_text(encoding="utf-8"))
        for where, regex in _regexes(doc, schema_file.name):
            found.append((where, regex))
    return found


def test_the_walk_reaches_pattern_values_and_pattern_property_keys():
    # Capability, proven against a hand-built document rather than the corpus:
    # a corpus-based check goes red for a non-defect the day the last
    # patternProperties leaves the schemas, and the tempting fix deletes it.
    doc = {
        "properties": {"a": {"type": "string", "pattern": "^x(?![\\s\\S])"}},
        "patternProperties": {"^k(?![\\s\\S])": {"type": "number"}},
        "items": [{"pattern": "^y(?![\\s\\S])"}],
    }
    found = dict(_regexes(doc, "doc"))
    assert found == {
        "doc/properties/a/pattern": "^x(?![\\s\\S])",
        "doc/patternProperties": "^k(?![\\s\\S])",
        "doc/items[0]/pattern": "^y(?![\\s\\S])",
    }, found


def test_the_derived_list_is_not_empty():
    # Floor guard: a broken glob would leave every parametrized test below with
    # an empty parameter set, which pytest reports as SKIPPED, and the file
    # would be green having run nothing. A derived list must prove it derived.
    assert len(_all_regexes()) >= 10, _all_regexes()


@pytest.mark.parametrize("where,regex", _all_regexes())
def test_no_schema_regex_ends_in_dollar(where, regex):
    assert not regex.endswith("$"), (where, regex)
    assert not regex.endswith("$)"), (where, regex)


@pytest.mark.parametrize("where,regex", [r for r in _all_regexes() if r[1].startswith("^")])
def test_an_end_anchored_regex_rejects_a_trailing_newline(where, regex):
    # A start-anchored pattern that is meant to bound the whole value must not
    # let a trailing newline through. Build a value that matches the body.
    if "(?![" not in regex and "\\Z" not in regex:
        pytest.skip(f"{where}: not end-anchored, nothing to police")
    compiled = re.compile(regex)
    sample = None
    for candidate in ("1.0", "A_B", "abc", "a", "9/35", "0", "id-1", "x.y",
                      "0" * 64):
        if compiled.search(candidate):
            sample = candidate
            break
    assert sample is not None, (where, regex, "no sample matched; extend the sample list")
    assert compiled.search(sample + "\n") is None, (where, regex)
